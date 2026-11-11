import os
import re
from pathlib import Path
from typing import Dict, Any, List, Optional
from xml.sax.saxutils import escape as xml_escape

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, PageBreak, Table, TableStyle,
    HRFlowable, KeepTogether,
)
from reportlab.pdfgen import canvas
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH

from config.settings import OUTPUT_DIR
from database.db import transaction
from services.manuscript_service import ManuscriptService, STATUS_READY

# ---------------------------------------------------------------------------
# Unicode fonts (rupee, accented Latin, Devanagari when a system font exists)
# ---------------------------------------------------------------------------
_FONT_CACHE: Dict[str, str] = {}


def _register_fonts() -> tuple[str, str, str, str]:
    if _FONT_CACHE:
        return (
            _FONT_CACHE["body"], _FONT_CACHE["bold"],
            _FONT_CACHE["italic"], _FONT_CACHE["bold_italic"],
        )

    candidates = [
        (r"C:\Windows\Fonts\Nirmala.ttf", r"C:\Windows\Fonts\NirmalaB.ttf"),
        (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf"),
        (r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\segoeuib.ttf"),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ]
    body = bold = italic = bold_italic = None
    for regular, heavy in candidates:
        if os.path.exists(regular) and os.path.exists(heavy):
            try:
                pdfmetrics.registerFont(TTFont("PSSBody", regular))
                pdfmetrics.registerFont(TTFont("PSSBold", heavy))
                body, bold = "PSSBody", "PSSBold"
                break
            except Exception:
                continue

    if not body:
        body, bold = "Helvetica", "Helvetica-Bold"

    # Italic is optional; fall back to the regular family.
    for regular, ital in [
        (r"C:\Windows\Fonts\ariali.ttf", r"C:\Windows\Fonts\arialbi.ttf"),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf"),
    ]:
        if os.path.exists(regular) and os.path.exists(ital):
            try:
                pdfmetrics.registerFont(TTFont("PSSItalic", regular))
                pdfmetrics.registerFont(TTFont("PSSBoldItalic", ital))
                italic, bold_italic = "PSSItalic", "PSSBoldItalic"
                break
            except Exception:
                continue
    if not italic:
        italic, bold_italic = body, bold

    _FONT_CACHE.update(body=body, bold=bold, italic=italic, bold_italic=bold_italic)
    return body, bold, italic, bold_italic


def _inline(text: str, code_font: str = "Courier") -> str:
    """Escape XML, then apply a small, safe Markdown subset."""
    s = xml_escape(text or "")
    s = re.sub(
        r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
        r'<link href="\2" color="#2563EB">\1</link>',
        s,
    )
    s = re.sub(r"`([^`]+)`", rf'<font name="{code_font}">\1</font>', s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<i>\1</i>", s)
    s = re.sub(r"__(.+?)__", r"<i>\1</i>", s)
    return s


class NumberedCanvas(canvas.Canvas):
    """Adds a customer-branded header, footer and optional draft watermark."""
    def __init__(self, *args, header_text="", footer_text="", draft_label="",
                 body_font="Helvetica", **kwargs):
        self._header_text = header_text
        self._footer_text = footer_text
        self._draft_label = draft_label
        self._body_font = body_font
        self._saved_page_states = []
        super().__init__(*args, **kwargs)

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_furniture(num_pages)
            super().showPage()
        super().save()

    def draw_page_furniture(self, page_count: int):
        if self._pageNumber == 1 and not self._draft_label:
            return
        self.saveState()
        self.setFont(self._body_font, 8)
        self.setFillColor(colors.HexColor("#64748B"))

        if self._pageNumber > 1:
            self.drawString(2 * cm, A4[1] - 1.5 * cm, self._header_text[:110])
            self.setStrokeColor(colors.HexColor("#E2E8F0"))
            self.setLineWidth(0.5)
            self.line(2 * cm, A4[1] - 1.7 * cm, A4[0] - 2 * cm, A4[1] - 1.7 * cm)

        if self._draft_label:
            self.setFillColor(colors.HexColor("#B91C1C"))
            self.drawString(2 * cm, 1.5 * cm, self._draft_label[:110])
            self.setFillColor(colors.HexColor("#64748B"))
        else:
            self.drawString(2 * cm, 1.5 * cm, self._footer_text[:110])

        self.drawRightString(A4[0] - 2 * cm, 1.5 * cm, f"Page {self._pageNumber} of {page_count}")
        self.line(2 * cm, 1.8 * cm, A4[0] - 2 * cm, 1.8 * cm)
        self.restoreState()


class ExportService:
    @staticmethod
    def _filter_chapters(
        chapters: List[Dict[str, Any]],
        chapter_number: Optional[int] = None,
        only_with_content: bool = True,
    ) -> List[Dict[str, Any]]:
        if chapter_number is not None:
            return [c for c in chapters if c.get("chapter_number") == chapter_number]
        if only_with_content:
            with_content = [
                c for c in chapters
                if c.get("content") and len(c.get("content", "").strip()) > 30
            ]
            return with_content if with_content else chapters
        return chapters

    @staticmethod
    def _branding(charter: Dict[str, Any], brand: Optional[Dict[str, Any]]) -> Dict[str, str]:
        brand = brand or {}
        author = (
            brand.get("brand_name")
            or charter.get("author_name")
            or charter.get("author")
            or charter.get("product_title")
            or "The Author"
        )
        title = charter.get("product_title", "Publication")
        return {
            "title": title,
            "author": author,
            "header": f"{title} — {author}",
            "footer": f"© {author}. All rights reserved.",
            "cover_concept": brand.get("cover_concept", ""),
        }

    @staticmethod
    def _completeness(
        project_id: int,
        chapters: List[Dict[str, Any]],
        sources: List[Dict[str, Any]],
        require_complete: bool,
        allow_partial: bool,
        assessment: Optional[Dict[str, Any]] = None,
    ) -> tuple[bool, str, Optional[Dict[str, Any]]]:
        if not require_complete:
            return True, "", assessment
        assessment = assessment or ManuscriptService.assess(project_id, chapters, sources)
        if assessment["can_publish"]:
            return True, "", assessment
        if allow_partial:
            return True, assessment["label"], assessment
        reasons = " ".join(assessment["blocking_reasons"][:8])
        return False, f"Publication blocked: {assessment['label']}. {reasons}".strip(), assessment

    # ----------------------------- PDF ------------------------------------
    @classmethod
    def generate_pdf(
        cls,
        project_id: int,
        charter: Dict[str, Any],
        chapters: List[Dict[str, Any]],
        sources: List[Dict[str, Any]],
        chapter_number: Optional[int] = None,
        require_complete: bool = False,
        allow_partial: bool = False,
        brand: Optional[Dict[str, Any]] = None,
        assessment: Optional[Dict[str, Any]] = None,
    ) -> tuple[bool, str, str]:
        """Create an A4 PDF.

        ``require_complete`` enforces the publication gate for full-book
        exports. ``allow_partial`` permits an explicitly labelled draft.
        """
        is_single_chapter = chapter_number is not None
        gate_ok, gate_label, assessment = cls._completeness(
            project_id, chapters, sources, require_complete and not is_single_chapter,
            allow_partial, assessment,
        )
        if not gate_ok:
            return False, "", gate_label

        active_chapters = cls._filter_chapters(chapters, chapter_number=chapter_number)
        if not active_chapters:
            return False, "", "No chapters with written content to export."

        branding = cls._branding(charter, brand)
        draft_label = gate_label if (require_complete and not is_single_chapter and assessment and not assessment["can_publish"]) else ""
        suffix = "_DRAFT" if draft_label else ""
        safe_title = "".join(
            c for c in charter.get("product_title", "Digital_Book")
            if c.isalnum() or c in (" ", "_", "-")
        ).strip().replace(" ", "_") or "Digital_Book"

        if is_single_chapter:
            filename = f"Project_{project_id}_Chapter_{chapter_number}_{safe_title}{suffix}.pdf"
        else:
            filename = f"Project_{project_id}_Full_Publication_{safe_title}{suffix}.pdf"
        output_path = OUTPUT_DIR / filename

        try:
            body_font, bold_font, italic_font, bold_italic_font = _register_fonts()
            doc = SimpleDocTemplate(
                str(output_path),
                pagesize=A4,
                leftMargin=2 * cm,
                rightMargin=2 * cm,
                topMargin=2.2 * cm,
                bottomMargin=2.2 * cm,
            )
            styles = getSampleStyleSheet()

            title_style = ParagraphStyle(
                "CoverTitle", parent=styles["Normal"], fontName=bold_font,
                fontSize=24, leading=30, textColor=colors.HexColor("#0F172A"),
                alignment=TA_CENTER, spaceAfter=15,
            )
            subtitle_style = ParagraphStyle(
                "CoverSubtitle", parent=styles["Normal"], fontName=body_font,
                fontSize=13, leading=17, textColor=colors.HexColor("#2563EB"),
                alignment=TA_CENTER, spaceAfter=22,
            )
            author_style = ParagraphStyle(
                "CoverAuthor", parent=styles["Normal"], fontName=italic_font,
                fontSize=11, leading=15, textColor=colors.HexColor("#475569"),
                alignment=TA_CENTER, spaceAfter=8,
            )
            draft_style = ParagraphStyle(
                "DraftBanner", parent=styles["Normal"], fontName=bold_font,
                fontSize=11, leading=15, textColor=colors.HexColor("#B91C1C"),
                alignment=TA_CENTER, spaceBefore=12, spaceAfter=12,
            )
            h1_style = ParagraphStyle(
                "ChapH1", parent=styles["Heading1"], fontName=bold_font,
                fontSize=16, leading=20, textColor=colors.HexColor("#0F172A"),
                spaceBefore=14, spaceAfter=8, keepWithNext=True,
            )
            h2_style = ParagraphStyle(
                "ChapH2", parent=styles["Heading2"], fontName=bold_font,
                fontSize=12, leading=16, textColor=colors.HexColor("#1E293B"),
                spaceBefore=10, spaceAfter=4, keepWithNext=True,
            )
            h3_style = ParagraphStyle(
                "ChapH3", parent=styles["Heading3"], fontName=bold_font,
                fontSize=10.5, leading=14, textColor=colors.HexColor("#334155"),
                spaceBefore=8, spaceAfter=3, keepWithNext=True,
            )
            body_style = ParagraphStyle(
                "BodyTextCustom", parent=styles["BodyText"], fontName=body_font,
                fontSize=9.5, leading=14.5, textColor=colors.HexColor("#334155"),
                spaceAfter=6,
            )
            cell_style = ParagraphStyle(
                "TableCell", parent=body_style, fontSize=8.5, leading=11, spaceAfter=0,
            )
            cell_header_style = ParagraphStyle(
                "TableHeader", parent=cell_style, fontName=bold_font,
            )
            quote_style = ParagraphStyle(
                "QuoteText", parent=body_style, fontName=italic_font,
                leftIndent=12, textColor=colors.HexColor("#475569"),
            )
            disclaimer_style = ParagraphStyle(
                "DisclaimerText", parent=styles["Normal"], fontName=italic_font,
                fontSize=8.5, leading=12, textColor=colors.HexColor("#64748B"),
                spaceAfter=4,
            )

            story: List[Any] = []

            # Cover
            story.append(Spacer(1, 4 * cm))
            story.append(Paragraph(_inline(branding["title"]), title_style))
            if is_single_chapter:
                story.append(Paragraph(
                    f"Special Edition: Chapter {chapter_number} — {_inline(str(active_chapters[0].get('title', '')))}",
                    subtitle_style,
                ))
            else:
                story.append(Paragraph(_inline(str(charter.get("subtitle", "Implementation Playbook"))), subtitle_style))
            story.append(HRFlowable(width="60%", thickness=1.5, color=colors.HexColor("#2563EB"), spaceBefore=10, spaceAfter=18))
            story.append(Paragraph(f"By {_inline(branding['author'])}", author_style))
            if draft_label:
                story.append(Paragraph(f"<b>{_inline(draft_label)}</b>", draft_style))
                story.append(Paragraph(
                    "This document is a working draft, not a finished publication.",
                    disclaimer_style,
                ))
            story.append(Spacer(1, 2.2 * cm))
            story.append(Paragraph(f"<b>Target Audience:</b> {_inline(str(charter.get('target_audience', 'Professional')))}", body_style))
            story.append(Paragraph(f"<b>Category:</b> {_inline(str(charter.get('product_category', 'Business')))}", body_style))
            if assessment:
                story.append(Paragraph(
                    f"<b>Completion:</b> {_inline(assessment['label'])} — {assessment['total_words']} words",
                    body_style,
                ))
            story.append(PageBreak())

            # Copyright
            story.append(Paragraph("Copyright & Legal Notice", h1_style))
            story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#CBD5E1"), spaceAfter=12))
            story.append(Paragraph(f"© {_inline(branding['author'])}. All rights reserved.", body_style))
            story.append(Paragraph(
                "This publication is provided for educational and informational purposes. "
                "No part may be reproduced or distributed without permission.",
                body_style,
            ))
            story.append(Spacer(1, 0.6 * cm))
            story.append(Paragraph("Important Disclaimers", h2_style))
            disclaimers = charter.get("disclaimer_requirements", [])
            if disclaimers:
                for disc in disclaimers:
                    story.append(Paragraph(f"• {_inline(str(disc))}", disclaimer_style))
            else:
                story.append(Paragraph(
                    "• Results vary with market conditions, individual effort and execution.",
                    disclaimer_style,
                ))
            story.append(PageBreak())

            # TOC
            if not is_single_chapter and len(active_chapters) > 1:
                story.append(Paragraph("Table of Contents", h1_style))
                story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#CBD5E1"), spaceAfter=12))
                toc_data = [["Chapter", "Title"]]
                for ch in active_chapters:
                    toc_data.append([f"Chapter {ch['chapter_number']}", _inline(str(ch.get("title", "")))])
                toc_data.append(["References", "Cited Empirical Sources"])
                t = Table(toc_data, colWidths=[3.5 * cm, 13 * cm])
                t.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F5F9")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#0F172A")),
                    ("FONTNAME", (0, 0), (-1, 0), bold_font),
                    ("FONTNAME", (0, 1), (-1, -1), body_font),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("LINEBELOW", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
                ]))
                story.append(t)
                story.append(PageBreak())

            # Chapters
            for idx, ch in enumerate(active_chapters):
                story.append(Paragraph(
                    f"Chapter {ch['chapter_number']}: {_inline(str(ch.get('title', '')))}", h1_style
                ))
                story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#2563EB"), spaceAfter=10))
                if ch.get("purpose"):
                    story.append(Paragraph(f"<b>Core Objective:</b> {_inline(str(ch['purpose']))}", disclaimer_style))
                    story.append(Spacer(1, 0.3 * cm))

                content = (ch.get("content") or "").strip()
                if content:
                    story.extend(cls._markdown_flowables(
                        content, body_style, h1_style, h2_style, h3_style,
                        cell_style, cell_header_style, quote_style, body_font, bold_font,
                    ))
                else:
                    story.append(Paragraph("<i>Content generation in progress.</i>", disclaimer_style))
                if idx < len(active_chapters) - 1:
                    story.append(PageBreak())

            # References
            story.append(PageBreak())
            story.append(Paragraph("References & Empirical Sources", h1_style))
            story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#CBD5E1"), spaceAfter=12))
            if sources:
                for s in sources:
                    ref_text = (
                        f"<b>[Source-{s['id']}]</b> {_inline(str(s.get('title', '')))} — "
                        f"{_inline(str(s.get('publisher', 'Unknown')))} ({_inline(str(s.get('publication_date', 'Unknown')))})"
                    )
                    if s.get("url"):
                        ref_text += f"<br/>&nbsp;&nbsp;&nbsp;&nbsp;<i>{_inline(str(s['url']))}</i>"
                    story.append(Paragraph(ref_text, body_style))
                    story.append(Spacer(1, 0.2 * cm))
            else:
                story.append(Paragraph("No external research sources were recorded for this edition.", body_style))

            doc.build(
                story,
                canvasmaker=lambda *a, **k: NumberedCanvas(
                    *a,
                    header_text=branding["header"],
                    footer_text=branding["footer"],
                    draft_label=draft_label,
                    body_font=body_font,
                    **k,
                ),
            )

            file_size = os.path.getsize(output_path)
            with transaction() as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT INTO exports (
                        project_id, export_type, file_path, file_size_bytes,
                        completeness_status, completeness_json
                    ) VALUES (?, 'PDF', ?, ?, ?, ?)
                    """,
                    (
                        project_id, str(output_path), file_size,
                        assessment["status"] if assessment else "UNVERIFIED",
                        cls._json(assessment) if assessment else None,
                    ),
                )
                cur.execute(
                    "INSERT INTO activity_logs (project_id, action, details) VALUES (?, ?, ?)",
                    (
                        project_id, "EXPORT_GENERATED",
                        f"Generated PDF: {filename} ({file_size / 1024:.1f} KB)"
                        + (f" — {draft_label}" if draft_label else ""),
                    ),
                )
            return True, str(output_path), (
                f"PDF successfully created ({file_size / 1024:.1f} KB)"
                + (f" — {draft_label}" if draft_label else "")
            )
        except Exception as e:
            return False, "", f"PDF export failed: {str(e)}"

    @staticmethod
    def _json(value: Any) -> str:
        import json
        return json.dumps(value, default=str)

    @classmethod
    def _markdown_flowables(
        cls, content: str, body_style, h1_style, h2_style, h3_style,
        cell_style, cell_header_style, quote_style, body_font, bold_font,
    ) -> List[Any]:
        flowables: List[Any] = []
        lines = content.split("\n")
        i = 0
        in_code = False
        code_buffer: List[str] = []

        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            if stripped.startswith("```"):
                if in_code:
                    code_text = "<br/>".join(xml_escape(x) for x in code_buffer) or " "
                    flowables.append(Paragraph(
                        f'<font name="Courier" size="8">{code_text}</font>',
                        body_style,
                    ))
                    code_buffer = []
                    in_code = False
                else:
                    in_code = True
                i += 1
                continue
            if in_code:
                code_buffer.append(line)
                i += 1
                continue

            # Tables: | a | b | followed by |---|---|
            if stripped.startswith("|") and i + 1 < len(lines) and re.match(
                r"^\s*\|?[\s:\-|]+\|[\s:\-|]*$", lines[i + 1]
            ):
                header_cells = [c.strip() for c in stripped.strip("|").split("|")]
                rows = [header_cells]
                i += 2
                while i < len(lines) and lines[i].strip().startswith("|"):
                    row = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                    rows.append(row)
                    i += 1
                ncols = max(len(r) for r in rows)
                table_data = []
                for ridx, row in enumerate(rows):
                    padded = row + [""] * (ncols - len(row))
                    style = cell_header_style if ridx == 0 else cell_style
                    table_data.append([Paragraph(_inline(c), style) for c in padded])
                width = 17 * cm
                col_width = width / max(ncols, 1)
                tbl = Table(table_data, colWidths=[col_width] * ncols, repeatRows=1)
                tbl.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F5F9")),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]))
                flowables.append(KeepTogether([tbl, Spacer(1, 0.2 * cm)]))
                continue

            if not stripped:
                flowables.append(Spacer(1, 0.12 * cm))
                i += 1
                continue

            if stripped.startswith("### "):
                flowables.append(Paragraph(_inline(stripped[4:]), h3_style))
            elif stripped.startswith("## "):
                flowables.append(Paragraph(_inline(stripped[3:]), h2_style))
            elif stripped.startswith("# "):
                flowables.append(Paragraph(_inline(stripped[2:]), h1_style))
            elif stripped in ("---", "***", "___"):
                flowables.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#CBD5E1"), spaceBefore=4, spaceAfter=6))
            elif re.match(r"^[-*+]\s*\[[ xX]\]\s+", stripped):
                checked = re.match(r"^[-*+]\s*\[[xX]\]", stripped) is not None
                text_part = re.sub(r"^[-*+]\s*\[[ xX]\]\s+", "", stripped)
                marker = "☑" if checked else "☐"
                flowables.append(Paragraph(f"{marker} {_inline(text_part)}", body_style))
            elif re.match(r"^[-*+]\s+", stripped):
                text_part = re.sub(r"^[-*+]\s+", "", stripped)
                flowables.append(Paragraph(f"• {_inline(text_part)}", body_style))
            elif re.match(r"^\d+[.)]\s+", stripped):
                flowables.append(Paragraph(_inline(stripped), body_style))
            elif stripped.startswith(">"):
                flowables.append(Paragraph(_inline(stripped.lstrip("> ").strip()), quote_style))
            else:
                flowables.append(Paragraph(_inline(stripped), body_style))
            i += 1

        if in_code and code_buffer:
            flowables.append(Paragraph(
                f'<font name="Courier" size="8">{"<br/>".join(xml_escape(x) for x in code_buffer)}</font>',
                body_style,
            ))
        return flowables

    # ----------------------------- DOCX -----------------------------------
    @classmethod
    def generate_docx(
        cls,
        project_id: int,
        charter: Dict[str, Any],
        chapters: List[Dict[str, Any]],
        sources: List[Dict[str, Any]],
        chapter_number: Optional[int] = None,
        require_complete: bool = False,
        allow_partial: bool = False,
        brand: Optional[Dict[str, Any]] = None,
        assessment: Optional[Dict[str, Any]] = None,
    ) -> tuple[bool, str, str]:
        is_single_chapter = chapter_number is not None
        gate_ok, gate_label, assessment = cls._completeness(
            project_id, chapters, sources, require_complete and not is_single_chapter,
            allow_partial, assessment,
        )
        if not gate_ok:
            return False, "", gate_label

        active_chapters = cls._filter_chapters(chapters, chapter_number=chapter_number)
        if not active_chapters:
            return False, "", "No chapters with written content to export."

        branding = cls._branding(charter, brand)
        draft_label = gate_label if (require_complete and not is_single_chapter and assessment and not assessment["can_publish"]) else ""
        suffix = "_DRAFT" if draft_label else ""
        safe_title = "".join(
            c for c in charter.get("product_title", "Digital_Book")
            if c.isalnum() or c in (" ", "_", "-")
        ).strip().replace(" ", "_") or "Digital_Book"

        if is_single_chapter:
            filename = f"Project_{project_id}_Chapter_{chapter_number}_{safe_title}{suffix}.docx"
        else:
            filename = f"Project_{project_id}_Full_Publication_{safe_title}{suffix}.docx"
        output_path = OUTPUT_DIR / filename

        try:
            doc = Document()
            for section in doc.sections:
                section.top_margin = Inches(1)
                section.bottom_margin = Inches(1)
                section.left_margin = Inches(1)
                section.right_margin = Inches(1)
                header_p = section.header.paragraphs[0]
                header_p.text = branding["header"]
                header_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                footer_p = section.footer.paragraphs[0]
                footer_p.text = draft_label if draft_label else branding["footer"]
                footer_p.alignment = WD_ALIGN_PARAGRAPH.CENTER

            title_p = doc.add_paragraph()
            title_run = title_p.add_run(branding["title"])
            title_run.font.name = "Arial"
            title_run.font.size = Pt(24)
            title_run.font.bold = True
            title_run.font.color.rgb = RGBColor(15, 23, 42)
            title_p.alignment = WD_ALIGN_PARAGRAPH.CENTER

            subtitle_p = doc.add_paragraph()
            sub_text = (
                f"Special Edition: Chapter {chapter_number} — {active_chapters[0].get('title')}"
                if is_single_chapter else charter.get("subtitle", "A Practical Guide")
            )
            sub_run = subtitle_p.add_run(sub_text)
            sub_run.font.name = "Arial"
            sub_run.font.size = Pt(14)
            sub_run.font.color.rgb = RGBColor(37, 99, 235)
            subtitle_p.alignment = WD_ALIGN_PARAGRAPH.CENTER

            author_p = doc.add_paragraph()
            author_run = author_p.add_run(f"By {branding['author']}")
            author_run.font.italic = True
            author_run.font.size = Pt(11)
            author_p.alignment = WD_ALIGN_PARAGRAPH.CENTER

            if draft_label:
                draft_p = doc.add_paragraph()
                draft_run = draft_p.add_run(draft_label)
                draft_run.font.bold = True
                draft_run.font.color.rgb = RGBColor(185, 28, 28)
                draft_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                note_p = doc.add_paragraph("This document is a working draft, not a finished publication.")
                note_p.alignment = WD_ALIGN_PARAGRAPH.CENTER

            doc.add_page_break()

            doc.add_heading("Copyright & Disclaimer", level=1)
            doc.add_paragraph(f"© {branding['author']}. All rights reserved.")
            for disc in charter.get("disclaimer_requirements", []):
                p = doc.add_paragraph()
                r = p.add_run(f"• {disc}")
                r.font.italic = True
                r.font.size = Pt(9.5)
            doc.add_page_break()

            for idx, ch in enumerate(active_chapters):
                doc.add_heading(f"Chapter {ch['chapter_number']}: {ch.get('title', '')}", level=1)
                if ch.get("purpose"):
                    p_obj = doc.add_paragraph()
                    r_obj = p_obj.add_run(f"Objective: {ch['purpose']}")
                    r_obj.font.italic = True
                    r_obj.font.size = Pt(10)
                content = (ch.get("content") or "").strip()
                if content:
                    cls._docx_markdown(doc, content)
                else:
                    doc.add_paragraph("Content pending generation.")
                if idx < len(active_chapters) - 1:
                    doc.add_page_break()

            doc.add_page_break()
            doc.add_heading("References & Cited Sources", level=1)
            if sources:
                for s in sources:
                    doc.add_paragraph(
                        f"[Source-{s['id']}] {s.get('title', '')} — "
                        f"{s.get('publisher', 'Unknown')} ({s.get('publication_date', 'Unknown')})"
                    )
                    if s.get("url"):
                        doc.add_paragraph(str(s["url"]))
            else:
                doc.add_paragraph("No external sources recorded.")

            doc.save(str(output_path))
            file_size = os.path.getsize(output_path)

            with transaction() as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT INTO exports (
                        project_id, export_type, file_path, file_size_bytes,
                        completeness_status, completeness_json
                    ) VALUES (?, 'DOCX', ?, ?, ?, ?)
                    """,
                    (
                        project_id, str(output_path), file_size,
                        assessment["status"] if assessment else "UNVERIFIED",
                        cls._json(assessment) if assessment else None,
                    ),
                )
                cur.execute(
                    "INSERT INTO activity_logs (project_id, action, details) VALUES (?, ?, ?)",
                    (project_id, "EXPORT_GENERATED", f"Generated DOCX: {filename}"),
                )
            return True, str(output_path), (
                f"DOCX created ({file_size / 1024:.1f} KB)"
                + (f" — {draft_label}" if draft_label else "")
            )
        except Exception as e:
            return False, "", f"DOCX export failed: {str(e)}"

    @staticmethod
    def _docx_markdown(doc: Document, content: str) -> None:
        lines = content.split("\n")
        i = 0
        while i < len(lines):
            stripped = lines[i].strip()
            if not stripped:
                i += 1
                continue
            if stripped.startswith("### "):
                doc.add_heading(stripped[4:], level=3)
            elif stripped.startswith("## "):
                doc.add_heading(stripped[3:], level=2)
            elif stripped.startswith("# "):
                doc.add_heading(stripped[2:], level=1)
            elif stripped in ("---", "***", "___"):
                doc.add_paragraph("—" * 20)
            elif stripped.startswith("|") and i + 1 < len(lines) and re.match(
                r"^\s*\|?[\s:\-|]+\|[\s:\-|]*$", lines[i + 1]
            ):
                headers = [c.strip() for c in stripped.strip("|").split("|")]
                rows = [headers]
                i += 2
                while i < len(lines) and lines[i].strip().startswith("|"):
                    rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                    i += 1
                table = doc.add_table(rows=len(rows), cols=max(len(r) for r in rows))
                table.style = "Table Grid"
                for ridx, row in enumerate(rows):
                    for cidx, value in enumerate(row):
                        cell = table.cell(ridx, cidx)
                        cell.text = value
                        if ridx == 0:
                            for p in cell.paragraphs:
                                for run in p.runs:
                                    run.font.bold = True
                continue
            elif re.match(r"^[-*+]\s*\[[ xX]\]\s+", stripped):
                checked = re.match(r"^[-*+]\s*\[[xX]\]", stripped) is not None
                text_part = re.sub(r"^[-*+]\s*\[[ xX]\]\s+", "", stripped)
                doc.add_paragraph(("☑ " if checked else "☐ ") + text_part, style="List Bullet")
            elif re.match(r"^[-*+]\s+", stripped):
                doc.add_paragraph(re.sub(r"^[-*+]\s+", "", stripped), style="List Bullet")
            elif re.match(r"^\d+[.)]\s+", stripped):
                doc.add_paragraph(stripped, style="List Number")
            elif stripped.startswith(">"):
                p = doc.add_paragraph(stripped.lstrip("> ").strip())
                for run in p.runs:
                    run.font.italic = True
            else:
                p = doc.add_paragraph()
                ExportService._docx_inline_runs(p, stripped)
            i += 1

    @staticmethod
    def _docx_inline_runs(paragraph, text: str) -> None:
        parts = re.split(r"(\*\*.+?\*\*|`.+?`)", text)
        for part in parts:
            if not part:
                continue
            if part.startswith("**") and part.endswith("**") and len(part) > 4:
                run = paragraph.add_run(part[2:-2])
                run.font.bold = True
            elif part.startswith("`") and part.endswith("`") and len(part) > 2:
                run = paragraph.add_run(part[1:-1])
                run.font.name = "Consolas"
            else:
                paragraph.add_run(part)
