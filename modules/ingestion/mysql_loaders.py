"""
本地文件写入 MySQL：`data/` 下 Excel → `faq_tab`，PDF → `legal_tab`（父子分块）。

Excel 列名支持中英文常见别名；PDF 默认按文件名关键字筛选税法/劳动法等。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from modules.database.models import FaqTab, LegalTab
from modules.ingestion.chunking import (
    chunk_pdf_pages_to_parents,
    split_children_from_parent,
)
from modules.ingestion.pdf_extract import extract_pages_pdf

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]  # 仓库根目录
DATA_DIR = PROJECT_ROOT / "data"  # 默认数据目录


def _pick_columns(df: pd.DataFrame) -> tuple[str, str]:
    """根据列名别名映射找出问答两列在 DataFrame 中的实际列名。"""
    cols = {str(c).strip(): c for c in df.columns}
    lower = {k.lower(): k for k in cols}

    def find(*names: str) -> str | None:
        for n in names:
            if n in cols:
                return str(cols[n])
            if n.lower() in lower:
                return str(lower[n.lower()])
        return None

    q_col = find("问题", "question", "Question", "标题", "问")
    a_col = find("答案", "answer", "Answer", "答", "回复")
    if not q_col or not a_col:
        raise ValueError(
            f"无法识别问答列，请检查 Excel 表头。当前列：{list(df.columns)}",
        )
    return str(q_col), str(a_col)


async def load_faq_excel_to_mysql(
    session: AsyncSession,
    xlsx_path: Path,
    *,
    replace_all: bool = True,
) -> int:
    """读取 xlsx 写入 FaqTab；replace_all 时先 DELETE 全表。"""
    df = pd.read_excel(xlsx_path)
    q_col, a_col = _pick_columns(df)
    if replace_all:
        await session.execute(delete(FaqTab))

    count = 0
    for _, row in df.iterrows():  # 逐行读取 Excel；列名已映射为 q_col / a_col
        q = row.get(q_col)  # 问题单元格
        a = row.get(a_col)  # 答案单元格
        if pd.isna(q) or pd.isna(a):  # 跳过空行
            continue
        session.add(
            FaqTab(
                question=str(q).strip(),
                answer=str(a).strip(),
                is_high_frequency=True,  # 默认全部参与 Milvus 同步
            ),
        )
        count += 1
    await session.commit()  # 批量提交当前 FAQ 事务
    logger.info("faq rows imported: %s from %s", count, xlsx_path)
    return count


async def load_legal_pdfs_to_mysql(
    session: AsyncSession,
    pdf_dir: Path | None = None,
    *,
    replace_all: bool = True,
) -> dict[str, Any]:
    """遍历目录内 PDF：抽页 → 父块 → 子块，写入 LegalTab。"""
    root = pdf_dir or DATA_DIR
    pdfs = sorted(
        p
        for p in root.glob("*.pdf")
        if ("税" in p.name) or ("劳动" in p.name) or ("税法" in p.name) or ("劳动法" in p.name)
    )
    if not pdfs:
        pdfs = sorted(root.glob("*.pdf"))
        logger.info("未匹配到文件名关键字，回退为导入目录内全部 PDF：%s", [p.name for p in pdfs])
    if not pdfs:
        logger.warning("未在 %s 找到任何 PDF，跳过 legal 入库", root)

    if replace_all:  # 全量重导前清空法律表；关外键检查避免父子删除顺序问题
        await session.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        await session.execute(text("DELETE FROM legal_tab"))
        await session.execute(text("SET FOREIGN_KEY_CHECKS=1"))

    stats = {"files": 0, "parents": 0, "children": 0}
    for pdf in pdfs:
        pages = extract_pages_pdf(pdf)
        parents = chunk_pdf_pages_to_parents(pages)
        parent_rows: list[LegalTab] = []
        for pc in parents:
            row = LegalTab(
                source_file=pdf.name,
                doc_role="parent",
                parent_id=None,
                chunk_index=0,
                title=pc.title,
                content=pc.text,
            )
            session.add(row)
            parent_rows.append(row)
        await session.flush()

        child_total = 0
        for idx, pr in enumerate(parent_rows):
            parent_text = pr.content
            children = split_children_from_parent(parent_text, idx)
            for ch in children:
                session.add(
                    LegalTab(
                        source_file=pdf.name,
                        doc_role="child",
                        parent_id=pr.id,
                        chunk_index=ch.chunk_index,
                        title=pr.title,
                        content=ch.text,
                    ),
                )
                child_total += 1
        stats["files"] += 1
        stats["parents"] += len(parent_rows)
        stats["children"] += child_total
        logger.info(
            "ingested pdf=%s parents=%s children=%s",
            pdf.name,
            len(parent_rows),
            child_total,
        )

    await session.commit()
    return stats


async def run_default_file_ingest() -> dict[str, Any]:
    """离线入口：建表 + 默认路径 FAQ + PDF。"""
    from modules.database.models import Base
    from modules.database.session import get_async_engine, get_session_factory

    engine = get_async_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = get_session_factory()
    async with factory() as session:
        faq_path = DATA_DIR / "法律问答对.xlsx"
        if not faq_path.exists():
            raise FileNotFoundError(f"缺少 FAQ Excel：{faq_path}")

        faq_n = await load_faq_excel_to_mysql(session, faq_path, replace_all=True)
        legal_stats = await load_legal_pdfs_to_mysql(session, DATA_DIR, replace_all=True)
        return {"faq_rows": faq_n, **legal_stats}
