"""Turn a verified draft into a Word file the person can edit, print and send."""
from __future__ import annotations

import io
import re

from docx import Document
from docx.enum.text import WD_COLOR_INDEX
from docx.shared import Pt

from .verifier import render_citations, split_paragraphs

PLACEHOLDER = re.compile(r"(\[[A-Z][^\]]*\])")


def _add_runs(paragraph, text: str) -> None:
    """Highlight [PLACEHOLDERS] in yellow so nothing gets sent unfilled by accident."""
    for part in PLACEHOLDER.split(text):
        if not part:
            continue
        run = paragraph.add_run(part)
        if PLACEHOLDER.fullmatch(part):
            run.font.highlight_color = WD_COLOR_INDEX.YELLOW


def to_docx(doc: dict) -> bytes:
    word = Document()
    style = word.styles["Normal"]
    style.font.name = "Times New Roman"
    style.font.size = Pt(12)
    header = word.sections[0].header.paragraphs[0]
    header.text = "DRAFT. Fill in the highlighted parts and check every fact. Delete this line before sending."
    header.runs[0].font.size = Pt(8)
    word.add_heading(doc["title"], level=1)
    for para in split_paragraphs(render_citations(doc["body"])):
        para = para.strip()
        if para.startswith("# "):
            word.add_heading(para[2:].strip(), level=2)
            continue
        for i, line in enumerate(para.split("\n")):
            if i == 0:
                current = word.add_paragraph()
            else:
                current.add_run().add_break()
            _add_runs(current, line)
    buffer = io.BytesIO()
    word.save(buffer)
    return buffer.getvalue()
