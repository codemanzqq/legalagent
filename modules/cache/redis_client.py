# =============================================================================
# 教学说明：本文件在整体链路中的位置
# -----------------------------------------------------------------------------
# 输入：`.env` 的 REDIS_URL；业务侧传入的缓存 key 或「用户+问题」拼成的 scope 字符串。
# 输出：`RedisCache` 实例（get/set JSON）；`cache_key_for_query` 返回稳定短 key。
# 被谁调用：`RagPipeline`（读缓存命中直出、写 FAQ/RAG 结果）、其它需复用 Redis 的模块通过 `get_redis()`。
# =============================================================================
"""
使用 `redis.asyncio`：所有方法都是 async def，可在 FastAPI 路由里 await。

缓存值存 JSON 字符串，便于存 dict（含 answer、route 等字段）。
"""

from __future__ import annotations

import json  # 把 Python 对象序列化为 str 存入 Redis
import logging  # JSON 损坏时打 warning
from functools import lru_cache  # Redis 客户端单例
from typing import Any  # get_json 返回值可能是 dict/list/None

import redis.asyncio as redis  # 官方异步客户端，与 asyncio 协作

from modules.core.config import get_settings  # 取 REDIS_URL

logger = logging.getLogger(__name__)


class RedisCache:
    """
    薄封装：固定用 JSON 编解码，调用方不用自己 dumps/loads。
    """

    def __init__(self, client: redis.Redis) -> None:
        """
        入参:
            client: 已创建的异步 Redis 客户端（建议 decode_responses=True）。
        返回:
            无。
        """
        self._r = client  # 保存底层客户端引用；decode_responses=True 时 value 已是 str

    async def get_json(self, key: str) -> Any | None:
        """
        await GET key；不存在返回 None；JSON 非法返回 None 并打日志。

        入参:
            key: Redis 键名。
        返回:
            反序列化后的 Python 对象（通常为 dict）；键不存在或 JSON 损坏时返回 None。
        """
        raw = await self._r.get(key)  # 异步 IO：等待 Redis 响应
        if raw is None:  # 键不存在
            return None  # 表示未命中缓存
        try:  # 尝试解析
            return json.loads(raw)  # str → Python 对象（通常是 dict）
        except json.JSONDecodeError:  # 值被手工改坏或版本不兼容
            logger.warning("cache corrupt for key=%s", key)  # 便于排查
            return None  # 当作未命中，避免抛异常打断主链路

    async def set_json(self, key: str, value: Any, ttl_seconds: int) -> None:
        """
        SET key value EX ttl；中文用 ensure_ascii=False 保持可读。

        入参:
            key: Redis 键名。
            value: 可 JSON 序列化的 Python 对象。
            ttl_seconds: 过期时间（秒），传给 Redis EX。
        返回:
            无。
        """
        await self._r.set(key, json.dumps(value, ensure_ascii=False), ex=ttl_seconds)  # ex= 过期秒数


@lru_cache
def _build_client() -> redis.Redis:
    """
    从 URL 解析出连接参数并创建连接池客户端；进程内只执行一次。

    入参:
        无；连接串取自 `get_settings().redis_url`。
    返回:
        异步 Redis 客户端单例。
    """
    settings = get_settings()  # 读配置
    return redis.from_url(settings.redis_url, decode_responses=True)  # True：bytes 自动 decode 成 str，JSON 友好


def get_redis() -> redis.Redis:
    """
    对外暴露单例：多处 `RedisCache(get_redis())` 共享同一连接池。

    入参:
        无。
    返回:
        与 `_build_client()` 相同的缓存客户端实例。
    """
    return _build_client()  # 返回缓存的客户端实例


def cache_key_for_query(q: str) -> str:
    """
    把任意长度问题映射为固定前缀 + 数字哈希，避免 key 过长。

    管线传入的 `q` 实为 `user_external_id:question`，使不同用户同问题不共用一个缓存桶。

    入参:
        q: 经 `strip()` 前后可能变化的原始 scope 字符串（用户 id 与问题拼接）。
    返回:
        形如 `xiaoyi:rag:qa:<hash>` 的稳定短键字符串。
    """
    return f"xiaoyi:rag:qa:{hash(q.strip())}"  # Python 内置 hash（进程生命周期内稳定；注意多进程不共享）
