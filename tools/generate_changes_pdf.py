"""
Genera un PDF de cambios desde un archivo Markdown editable.

Uso:
    python tools/generate_changes_pdf.py

Entrada:
    docs/cambios_2026-03-17.md

Salida:
    docs/cambios_2026-03-17.pdf
"""

from __future__ import annotations

from pathlib import Path
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer


ROOT = Path(__file__).resolve().parents[1]
INPUT_MD = ROOT / "docs" / "cambios_2026-03-17.md"
OUTPUT_PDF = ROOT / "docs" / "cambios_2026-03-17.pdf"


def build_styles():
    return {
        "title": ParagraphStyle(
            "title",
            fontName="Helvetica-Bold",
            fontSize=16,
            textColor=colors.HexColor("#1E3A8A"),
            spaceAfter=10,
        ),
        "h2": ParagraphStyle(
            "h2",
            fontName="Helvetica-Bold",
            fontSize=12,
            textColor=colors.HexColor("#1D4ED8"),
            spaceBefore=8,
            spaceAfter=4,
        ),
        "h3": ParagraphStyle(
            "h3",
            fontName="Helvetica-Bold",
            fontSize=10.5,
            textColor=colors.HexColor("#334155"),
            spaceBefore=6,
            spaceAfter=3,
        ),
        "body": ParagraphStyle(
            "body",
            fontName="Helvetica",
            fontSize=9.5,
            leading=13,
            spaceAfter=3,
        ),
        "bullet": ParagraphStyle(
            "bullet",
            fontName="Helvetica",
            fontSize=9.5,
            leading=13,
            leftIndent=12,
            bulletIndent=2,
            spaceAfter=2,
        ),
        "mono": ParagraphStyle(
            "mono",
            fontName="Courier",
            fontSize=8.8,
            textColor=colors.HexColor("#0F172A"),
            leftIndent=10,
            spaceAfter=2,
        ),
    }


def esc(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def md_to_story(md_text: str):
    styles = build_styles()
    story = []

    for raw in md_text.splitlines():
        line = raw.rstrip()

        if not line.strip():
            story.append(Spacer(1, 4))
            continue

        if line.startswith("# "):
            story.append(Paragraph(esc(line[2:].strip()), styles["title"]))
            continue

        if line.startswith("## "):
            story.append(Paragraph(esc(line[3:].strip()), styles["h2"]))
            continue

        if line.startswith("### "):
            story.append(Paragraph(esc(line[4:].strip()), styles["h3"]))
            continue

        if line.startswith("- "):
            bullet_text = esc(line[2:].strip())
            story.append(Paragraph(f"• {bullet_text}", styles["bullet"]))
            continue

        # Soporta listas numeradas simples
        if len(line) > 2 and line[0].isdigit() and line[1] == "." and line[2] == " ":
            story.append(Paragraph(esc(line), styles["body"]))
            continue

        # Si parece bloque de comando o ruta, usa fuente monoespaciada
        if line.strip().startswith("`") and line.strip().endswith("`"):
            story.append(Paragraph(esc(line.strip("`")), styles["mono"]))
            continue

        story.append(Paragraph(esc(line), styles["body"]))

    return story


def build_pdf(input_md: Path = INPUT_MD, output_pdf: Path = OUTPUT_PDF) -> Path:
    if not input_md.exists():
        raise FileNotFoundError(f"No existe el archivo fuente: {input_md}")

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    text = input_md.read_text(encoding="utf-8")
    story = md_to_story(text)

    doc = SimpleDocTemplate(
        str(output_pdf),
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=1.8 * cm,
        bottomMargin=1.8 * cm,
        title="Cambios del día",
        author="Production Calculator",
    )
    doc.build(story)
    return output_pdf


if __name__ == "__main__":
    out = build_pdf()
    print(f"PDF generado: {out}")
