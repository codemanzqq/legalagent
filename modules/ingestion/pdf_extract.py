"""PDF 文本抽取：基于 PyPDF 按页读取纯文本（复杂排版可能需 OCR 方案替换）。"""

from __future__ import annotations

from pathlib import Path  # 路径类型注解

from pypdf import PdfReader  # 轻量 PDF 解析库


def extract_pages_pdf(path: Path) -> list[str]:
    """返回每页一个字符串；解析失败页填空字符串以保持页码对齐。"""
    reader = PdfReader(str(path))  # 打开文件
    pages: list[str] = []
    for p in reader.pages:  # 顺序遍历页对象
        try:
            pages.append(p.extract_text() or "")  # 抽取文本；None 转空串
        except Exception:  # noqa: BLE001  # 单页损坏不影响全书
            pages.append("")
    return pages
