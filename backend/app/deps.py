"""
依赖注入：为路由函数提供「进程级单例」的 RAG 管线实例。

Embedding / Rerank 模型占用内存大，复用同一 `RagPipeline` 可避免重复加载。
"""

from functools import lru_cache  # 进程内缓存工厂函数结果，实现单例效果

from modules.rag.pipeline import RagPipeline  # 端到端检索增强生成管线


@lru_cache  # 首次调用时构造，之后始终返回同一实例
def get_pipeline() -> RagPipeline:
    """FastAPI `Depends(get_pipeline)` 使用的提供者。"""
    return RagPipeline()  # 构造管线（内部再懒加载向量模型与重排模型）
