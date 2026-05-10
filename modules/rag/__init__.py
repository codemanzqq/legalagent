"""RAG 子模块：对外导出管线类供 FastAPI 注入。"""

from modules.rag.pipeline import RagPipeline

__all__ = ["RagPipeline"]
