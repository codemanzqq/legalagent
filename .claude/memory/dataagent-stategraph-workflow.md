---
name: dataagent-stategraph-workflow
description: DataAgent 的 StateGraph 工作流、Human-in-the-loop 和多分块器实现
metadata:
  type: project
---

## DataAgent - StateGraph 工作流与 Human-in-the-loop

**StateGraph 工作流** (`GraphServiceImpl`)：
- 16+ 节点编排：意图识别→证据召回→计划生成→SQL/Python 执行→报告生成
- 编译时配置：`interruptBefore(HUMAN_FEEDBACK_NODE)`
- 支持多轮对话与动态分支决策

**Human-in-the-loop 实现** (`HumanFeedbackNode.java`)：
- 用户审批 (Approve) → PLAN_EXECUTOR_NODE
- 用户驳回 (Reject) → PLANNER_NODE + 重试计数 +1
- 前端 `AgentKnowledgeConfig.vue` 展示计划供用户确认

**多分块器可选** (`SplitterType.java`)：
- 5 种类型：TOKEN / RECURSIVE / SENTENCE / PARAGRAPH / SEMANTIC
- 前端下拉选择，helper text 显示策略说明
- SemanticTextSplitter 基于嵌入相似度滑动窗口切分

**动态 LLM 路由** (`AiModelRegistry.java`)：
- 懒加载 ChatClient，`volatile` 缓存
- `refreshChat()` 热更新模型
- **手动配置切换**，非自动复杂度路由

**技术栈**：Spring AI Alibaba Graph + StateGraph + MCP 协议
