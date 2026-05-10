"""
本地 Embedding：基于 LangChain HuggingFace 封装 BGE-M3，向量用于 Milvus 建索引与在线检索。

模型目录默认位于仓库根下 `models/bge-m3`（可通过 `.env` 修改）。
"""

from __future__ import annotations

import asyncio  # to_thread 包装同步 encode
import logging  # 路径不存在时告警
from functools import lru_cache  # 全局单例嵌入模型
from pathlib import Path  # 拼接模型绝对路径
from typing import Sequence  # 批量文档类型

from modules.core.config import get_settings  # 设备与路径配置

logger = logging.getLogger(__name__)


@lru_cache
def _make_lc_embeddings():
    """延迟导入 HuggingFaceEmbeddings，减轻仅跑数据库脚本时的 import 负担。"""
    from langchain_huggingface import HuggingFaceEmbeddings

    settings = get_settings()
    root = Path(__file__).resolve().parents[2]  # 仓库根（modules 的上两级）
    path = root / settings.embedding_model_path
    if not path.exists():
        logger.warning(
            "Embedding 模型路径不存在：%s（请将模型下载到该目录）",
            path,
        )
    return HuggingFaceEmbeddings(
        model_name=str(path),
        model_kwargs={"device": settings.embedding_device, "trust_remote_code": True},
        encode_kwargs={"normalize_embeddings": True},  # 归一化后便于 COSINE 检索
    )


class LocalEmbeddingService:
    """对外提供异步接口；底层 LangChain 同步方法放到线程池执行。"""

    def __init__(self) -> None:
        self._emb = _make_lc_embeddings()  # 构造时即加载模型（较重）

    @property
    def dimension(self) -> int:
        """通过一次虚拟编码推断向量维度（用于 Milvus 建集合）。"""
        v = self._emb.embed_query("ping")  # 任意短文本
        return len(v)

    async def embed_query(self, text: str) -> list[float]:
        """单条查询向量（在线问答主路径）。"""
        return await asyncio.to_thread(self._emb.embed_query, text)

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """批量文档向量（离线同步 FAQ / 子块）。"""
        if not texts:
            return []
        return await asyncio.to_thread(self._emb.embed_documents, list(texts))
