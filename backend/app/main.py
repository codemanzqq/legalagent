# =============================================================================
# 教学说明：本文件在整体链路中的位置
# -----------------------------------------------------------------------------
# 输入：进程环境 / `.env`（经 `get_settings()`）；Uvicorn 加载本模块时执行模块级代码。
# 输出：全局变量 `app`（FastAPI 实例），含 CORS、`/health`、挂载 `/api/chat/...`。
# 被谁调用：Uvicorn / Gunicorn 以 `backend.app.main:app` 为入口；不由业务代码 import 执行启动逻辑。
# =============================================================================
"""
FastAPI 应用工厂文件：`app = FastAPI(lifespan=...)` 在 import 时创建。

`lifespan` 在第一个请求前跑启动钩子（建表、连 Milvus、热更新任务）。
"""

from __future__ import annotations  # 推迟注解求值

import logging  # 配置 basicConfig 后各模块 logger 生效

from fastapi import FastAPI  # Web 框架主类
from fastapi.middleware.cors import CORSMiddleware  # Starlette 层 CORS 中间件

from backend.app.api.chat import router as chat_router  # 聊天 SSE 路由
from backend.app.lifespan import app_lifespan  # asynccontextmanager：启动/关闭逻辑
from backend.app.schemas import HealthResponse  # Pydantic 响应模型
from modules.core.config import get_settings  # 读 CORS 白名单等

logging.basicConfig(level=logging.INFO)  # 根日志级别 INFO：控制台可见 uvicorn 与业务日志
logger = logging.getLogger(__name__)  # 本模块 logger（当前 health 路由几乎不打日志）

settings = get_settings()  # 模块 import 时读一次配置（单例缓存）

app = FastAPI(
    title="小易 RAG API",
    version="0.1.0",
    lifespan=app_lifespan,
)  # 创建应用；lifespan 绑定到 app 生命周期

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list(),  # 列表形式 Origin 白名单
    allow_credentials=True,  # 允许带 Cookie（本项目 mainly JSON，但开无妨）
    allow_methods=["*"],  # 允许任意 HTTP 方法（含 OPTIONS 预检）
    allow_headers=["*"],  # 允许任意请求头
)  # 加在最外层：先于路由执行


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """
    GET /health：运维探活；不查数据库，固定返回 ok。
    """
    return HealthResponse()  # Pydantic 模型默认字段 ok=True


app.include_router(chat_router, prefix="/api")  # 路由前缀叠加：chat_router 自带 /chat → 完整 /api/chat/...
