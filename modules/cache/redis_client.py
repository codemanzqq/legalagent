"""
Redis 异步客户端封装：以 JSON 形式缓存「问题 → 完整回答」，降低重复调用 LLM 成本。

使用 `redis.asyncio` 与 FastAPI 协程风格一致。
"""

from __future__ import annotations

import json  # 序列化缓存值为 UTF-8 JSON 字符串
import logging  # 记录损坏缓存键
from functools import lru_cache  # 客户端单例
from typing import Any  # JSON 解析结果类型

import redis.asyncio as redis  # 异步 Redis 客户端

from modules.core.config import get_settings  # 读取 REDIS_URL

logger = logging.getLogger(__name__)


class RedisCache:
    """在 Redis 字符串类型上存取 JSON 对象的小工具类。"""

    def __init__(self, client: redis.Redis) -> None:
        self._r = client  # 持有底层异步客户端引用

    async def get_json(self, key: str) -> Any | None:
        """GET 并 json.loads；键不存在或 JSON 损坏时返回 None。"""
        raw = await self._r.get(key)  # 异步读取
        if raw is None:
            return None
        try:
            return json.loads(raw)  # 反序列化
        except json.JSONDecodeError:
            logger.warning("cache corrupt for key=%s", key)
            return None

    async def set_json(self, key: str, value: Any, ttl_seconds: int) -> None:
        """SET 带过期时间；ensure_ascii=False 保留中文可读。"""
        await self._r.set(key, json.dumps(value, ensure_ascii=False), ex=ttl_seconds)


@lru_cache
def _build_client() -> redis.Redis:
    """基于配置 URL 构造解码为 str 的异步客户端（decode_responses=True）。"""
    settings = get_settings()
    return redis.from_url(settings.redis_url, decode_responses=True)


def get_redis() -> redis.Redis:
    """进程级单例：各模块共享同一连接池。"""
    return _build_client()


def cache_key_for_query(q: str) -> str:
    """
    生成问答缓存键：`q` 应为「用户标识 + 问题」或纯问题的拼接串。

    管线侧传入 `scope = f"{user_external_id}:{question}"`，使不同用户或不登录匿名态互不覆盖缓存。
    """
    return f"xiaoyi:rag:qa:{hash(q.strip())}"
