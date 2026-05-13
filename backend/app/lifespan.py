# =============================================================================
# 教学说明：本文件在整体链路中的位置
# -----------------------------------------------------------------------------
# 输入：FastAPI `app` 实例（仅类型用）；配置项 `HOT_UPDATE_ENABLED`、间隔秒数。
# 输出：`asynccontextmanager`：yield 前完成建表、连 Milvus、可选后台任务；yield 后取消任务。
# 被谁调用：`main.py` 中 `FastAPI(..., lifespan=app_lifespan)`，由 ASGI 服务器在启动/关闭时驱动。
# =============================================================================
"""
启动顺序：MySQL 表结构 ensure → Milvus connect →（可选）后台循环全量同步 MySQL→Milvus。

热更新适合开发环境「改库后自动刷新向量」；生产需评估全量 recreate 成本。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from modules.core.config import get_settings
from modules.database.models import Base
from modules.database.session import get_async_engine
from modules.ingestion.milvus_sync import run_sync_job
from modules.milvus_store.client import ensure_milvus

logger = logging.getLogger(__name__)


async def _ensure_tables() -> None:
    """
    `Base.metadata.create_all`：已存在的表不会删数据，只补缺失表（开发友好）。
    """
    engine = get_async_engine()
    async with engine.begin() as conn:  # begin 自动事务
        await conn.run_sync(Base.metadata.create_all)  # SQLAlchemy 同步 API 包到 run_sync


async def _ensure_milvus() -> None:
    """
    `ensure_milvus` 为同步阻塞：用 to_thread 避免阻塞 asyncio 启动阶段其它协程。
    """
    await asyncio.to_thread(ensure_milvus)


async def _hot_update_loop(stop: asyncio.Event) -> None:
    """
    循环：每 `interval` 秒或被 `stop` 唤醒；超时则尝试 `run_sync_job()`。

    `stop.wait()` 与 `wait_for` 结合：既支持定时又支持优雅退出。
    """
    settings = get_settings()
    interval = float(settings.effective_hot_update_interval_seconds)
    logger.info("milvus hot-update worker started, interval=%ss", interval)
    while not stop.is_set():  # 主循环直到 stop 被 set
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)  # 等 stop 或超时
        except asyncio.TimeoutError:
            pass  # 正常定时唤醒
        else:
            break  # stop.wait() 返回说明 Event 已触发，退出 while
        if not settings.hot_update_enabled:  # 运行中可在下一轮检查（注：settings 非热重载单例细节略）
            continue
        try:
            await run_sync_job()  # 异步全量同步（内部 to_thread 写 Milvus）
        except Exception as exc:  # noqa: BLE001
            logger.exception("hot milvus sync failed: %s", exc)


@asynccontextmanager
async def app_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    FastAPI 约定：`yield` 之前是 startup，`yield` 之后是 shutdown（在 finally 语义里）。

    `yield` 的值类型此处为 `None`，表示不向上下文块传递对象。
    """
    await _ensure_tables()
    await _ensure_milvus()
    settings = get_settings()
    stop = asyncio.Event()  # 子任务看此事件决定是否退出
    task: asyncio.Task | None = None
    if settings.hot_update_enabled:
        task = asyncio.create_task(_hot_update_loop(stop))  # 丢到事件循环并发执行
    try:
        yield  # 此处放行：开始接受 HTTP 请求
    finally:
        stop.set()  # 通知后台循环结束
        if task:
            task.cancel()  # 向 Task 注入 CancelledError
            try:
                await task  # 等待任务真正结束
            except asyncio.CancelledError:
                pass  # 取消是预期路径
