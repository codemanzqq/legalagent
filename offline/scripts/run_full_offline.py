# =============================================================================
# 教学说明：本文件在整体链路中的位置
# -----------------------------------------------------------------------------
# 输入：同「先 MySQL 再 Milvus」两条链路的合并前置条件（data 目录、.env）。
# 输出：先打印 mysql 统计，再打印 milvus 统计；数据库与向量库均为最新全量。
# 被谁调用：人工一键 `python offline/scripts/run_full_offline.py`。
# =============================================================================
"""
顺序：`run_default_file_ingest` → `run_sync_job`。

等价于先执行 `run_mysql_ingest.py` 再执行 `run_milvus_sync.py`，减少运维记两条命令的成本。
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from modules.ingestion.milvus_sync import run_sync_job  # noqa: E402
from modules.ingestion.mysql_loaders import run_default_file_ingest  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("full_offline")


async def _main() -> None:
    mysql_stats = await run_default_file_ingest()  # 第一步：落 MySQL
    logger.info("mysql: %s", mysql_stats)
    milvus_stats = await run_sync_job()  # 第二步：读 MySQL 写 Milvus
    logger.info("milvus: %s", milvus_stats)


if __name__ == "__main__":
    asyncio.run(_main())
