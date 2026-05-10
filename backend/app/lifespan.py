"""
应用生命周期（lifespan）：启动时初始化数据库与 Milvus，可选启动后台热更新任务。

热更新开关与间隔来自 `.env`：`HOT_UPDATE_ENABLED`、`HOT_UPDATE_INTERVAL_SECONDS`。
"""

from __future__ import annotations

import asyncio  # 异步 IO 与后台任务
import logging  # 日志
from collections.abc import AsyncIterator  # 异步生成器类型注解
from contextlib import asynccontextmanager  # 将异步上下文管理器用于 FastAPI lifespan

from fastapi import FastAPI  # 仅用于类型提示 lifespan 参数

from modules.core.config import get_settings  # 读取配置
from modules.database.models import Base  # ORM 元数据，用于 create_all
from modules.database.session import get_async_engine  # 异步引擎工厂
from modules.ingestion.milvus_sync import run_sync_job  # MySQL → Milvus 全量同步
from modules.milvus_store.client import ensure_milvus  # 建立 PyMilvus 连接

logger = logging.getLogger(__name__)


async def _ensure_tables() -> None:
    """若表不存在则创建（幂等）；与离线脚本共用同一套模型定义。"""
    engine = get_async_engine()  # 获取单例异步引擎
    async with engine.begin() as conn:  # 开启事务上下文
        await conn.run_sync(Base.metadata.create_all)  # 同步函数包一层以在 async 中执行


async def _ensure_milvus() -> None:
    """Web 进程与离线脚本进程分离，必须在服务启动时单独 connect，否则首次向量检索报错。"""
    await asyncio.to_thread(ensure_milvus)  # pymilvus 为同步 API，放到线程池避免阻塞事件循环


async def _hot_update_loop(stop: asyncio.Event) -> None:
    """后台循环：按固定间隔触发一次 Milvus 全量同步（开发期简化一致性）。"""
    settings = get_settings()  # 读取最新配置（若支持热重载可生效）
    interval = float(settings.effective_hot_update_interval_seconds)  # <=0 时回退为 60 秒
    logger.info("milvus hot-update worker started, interval=%ss", interval)
    while not stop.is_set():  # 收到停止信号前一直运行
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)  # 等待 stop 或超时
        except asyncio.TimeoutError:
            pass  # 超时表示到达下一轮同步周期
        else:
            break  # stop 被设置，退出循环
        if not settings.hot_update_enabled:  # 运行中关闭热更新则跳过本次
            continue
        try:
            await run_sync_job()  # 执行全量同步（内部会 recreate 集合，视实现而定）
        except Exception as exc:  # noqa: BLE001 — 后台任务不因单次失败退出
            logger.exception("hot milvus sync failed: %s", exc)


@asynccontextmanager
async def app_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """FastAPI lifespan：yield 之前做启动逻辑，yield 之后做清理逻辑。"""
    await _ensure_tables()  # 先保证 MySQL 表结构存在
    await _ensure_milvus()  # 再连接向量库
    settings = get_settings()
    stop = asyncio.Event()  # 用于通知后台任务结束
    task: asyncio.Task | None = None  # 后台任务句柄
    if settings.hot_update_enabled:  # 仅开启时启动同步循环
        task = asyncio.create_task(_hot_update_loop(stop))
    try:
        yield  # 此处开始对外提供服务
    finally:
        stop.set()  # 通知循环退出
        if task:  # 若曾创建任务则取消并等待
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass  # 取消是预期行为
