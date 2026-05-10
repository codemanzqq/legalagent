"""
MySQL → Milvus 全量同步：读 FAQ 与子块行，编码向量后批量插入对应集合。

在线热更新当前实现为周期性「删表重建/全量插入」简化一致性（中等数据量可接受）。
"""

from __future__ import annotations

import asyncio  # 将阻塞型 pymilvus 写入放进线程池，避免卡住 asyncio 事件循环
import logging  # 同步完成后的统计日志
from typing import Any  # Milvus 同步函数返回的统计 dict 类型注解

from pymilvus import Collection  # 面向集合的高层 API：insert / flush / load
from sqlalchemy import select  # 构造异步查询 FAQ / legal 子块行
from sqlalchemy.ext.asyncio import AsyncSession  # ORM 异步会话类型

from modules.database.models import FaqTab, LegalTab  # 与 Milvus 主键对齐的 MySQL 行模型
from modules.database.session import get_session_factory  # 获取 AsyncSession 工厂（进程单例）
from modules.embeddings.local_embedding import LocalEmbeddingService  # 句向量编码（与在线共用）
from modules.milvus_store.collections import COLLECTION_FAQ, COLLECTION_LEGAL_CHILD, recreate_collections  # 集合名与删建工具

logger = logging.getLogger(__name__)


async def _fetch_faq_rows(session: AsyncSession) -> list[FaqTab]:
    """只拉取「参与向量索引」的高频 FAQ；is_high_frequency=False 的行不入 Milvus。"""
    res = await session.execute(select(FaqTab).where(FaqTab.is_high_frequency.is_(True)))  # 异步执行 SELECT
    return list(res.scalars().all())  # 转为 Python 列表便于后续 zip 向量


async def _fetch_legal_children(session: AsyncSession) -> list[LegalTab]:
    """只同步 doc_role=child 的行；parent 行仅存 MySQL 正文，不向 Milvus 插入向量。"""
    res = await session.execute(select(LegalTab).where(LegalTab.doc_role == "child"))  # 过滤子块
    return list(res.scalars().all())


def _insert_faq(col: Collection, rows: list[FaqTab], vectors: list[list[float]]) -> None:
    """insert 列顺序需与 schema 字段声明顺序一致（不含向量索引元数据）。"""
    if not rows:  # 无高频 FAQ 则跳过
        return
    ids = [r.id for r in rows]  # 与 faq_tab.id 对齐
    questions = [r.question[:4000] for r in rows]  # VARCHAR 上限截断
    answers = [r.answer[:32000] for r in rows]
    col.insert([ids, questions, answers, vectors])  # 末列为向量列 embedding
    col.flush()
    col.load()


def _insert_legal(
    col: Collection,
    rows: list[LegalTab],
    vectors: list[list[float]],
    parent_ids: list[int],
    sources: list[str],
) -> None:
    """法律子块插入：列顺序必须与 collections._legal_child_fields 声明顺序一致。"""
    if not rows:  # 无子块则跳过（例如仅导入了 FAQ）
        return
    ids = [r.id for r in rows]  # 与 legal_tab 子行主键一致，便于幂等重建
    texts = [r.content[:32000] for r in rows]  # VARCHAR 上限防护，截断超长正文
    col.insert([ids, parent_ids, sources, texts, vectors])  # 批量写入一行 schema 对应多列
    col.flush()  # 确保数据落段，便于后续检索可见
    col.load()  # 加载到内存供在线 search（若集合已在内存可视为幂等）


def _blocking_milvus_full_write(
    dim: int,
    faqs: list[FaqTab],
    faq_vecs: list[list[float]],
    children: list[LegalTab],
    child_vecs: list[list[float]],
    *,
    recreate: bool,
) -> None:
    """在同一线程串行执行连接、建表、插入，避免 pymilvus 线程安全问题。"""
    from modules.milvus_store.client import ensure_milvus
    from modules.milvus_store.collections import create_collections_if_not_exist

    ensure_milvus()  # 注册默认连接
    if recreate:  # 热更新/离线全量：先删集合再建，保证 schema 与数据一致
        recreate_collections(dim)
    else:  # 仅开发时可能用：集合已存在则 load，不破坏数据
        create_collections_if_not_exist(dim)

    faq_col = Collection(COLLECTION_FAQ)  # 绑定 FAQ 集合句柄
    legal_col = Collection(COLLECTION_LEGAL_CHILD)  # 绑定法律子块集合句柄
    _insert_faq(faq_col, faqs, faq_vecs)  # 先写 FAQ，失败时便于单独排查

    parent_ids: list[int] = []  # 与 children 行一一对应的父文档 id
    sources: list[str] = []  # 与 children 行一一对应的来源文件名
    for c in children:  # 子块在 MySQL 中已带 parent_id，这里拆成与 insert 列对齐的平行列表
        assert c.parent_id is not None  # 子块行在入库阶段应已保证外键
        parent_ids.append(int(c.parent_id))  # Milvus 标量字段存 int64
        sources.append(c.source_file[:1024])  # 截断到 schema VARCHAR 上限

    _insert_legal(legal_col, children, child_vecs, parent_ids, sources)


async def sync_mysql_to_milvus(*, recreate: bool = True) -> dict[str, Any]:
    """异步侧读完数据库与向量后，仅把阻塞写 Milvus 交给线程池。"""
    emb = LocalEmbeddingService()  # 与在线问答共用同一套 BGE-M3
    dim = len(await emb.embed_query("dimension_probe"))  # 实际维度随模型权重而定，用于建集合

    factory = get_session_factory()
    async with factory() as session:  # 只读事务：拉全表 FAQ / 子块
        faqs = await _fetch_faq_rows(session)
        children = await _fetch_legal_children(session)

    faq_vecs: list[list[float]] = []
    if faqs:  # 有问题文本才调用编码，避免空列表传入 embed_documents
        faq_vecs = await emb.embed_documents([f.question for f in faqs])  # 与 FAQ 行顺序严格对齐

    child_vecs: list[list[float]] = []
    if children:
        child_vecs = await emb.embed_documents([c.content for c in children])  # 子块正文批量编码

    await asyncio.to_thread(
        _blocking_milvus_full_write,
        dim,
        faqs,
        faq_vecs,
        children,
        child_vecs,
        recreate=recreate,
    )  # pymilvus 同步 API 必须在独立线程执行，避免阻塞 FastAPI 事件循环

    return {
        "embedding_dim": dim,  # 写入统计：维度
        "faq_vectors": len(faqs),  # 写入统计：FAQ 条数
        "legal_child_vectors": len(children),  # 写入统计：法律子块条数
    }


async def run_sync_job() -> dict[str, Any]:
    """供 FastAPI 热更新或离线脚本调用：固定 recreate=True 全量。"""
    stats = await sync_mysql_to_milvus(recreate=True)
    logger.info("milvus sync done: %s", stats)
    return stats
