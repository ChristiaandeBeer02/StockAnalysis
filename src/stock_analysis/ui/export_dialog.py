"""Shared export dialog helpers."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget

from stock_analysis.export.excel import export_table_excel, export_table_pdf


def prompt_export_excel(
    parent: QWidget,
    title: str,
    headers: list[str],
    rows: list[list],
    default_name: str = "report.xlsx",
) -> bool:
    path, _ = QFileDialog.getSaveFileName(
        parent,
        "Export to Excel",
        default_name,
        "Excel Files (*.xlsx)",
    )
    if not path:
        return False
    try:
        export_table_excel(Path(path), headers, rows, title=title)
    except Exception as exc:
        QMessageBox.critical(parent, "Export Failed", str(exc))
        return False
    QMessageBox.information(parent, "Export Complete", f"Saved to {path}")
    return True


def prompt_export_pdf(
    parent: QWidget,
    title: str,
    headers: list[str],
    rows: list[list],
    default_name: str = "report.pdf",
) -> bool:
    path, _ = QFileDialog.getSaveFileName(
        parent,
        "Export to PDF",
        default_name,
        "PDF Files (*.pdf)",
    )
    if not path:
        return False
    try:
        export_table_pdf(Path(path), headers, rows, title=title)
    except Exception as exc:
        QMessageBox.critical(parent, "Export Failed", str(exc))
        return False
    QMessageBox.information(parent, "Export Complete", f"Saved to {path}")
    return True
