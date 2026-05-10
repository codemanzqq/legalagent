"""
CrossEncoder 重排序：对「父文档」级长文本与查询构造 (query, passage) 对并打分。

分数越高表示 passage 越适合回答该问题；用于 RRF 之后精选少量上下文。
"""

from __future__ import annotations

import asyncio
import logging
from functools import lru_cache
from pathlib import Path
from typing import Sequence

from sentence_transformers import CrossEncoder

from modules.core.config import get_settings

logger = logging.getLogger(__name__)


@lru_cache
def _make_cross_encoder() -> CrossEncoder:
    """加载本地 bge-reranker-large（或配置路径下的模型）。"""
    settings = get_settings()
    root = Path(__file__).resolve().parents[2]
    path = root / settings.rerank_model_path
    if not path.exists():
        logger.warning("Rerank 模型路径不存在：%s", path)
    return CrossEncoder(str(path), device=settings.embedding_device)


class LocalRerankService:
    """封装 predict 到线程池，避免阻塞事件循环。"""

    def __init__(self) -> None:
        self._model = _make_cross_encoder()

    async def rank(self, query: str, passages: Sequence[str]) -> list[float]:
        """返回与 passages 等长的浮点分列表。"""
        if not passages:  # 无父文档则无需重排
            return []
        pairs = [(query, p) for p in passages]  # CrossEncoder 输入：查询- passage 对

        def _run() -> list[float]:
            scores = self._model.predict(list(pairs))  # 同步 GPU/CPU 推理
            return [float(s) for s in scores]  # 统一为 Python float 列表

        return await asyncio.to_thread(_run)  # 避免阻塞 FastAPI 事件循环
