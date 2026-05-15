# =============================================================================
# 教学说明：本文件在整体链路中的位置
# -----------------------------------------------------------------------------
# 输入：`AsyncSession` + `external_id` / `user_id`；或 SSE 结束后的 `question`/`answer` 文本。
# 输出：`resolve_user_id` 返回 int；`fetch_recent_chat_lines` 返回 ORM 行列表；`persist_user_turn` 无返回（落库）。
# 被谁调用：`pipeline.stream_chat`（只读+commit）；`api/chat.py` 的 `persist_user_turn`（写 his_chat）。
# =============================================================================
"""
用户与聊天历史的数据访问层：与 `users_tab` / `his_chat_tab` 表一一对应。

`resolve_user_id` 故意不 commit，便于与同一 session 内其它查询共事务。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from modules.database.models import HisChatTab, UserTab

DEFAULT_MEMORY_CONTEXT_LINES = 10  # 与 pipeline 里 fetch_recent_chat_lines 的 limit 一致


async def resolve_user_id(session: AsyncSession, external_id: str) -> int:
    """
    SELECT users_tab by external_id；无则 INSERT 一行并 flush 得到自增 id。

    不在此 commit：调用方负责 commit（pipeline 在读完记忆后 commit 一次）。

    入参:
        session: 异步 ORM 会话。
        external_id: 前端传入的用户外部唯一标识（非空字符串）。
    返回:
        `users_tab.id` 整型主键；`external_id` 为空时抛出 ValueError。
    """
    ext = external_id.strip()  # 去空白
    if not ext:  # 空串非法
        raise ValueError("external_id empty")
    res = await session.execute(select(UserTab).where(UserTab.external_id == ext))  # 异步查询
    row = res.scalar_one_or_none()  # 0 行 None，多行会抛异常（unique 约束下不应发生）
    if row is not None:  # 已注册过
        return int(row.id)  # 返回内部主键
    u = UserTab(external_id=ext)  # 新建 ORM 对象
    session.add(u)  # 挂到 session
    await session.flush()  # 发 INSERT 并回填 u.id，但不结束事务
    return int(u.id)  # 新用户 id


async def fetch_recent_chat_lines(
    session: AsyncSession,
    user_id: int,
    limit: int = DEFAULT_MEMORY_CONTEXT_LINES,
) -> list[HisChatTab]:
    """
    ORDER BY created_at DESC LIMIT n，再在 Python 里 reverse，使列表按时间正序（旧→新）。

    正序便于 Prompt 里写「从早到晚」。

    入参:
        session: 异步 ORM 会话。
        user_id: `users_tab` 主键。
        limit: 最多返回的记录条数（默认与管线常量一致）。
    返回:
        按时间升序排列的 `HisChatTab` ORM 对象列表。
    """
    res = await session.execute(
        select(HisChatTab)
        .where(HisChatTab.user_id == user_id)  # 只查该用户
        .order_by(HisChatTab.created_at.desc())  # 新的在前
        .limit(limit),  # 最多 n 条
    )
    rows = list(res.scalars().all())  # materialize
    rows.reverse()  # 原地反转为时间升序
    return rows


def format_chat_history_for_prompt(rows: list[HisChatTab]) -> str:
    """
    无记录时返回固定提示句；有记录则格式化为「序号. 用户问：… 助手答：…」多行文本。

    对超长 question/answer 做截断，避免撑爆模型上下文。

    入参:
        rows: 已按时间排序的聊天记录 ORM 行列表。
    返回:
        可直接拼入 Prompt 的多行中文说明字符串；无记录时为固定占位句。
    """
    if not rows:  # 新用户从未对话
        return "（当前尚无已存储的聊天记录。）"
    lines: list[str] = []
    for i, r in enumerate(rows, start=1):  # 展示序号从 1 起
        q = (r.question or "").replace("\n", " ").strip()[:2000]  # 换行压空格，限制长度
        a = (r.answer or "").replace("\n", " ").strip()[:4000]
        lines.append(f"{i}. 用户问：{q}\n   助手答：{a}")  # 两行一条记录，缩进对齐助手行
    return "\n".join(lines)  # 单个大字符串


async def persist_user_turn(external_id: str | None, question: str, answer: str) -> None:
    """
    独立开 session：INSERT his_chat_tab 一行并 commit。

    无 external_id 或空答案则 no-op，避免写入无意义行。

    入参:
        external_id: 用户外部 id；None 或空白时不写入。
        question: 用户本轮问题（会按长度截断后入库）。
        answer: 助手完整回复（会按长度截断后入库）；空白时不写入。
    返回:
        无。
    """
    if not external_id or not external_id.strip() or not (answer or "").strip():  # 任一条件不满足
        return  # 直接返回，不发 SQL
    from modules.database.session import get_session_factory  # 函数内导入，避免循环 import

    factory = get_session_factory()
    async with factory() as session:  # 新会话 = 新事务，与 pipeline 内 session 隔离
        uid = await resolve_user_id(session, external_id.strip())  # 确保 users 表有主键
        session.add(
            HisChatTab(
                user_id=uid,  # 外键
                question=question[:8000],  # 与 API 校验上限对齐
                answer=answer[:65000],  # 极长 SSE 拼接仍留余量
            ),
        )
        await session.commit()  # 立即可被后续 fetch_recent_chat_lines 读到
