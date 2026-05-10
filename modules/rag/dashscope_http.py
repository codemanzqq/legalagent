"""
为 LangChain OpenAI 兼容客户端提供自定义 httpx 实例：`trust_env=False` 禁用环境代理。

避免终端 HTTP_PROXY 导致 DashScope 请求绕行失败。
"""

from __future__ import annotations

from functools import lru_cache  # 全进程复用单一 httpx 实例，减少连接开销

import httpx  # LangChain ChatOpenAI 底层 HTTP 客户端


@lru_cache(maxsize=1)
def get_dashscope_sync_client() -> httpx.Client:
    """同步阻塞调用路径使用的客户端。"""
    return httpx.Client(trust_env=False, timeout=120.0)  # trust_env=False：忽略 HTTP_PROXY，直连 DashScope


@lru_cache(maxsize=1)
def get_dashscope_async_client() -> httpx.AsyncClient:
    """异步流式生成主路径使用的客户端。"""
    return httpx.AsyncClient(trust_env=False, timeout=120.0)  # 长超时适配长答案生成
