# =============================================================================
# 教学说明：本文件在整体链路中的位置
# -----------------------------------------------------------------------------
# 输入：用户问题字符串 `query`；父文档全文列表 `passages`（来自 MySQL 拉取）。
# 输出：与 passages 等长的浮点分数列表，分数越高表示该父文档越适合回答该问题。
# 被谁调用：`RagPipeline` 在法律混合检索分支末尾（懒加载 `_reranker()`）。
# =============================================================================
"""
CrossEncoder（如 bge-reranker-large）：对 (query, passage) 对逐对打分，比双塔向量「点积」更准但更慢。

`sentence_transformers.CrossEncoder.predict` 是同步的，必须用 `asyncio.to_thread` 避免卡死事件循环。
"""

from __future__ import annotations

import asyncio  # to_thread
import logging
from functools import lru_cache
from pathlib import Path
from typing import Sequence

from sentence_transformers import CrossEncoder  # 官方 CrossEncoder 封装

from modules.core.config import get_settings

logger = logging.getLogger(__name__)


@lru_cache
def _make_cross_encoder() -> CrossEncoder:
    """
    从本地目录加载 CrossEncoder 权重；设备与 embedding 共用 EMBEDDING_DEVICE。
    """
    settings = get_settings()
    root = Path(__file__).resolve().parents[2]  # 仓库根
    path = root / settings.rerank_model_path  # models/bge-reranker-large
    if not path.exists():
        logger.warning("Rerank 模型路径不存在：%s", path)
    return CrossEncoder(str(path), device=settings.embedding_device)  # device 如 cpu、cuda


class LocalRerankService:
    """
    仅暴露 async `rank`，内部转线程池。
    """

    def __init__(self) -> None:
        self._model = _make_cross_encoder()  # 初始化即加载模型

    async def rank(self, query: str, passages: Sequence[str]) -> list[float]:
        """
        返回每个 passage 的相关性分数；passages 为空则返回空列表。
        """
        if not passages:  # 无父文档可排
            return []  # 避免 predict 收到空输入
        pairs = [(query, p) for p in passages]  # CrossEncoder 输入：N 个二元组列表

        def _run() -> list[float]:
            scores = self._model.predict(list(pairs))  # numpy 或 tensor 转成的数组-like
            return [float(s) for s in scores]  # 统一为 Python float，便于 sorted / zip

        return await asyncio.to_thread(_run)  # 在默认线程池执行 _run，释放事件循环
