"""Excel and PDF export helpers."""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def export_table_excel(
    path: Path,
    headers: list[str],
    rows: list[list],
    sheet_name: str = "Report",
    title: str | None = None,
) -> None:
    frame = pd.DataFrame(rows, columns=headers)
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        if title:
            meta = pd.DataFrame([[title]], columns=["Report"])
            meta.to_excel(writer, sheet_name=sheet_name, index=False, header=False, startrow=0)
            frame.to_excel(writer, sheet_name=sheet_name, index=False, startrow=2)
        else:
            frame.to_excel(writer, sheet_name=sheet_name, index=False)


def export_table_pdf(
    path: Path,
    headers: list[str],
    rows: list[list],
    title: str = "Report",
) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(path), pagesize=landscape(A4))
    styles = getSampleStyleSheet()
    elements = [Paragraph(title, styles["Heading2"]), Spacer(1, 12)]

    table_data = [headers] + [[str(cell) for cell in row] for row in rows[:500]]
    table = Table(table_data, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#243b64")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
            ]
        )
    )
    elements.append(table)
    doc.build(elements)
