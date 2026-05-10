"""
用户与聊天历史的数据访问：解析外部用户标识、按时间拉取最近记录、持久化一轮问答。

注意：`persist_user_turn` 在独立会话内 `commit`；`resolve_user_id` 仅 `flush`，由调用方 `commit`。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from modules.database.models import HisChatTab, UserTab

# 注入模型上下文的默认条数（与 pipeline 中 fetch_recent_chat_lines 的 limit 一致）
DEFAULT_MEMORY_CONTEXT_LINES = 10


async def resolve_user_id(session: AsyncSession, external_id: str) -> int:
    """
    根据前端传入的 external_id 返回 `users_tab.id`。

    - 已存在：直接返回主键。
    - 不存在：插入一行后 `flush` 拿 id；**不**在本函数内 commit，便于与同一事务内的查询组合。
    """
    ext = external_id.strip()
    if not ext:
        raise ValueError("external_id empty")
    res = await session.execute(select(UserTab).where(UserTab.external_id == ext))
    row = res.scalar_one_or_none()
    if row is not None:
        return int(row.id)
    u = UserTab(external_id=ext)
    session.add(u)
    await session.flush()  # 生成自增 id，供尚未 commit 时的外键使用
    return int(u.id)


async def fetch_recent_chat_lines(
    session: AsyncSession,
    user_id: int,
    limit: int = DEFAULT_MEMORY_CONTEXT_LINES,
) -> list[HisChatTab]:
    """
    读取某用户最近 `limit` 条聊天行（默认 5 条，供模型作短期记忆上下文）。

    SQL 按 `created_at DESC`；返回前 **reverse** 成时间正序，便于 Prompt 中「从早到晚」叙述。
    """
    res = await session.execute(
        select(HisChatTab)
        .where(HisChatTab.user_id == user_id)
        .order_by(HisChatTab.created_at.desc())
        .limit(limit),
    )
    rows = list(res.scalars().all())
    rows.reverse()
    return rows


def format_chat_history_for_prompt(rows: list[HisChatTab]) -> str:
    """
    将 ORM 行转为纯文本块；供 `prompts.augment_question_with_memory` 拼入用户消息。

    单行过长时截断 question/answer，防止极端长文本撑爆上下文。
    """
    if not rows:
        return "（当前尚无已存储的聊天记录。）"
    lines: list[str] = []
    for i, r in enumerate(rows, start=1):
        q = (r.question or "").replace("\n", " ").strip()[:2000]
        a = (r.answer or "").replace("\n", " ").strip()[:4000]
        lines.append(f"{i}. 用户问：{q}\n   助手答：{a}")
    return "\n".join(lines)


async def persist_user_turn(external_id: str | None, question: str, answer: str) -> None:
    """
    SSE 整轮成功后调用：插入 `his_chat_tab`。

    - 无 external_id、空回答：直接返回（不落库）。
    - 使用独立 session + 单次 commit，与管线内只读会话隔离。
    """
    if not external_id or not external_id.strip() or not (answer or "").strip():
        return  # 无用户标识或空答案不落库，避免脏数据
    from modules.database.session import get_session_factory  # 延迟导入，减少 import 环依赖风险

    factory = get_session_factory()
    async with factory() as session:  # 独立事务：与管线内只读 session 分离
        uid = await resolve_user_id(session, external_id.strip())  # 确保 users_tab 有对应行
        session.add(
            HisChatTab(
                user_id=uid,
                question=question[:8000],  # 与 ChatRequest.message 上限对齐
                answer=answer[:65000],  # TEXT 足够大，仍截断防止异常超长
            ),
        )
        await session.commit()  # 立即持久化，供后续「自述历史」查询
