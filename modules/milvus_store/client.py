"""
Milvus 连接：使用 pymilvus 全局 `connections` 注册别名 `default`，供 Collection 操作复用。

单进程内多次调用 `ensure_milvus` 因 lru_cache 只真正连接一次。
"""

from __future__ import annotations

import logging  # 记录连接地址

from functools import lru_cache  # 连接单例

from pymilvus import connections  # Milvus 连接管理 API

from modules.core.config import get_settings  # 主机端口账号

logger = logging.getLogger(__name__)

_ALIAS = "default"  # pymilvus 默认别名，与文档示例一致


@lru_cache
def ensure_milvus() -> str:
    """若尚未连接则建立 TCP 连接；返回别名供调试日志。"""
    s = get_settings()
    addr = f"{s.milvus_host}:{s.milvus_port}"
    connections.connect(
        alias=_ALIAS,
        host=s.milvus_host,
        port=str(s.milvus_port),
        user=s.milvus_user or None,
        password=s.milvus_password or None,
    )
    logger.info("milvus connected: %s", addr)
    return _ALIAS


def close_milvus() -> None:
    """进程退出时可手动断开（当前主流程较少调用）。"""
    try:
        connections.disconnect(_ALIAS)
    except Exception:  # noqa: BLE001
        pass
