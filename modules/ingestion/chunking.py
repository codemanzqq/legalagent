"""
父子分块策略：法律 PDF 先合并为「父块」（较长上下文），再在父文本上滑动窗口得到「子块」。

在线流程：向量检索命中子块 → 用 parent_id 换父文档全文参与重排与生成。
"""

from __future__ import annotations

import re  # 正则压缩空白
from dataclasses import dataclass  # 不可变数据结构描述切块


def normalize_ws(text: str) -> str:
    """统一换行与空格，减少噪声提高 BM25 与向量稳定性。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")  # 换行符统一
    text = re.sub(r"[ \t]+", " ", text)  # 连续空格压成单空格
    text = re.sub(r"\n{3,}", "\n\n", text)  # 过多空行压缩为双换行
    return text.strip()


@dataclass(frozen=True)
class ParentChunk:
    """父级聚合块：携带标题摘要、正文与起始页号（用于追溯）。"""

    title: str
    text: str
    page_no: int


@dataclass(frozen=True)
class ChildChunk:
    """子级检索块：隶属于某个父块索引下的滑动窗口切片。"""

    parent_index: int
    chunk_index: int
    text: str


def chunk_pdf_pages_to_parents(page_texts: list[str], max_chars: int = 1800) -> list[ParentChunk]:
    """按页缓冲合并：总长超过 max_chars 时 flush 为一个父块；标题标注页范围。"""
    parents: list[ParentChunk] = []
    buf: list[str] = []  # 当前聚合中的页文本片段
    buf_pages: list[int] = []  # 对应页码（从 1 起）
    buf_len = 0  # 字符计数用于粗估体积

    def flush() -> None:
        """把缓冲区写成 ParentChunk 并清空缓冲区。"""
        nonlocal buf, buf_pages, buf_len
        if not buf:
            return
        merged = normalize_ws("\n".join(buf))
        title = f"第{buf_pages[0]}-{buf_pages[-1]}页" if len(set(buf_pages)) > 1 else f"第{buf_pages[0]}页"
        parents.append(ParentChunk(title=title, text=merged, page_no=buf_pages[0]))
        buf, buf_pages, buf_len = [], [], 0

    for i, pg in enumerate(page_texts, start=1):  # i 为页码
        t = normalize_ws(pg)
        if not t:
            continue
        if buf_len + len(t) > max_chars and buf:  # 再加当前页会超长且缓冲区非空
            flush()
        buf.append(t)
        buf_pages.append(i)
        buf_len += len(t)
    flush()

    split_parents: list[ParentChunk] = []
    for p in parents:  # 极端超长父块再硬切（少见）
        if len(p.text) <= max_chars * 2:
            split_parents.append(p)
            continue
        start = 0
        part = 0
        while start < len(p.text):
            piece = p.text[start : start + max_chars * 2]
            split_parents.append(
                ParentChunk(title=f"{p.title}-{part}", text=piece, page_no=p.page_no),
            )
            start += max_chars * 2
            part += 1
    return split_parents or parents


def split_children_from_parent(
    parent_text: str,
    parent_index: int,
    child_chars: int = 512,
    overlap: int = 128,
) -> list[ChildChunk]:
    """滑动窗口：步长 = child_chars - overlap；过短父文本整段为单个子块。"""
    t = normalize_ws(parent_text)
    if not t:
        return []
    if len(t) <= child_chars:
        return [ChildChunk(parent_index=parent_index, chunk_index=0, text=t)]

    children: list[ChildChunk] = []
    step = max(child_chars - overlap, 1)  # 至少前进 1 字符避免死循环
    idx = 0
    pos = 0
    while pos < len(t):
        piece = t[pos : pos + child_chars]
        if piece.strip():
            children.append(ChildChunk(parent_index=parent_index, chunk_index=idx, text=piece))
            idx += 1
        pos += step
    return children
