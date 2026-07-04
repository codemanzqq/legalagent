---
name: legal-rag-rag-strategy
description: Legal RAG 项目的 RAG 技术选型：父子分块、混合检索、本地模型部署
metadata:
  type: project
---

## Legal RAG - RAG 技术选型

**分块策略**：Parent-Child 父子分块
- 父块：1800 字符，合并 PDF 连续页
- 子块：512 字符，滑动窗口（重叠 128 字符）
- 检索命中子块 → 扩展回父块提供完整上下文

**检索策略**：
- 混合检索：BM25 关键词 + 向量语义
- RRF 融合排序
- CrossEncoder 重排 (bge-reranker-large)

**模型部署**：纯本地化
- Embedding: bge-m3 (HuggingFace)
- Rerank: bge-reranker-large
- 无外部 API 依赖，适合私有化部署

**技术栈**：FastAPI + LangChain + PyMilvus
