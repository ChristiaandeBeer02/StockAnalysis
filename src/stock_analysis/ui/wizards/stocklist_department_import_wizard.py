"""Stocklist department import wizard."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)
from sqlalchemy import select

from stock_analysis.analytics.cache import invalidate_summaries
from stock_analysis.baseline.manager import apply_stocklist_departments
from stock_analysis.db.models import Item
from stock_analysis.db.session import get_session, has_initial_baseline
from stock_analysis.importers.stocklist_parser import StocklistParseResult, parse_stocklist_file
from stock_analysis.ui.workers.import_worker import run_in_background


def _department_empty(department: str | None) -> bool:
    return department is None or department.strip() == ""


def _preview_assignable_count(parsed: StocklistParseResult) -> int:
    with get_session() as session:
        dept_by_sku = dict(
            session.execute(select(Item.sku, Item.department)).all()
        )
    count = 0
    for row in parsed.rows:
        if row.code not in dept_by_sku:
            continue
        if _department_empty(dept_by_sku[row.code]) and row.department.strip():
            count += 1
    return count


def _format_success_message(result) -> str:
    lines = [
        f"Items updated: {result.items_updated:,}",
        f"Items already had a department: {result.items_already_set:,}",
        f"CSV SKUs not found in inventory: {result.csv_unmatched_skus:,}",
    ]
    if result.discrepancies:
        lines.append(f"\nDiscrepancies (not overwritten): {len(result.discrepancies):,}")
        for sku, existing, csv_dept in result.discrepancies[:10]:
            lines.append(f"  {sku}: {existing} → {csv_dept}")
        if len(result.discrepancies) > 10:
            lines.append(f"  (+{len(result.discrepancies) - 10} more)")
    return "\n".join(lines)


class StocklistDepartmentImportWizard(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import Departments")
        self.setMinimumWidth(520)
        self._selected_path: Path | None = None
        self._parsed: StocklistParseResult | None = None
        self._importing = False

        layout = QVBoxLayout(self)

        intro = QLabel(
            "Select an IQ Retail Stocklist export. "
            "Department codes (SUBDEPARTM) are assigned to inventory items by SKU (CODE) "
            "when the item does not already have a department."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        file_row = QHBoxLayout()
        self._file_label = QLabel("No file selected")
        self._file_label.setWordWrap(True)
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse)
        file_row.addWidget(self._file_label, stretch=1)
        file_row.addWidget(browse_btn)
        layout.addLayout(file_row)

        self._preview = QFormLayout()
        self._parsed_label = QLabel("—")
        self._junk = QLabel("—")
        self._assignable = QLabel("—")
        self._preview.addRow("Rows parsed:", self._parsed_label)
        self._preview.addRow("Junk skipped:", self._junk)
        self._preview.addRow("Items to update:", self._assignable)
        layout.addLayout(self._preview)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self.reject)
        self._import_btn = QPushButton("Import Departments")
        self._import_btn.setEnabled(False)
        self._import_btn.clicked.connect(self._import)
        buttons.addWidget(self._cancel_btn)
        buttons.addWidget(self._import_btn)
        layout.addLayout(buttons)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._importing:
            event.ignore()
            return
        super().closeEvent(event)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Stocklist CSV",
            "",
            "CSV Files (*.csv);;All Files (*)",
        )
        if not path:
            return
        self._selected_path = Path(path)
        self._file_label.setText(self._selected_path.name)
        self._load_preview()

    def _load_preview(self) -> None:
        if not self._selected_path:
            return
        try:
            self._parsed = parse_stocklist_file(self._selected_path)
        except Exception as exc:
            QMessageBox.critical(self, "Parse Error", str(exc))
            self._import_btn.setEnabled(False)
            return

        parsed = self._parsed
        assignable = _preview_assignable_count(parsed)
        self._parsed_label.setText(f"{parsed.stats.eligible_rows:,}")
        self._junk.setText(f"{parsed.stats.junk_rows:,}")
        self._assignable.setText(f"{assignable:,}")
        self._import_btn.setEnabled(parsed.stats.eligible_rows > 0)

    def _import(self) -> None:
        if not self._selected_path or not self._parsed or self._importing:
            return

        with get_session() as session:
            if not has_initial_baseline(session):
                QMessageBox.warning(
                    self,
                    "No Inventory",
                    "Import initial baseline before importing departments.",
                )
                return

        path = self._selected_path
        parsed = self._parsed
        total_rows = len(parsed.rows)
        self._importing = True
        self._import_btn.setEnabled(False)
        self._cancel_btn.setEnabled(False)

        def operation_builder(progress_callback, cancel_event):
            def operation():
                with get_session() as session:
                    return apply_stocklist_departments(
                        session,
                        path,
                        parsed=parsed,
                        progress_callback=progress_callback,
                        cancel_event=cancel_event,
                    )

            return operation

        def on_success(result) -> None:
            self._importing = False
            self._cancel_btn.setEnabled(True)
            invalidate_summaries()
            QMessageBox.information(
                self,
                "Import Complete",
                _format_success_message(result),
            )
            if result.items_without_department > 0:
                QMessageBox.information(
                    self,
                    "Departments Incomplete",
                    (
                        f"There are still {result.items_without_department:,} item(s) "
                        "in the inventory without a department."
                    ),
                )
            self.accept()

        def on_error(message: str) -> None:
            self._importing = False
            self._import_btn.setEnabled(True)
            self._cancel_btn.setEnabled(True)
            if "cancelled" in message.lower():
                QMessageBox.information(self, "Import Cancelled", "Department import was cancelled.")
                return
            QMessageBox.critical(self, "Import Failed", message)

        run_in_background(
            self,
            operation_builder=operation_builder,
            title="Importing departments…",
            maximum=total_rows,
            on_success=on_success,
            on_error=on_error,
        )


def run_stocklist_department_import_wizard(parent) -> bool:
    wizard = StocklistDepartmentImportWizard(parent)
    return wizard.exec() == QDialog.DialogCode.Accepted
