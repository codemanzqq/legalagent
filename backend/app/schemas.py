"""
Pydantic 模型：
核心作用是用 Pydantic 做数据校验，这是 FastAPI 项目的最佳实践 —— 前端传过来的参数、后端返回的结果，
都要通过 Pydantic 模型约束，既保证数据合法性，又能自动生成 OpenAPI 文档。
"""

from pydantic import BaseModel, Field  # BaseModel：声明数据类；Field：字段约束、默认值与 OpenAPI 文档


class ChatRequest(BaseModel):
    """
    前端 POST /api/chat/stream 的 JSON 体。

    - `message`：本轮用户输入。
    - `user_external_id`：可选；用于绑定 `users_tab` / `his_chat_tab`。不传则等同匿名纯 RAG（不落库、不按用户查历史）。
    """

    message: str = Field(
        ...,
        min_length=1,
        max_length=8000,
        description="用户自然语言问题",
    )  # ... 表示必填；长度限制防止超大正文撑爆上下文
    user_external_id: str | None = Field(
        default=None,
        max_length=128,
        description="客户端稳定用户标识（建议 UUID）；缺省时不写入聊天历史、不检索记忆",
    )  # None：匿名；非空：启用记忆持久化与自述历史类查询


class HealthResponse(BaseModel):
    """GET /health 返回的简单状态（便于负载均衡或探活）。
    是/health接口的响应模型，固定返回ok=True表示进程存活，assistant字段是对外展示的名称，和前端约定一致即可。
    """

    ok: bool = True  # 固定为 True 表示进程存活（尚未对接 DB/Milvus 深度检查）
    assistant: str = "小易"  # 对外展示名称，可与前端约定一致
