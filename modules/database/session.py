"""
异步数据库引擎与会话工厂：业务层通过 `async_sessionmaker` 获取 `AsyncSession`。

连接池参数可按部署规模调整；`pool_pre_ping` 可在 MySQL 端重启后自动捡活连接。
"""

from __future__ import annotations

from functools import lru_cache  # 引擎与会话工厂进程内单例

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from modules.core.config import get_settings  # DSN 来源


@lru_cache
def get_async_engine() -> AsyncEngine:
    """创建异步引擎（每个进程一份）：URL 使用 mysql+aiomysql。"""
    settings = get_settings()
    return create_async_engine(
        settings.mysql_dsn_async,
        pool_pre_ping=True,  # 使用前 ping，避免断连报错
        pool_size=20,  # 常驻连接数
        max_overflow=40,  # 超出 pool_size 时短时额外连接上限
        echo=False,  # True 时打印 SQL，一般仅调试开启
    )


@lru_cache
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """返回绑定到上述引擎的会话工厂；`expire_on_commit=False` 便于提交后仍访问属性。"""
    return async_sessionmaker(get_async_engine(), expire_on_commit=False)
