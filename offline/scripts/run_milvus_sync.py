"""
命令行入口：在 MySQL 已有数据的前提下，将 FAQ 与法律子块全量写入 Milvus。

用法::

    python offline/scripts/run_milvus_sync.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]  # 仓库根，保证直接运行脚本时可 import modules
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from modules.core.strip_proxy_env import strip_http_proxy_environment  # noqa: E402

strip_http_proxy_environment()  # 清除代理环境变量

from modules.ingestion.milvus_sync import run_sync_job  # noqa: E402 — MySQL→Milvus 全量同步

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("milvus_sync")


async def _main() -> None:
    stats = await run_sync_job()  # recreate=True：删集合重建并全量插入
    logger.info("result: %s", stats)


if __name__ == "__main__":
    asyncio.run(_main())
