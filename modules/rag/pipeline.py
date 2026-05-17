# =============================================================================
# 教学说明：本文件在整体链路中的位置
# -----------------------------------------------------------------------------
# 这是在线问答“总编排文件”：把缓存、记忆、意图、检索、融合、重排、生成串成一条链。
# 你可以把它理解为“问答总调度器”。
#
# 输入：
# - question：用户本轮问题（字符串）
# - user_external_id：可选用户标识（用于“用户隔离缓存 + 历史记忆”）
#
# 输出：
# - AsyncIterator[str]：异步文本片段流（供 SSE 逐块输出）
#
# 主要调用方：
# - backend/app/api/chat.py 中的 pipeline.stream_chat(...)
#
# 关键特点：
# - 先缓存短路，再进入复杂链路；
# - FAQ 高置信可直达，不走大模型；
# - 法律问题走“向量检索 + BM25 + RRF + 重排 + LLM”。
# =============================================================================

"""在线 RAG 主流程（含缓存、记忆、检索、生成）的教学注释版实现。"""

from __future__ import annotations  # 允许把类型注解延迟求值，避免前向引用问题

import asyncio  # 用于 to_thread：把同步阻塞代码放线程池
import logging  # 模块日志
import re  # 正则分词（供 BM25）
from collections.abc import AsyncIterator  # 标注“异步迭代器”返回类型
from typing import Any  # 标注动态返回值

from langchain_core.messages import AIMessageChunk, HumanMessage, SystemMessage  # LangChain 消息类型
from langchain_openai import ChatOpenAI  # OpenAI 兼容客户端（本项目对接 DashScope 兼容接口）
from pymilvus import Collection  # Milvus 集合对象（load/search/insert）
from sqlalchemy import select  # SQLAlchemy 查询构造器

from modules.cache.redis_client import RedisCache, cache_key_for_query, get_redis  # Redis 缓存能力
from modules.core.config import ASSISTANT_NAME, get_settings  # 全局配置 + 助手名称
from modules.database.models import LegalTab  # 法律文档 ORM 模型（回查父文档用）
from modules.database.session import get_session_factory  # AsyncSession 工厂
from modules.embeddings.local_embedding import LocalEmbeddingService  # 向量编码服务
from modules.memory.service import (
    DEFAULT_MEMORY_CONTEXT_LINES,  # 默认读取的历史条数
    fetch_recent_chat_lines,  # 读最近历史问答
    format_chat_history_for_prompt,  # 历史行格式化成 prompt 文本
    resolve_user_id,  # external_id -> user_id
)
from modules.milvus_store.client import ensure_milvus  # 确保 Milvus 连接
from modules.milvus_store.collections import COLLECTION_FAQ, COLLECTION_LEGAL_CHILD  # Milvus 集合名
from modules.rag.dashscope_http import (
    get_dashscope_async_client,  # 异步 httpx 客户端
    get_dashscope_sync_client,  # 同步 httpx 客户端
)
from modules.rag.hybrid_rrf import reciprocal_rank_fusion  # RRF 融合算法
from modules.rag.intent import is_professional_query  # 意图分类（专业/非专业）
from modules.rag.prompts import (
    GUIDE_NON_PROFESSIONAL,  # 非专业引导提示词
    RAG_SYSTEM,  # 专业 RAG 系统提示词
    augment_question_with_memory,  # 把历史拼到问题里
    build_user_message,  # 构造最终用户消息
)

logger = logging.getLogger(__name__)  # 当前模块 logger


def _tokenize(text: str) -> list[str]:  # 定义分词函数：把原始文本切成 BM25 可用 token 列表
    """
    把输入文本粗分词（给 BM25 用）。

    参数：
    - text: 原始文本（可能是用户问题或法律子块文本）

    返回：
    - list[str]: token 列表（中文单字 + 英文词 + 数字）
    """
    # text.lower()：统一小写，减少英文大小写差异噪声
    # 正则说明：
    # - [\u4e00-\u9fff]：单个中文字符
    # - [a-zA-Z]+：连续英文单词
    # - [0-9]+：连续数字
    return re.findall(r"[\u4e00-\u9fff]|[a-zA-Z]+|[0-9]+", text.lower())  # 返回分词结果列表


def _entity_to_dict(entity: Any) -> dict:  # 定义实体归一化函数：把不同类型 entity 统一为 dict
    """
    兼容不同 pymilvus 版本的 entity 结构，统一转成 dict。

    参数：
    - entity: 可能是 dict、Row-like、或带 to_dict 方法的对象

    返回：
    - dict: 可 .get(...) 的标准字典；失败时返回空字典
    """
    if entity is None:  # 命中无附加字段
        return {}  # 空实体直接返回空字典
    if isinstance(entity, dict):  # 已经是 dict
        return entity  # 已经是字典则原样返回
    if hasattr(entity, "to_dict"):  # 新版本对象通常有 to_dict
        return entity.to_dict()  # type: ignore[no-any-return]  # 调用对象自带 to_dict 转换
    try:
        return dict(entity)  # 尝试把可迭代键值对对象强制转成 dict
    except Exception:  # noqa: BLE001
        return {}  # 兜底为空，不影响主流程


def _parse_milvus_hits(raw_hits: Any) -> list[tuple[int, float, dict]]:  # 定义命中解析函数：把 Milvus 原始结果转统一结构
    """
    解析 Milvus 搜索返回，统一成三元组列表。

    参数：
    - raw_hits: Collection.search(...) 的原始返回

    返回：
    - list[(id, distance_or_similarity, entity_dict)]
    """
    out: list[tuple[int, float, dict]] = []  # 输出容器
    if not raw_hits:  # 没有任何结果
        return out  # 无命中时返回空列表
    if not raw_hits[0]:  # 第一条查询（本代码每次只查1条）无命中
        return out  # 第一查询无命中时返回空列表

    for hit in raw_hits[0]:  # 遍历命中列表
        ent = _entity_to_dict(getattr(hit, "entity", None))  # 规范化 entity
        out.append((int(hit.id), float(hit.distance), ent))  # 统一类型后写入

    return out  # 返回结构化命中列表


class RagPipeline:  # 定义在线问答总编排类：封装缓存、检索、重排、生成全流程
    """在线问答业务总编排类（建议进程内单例）。"""

    def __init__(self) -> None:  # 初始化管线对象，挂载配置/embedding/缓存等组件
        """
        初始化总编排对象。

        成员说明：
        - self.settings: 全局配置对象
        - self._emb: 向量编码服务
        - self._rerank: 重排服务（懒加载）
        - self._cache: Redis 缓存服务
        """
        self.settings = get_settings()  # 读取全局配置（单例）
        self._emb = LocalEmbeddingService()  # 初始化 embedding 服务
        self._rerank = None  # 暂不加载重排模型，等用到时再加载
        self._cache = RedisCache(get_redis())  # 初始化缓存封装

    def _llm(self) -> ChatOpenAI:  # 构建并返回一个流式 ChatOpenAI 客户端
        """
        构造一个 LLM 客户端实例（流式）。

        返回：
        - ChatOpenAI: 已绑定 DashScope 配置 + httpx 客户端
        """
        s = self.settings  # 局部变量缩短书写
        return ChatOpenAI(
            model=s.llm_model,  # 主模型名称（配置项）
            temperature=0.2,  # 降低随机性，减少胡编
            api_key=s.dashscope_api_key,  # API Key
            base_url=s.dashscope_base_url,  # 兼容接口地址
            streaming=True,  # 打开流式输出（SSE必须）
            timeout=120,  # 请求超时秒数
            http_client=get_dashscope_sync_client(),  # 同步客户端
            http_async_client=get_dashscope_async_client(),  # 异步客户端
        )

    def _reranker(self):  # 获取重排器：首次调用时懒加载，后续复用
        """
        懒加载重排模型（仅法律复杂链路会用到）。

        返回：
        - LocalRerankService: 重排服务单例
        """
        if self._rerank is None:  # 首次调用才初始化
            from modules.rerank.local_rerank import LocalRerankService  # 延迟导入，减少冷启动

            self._rerank = LocalRerankService()  # 实例化重排服务
        return self._rerank  # 返回已初始化对象

    async def _milvus_search(  # 异步检索包装：把同步 pymilvus search 放在线程池执行
        self,
        collection: str,  # 要检索的集合名（FAQ或法律子块）
        vector: list[float],  # 查询向量（单条）
        limit: int,  # 返回 top-k 条数
        output_fields: list[str],  # 需要返回的标量字段
    ) -> Any:
        """
        在线程中执行 Milvus 同步检索，避免阻塞 asyncio 事件循环。

        参数：
        - collection: 集合名
        - vector: 查询向量
        - limit: top-k
        - output_fields: 附带返回的字段
        """

        def _run() -> Any:  # 定义同步闭包：线程中真正执行 Milvus 检索
            """同步闭包：真正调用 pymilvus search。"""
            ensure_milvus()  # 先确保连接存在
            col = Collection(collection)  # 绑定集合对象
            col.load()  # 加载到内存，避免 search 失败
            return col.search(
                data=[vector],  # 单条查询，包装成 batch 形式
                anns_field="embedding",  # 向量字段名
                param={"metric_type": "COSINE", "params": {"ef": 128}},  # 检索参数
                limit=limit,  # top-k
                output_fields=output_fields,  # 返回字段
            )

        return await asyncio.to_thread(_run)  # 在线程池执行同步检索

    async def _fetch_parents(self, ids: list[int]) -> dict[int, str]:  # 批量回查父文档：输入 parent_id 列表，输出 id->正文映射
        """
        按 parent id 批量回查父文档全文。

        参数：
        - ids: 父文档 id 列表

        返回：
        - dict[parent_id, parent_text]
        """
        if not ids:  # 空输入直接返回空映射
            return {}

        factory = get_session_factory()  # 取会话工厂
        async with factory() as session:  # 打开会话
            res = await session.execute(
                select(LegalTab).where(LegalTab.id.in_(ids)),  # 按 id in 批量查询
            )
            rows = list(res.scalars().all())  # 提取 ORM 列表

        # 保护上下文长度：每篇父文档最多取 8000 字符
        return {r.id: (r.content or "")[:8000] for r in rows}

    async def stream_chat(  # 在线主入口：按业务链路逐步产出回答分片
        self,
        question: str,  # 用户问题（本轮输入）
        user_external_id: str | None = None,  # 用户外部 ID（用于记忆和缓存隔离）
    ) -> AsyncIterator[str]:
        """
        对外主入口：返回异步文本片段流。

        主流程：
        1) 查缓存
        2) 读取用户历史记忆
        3) 意图分流（非专业走引导）
        4) 专业链路：FAQ 检索 -> 法律检索 -> 融合 -> 重排 -> 生成
        """
        # ---------------------------------------------------------------------
        # 步骤1：缓存短路
        # ---------------------------------------------------------------------
        # scope 设计成“用户ID:问题”，确保不同用户同问题不会共享答案缓存
        scope = f"{(user_external_id or '').strip()}:{question}"
        key = cache_key_for_query(scope)  # 生成缓存 key
        cached = await self._cache.get_json(key)  # 读缓存
        if isinstance(cached, dict) and cached.get("answer"):  # 判断缓存对象合法且包含 answer 字段
            # 命中缓存：直接输出答案并结束，跳过后续所有链路
            yield str(cached["answer"])
            return  # 缓存命中后结束主流程

        # ---------------------------------------------------------------------
        # 步骤2：用户历史记忆（仅有 user_external_id 时启用）
        # ---------------------------------------------------------------------
        memory_snippet: str | None = None  # 默认无记忆
        if user_external_id and user_external_id.strip():  # 仅在用户ID有效时启用记忆链路
            factory = get_session_factory()  # 会话工厂
            async with factory() as session:  # 打开数据库会话
                uid = await resolve_user_id(session, user_external_id.strip())  # external_id -> user_id
                rows = await fetch_recent_chat_lines(
                    session,  # 当前数据库会话（用于查询历史）
                    uid,  # 当前用户内部主键
                    DEFAULT_MEMORY_CONTEXT_LINES,  # 最近N条
                )
                memory_snippet = format_chat_history_for_prompt(rows)  # 格式化为 prompt 文本
                await session.commit()  # 提交事务，结束本轮记忆读取会话

        # ---------------------------------------------------------------------
        # 步骤3：意图分流（非专业 -> 引导）
        # ---------------------------------------------------------------------
        if not await is_professional_query(question):  # 意图判定为非专业问题
            async for piece in self._stream_simple_llm(
                [
                    SystemMessage(content=GUIDE_NON_PROFESSIONAL),  # 系统提示：引导回专业问题
                    HumanMessage(content=augment_question_with_memory(question, memory_snippet)),  # 人类消息附带记忆
                ],
            ):
                yield piece  # 逐片返回
            return  # 非专业路径返回，不进入专业检索

        # ---------------------------------------------------------------------
        # 步骤4：专业链路先做问题向量化
        # ---------------------------------------------------------------------
        qvec = await self._emb.embed_query(question)  # 将用户问题编码为检索向量

        # ---------------------------------------------------------------------
        # 步骤5：FAQ 检索分支
        # ---------------------------------------------------------------------
        faq_raw = await self._milvus_search(
            COLLECTION_FAQ,  # FAQ 集合
            qvec,  # 查询向量
            limit=10,  # 取前10条
            output_fields=["question", "answer"],  # 返回 question/answer
        )
        faq_parsed = _parse_milvus_hits(faq_raw)  # 解析命中结果

        # 配置阈值：代码里用“相似度阈值”，配置里是“距离阈值”
        th_direct = self.settings.faq_direct_distance_threshold  # FAQ 直达阈值（距离）
        th_llm = self.settings.faq_llm_distance_threshold  # FAQ 进入 LLM 阈值（距离）
        sim_direct = 1.0 - th_direct  # 转相似度阈值
        sim_llm = 1.0 - th_llm  # 转相似度阈值

        if faq_parsed:  # FAQ 集合存在命中候选
            _best_id, best_sim, ent = faq_parsed[0]  # 取最相似的一条 FAQ 命中

            # 5.1 FAQ 高置信：直接输出，不走 LLM
            if best_sim >= sim_direct and ent.get("answer"):  # 满足高置信阈值且有标准答案
                ans = str(ent["answer"])  # 标准答案文本
                await self._cache.set_json(
                    key,  # 当前问题缓存 key
                    {"answer": ans, "route": "faq_direct"},  # 标记为 FAQ 直达路径
                    self.settings.cache_ttl_seconds,  # 过期时间
                )
                # 为了前端“流式观感”，把长文本分块输出
                for i in range(0, len(ans), 40):
                    yield ans[i : i + 40]
                return  # FAQ 直达输出后结束

            # 5.2 FAQ 中等相似：把 FAQ 答案作为上下文交给 LLM
            close = [x for x in faq_parsed if x[1] >= sim_llm][: self.settings.faq_top_k_for_llm]
            if close and close[0][1] >= sim_llm:  # 存在可用于 LLM 参考的中等相似 FAQ
                ctx: list[str] = []  # FAQ 上下文片段容器
                for _, d, e in close:  # 遍历近似 FAQ，逐条构建参考上下文
                    if e.get("answer"):  # 只取有答案的项
                        ctx.append(f"问答参考（相似度={d:.4f}）：{e['answer']}")  # 拼接上下文
                if ctx:  # 有上下文才调用 LLM
                    async for p in self._rag_stream_llm(
                        question=question,  # 原问题
                        contexts=ctx,  # FAQ 参考上下文
                        memory_snippet=memory_snippet,  # 用户历史
                        user_external_id=user_external_id,  # 缓存隔离
                    ):
                        yield p
                    return  # FAQ+LLM 路径结束

        # ---------------------------------------------------------------------
        # 步骤6：法律检索分支（FAQ 不足时）
        # ---------------------------------------------------------------------
        dense_limit = self.settings.hybrid_dense_candidate_k  # dense 候选数量
        legal_raw = await self._milvus_search(
            COLLECTION_LEGAL_CHILD,  # 法律子块集合
            qvec,  # 查询向量
            limit=dense_limit,  # 候选数量
            output_fields=["text", "parent_id", "source_file"],  # 取出子块文本和父id
        )
        legal_parsed = _parse_milvus_hits(legal_raw)  # 解析结果

        if not legal_parsed:  # 法律子块检索无命中
            async for p in self._stream_simple_llm(
                [
                    SystemMessage(
                        content=f"你是{ASSISTANT_NAME}。知识库暂无法律片段命中，请诚实说明并给出通用建议。",
                    ),
                    HumanMessage(content=augment_question_with_memory(question, memory_snippet)),
                ],
            ):
                yield p
            return  # 执行兜底回复后结束

        # 把命中结果拆成常用结构
        child_ids = [x[0] for x in legal_parsed]  # 子块 id 列表
        id_to_text = {x[0]: str(x[2].get("text", "")) for x in legal_parsed}  # 子块 id -> 子块文本
        dense_ranked = [x[0] for x in legal_parsed]  # dense 路排序结果

        # ---------------------------------------------------------------------
        # 步骤7：可选 BM25 + RRF 融合
        # ---------------------------------------------------------------------
        if self.settings.legal_hybrid_bm25_enabled and len(child_ids) > 1:  # 开关开启且候选足够时启用 BM25 融合
            from rank_bm25 import BM25Okapi  # 延迟导入，减少不必要开销

            tokenized_corpus = [_tokenize(id_to_text[i]) for i in child_ids]  # 子块语料分词
            tokenized_q = _tokenize(question)  # 问题分词
            bm25 = BM25Okapi(tokenized_corpus)  # 基于候选语料构建 BM25 打分器
            scores = bm25.get_scores(tokenized_q)  # 计算查询对每个候选的 BM25 分数
            bm25_order = [
                child_ids[i]
                for i in sorted(
                    range(len(child_ids)),
                    key=lambda k: scores[k],  # 按 BM25 分数排序
                    reverse=True,
                )
            ]
            ranked_lists = [
                dense_ranked,  # dense 排序
                bm25_order[: self.settings.hybrid_bm25_candidate_k],  # BM25 截断排序
            ]
        else:
            ranked_lists = [dense_ranked]  # 未启用 BM25 时只保留 dense 排序

        fused = reciprocal_rank_fusion(
            ranked_lists,  # 多路排序输入
            k=self.settings.hybrid_rrf_k,  # RRF 衰减参数
        )
        top_child_ids = [doc for doc, _ in fused[:30]]  # 融合后取前30个子块

        # ---------------------------------------------------------------------
        # 步骤8：子块 -> 父文档回溯（去重保持顺序）
        # ---------------------------------------------------------------------
        parent_ids_ordered: list[int] = []  # 有序父id列表
        seen: set[int] = set()  # 去重集合
        for cid in top_child_ids:  # 按融合顺序遍历子块
            ent = next((e for hid, _, e in legal_parsed if hid == cid), {})  # 找到对应实体字段
            pid = ent.get("parent_id")  # 取父文档 id
            if pid is None:  # 命中缺少 parent_id 时跳过该子块
                continue
            pid = int(pid)  # 转 int
            if pid not in seen:  # 未出现过才加入
                seen.add(pid)
                parent_ids_ordered.append(pid)

        parent_texts_map = await self._fetch_parents(parent_ids_ordered)  # 批量回查父文档全文
        passages = [
            parent_texts_map[pid]
            for pid in parent_ids_ordered
            if parent_texts_map.get(pid)  # 过滤空文本
        ]

        if not passages:  # 回查后父文档正文为空或缺失
            async for p in self._stream_simple_llm(
                [
                    SystemMessage(content=RAG_SYSTEM),  # 用 RAG 系统提示
                    HumanMessage(content=augment_question_with_memory(question, memory_snippet)),
                ],
            ):
                yield p
            return  # 执行兜底路径后结束

        # ---------------------------------------------------------------------
        # 步骤9：重排（CrossEncoder）
        # ---------------------------------------------------------------------
        reranker = self._reranker()  # 获取重排服务（懒加载）
        scores = await reranker.rank(question, passages)  # 对父文档逐条打分
        ranked_idx = sorted(
            range(len(passages)),
            key=lambda i: scores[i],  # 按分数排序
            reverse=True,
        )
        top_n = self.settings.legal_rerank_top_n  # 取前N条
        final_ctx = [passages[i] for i in ranked_idx[:top_n]]  # 最终上下文

        # ---------------------------------------------------------------------
        # 步骤10：RAG 生成
        # ---------------------------------------------------------------------
        async for p in self._rag_stream_llm(
            question=question,  # 原问题
            contexts=final_ctx,  # 重排后的上下文
            memory_snippet=memory_snippet,  # 用户历史记忆
            user_external_id=user_external_id,  # 用户标识（缓存隔离）
        ):
            yield p  # 逐片返回

    async def _rag_stream_llm(  # RAG 生成子流程：带参考资料的流式输出并写缓存
        self,
        question: str,  # 用户问题
        contexts: list[str],  # 检索得到的参考资料列表
        *,
        memory_snippet: str | None = None,  # 可选：历史记忆文本
        user_external_id: str | None = None,  # 可选：用户标识（用于缓存 scope）
    ) -> AsyncIterator[str]:
        """
        带检索上下文的流式生成路径。

        行为：
        - 构造 RAG 系统消息 + 用户消息
        - 流式产出模型文本
        - 结束后写入缓存（route=rag_llm）
        """
        scope = f"{(user_external_id or '').strip()}:{question}"  # 拼接用户隔离作用域
        key = cache_key_for_query(scope)  # 根据作用域生成缓存键
        llm = self._llm()  # 创建本次生成使用的 LLM 客户端
        messages = [
            SystemMessage(content=RAG_SYSTEM),  # 专业 RAG 系统提示
            HumanMessage(content=build_user_message(question, contexts, memory_snippet)),  # 用户消息（问题+上下文+记忆）
        ]

        buf: list[str] = []  # 收集所有分片文本，最终拼成完整答案
        async for chunk in llm.astream(messages):  # 异步流式接收 token chunk
            if isinstance(chunk, AIMessageChunk) and chunk.content:  # 过滤空 chunk
                text_piece = str(chunk.content)  # 统一转字符串
                buf.append(text_piece)  # 累积完整答案
                yield text_piece  # 向上游逐片输出

        # 流式结束后写缓存，便于同问题命中
        await self._cache.set_json(
            key,
            {"answer": "".join(buf), "route": "rag_llm"},
            self.settings.cache_ttl_seconds,
        )

    async def _stream_simple_llm(  # 简单生成子流程：无检索上下文，仅做引导/兜底回复
        self,
        messages: list,  # 调用方传入的 LangChain 消息列表
    ) -> AsyncIterator[str]:
        """
        不带检索上下文的简单生成路径（闲聊引导 / 无命中兜底）。

        说明：
        - 本路径默认不写“专业问答同 key”缓存，避免污染专业缓存。
        """
        llm = self._llm()  # 创建 LLM 客户端用于流式生成
        buf: list[str] = []  # 收集分片文本（当前仅为占位，不用于缓存写入）
        async for chunk in llm.astream(messages):  # 流式生成
            if isinstance(chunk, AIMessageChunk) and chunk.content:  # 过滤空 chunk
                text_piece = str(chunk.content)  # 统一转字符串
                buf.append(text_piece)  # 累积
                yield text_piece  # 输出
        _ = buf  # 显式占位，强调此路径不写专业缓存
