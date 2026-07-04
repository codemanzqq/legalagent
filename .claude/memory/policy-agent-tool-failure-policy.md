---
name: policy-agent-tool-failure-policy
description: Policy Agent 的 ToolFailurePolicyCenter 统一失败策略中心实现
metadata:
  type: project
---

## Policy Agent - ToolFailurePolicyCenter 失败策略中心

**核心实现** (`ToolFailurePolicyCenter.java`)：
- 统一重试配置：最多 2 次，间隔 300ms
- 可重试错误过滤：timeout / 502 / 503 / connection
- 兜底提示模板：按工具名返回用户友好提示

**三层降级链路**：
1. **自动重试**：仅临时错误触发
2. **缓存降级**：`serveStaleOnError=true` 返回过期缓存
3. **兜底提示**：`fallbackMessage()` 返回保守回答

**优化效果**（无效调用↓40%）：
- SingleFlight 合并并发请求
- 缓存降级减少 API 调用
- 可重试错误过滤避免浪费
- ToolStateManager 支持动态禁用故障工具

**应用案例**：WebSearchTool、DashScopeRerankService
