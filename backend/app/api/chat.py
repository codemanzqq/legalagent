"""
流式问答接口：使用 SSE（Server-Sent Events）向浏览器推送增量文本。

事件格式：`data: {"chunk":"..."}\\n\\n`，结束时发送 `data: [DONE]\\n\\n`。
一轮结束后若携带 `user_external_id`，将本轮完整问答写入 `his_chat_tab`（闲聊引导与专业回答均落库）。
"""

from __future__ import annotations

import json  # 将片段序列化为 JSON 字符串嵌入 SSE 的 data: 行
import logging

from fastapi import APIRouter, Depends  # 路由器与依赖注入
from fastapi.responses import StreamingResponse  # 流式 HTTP 响应（text/event-stream）

from backend.app.deps import get_pipeline  # 注入 RAG 管线单例
from backend.app.schemas import ChatRequest  # 请求体验证模型
from modules.memory.service import persist_user_turn  # 一轮结束后写入 his_chat_tab
from modules.rag.pipeline import RagPipeline  # 管线类型注解

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])  # 挂载后完整路径含 main 中的 /api 前缀 → /api/chat/stream


@router.post("/stream")
async def chat_stream(
    body: ChatRequest,
    pipeline: RagPipeline = Depends(get_pipeline),
) -> StreamingResponse:
    """接收用户消息，异步迭代管线输出并封装为 SSE。"""

    async def gen():
        """内部异步生成器：逐段 yield SSE 行；成功结束时写入用户聊天历史。"""
        buf: list[str] = []  # 累积助手全文，供落库
        try:
            async for piece in pipeline.stream_chat(body.message, body.user_external_id):  # 流式字符串片段
                buf.append(piece)
                yield f"data: {json.dumps({'chunk': piece}, ensure_ascii=False)}\n\n"  # 每条 SSE 事件一行 JSON
            yield "data: [DONE]\n\n"  # 约定结束标记，前端可据此关闭读流
        except Exception as exc:  # noqa: BLE001 — 将异常传递给前端便于排查
            err = {"error": str(exc)}
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
            return  # 出错则不尝试 persist（答案不完整）
        full_answer = "".join(buf)
        try:
            await persist_user_turn(body.user_external_id, body.message, full_answer)  # 无 external_id 时函数内直接返回
        except Exception:
            logger.exception("persist chat history failed")

    return StreamingResponse(gen(), media_type="text/event-stream")  # 浏览器 fetch 流式解析 SSE
