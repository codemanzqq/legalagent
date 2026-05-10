"""
Milvus 集合 schema 与生命周期：FAQ 与法律子块两张向量表，字段名需与同步脚本 insert 顺序一致。

向量索引采用 HNSW + COSINE（与检索 param 一致）。
"""

from __future__ import annotations

import logging

from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, utility

from modules.milvus_store.client import ensure_milvus

logger = logging.getLogger(__name__)

COLLECTION_FAQ = "xiaoyi_faq_highfreq"  # FAQ 集合：按问题向量检索
COLLECTION_LEGAL_CHILD = "xiaoyi_legal_child"  # 法律子块集合


def _faq_fields(dim: int) -> list[FieldSchema]:
    """FAQ：主键与 MySQL faq_tab.id 对齐，便于全量重建。"""
    return [
        FieldSchema(
            name="id",
            dtype=DataType.INT64,
            is_primary=True,
            auto_id=False,
            description="与 MySQL faq_tab.id 对齐，便于幂等 upsert/热更新",
        ),
        FieldSchema(name="question", dtype=DataType.VARCHAR, max_length=4096),
        FieldSchema(name="answer", dtype=DataType.VARCHAR, max_length=32768),
        FieldSchema(
            name="embedding",
            dtype=DataType.FLOAT_VECTOR,
            dim=dim,
        ),
    ]


def _legal_child_fields(dim: int) -> list[FieldSchema]:
    """法律子块：携带 parent_id 与 source_file 便于在线回溯与调试。"""
    return [
        FieldSchema(
            name="id",
            dtype=DataType.INT64,
            is_primary=True,
            auto_id=False,
            description="与 MySQL legal_tab.id（子块行）对齐",
        ),
        FieldSchema(name="parent_id", dtype=DataType.INT64, description="父文档 legal_tab.id"),
        FieldSchema(name="source_file", dtype=DataType.VARCHAR, max_length=1024),
        FieldSchema(name="text", dtype=DataType.VARCHAR, max_length=32768),
        FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=dim),
    ]


def _create_index(collection: Collection) -> None:
    """为 embedding 列建 HNSW 索引；metric_type 与 search 时一致。"""
    index = {
        "index_type": "HNSW",
        "metric_type": "COSINE",
        "params": {"M": 16, "efConstruction": 200},
    }
    collection.create_index(field_name="embedding", index_params=index)


def _drop_if_exists(name: str) -> None:
    """开发期全量重建前删除旧集合。"""
    if utility.has_collection(name):
        utility.drop_collection(name)
        logger.info("dropped milvus collection: %s", name)


def create_collections_if_not_exist(dim: int) -> None:
    """幂等创建：已存在则 load；不存在则 schema + index + load。"""
    ensure_milvus()
    for name, fields_fn in (
        (COLLECTION_FAQ, _faq_fields),
        (COLLECTION_LEGAL_CHILD, _legal_child_fields),
    ):
        if utility.has_collection(name):
            col = Collection(name)
            col.load()
            logger.info("milvus collection exists (loaded): %s", name)
            continue

        fields = fields_fn(dim)
        schema = CollectionSchema(fields=fields, description=f"xiaoyi::{name}")
        collection = Collection(name=name, schema=schema)
        _create_index(collection)
        collection.load()
        logger.info("created milvus collection: %s dim=%s", name, dim)


def recreate_collections(dim: int) -> None:
    """先删后建：用于离线同步或 schema 变更。"""
    ensure_milvus()
    _drop_if_exists(COLLECTION_FAQ)
    _drop_if_exists(COLLECTION_LEGAL_CHILD)
    create_collections_if_not_exist(dim)
