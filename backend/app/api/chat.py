# =============================================================================
# 教学说明：本文件在整体链路中的位置
# -----------------------------------------------------------------------------
# 输入：HTTP POST JSON 体（`ChatRequest`：`message`、`user_external_id` 可选）。
# 输出：`StreamingResponse`，`media_type=text/event-stream`，body 为 SSE 文本流。
# 被谁调用：浏览器或前端 `fetch("/api/chat/stream")`；路由由 `main.py` 挂载到 `/api` 前缀下。
# =============================================================================
"""
SSE 约定：每行 `data: {...}\n\n`；结束行 `data: [DONE]\n\n`。

成功流完后调用 `persist_user_turn` 把整轮问答写入 MySQL（与流式生成解耦）。
"""

from __future__ import annotations

import json  # dumps 把 dict 变成 JSON 字符串嵌入 SSE
import logging

from fastapi import APIRouter, Depends  # APIRouter 分组路由；Depends 注入依赖
from fastapi.responses import StreamingResponse  # 流式响应类

from backend.app.deps import get_pipeline  # 单例 RagPipeline 提供者
from backend.app.schemas import ChatRequest  # Pydantic：校验 JSON 字段类型与长度
from modules.memory.service import persist_user_turn  # 写 his_chat_tab
from modules.rag.pipeline import RagPipeline  # 类型注解

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])  # 最终路径 = /api + /chat + /stream


@router.post("/stream")
async def chat_stream(
    body: ChatRequest,
    pipeline: RagPipeline = Depends(get_pipeline),
) -> StreamingResponse:
    """
    异步生成器 `gen`：迭代 `pipeline.stream_chat`，把每个片段包装成 SSE 行。

    依赖注入：`pipeline` 由框架调用 `get_pipeline()` 填入。
    """

    async def gen():
        """
        内部 async generator：不能用普通 def，否则无法 async for。
        """
        buf: list[str] = []  # 累积所有 yield 的助手片段，最后拼接为 full_answer
        try:
            async for piece in pipeline.stream_chat(body.message, body.user_external_id):  # 主 RAG 循环
                buf.append(piece)  # 累积
                yield f"data: {json.dumps({'chunk': piece}, ensure_ascii=False)}\n\n"  # SSE 要求双换行结束事件
            yield "data: [DONE]\n\n"  # 前端据此知道流结束
        except Exception as exc:  # noqa: BLE001 — 任意异常转成 JSON 事件给前端展示
            err = {"error": str(exc)}
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
            return  # 出错不再 persist，避免存半截答案
        full_answer = "".join(buf)  # 完整助手回复
        try:
            await persist_user_turn(body.user_external_id, body.message, full_answer)  # 无 external_id 时内部 return
        except Exception:
            logger.exception("persist chat history failed")  # 落库失败打栈，但不影响已发送的 SSE

    return StreamingResponse(gen(), media_type="text/event-stream")  # 浏览器 EventSource/fetch 流式可读
