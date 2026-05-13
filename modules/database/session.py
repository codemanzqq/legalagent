# =============================================================================
# 教学说明：本文件在整体链路中的位置
# -----------------------------------------------------------------------------
# 输入：`get_settings().mysql_dsn_async`（由 `.env` 决定连接串）。
# 输出：`AsyncEngine`（连接池）与 `async_sessionmaker`（每次请求拿一个 `AsyncSession`）。
# 被谁调用：`mysql_loaders`、`milvus_sync`（读 MySQL）、`pipeline._fetch_parents`、`memory.service`、
#          `lifespan._ensure_tables` 等所有需要异步访问 MySQL 的模块。
# =============================================================================
"""
异步数据库引擎与会话工厂。

`pool_pre_ping=True`：从池里取出连接前先 ping MySQL，断线则丢弃并重连，避免半夜 MySQL 重启后首请求报错。
"""

from __future__ import annotations  # 允许在类型注解里写尚未定义的类名（Python 3.10+ 也可省略部分场景）

from functools import lru_cache  # 引擎与 sessionmaker 各缓存一次，全进程单例

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine  # SQLAlchemy 2.x 异步 API

from modules.core.config import get_settings  # 读取 DSN 与各配置


@lru_cache
def get_async_engine() -> AsyncEngine:
    """
    创建（或返回已缓存的）异步引擎：底层用 aiomysql 驱动。
    """
    settings = get_settings()  # 单例配置
    return create_async_engine(
        settings.mysql_dsn_async,  # 形如 mysql+aiomysql://user:pass@host:port/db
        pool_pre_ping=True,  # 借连接前检测是否存活
        pool_size=20,  # 池中常驻连接数；并发高时可调大
        max_overflow=40,  # 瞬时超出 pool_size 时最多再建多少条「溢出连接」
        echo=False,  # True 会在日志打印每条 SQL，仅调试打开
    )


@lru_cache
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """
    返回绑定到上面引擎的「会话工厂」：`async with factory() as session:` 获取会话。

    `expire_on_commit=False`：commit 之后内存里的 ORM 对象属性仍可访问，不会被强制过期重查。
    """
    return async_sessionmaker(get_async_engine(), expire_on_commit=False)  # 第一个参数是引擎；第二个控制提交后对象状态
