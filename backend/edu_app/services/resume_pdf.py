"""简历导出 PDF。

用 reportlab 直接排版，不依赖浏览器：用户点「导出 PDF」要立刻拿到文件，
而不是被要求「在弹出的打印对话框里另存为」。

中文字体是唯一的坑——reportlab 内置字体没有汉字，不注册 TTF 会得到一片空白。
所以这里按常见路径找系统字体，找不到就明确报错，而不是悄悄输出乱码。
"""
from __future__ import annotations

import io
import os
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer

INK = colors.HexColor("#1b2430")
MUTED = colors.HexColor("#5a6b80")
ACCENT = colors.HexColor("#2b4bd0")
LINE = colors.HexColor("#c9d4e4")

# (路径, .ttc 子字体序号)。simhei/simsun 是单字体文件，优先用，解析最省事。
_FONT_CANDIDATES = [
    ("C:/Windows/Fonts/simhei.ttf", 0),
    ("C:/Windows/Fonts/msyh.ttc", 0),
    ("C:/Windows/Fonts/msyhl.ttc", 0),
    ("C:/Windows/Fonts/simsun.ttc", 0),
    ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 0),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 0),
    ("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc", 0),
    ("/System/Library/Fonts/PingFang.ttc", 0),
]

_registered: str | None = None


def _cjk_font() -> str:
    """返回已注册的中文字体名；没有可用字体时抛 RuntimeError。"""
    global _registered
    if _registered:
        return _registered

    for path, index in _FONT_CANDIDATES:
        if not os.path.exists(path):
            continue
        try:
            pdfmetrics.registerFont(TTFont("ResumeCJK", path, subfontIndex=index))
            _registered = "ResumeCJK"
            return _registered
        except Exception:
            continue

    # 最后再扫一遍字体目录，避免系统把字体装到了非常规位置
    for root in ("C:/Windows/Fonts", "/usr/share/fonts", "/usr/local/share/fonts"):
        if not os.path.isdir(root):
            continue
        for dirpath, _, files in os.walk(root):
            for name in files:
                if not name.lower().endswith((".ttf", ".ttc")):
                    continue
                if not any(k in name.lower() for k in ("hei", "song", "yahei", "cjk", "noto")):
                    continue
                try:
                    pdfmetrics.registerFont(
                        TTFont("ResumeCJK", os.path.join(dirpath, name), subfontIndex=0)
                    )
                    _registered = "ResumeCJK"
                    return _registered
                except Exception:
                    continue

    raise RuntimeError("no_cjk_font")


def _styles() -> dict[str, ParagraphStyle]:
    font = _cjk_font()
    base = ParagraphStyle(
        "base", fontName=font, fontSize=10, leading=15.5, textColor=INK
    )
    return {
        "name": ParagraphStyle(
            "name", parent=base, fontSize=21, leading=27, textColor=INK
        ),
        "contact": ParagraphStyle(
            "contact", parent=base, fontSize=9, leading=14, textColor=MUTED
        ),
        "section": ParagraphStyle(
            "section",
            parent=base,
            fontSize=11.5,
            leading=17,
            textColor=ACCENT,
            spaceBefore=10,
            spaceAfter=2,
        ),
        "role": ParagraphStyle("role", parent=base, fontSize=10.5, leading=15),
        "meta": ParagraphStyle(
            "meta", parent=base, fontSize=9, leading=14, textColor=MUTED
        ),
        "body": ParagraphStyle(
            "body", parent=base, fontSize=9.6, leading=15, alignment=TA_JUSTIFY
        ),
        "bullet": ParagraphStyle(
            "bullet", parent=base, fontSize=9.6, leading=15, leftIndent=11, bulletIndent=2
        ),
    }


def _p(text: Any, style: ParagraphStyle) -> Paragraph | None:
    value = str(text or "").strip()
    if not value:
        return None
    return Paragraph(escape(value), style)


def _date_range(item: dict) -> str:
    start = str(item.get("start") or "").strip()
    end = str(item.get("end") or "").strip()
    if start and end:
        return f"{start} – {end}"
    return start or end or ""


def _section(title: str, styles: dict) -> list:
    return [
        Paragraph(escape(title), styles["section"]),
        HRFlowable(width="100%", thickness=0.7, color=LINE, spaceBefore=1, spaceAfter=5),
    ]


def build_resume_pdf(data: dict) -> bytes:
    """把结构化简历排成 A4 PDF，返回字节流。"""
    styles = _styles()
    basics = data.get("basics") or {}
    story: list = []

    name = str(basics.get("name") or "").strip() or "未命名"
    story.append(Paragraph(escape(name), styles["name"]))

    contact_bits = [
        basics.get("phone"),
        basics.get("email"),
        basics.get("city"),
        basics.get("github"),
    ]
    contact = "  |  ".join(str(b).strip() for b in contact_bits if str(b or "").strip())
    if contact:
        story.append(Paragraph(escape(contact), styles["contact"]))
    story.append(Spacer(1, 6))

    summary = _p(basics.get("summary"), styles["body"])
    if summary:
        story.append(summary)

    educations = [e for e in (data.get("education") or []) if str(e.get("school") or "").strip()]
    if educations:
        story.extend(_section("教育经历", styles))
        for edu in educations:
            school = str(edu.get("school") or "").strip()
            major = str(edu.get("major") or "").strip()
            degree = str(edu.get("degree") or "").strip()
            head = " · ".join(x for x in (school, major, degree) if x)
            gpa = str(edu.get("gpa") or "").strip()
            meta = "  ".join(x for x in (_date_range(edu), f"GPA {gpa}" if gpa else "") if x)
            story.append(Paragraph(escape(head), styles["role"]))
            if meta:
                story.append(Paragraph(escape(meta), styles["meta"]))
            for point in edu.get("highlights") or []:
                para = _p(point, styles["bullet"])
                if para:
                    para.bulletText = "•"
                    story.append(para)
            story.append(Spacer(1, 4))

    for title, key, head_parts in (
        ("实习/工作经历", "experience", ("company", "role")),
        ("项目经历", "projects", ("name", "role")),
    ):
        block = [x for x in (data.get(key) or []) if str(x.get(head_parts[0]) or "").strip()]
        if not block:
            continue
        story.extend(_section(title, styles))
        for item in block:
            head = " · ".join(
                str(item.get(part) or "").strip() for part in head_parts
            ).strip(" ·")
            meta = _date_range(item)
            story.append(Paragraph(escape(head), styles["role"]))
            if meta:
                story.append(Paragraph(escape(meta), styles["meta"]))
            for point in item.get("bullets") or []:
                para = _p(point, styles["bullet"])
                if para:
                    para.bulletText = "•"
                    story.append(para)
            story.append(Spacer(1, 4))

    skills = [str(s).strip() for s in (data.get("skills") or []) if str(s or "").strip()]
    if skills:
        story.extend(_section("技能", styles))
        story.append(Paragraph(escape("、".join(skills)), styles["body"]))

    awards = [str(a).strip() for a in (data.get("awards") or []) if str(a or "").strip()]
    if awards:
        story.extend(_section("奖项与证书", styles))
        for item in awards:
            para = _p(item, styles["bullet"])
            if para:
                para.bulletText = "•"
                story.append(para)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"{name} 的简历",
        author=name,
    )
    doc.build(story)
    return buffer.getvalue()
