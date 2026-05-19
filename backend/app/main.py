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
from fastapi.middleware.cors import CORSMiddleware  # 导入「跨域中间件」—— 解决前端页面调用后端接口时的「跨域报错

from backend.app.api.chat import router as chat_router  # 聊天 SSE 路由
from backend.app.lifespan import app_lifespan  # asynccontextmanager：启动/关闭逻辑
from backend.app.schemas import HealthResponse  # Pydantic 响应模型「健康检查的响应格式」—— 规定 /health 接口返回的数据长什么样
from modules.core.config import get_settings  # 读 CORS 白名单等

logging.basicConfig(level=logging.INFO)  # 根日志级别 INFO：控制台可见 uvicorn 与业务日志
logger = logging.getLogger(__name__)  # 本模块 logger（当前 health 路由几乎不打日志）

settings = get_settings()  # 模块 import 时读一次配置（单例缓存）

app = FastAPI(
    title="小易 RAG API",
    version="0.1.0",
    lifespan=app_lifespan,
)  # 创建应用；lifespan 绑定到 app 生命周期，在服务「刚启动」时做一些初始化操作（比如建数据库表、连向量数据库 Milvus），关闭时优雅停止后台任务，释放资源

#给 app 服务添加「跨域中间件」，解决前端调用后端接口的跨域问题：
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list(),  # 只允许配置文件里的「白名单域名」访问
    allow_credentials=True,  # 允许前端请求带 Cookie（比如用户登录后的凭证）；
    allow_methods=["*"],  # 允许任意 HTTP 方法（含 OPTIONS 预检）
    allow_headers=["*"],  # 允许任意请求头
)  # 加在最外层：先于路由执行


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """
    给 app 服务添加一个 GET 类型的接口，路径是 /health, 返回一个 HealthResponse 实例，默认 ok=True。
    用途：运维人员 / 服务器会定期调用这个接口，如果返回 ok=True，说明服务还活着；如果没返回，说明服务挂了，需要告警。。

    入参:
        无。
    返回:
        `HealthResponse` 实例（默认 ok=True）。
    """
    return HealthResponse()  # Pydantic 模型默认字段 ok=True

#添加业务核心接口
app.include_router(chat_router, prefix="/api")  # 路由前缀叠加：chat_router 自带 /chat → 完整 /api/chat/...
