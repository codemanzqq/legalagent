"""
小易 RAG — FastAPI 入口模块。

启动示例（在项目根目录执行）::

    uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --workers 1

说明：本地 Embedding/Rerank 体积大，多 worker 会重复占内存；横向扩展建议拆向量服务或使用外部推理网关。
"""

from __future__ import annotations  # PEP 563：推迟注解求值，避免前向引用类名需加引号

import logging  # 标准库日志，默认输出到 stderr

from modules.core.strip_proxy_env import strip_http_proxy_environment  # 清除进程内代理环境变量

strip_http_proxy_environment()  # 必须在发起任何 HTTP 客户端请求之前执行，避免误走 HTTP_PROXY

from fastapi import FastAPI  # Web 框架
from fastapi.middleware.cors import CORSMiddleware  # 跨域中间件，供浏览器前端访问

from backend.app.api.chat import router as chat_router  # 流式问答路由
from backend.app.lifespan import app_lifespan  # 启动/关闭钩子（建表、Milvus、热更新）
from backend.app.schemas import HealthResponse  # 健康检查响应模型
from modules.core.config import get_settings  # 读取 .env 配置

logging.basicConfig(level=logging.INFO)  # 默认 INFO 级别，便于观察检索与同步日志
logger = logging.getLogger(__name__)  # 当前模块日志记录器

settings = get_settings()  # 单例配置（进程内缓存）

app = FastAPI(
    title="小易 RAG API",
    version="0.1.0",
    lifespan=app_lifespan,
)  # 创建应用并绑定生命周期上下文管理器

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)  # 允许前端开发机（如 localhost:5173）跨域调用 API


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """极简探活接口；负载均衡或运维脚本可轮询此路径。"""
    return HealthResponse()  # 返回默认 ok=True


app.include_router(chat_router, prefix="/api")  # 注册聊天路由，完整前缀为 /api/chat/...
