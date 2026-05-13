# =============================================================================
# 教学说明：本文件在整体链路中的位置
# -----------------------------------------------------------------------------
# 输入：无（`get_pipeline` 无参）；依赖进程内首次调用时的构造副作用（加载大模型）。
# 输出：单例 `RagPipeline` 实例，供 FastAPI `Depends` 注入到路由处理函数。
# 被谁调用：`backend/app/api/chat.py` 中 `pipeline: RagPipeline = Depends(get_pipeline)`。
# =============================================================================
"""
FastAPI 依赖注入：把「重对象」与「请求处理函数参数」解耦。

`lru_cache` 保证全进程一个 `RagPipeline`，避免每请求 new 一次导致显存爆炸。
"""

from functools import lru_cache  # 标准库单例装饰器

from modules.rag.pipeline import RagPipeline  # 管线定义在 modules 层，backend 只组装


@lru_cache  # 无括号等价 maxsize=None：缓存任意多次调用中「唯一」无参调用结果
def get_pipeline() -> RagPipeline:
    """
    FastAPI 解析 Depends(get_pipeline) 时：第一次请求调用本函数，之后直接返回缓存实例。
    """
    return RagPipeline()  # 构造：内部会 new LocalEmbeddingService 等
