# =============================================================================
# 教学说明：本文件在整体链路中的位置
# -----------------------------------------------------------------------------
# 输入：用户问题、参考资料字符串列表、可选记忆摘要；常量 `ASSISTANT_NAME` 来自 config。
# 输出：拼好的多行字符串，作为 `HumanMessage.content` 或系统提示片段。
# 被谁调用：`pipeline._stream_simple_llm` / `_rag_stream_llm` 传入 LangChain 消息列表。
# =============================================================================
"""
集中管理「人设 + 引用纪律 + 记忆块格式」，避免在 pipeline 里散落长 f-string。

`GUIDE_NON_PROFESSIONAL`：用户被意图模型判为非专业时使用。
`RAG_SYSTEM`：走知识库检索时使用。
"""

from modules.core.config import ASSISTANT_NAME  # 与 config 中常量一致，改一处全局生效

GUIDE_NON_PROFESSIONAL = f"""你是「{ASSISTANT_NAME}」，一位严谨、友好的法律与税务知识助手。
当前用户的问题不属于专业知识范畴或与税法/劳动法等场景无关。
用户消息中可能附带「最近问答记录」摘要，仅供对话连贯；引导时仍以礼貌邀请用户提出税法、劳动法等专业问题为主，不要编造法条。"""

RAG_SYSTEM = f"""你是「{ASSISTANT_NAME}」，一位面向中国用户的法律与税务智能助手。
请严格依据提供的「参考资料」作答；若资料不足，请明确说明并提示用户咨询专业人士。

作答风格（重要）：
- 当参考资料中已有与用户问题直接对应的条文、段落或确定性表述时，应优先将其原样引用或仅在标点、分段上做最小整理；不要先用长篇引言、套话总结或「一言以蔽之」式的改写包装。
- 用户询问「第几条」「条文内容」「如何规定」等时：先给出条文或核心句子的直接表述，再视需要极简短说明（一两句内）；禁止为了「看起来完整」而主观扩写、煽情或编造未出现在参考资料中的细节。
- 若多条参考资料片段重复或互补，合并为一段连贯引用即可，避免同一要点拆成多条bullet重复陈述。
- 仅在参考资料确实无法覆盖问题时再概括推断；避免编造法条号。"""


def augment_question_with_memory(question: str, memory_snippet: str | None) -> str:
    """
    若 `memory_snippet` 为 None 或空，原样返回 `question`；否则在问题后追加固定格式的历史块。

    记忆块由 `memory.service.format_chat_history_for_prompt` 生成。
    """
    if not memory_snippet:  # 匿名用户或未查到历史
        return question  # 不修改原问题
    return (
        f"{question}\n\n"
        "【以下为该用户在系统中的最近若干条问答记录（按时间从早到晚），本轮回复均可作为上下文参考（含闲聊引导与专业作答）。"
        "若用户询问与往期对话相关的内容请据实依据记录；勿编造记录中不存在的内容。】\n"
        f"{memory_snippet}"
    )  # 一大段字符串，整体作为「用户侧语义」进入模型


def build_user_message(question: str, contexts: list[str], memory_snippet: str | None = None) -> str:
    """
    把「用户问题（可含记忆）」与「编号参考资料」拼成单条 Human 消息，供 RAG 主模型消费。

    `contexts` 已是父文档全文或 FAQ 参考片段列表。
    """
    q = augment_question_with_memory(question, memory_snippet)  # 先合并记忆
    blocks = "\n\n".join(f"[片段{i+1}]\n{c}" for i, c in enumerate(contexts))  # enumerate 从 0 开始故显示 i+1
    return (
        f"用户问题：{q}\n\n"
        "作答提示：若参考资料中已有可直接回答该问题的原文或条文，请优先忠实引用该部分，"
        "避免冗长铺垫与过度归纳；仅在必要时用一两句话补充。\n\n"
        f"参考资料：\n{blocks}"
    )  # 返回 str，外层包装为 HumanMessage(content=...)
