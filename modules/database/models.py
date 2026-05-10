"""
SQLAlchemy ORM 模型：FAQ / 法律文档入库表，以及在线用户的聊天记忆表。

`faq_tab`：Excel 问答对；`legal_tab`：PDF 切块；`users_tab` / `his_chat_tab`：用户与历史对话。
"""

from __future__ import annotations

from datetime import datetime  # 时间戳字段默认值

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text  # 列类型
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship  # ORM 声明式 API


class Base(DeclarativeBase):
    """所有模型的公共元类（metadata 挂载点）。"""


class FaqTab(Base):
    """
    高频问答业务表：一行对应 Excel 里的一条「问题—答案」。

    离线：`mysql_loaders.load_faq_excel_to_mysql` 读 `data/法律问答对.xlsx` 写入本表；
    同步：`milvus_sync` 仅将 `is_high_frequency=True` 的行的 `question` 做向量写入 Milvus FAQ 集合，
    在线检索命中后用 `answer` 直出或拼进 LLM。
    """

    __tablename__ = "faq_tab"  # 与离线脚本、SQLAlchemy metadata.create_all 使用的物理表名一致

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )  # 主键：入库后生成；Milvus `xiaoyi_faq_highfreq` 集合主键与此相同，便于全量重建向量时不漂移

    question: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )  # 用户侧检索用语：在线 FAQ 分支对「当前用户问题」与该字段做向量相似度比对（COSINE）

    answer: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )  # 业务答案正文：高相似度时可不经 LLM 直接返回；略低相似度时可作为 Few-shot 上下文片段

    is_high_frequency: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )  # True：参与 `_fetch_faq_rows` → Milvus 同步；False：仅占 MySQL，不向 FAQ 向量集合写入（可用于临时下架）

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
    )  # 首次插入时间：审计/排查导入批次（UTC，与 ORM 默认一致）

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )  # 行变更时间：若后续提供后台改 FAQ，可用于增量同步策略（当前热更新多为全量重建 Milvus）


class LegalTab(Base):
    """
    法律长文档分层存储：同一物理表混存「父文档」与「子块」，服务 RAG 两段式检索。

    离线：`mysql_loaders.load_legal_pdfs_to_mysql` 按 PDF 页生成 parent 长文本，再在父文本上滑动窗口得到 child；
    在线：`pipeline` 只在 Milvus `legal_child` 集合里检索子块向量，命中后用 `parent_id` 拉回父行 `content`
    做 BM25/RRF 之后的父文档重排与生成上下文。
    """

    __tablename__ = "legal_tab"  # 父子两行共用此表，通过 doc_role / parent_id 区分角色

    __table_args__ = (
        Index("ix_legal_tab_source_file", "source_file", mysql_length=191),
    )  # 按来源文件名筛选时走索引；utf8mb4 下一列索引超长，191 字符前缀即可支撑常用文件名过滤且避免 MySQL 1071

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )  # 主键：parent 行与 child 行各自独立 id；Milvus 法律子集合仅嵌入 **child 行** id

    source_file: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
    )  # 来源 PDF 文件名（非路径）：入库脚本据此归类；在线检索结果可展示给用户便于溯源

    doc_role: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        index=True,
    )  # 取值约定："parent" 表示聚合后的长段落；"child" 表示供向量/BM25 用的短切片（见 chunking 模块）

    parent_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("legal_tab.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )  # child 行必填：指向本表中其父文档行的 id；parent 行自身为根节点故为 NULL；级联删除保证删父时子块一并清理

    chunk_index: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )  # 同一父文档下子块序号（0..n）：滑动窗口切分顺序；排查重复内容时可对照

    title: Mapped[str] = mapped_column(
        String(1024),
        default="",
    )  # 人类可读短标题：离线时常写入页码范围（如「第3-5页」）；检索展示可选

    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )  # parent 存较长合并正文；child 存单块切片文本——在线向量检索读 child.content，生成阶段优先取 parent.content

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
    )  # 入库时间：区分多次重新导入同一 PDF 时的批次（当前替换策略多为整表重建）

    children: Mapped[list["LegalTab"]] = relationship(
        "LegalTab",
        back_populates="parent",
        foreign_keys="LegalTab.parent_id",
    )  # ORM 便捷访问：从父行导航到其下所有子行（内存侧构建树，不落库额外字段）

    parent: Mapped["LegalTab | None"] = relationship(
        "LegalTab",
        back_populates="children",
        remote_side="LegalTab.id",
        foreign_keys="LegalTab.parent_id",
    )  # 从子行回溯父行：`pipeline._fetch_parents` 根据子块携带的 parent_id 批量拉父文档全文


class UserTab(Base):
    """在线用户：一行对应一个浏览器/客户端身份（external_id），与知识库 FAQ 无关。"""

    __tablename__ = "users_tab"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    external_id: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        unique=True,
        index=True,
    )  # 与 ChatRequest.user_external_id 对应；UUID 或业务侧 OpenID 等
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    chats: Mapped[list["HisChatTab"]] = relationship(
        "HisChatTab",
        back_populates="user",
        cascade="all, delete-orphan",
    )  # 删用户则删除其全部聊天行


class HisChatTab(Base):
    """用户聊天历史：每轮对话一行；查询侧按 user_id + created_at 取最近 N 条。"""

    __tablename__ = "his_chat_tab"
    __table_args__ = (Index("ix_his_chat_user_created", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users_tab.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )  # 指向 users_tab.id
    question: Mapped[str] = mapped_column(Text, nullable=False)  # 用户本轮原始问题全文（截断由 service 写入时限制）
    answer: Mapped[str] = mapped_column(Text, nullable=False)  # 助手 SSE 拼接后的完整回复
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped["UserTab"] = relationship("UserTab", back_populates="chats")
