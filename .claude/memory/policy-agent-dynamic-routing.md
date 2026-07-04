---
name: policy-agent-dynamic-routing
description: Policy Agent 的嵌入模型动态路由和混合检索实现机制
metadata:
  type: project
---

## Policy Agent - 动态嵌入路由与混合检索

**嵌入模型动态路由**：
- 配置多模型池 (`app.knowledge.embedding.models`)
- 知识库实体绑定 `embedding_model` 字段
- 运行时 `RuntimeRagVectorStore.resolveRetrievalContext()` 读取绑定 → 路由到对应向量表
- 支持：Ollama 本地模型 + DashScope 云端模型混合部署

**混合检索实现** (`RagRetrievalService`)：
1. 查询扩展（同义词：笔记本↔电脑）
2. 向量检索 (余弦相似度) + 关键词检索 (BM25+LIKE)
3. RRF 融合：向量权重 0.65，关键词 0.35，k=60
4. DashScope Rerank 重排 (qwen3-vl-rerank)
5. 阈值过滤 → Child→Parent 扩展 → 优先最新政策文档

**Rerank 可选机制**：三层回退
- 配置开关 → API Key 检查 → 错误捕获降级

**技术栈**：Spring Boot 3.4.1 + Spring AI 1.0.3 + PostgreSQL+pgvector
