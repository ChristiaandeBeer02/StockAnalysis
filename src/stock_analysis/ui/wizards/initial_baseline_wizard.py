"""Initial baseline import wizard."""

from __future__ import annotations

import threading
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

from stock_analysis.analytics.cache import invalidate_summaries
from stock_analysis.baseline.manager import apply_initial_baseline
from stock_analysis.db.session import get_session, has_initial_baseline
from stock_analysis.importers.stockholding_parser import (
    StockholdingParseResult,
    parse_stockholding_file,
)
from stock_analysis.ui.workers.import_worker import run_in_background


class InitialBaselineWizard(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._is_reimport = False
        with get_session() as session:
            self._is_reimport = has_initial_baseline(session)

        self.setWindowTitle(
            "Re-import Initial Baseline" if self._is_reimport else "Import Initial Baseline"
        )
        self.setMinimumWidth(520)
        self._selected_path: Path | None = None
        self._parsed: StockholdingParseResult | None = None
        self._summary: dict | None = None
        self._importing = False

        layout = QVBoxLayout(self)

        intro = QLabel(
            "Select a Detailed Stockholding export (sthold2 format). "
            "This establishes your opening stock levels and item codes."
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
        self._deprecated = QLabel("—")
        self._stock_value = QLabel("—")
        self._period = QLabel("—")
        self._preview.addRow("Items parsed:", self._parsed_label)
        self._preview.addRow("Junk skipped:", self._junk)
        self._preview.addRow("Deprecated:", self._deprecated)
        self._preview.addRow("Total stock value:", self._stock_value)
        self._preview.addRow("Report period:", self._period)
        layout.addLayout(self._preview)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self.reject)
        self._import_btn = QPushButton("Import Baseline")
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
            "Select Stockholding CSV",
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
            self._parsed = parse_stockholding_file(self._selected_path)
        except Exception as exc:
            QMessageBox.critical(self, "Parse Error", str(exc))
            self._import_btn.setEnabled(False)
            return

        parsed = self._parsed
        total_value = sum(r.stock_value for r in parsed.rows)
        period = "—"
        if parsed.period_start and parsed.period_end:
            period = f"{parsed.period_start} to {parsed.period_end}"

        self._parsed_label.setText(f"{parsed.stats.total_rows:,}")
        self._junk.setText(
            f"{parsed.stats.junk_rows:,}  "
            f"(metadata lines skipped: {parsed.stats.metadata_skipped_rows:,})"
        )
        self._deprecated.setText(str(parsed.stats.deprecated_rows))
        self._stock_value.setText(f"R {total_value:,.2f}")
        self._period.setText(period)
        self._import_btn.setEnabled(parsed.stats.total_rows > 0)

    def _eligible_row_count(self) -> int:
        if not self._parsed:
            return 0
        from stock_analysis.importers.item_filters import should_skip_item

        return sum(
            1
            for row in self._parsed.rows
            if not should_skip_item(row.code, row.description)
        )

    def _import(self) -> None:
        if not self._selected_path or not self._parsed or self._importing:
            return

        if self._is_reimport:
            reply = QMessageBox.warning(
                self,
                "Confirm Re-import",
                "This will delete all existing inventory, turn reports, stock takes, "
                "and import history, then load the new baseline.\n\n"
                "You will need to re-run enrichment (Step 2).\n\nContinue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        path = self._selected_path
        parsed = self._parsed
        total_rows = self._eligible_row_count()
        self._importing = True
        self._import_btn.setEnabled(False)
        self._cancel_btn.setEnabled(False)

        def operation_builder(progress_callback, cancel_event):
            def operation():
                with get_session() as session:
                    return apply_initial_baseline(
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
            self._summary = {
                "items": result.items_imported,
                "version": result.baseline_version,
            }
            QMessageBox.information(
                self,
                "Import Complete",
                f"Imported {result.items_imported:,} items (baseline v{result.baseline_version}).",
            )
            self.accept()

        def on_error(message: str) -> None:
            self._importing = False
            self._import_btn.setEnabled(True)
            self._cancel_btn.setEnabled(True)
            if "cancelled" in message.lower():
                QMessageBox.information(self, "Import Cancelled", "Baseline import was cancelled.")
                return
            QMessageBox.critical(self, "Import Failed", message)

        run_in_background(
            self,
            operation_builder=operation_builder,
            title="Importing baseline…",
            maximum=total_rows,
            on_success=on_success,
            on_error=on_error,
        )
