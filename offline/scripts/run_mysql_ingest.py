"""
命令行入口：仅执行 MySQL 侧入库（Excel FAQ + PDF 法律文本）。

用法（在项目根目录）::

    python offline/scripts/run_mysql_ingest.py
"""

from __future__ import annotations

import asyncio  # 异步入口：驱动异步入库函数
import logging  # 控制台输出导入统计
import sys  # 修改模块搜索路径以便「直接 python 本脚本」时能 import modules
from pathlib import Path  # 定位仓库根目录

_ROOT = Path(__file__).resolve().parents[2]  # offline/scripts → 上两级为 Legal_System 根
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))  # 将项目根插入 sys.path 首位

from modules.core.strip_proxy_env import strip_http_proxy_environment  # noqa: E402 — 须在路径就绪后导入

strip_http_proxy_environment()  # 避免终端代理影响（若有）连 MySQL

from modules.ingestion.mysql_loaders import run_default_file_ingest  # noqa: E402

logging.basicConfig(level=logging.INFO)  # INFO：打印 faq_rows、legal 统计等
logger = logging.getLogger("mysql_ingest")


async def _main() -> None:
    stats = await run_default_file_ingest()  # 建表 + data/ 默认 Excel/PDF 入库
    logger.info("done: %s", stats)


if __name__ == "__main__":
    asyncio.run(_main())  # 启动异步事件循环执行 _main
