"""Shared export dialog helpers."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QMessageBox, QPushButton, QWidget

from stock_analysis.export.excel import export_table_excel, export_table_pdf
from stock_analysis.ui.widgets.dashboard_tile import DashboardTile
from stock_analysis.ui.widgets.data_table import DataTable


def stamped_export_name(table_name: str, ext: str, stamp: datetime | None = None) -> str:
    when = stamp or datetime.now()
    return f"{table_name}_{when.strftime('%Y-%m-%d_%H%M')}.{ext}"


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


def attach_table_export(
    parent: QWidget,
    tile: DashboardTile,
    table: DataTable,
    title_fn: Callable[[], str],
    table_name: str | Callable[[], str],
    *,
    excel: bool = True,
    pdf: bool = True,
) -> None:
    def _table_name() -> str:
        return table_name() if callable(table_name) else table_name

    def _export(fmt: str) -> None:
        headers = table.headers()
        selected = table.selected_row_indices()
        rows = table.rows_as_lists(selected or None)
        title = title_fn()
        stem = _table_name()
        stamp = datetime.now()
        if fmt == "excel":
            prompt_export_excel(
                parent, title, headers, rows, stamped_export_name(stem, "xlsx", stamp)
            )
        else:
            prompt_export_pdf(
                parent, title, headers, rows, stamped_export_name(stem, "pdf", stamp)
            )

    if excel:
        excel_btn = QPushButton("Export Excel…")
        excel_btn.clicked.connect(lambda: _export("excel"))
        tile.add_action(excel_btn)
    if pdf:
        pdf_btn = QPushButton("Export PDF…")
        pdf_btn.clicked.connect(lambda: _export("pdf"))
        tile.add_action(pdf_btn)
