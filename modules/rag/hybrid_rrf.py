"""
RRF（Reciprocal Rank Fusion）：把多路有序候选列表融合为统一相关性分数。

典型输入：稠密向量排序列表 + BM25 排序列表；输出按融合分降序。
"""

from __future__ import annotations

from collections import defaultdict  # doc_id -> 累加 RRF 分数；首次访问默认 0.0
from typing import Hashable  # doc_id 类型约束（int/str 等可哈希类型）


def reciprocal_rank_fusion(
    ranked_lists: list[list[Hashable]],
    k: int = 60,
) -> list[tuple[Hashable, float]]:
    """
    对每一路列表的第 rank 名文档贡献 1/(k+rank)；同一文档在多路累计加分。

    k 常用 60（论文经验值）；越大则排名权重衰减越缓。
    """
    scores: dict[Hashable, float] = defaultdict(float)  # 融合分字典
    for lst in ranked_lists:  # 遍历稠密排序、BM25 排序等各路列表
        for rank, doc_id in enumerate(lst, start=1):  # rank 从 1 起，与 RRF 公式一致
            scores[doc_id] += 1.0 / (k + rank)  # 名次越靠前权重越大
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)  # 按融合分从高到低输出 (doc, score)
