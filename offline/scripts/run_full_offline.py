"""
一键离线：顺序执行 MySQL 入库与 Milvus 向量同步。

用法::

    python offline/scripts/run_full_offline.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]  # 项目根目录
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))  # 支持「python offline/scripts/run_full_offline.py」

from modules.core.strip_proxy_env import strip_http_proxy_environment  # noqa: E402

strip_http_proxy_environment()

from modules.ingestion.milvus_sync import run_sync_job  # noqa: E402
from modules.ingestion.mysql_loaders import run_default_file_ingest  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("full_offline")


async def _main() -> None:
    mysql_stats = await run_default_file_ingest()  # 第一步：Excel/PDF → MySQL
    logger.info("mysql: %s", mysql_stats)
    milvus_stats = await run_sync_job()  # 第二步：MySQL → Milvus 向量索引
    logger.info("milvus: %s", milvus_stats)


if __name__ == "__main__":
    asyncio.run(_main())
