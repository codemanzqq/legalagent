# =============================================================================
# 教学说明：本文件在整体链路中的位置
# -----------------------------------------------------------------------------
# 输入：用户问题字符串 `question`；可选 `user_external_id`（前端 UUID，用于缓存隔离与记忆）。
# 输出：异步生成器 `AsyncIterator[str]`，按时间顺序 yield 小文本片段，供 SSE 逐块推送。
# 被谁调用：`backend/app/api/chat.py` 中 `pipeline.stream_chat(...)`；通过 `deps.get_pipeline` 单例注入。
# 数据流摘要：Redis 命中 → 否则（已登录）组装记忆摘要 → 意图模型分流 → FAQ 向量 / 法律混合检索
#           →（可选）CrossEncoder 重排 → LangChain 流式 LLM；FAQ 极高相似度时直出不走 LLM。
# 注意：`his_chat_tab` 写入不在本类，而在 API 层 `persist_user_turn`。
# =============================================================================
"""
在线 RAG 主链路：Redis → 记忆摘要 → 意图 → Milvus FAQ / 法律混合检索 → LLM 流式输出。
"""

from __future__ import annotations  # 允许类体内前向引用尚未定义的类名（本文件主要用 AsyncIterator 等）

import asyncio  # 提供 to_thread：把 pymilvus 同步 search 放到线程池，避免阻塞事件循环
import logging  # 本模块如需打日志可用 logger（当前逻辑以 yield 为主，日志较少）
import re  # 正则 findall：把中英文粗切成 token，供 BM25Okapi 使用
from collections.abc import AsyncIterator  # 类型注解：async def stream_chat 的返回类型「异步迭代器」

from langchain_core.messages import AIMessageChunk, HumanMessage, SystemMessage  # 三种消息：系统/人类/流式 AI 块
from langchain_openai import ChatOpenAI  # 兼容 OpenAI API 的聊天客户端，本项目指向 DashScope base_url
from pymilvus import Collection  # 绑定集合名并调用 search/load 的高层类
from sqlalchemy import select  # 构造 SELECT ORM 查询，用于按 id 批量拉父文档

from modules.cache.redis_client import RedisCache, cache_key_for_query, get_redis  # 问答缓存封装与 key 规则
from modules.core.config import ASSISTANT_NAME, get_settings  # 兜底文案里的助手名 + 全站配置
from modules.database.models import LegalTab  # 只查法律父行（doc_role=parent 的长正文）
from modules.database.session import get_session_factory  # 创建 AsyncSession 的工厂（单例）
from modules.embeddings.local_embedding import LocalEmbeddingService  # BGE-M3 异步句向量
from modules.milvus_store.client import ensure_milvus  # 在线程闭包里调用，保证已 connect
from modules.milvus_store.collections import COLLECTION_FAQ, COLLECTION_LEGAL_CHILD  # 两个集合名字符串常量
from modules.rag.dashscope_http import get_dashscope_async_client, get_dashscope_sync_client  # 注入 ChatOpenAI 的 httpx
from modules.rag.hybrid_rrf import reciprocal_rank_fusion  # 稠密排序 + BM25 排序 多路融合
from modules.memory.service import (
    DEFAULT_MEMORY_CONTEXT_LINES,  # 默认取最近几条 his_chat（与 fetch 的 limit 一致）
    fetch_recent_chat_lines,  # 异步读库：最近 N 条 ORM 行
    format_chat_history_for_prompt,  # 把行转成可读多行字符串，塞进 HumanMessage
    resolve_user_id,  # external_id → users_tab.id，没有则插入再 flush
)
from modules.rag.intent import is_professional_query  # 异步：是否走专业检索（否则闲聊引导）
from modules.rag.prompts import (
    GUIDE_NON_PROFESSIONAL,  # 非专业问题的系统提示
    RAG_SYSTEM,  # 专业 RAG 系统提示
    augment_question_with_memory,  # 把记忆摘要拼到用户问题后
    build_user_message,  # 把问题 + 参考资料片段拼成一条 Human 正文
)

logger = logging.getLogger(__name__)  # 模块 logger，级别继承根 logging 配置


def _tokenize(text: str) -> list[str]:
    """
    BM25 需要「分词后的 token 列表」；这里不用 jieba，用正则粗切中文连续字、英文单词、数字。
    """
    return re.findall(r"[\u4e00-\u9fff]|[a-zA-Z]+|[0-9]+", text.lower())  # \u4e00-\u9fff 为常用汉字 Unicode 范围；英文转小写降低大小写噪声


def _entity_to_dict(entity) -> dict:
    """
    Milvus Hit.entity 在不同版本可能是 dict 或 Row-like：统一成 dict 方便 `.get`。
    """
    if entity is None:  # 某些命中无标量字段
        return {}  # 空 dict 表示无附加字段
    if isinstance(entity, dict):  # 已是 Python dict
        return entity  # 直接返回
    if hasattr(entity, "to_dict"):  #  duck typing：有 to_dict 就调用
        return entity.to_dict()  # type: ignore[no-any-return]
    try:
        return dict(entity)  # type: ignore[arg-type]  # 尝试强转
    except Exception:  # noqa: BLE001
        return {}  # 任何失败都降级为空 dict，不中断检索


def _parse_milvus_hits(raw_hits) -> list[tuple[int, float, dict]]:
    """
    pymilvus search 返回 SearchResult：第 0 维对应 batch 中第 1 个查询（本代码每次只查 1 个向量）。
    """
    out: list[tuple[int, float, dict]] = []  # 结构化后的列表
    if not raw_hits or not raw_hits[0]:  # 无结果或第一查询无 hits
        return out  # 空列表
    for hit in raw_hits[0]:  # 遍历 Hit 对象
        ent = _entity_to_dict(getattr(hit, "entity", None))  # hit.entity 为动态字段容器
        out.append((int(hit.id), float(hit.distance), ent))  # id 主键；distance 在 COSINE 下为相似度
    return out


class RagPipeline:
    """封装端到端异步 RAG；建议每个 worker 进程只实例化一次（依赖注入单例）。"""

    def __init__(self) -> None:
        self.settings = get_settings()  # 读取配置单例
        self._emb = LocalEmbeddingService()  # 句向量服务（懒加载底层模型在首次使用时）
        self._rerank = None  # CrossEncoder 较重，首次法律检索时再加载
        self._cache = RedisCache(get_redis())  # 异步 Redis 缓存封装

    def _llm(self) -> ChatOpenAI:
        """构造带流式与自定义 httpx 客户端的 ChatOpenAI（DashScope OpenAI 兼容）。"""
        s = self.settings  # 缩短引用
        return ChatOpenAI(
            model=s.llm_model,  # 如 qwen-max
            temperature=0.2,  # 略低以降低胡编概率
            api_key=s.dashscope_api_key,
            base_url=s.dashscope_base_url,
            streaming=True,  # 必须开启以便 astream
            timeout=120,
            http_client=get_dashscope_sync_client(),  # 同步调用路径（部分 LangChain 内部使用）
            http_async_client=get_dashscope_async_client(),  # 异步流式主路径
        )

    def _reranker(self):
        """懒加载本地重排模型，避免 FAQ 直达路径也加载 CrossEncoder。"""
        if self._rerank is None:  # 尚未初始化
            from modules.rerank.local_rerank import LocalRerankService  # 延迟导入减轻冷启动

            self._rerank = LocalRerankService()
        return self._rerank

    async def _milvus_search(
        self,
        collection: str,
        vector: list[float],
        limit: int,
        output_fields: list[str],
    ):
        """在线程中执行 pymilvus Collection.search，避免阻塞 asyncio 事件循环。"""

        def _run():
            """同步 Milvus 检索闭包。"""
            ensure_milvus()  # 在线进程必须显式连接
            col = Collection(collection)  # 绑定集合
            col.load()  # 加载段到内存
            return col.search(
                data=[vector],  # 单查询向量批
                anns_field="embedding",  # 向量列名（与建表一致）
                param={"metric_type": "COSINE", "params": {"ef": 128}},  # HNSW 查询参数 ef
                limit=limit,  # Top-K
                output_fields=output_fields,  # 需要返回的标量字段
            )

        return await asyncio.to_thread(_run)  # 线程池执行同步 pymilvus API

    async def _fetch_parents(self, ids: list[int]) -> dict[int, str]:
        """按父文档主键批量查询 MySQL，返回 id → 正文（截断防止超长上下文）。"""
        if not ids:  # 无 id 则跳过数据库
            return {}
        factory = get_session_factory()  # 会话工厂单例
        async with factory() as session:  # 自动关闭会话
            res = await session.execute(select(LegalTab).where(LegalTab.id.in_(ids)))  # IN 查询
            rows = list(res.scalars().all())  # 取出 ORM 对象列表
        return {r.id: (r.content or "")[:8000] for r in rows}  # 限制单篇最大字符，保护上下文窗口

    async def stream_chat(
        self,
        question: str,
        user_external_id: str | None = None,
    ) -> AsyncIterator[str]:
        """
        统一对外接口：按顺序 yield 字符串片段；调用方（SSE）原样转发。

        分支顺序：缓存 →（已登录）加载最近 10 条库内问答作记忆 → 意图闲聊或专业 → FAQ / 法律检索 / 兜底（凡调用 LLM 均附带同一记忆块）。

        匿名不传 `user_external_id` 时不查 `his_chat_tab`，记忆块为空。
        """
        # ----- ① Redis：完全相同 scope（用户+问题）命中则直接返回答案，跳过后续检索与 LLM -----
        scope = f"{(user_external_id or '').strip()}:{question}"  # 匿名用户 external_id 为空串，仍可与问题组成 key
        key = cache_key_for_query(scope)
        cached = await self._cache.get_json(key)
        if isinstance(cached, dict) and cached.get("answer"):
            yield str(cached["answer"])
            return

        # ----- ② 记忆（已登录）：每次生成前统一拉取最近 N 轮问答，闲聊与专业共用；匿名为 None -----
        memory_snippet: str | None = None
        if user_external_id and user_external_id.strip():
            factory = get_session_factory()
            async with factory() as session:
                uid = await resolve_user_id(session, user_external_id.strip())
                rows = await fetch_recent_chat_lines(session, uid, DEFAULT_MEMORY_CONTEXT_LINES)
                memory_snippet = format_chat_history_for_prompt(rows)
                await session.commit()

        # ----- ③ 意图：非专业 → 闲聊引导（Human 消息同样附带上述记忆块） -----
        if not await is_professional_query(question):
            async for piece in self._stream_simple_llm(
                [
                    SystemMessage(content=GUIDE_NON_PROFESSIONAL),
                    HumanMessage(content=augment_question_with_memory(question, memory_snippet)),
                ],
            ):
                yield piece
            return

        # ----- ④ 以下进入「专业检索」主路径：先对当前问题编码查询向量 -----
        qvec = await self._emb.embed_query(question)

        # ----- ⑤ FAQ：Milvus 高频问答集合 Top10；高置信直出无 LLM（不注入记忆）；否则拼 FAQ 进 LLM（带记忆） -----
        faq_raw = await self._milvus_search(
            COLLECTION_FAQ,
            qvec,
            limit=10,
            output_fields=["question", "answer"],
        )
        faq_parsed = _parse_milvus_hits(faq_raw)

        th_direct = self.settings.faq_direct_distance_threshold  # 配置：允许的「非相似」比例上界
        th_llm = self.settings.faq_llm_distance_threshold  # FAQ 多路拼上下文时的相似度门槛
        # Milvus COSINE：hit.distance 为相似度 ∈[0,1] 左右，越大越相似；阈值换算为最低相似度 = 1 - th
        sim_direct = 1.0 - th_direct
        sim_llm = 1.0 - th_llm

        if faq_parsed:  # FAQ 集合有返回（即使分数低）
            _best_id, best_sim, ent = faq_parsed[0]  # 按相似度排序后的第一条
            if best_sim >= sim_direct and ent.get("answer"):  # 高置信：直接返回答案文本
                ans = str(ent["answer"])
                await self._cache.set_json(
                    key,
                    {"answer": ans, "route": "faq_direct"},
                    self.settings.cache_ttl_seconds,
                )
                for i in range(0, len(ans), 40):  # 人为切块 yield，改善 SSE 首包观感
                    yield ans[i : i + 40]
                return

            close = [x for x in faq_parsed if x[1] >= sim_llm][: self.settings.faq_top_k_for_llm]  # 过滤 + 截断条数
            if close and close[0][1] >= sim_llm:  # 至少一条满足中等相似度
                ctx = []
                for _, d, e in close:
                    if e.get("answer"):
                        ctx.append(f"问答参考（相似度={d:.4f}）：{e['answer']}")  # 拼若干 FAQ 作参考
                if ctx:
                    async for p in self._rag_stream_llm(
                        question,
                        ctx,
                        memory_snippet=memory_snippet,
                        user_external_id=user_external_id,
                    ):
                        yield p
                    return

        # ---------- 法律长文档混合检索分支 ----------
        dense_limit = self.settings.hybrid_dense_candidate_k  # 向量检索候选数量
        legal_raw = await self._milvus_search(
            COLLECTION_LEGAL_CHILD,
            qvec,
            limit=dense_limit,
            output_fields=["text", "parent_id", "source_file"],
        )
        legal_parsed = _parse_milvus_hits(legal_raw)
        if not legal_parsed:  # 向量库无任何子块命中
            async for p in self._stream_simple_llm(
                [
                    SystemMessage(
                        content=f"你是{ASSISTANT_NAME}。知识库暂无法律片段命中，请诚实说明并给出通用建议。",
                    ),
                    HumanMessage(content=augment_question_with_memory(question, memory_snippet)),
                ],
            ):
                yield p
            return

        child_ids = [x[0] for x in legal_parsed]  # 子块 id 列表（与向量检索顺序一致）
        id_to_text = {x[0]: str(x[2].get("text", "")) for x in legal_parsed}  # id → 子块文本

        dense_ranked = [x[0] for x in legal_parsed]  # 稠密检索给出的 doc 顺序

        if self.settings.legal_hybrid_bm25_enabled and len(child_ids) > 1:  # 开启 BM25 且候选多于 1
            from rank_bm25 import BM25Okapi  # 延迟导入

            tokenized_corpus = [_tokenize(id_to_text[i]) for i in child_ids]  # 每个子块一篇伪文档
            tokenized_q = _tokenize(question)  # 查询分词
            bm25 = BM25Okapi(tokenized_corpus)
            scores = bm25.get_scores(tokenized_q)  # 每个子块得分
            bm25_order = [child_ids[i] for i in sorted(range(len(child_ids)), key=lambda k: scores[k], reverse=True)]
            ranked_lists = [dense_ranked, bm25_order[: self.settings.hybrid_bm25_candidate_k]]  # 两路排序列表
        else:
            ranked_lists = [dense_ranked]  # 仅稠密一路

        fused = reciprocal_rank_fusion(
            ranked_lists,
            k=self.settings.hybrid_rrf_k,
        )  # RRF 融合得分，返回 (doc_id, score) 降序
        top_child_ids = [doc for doc, _ in fused[:30]]  # 取融合后前若干子块

        parent_ids_ordered: list[int] = []
        seen = set()
        for cid in top_child_ids:  # 按融合顺序遍历子块
            ent = next((e for hid, _, e in legal_parsed if hid == cid), {})  # 找回该子块实体字段
            pid = ent.get("parent_id")
            if pid is None:
                continue
            pid = int(pid)
            if pid not in seen:  # 父文档去重并保持首次出现顺序
                seen.add(pid)
                parent_ids_ordered.append(pid)

        parent_texts_map = await self._fetch_parents(parent_ids_ordered)  # 批量拉父文档全文
        passages = [parent_texts_map[pid] for pid in parent_ids_ordered if parent_texts_map.get(pid)]  # 按序组装 passages
        if not passages:  # 父文档均被过滤空
            async for p in self._stream_simple_llm(
                [
                    SystemMessage(content=RAG_SYSTEM),
                    HumanMessage(content=augment_question_with_memory(question, memory_snippet)),
                ],
            ):
                yield p
            return

        reranker = self._reranker()
        scores = await reranker.rank(question, passages)  # CrossEncoder 打分
        ranked_idx = sorted(range(len(passages)), key=lambda i: scores[i], reverse=True)  # 得分降序索引
        top_n = self.settings.legal_rerank_top_n
        final_ctx = [passages[i] for i in ranked_idx[:top_n]]  # 取 Top-N 父文档全文作为上下文

        async for p in self._rag_stream_llm(
            question,
            final_ctx,
            memory_snippet=memory_snippet,
            user_external_id=user_external_id,
        ):
            yield p

    async def _rag_stream_llm(
        self,
        question: str,
        contexts: list[str],
        *,
        memory_snippet: str | None = None,
        user_external_id: str | None = None,
    ) -> AsyncIterator[str]:
        """使用 RAG 系统提示词与拼装后的参考资料进行流式生成，并在结束后写入缓存。"""
        scope = f"{(user_external_id or '').strip()}:{question}"
        key = cache_key_for_query(scope)
        llm = self._llm()  # 每次调用新建 ChatOpenAI（内部 httpx 客户端仍单例）
        messages = [
            SystemMessage(content=RAG_SYSTEM),
            HumanMessage(content=build_user_message(question, contexts, memory_snippet)),
        ]
        buf: list[str] = []  # 拼接完整回答以便缓存
        async for chunk in llm.astream(messages):  # LangChain 异步 token 流
            if isinstance(chunk, AIMessageChunk) and chunk.content:
                buf.append(str(chunk.content))
                yield str(chunk.content)
        await self._cache.set_json(
            key,
            {"answer": "".join(buf), "route": "rag_llm"},
            self.settings.cache_ttl_seconds,
        )  # 仅带参考资料生成的路径写 Redis，便于重复提问命中

    async def _stream_simple_llm(self, messages: list) -> AsyncIterator[str]:
        """不使用参考资料时的通用流式生成（意图引导、无命中兜底等）；默认不写长期缓存。"""
        llm = self._llm()
        buf: list[str] = []
        async for chunk in llm.astream(messages):
            if isinstance(chunk, AIMessageChunk) and chunk.content:
                buf.append(str(chunk.content))
                yield str(chunk.content)
        _ = buf  # 占位：避免未使用变量告警；刻意不缓存闲聊引导内容，防止缓存污染专业问答 key
