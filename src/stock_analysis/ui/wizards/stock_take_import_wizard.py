"""Stock take upload, preview, and reconcile wizard."""

from pathlib import Path

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

from stock_analysis.analytics.stock_take import StockTakeComparison
from stock_analysis.baseline.manager import apply_stock_take_reconcile, preview_stock_take
from stock_analysis.db.session import get_session
from stock_analysis.importers.stockholding_parser import parse_stockholding_file
from stock_analysis.ui.stockhold_warnings import confirm_ongoing_stockhold
from stock_analysis.ui.widgets.data_table import DataTable
from stock_analysis.ui.workers.import_worker import run_in_background


class StockTakeImportWizard(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Stock Take Upload & Reconcile")
        self.setMinimumSize(720, 520)
        self._selected_path: Path | None = None
        self._comparison: StockTakeComparison | None = None

        layout = QVBoxLayout(self)

        intro = QLabel(
            "Upload a Detailed Stockholding export (sthold2 format) from your stock take. "
            "The file is compared against the current baseline. Reconciling updates baseline "
            "quantities to match counted values for items in the file."
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
        self._parsed = QLabel("—")
        self._junk = QLabel("—")
        self._deprecated = QLabel("—")
        self._items = QLabel("—")
        self._matches = QLabel("—")
        self._variances = QLabel("—")
        self._shrinkage = QLabel("—")
        self._overage = QLabel("—")
        self._period = QLabel("—")
        self._preview.addRow("Items parsed:", self._parsed)
        self._preview.addRow("Junk skipped:", self._junk)
        self._preview.addRow("Deprecated (excluded):", self._deprecated)
        self._preview.addRow("Items compared:", self._items)
        self._preview.addRow("Exact matches:", self._matches)
        self._preview.addRow("Variances:", self._variances)
        self._preview.addRow("Shrinkage value:", self._shrinkage)
        self._preview.addRow("Overage value:", self._overage)
        self._preview.addRow("Report period:", self._period)
        layout.addLayout(self._preview)

        variance_label = QLabel("Variance preview (largest shrinkage first)")
        layout.addWidget(variance_label)
        self._variance_table = DataTable()
        self._variance_table.set_headers(
            ["SKU", "Name", "Baseline", "Counted", "Variance", "Value", "Type"]
        )
        layout.addWidget(self._variance_table)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        self._reconcile_btn = QPushButton("Reconcile Baseline")
        self._reconcile_btn.setEnabled(False)
        self._reconcile_btn.clicked.connect(self._reconcile)
        buttons.addWidget(cancel_btn)
        buttons.addWidget(self._reconcile_btn)
        layout.addLayout(buttons)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Stock Take CSV",
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
            with get_session() as session:
                self._comparison = preview_stock_take(session, self._selected_path)
        except Exception as exc:
            QMessageBox.critical(self, "Compare Error", str(exc))
            self._reconcile_btn.setEnabled(False)
            return

        comparison = self._comparison
        period = "—"
        if comparison.period_start and comparison.period_end:
            period = f"{comparison.period_start} to {comparison.period_end}"

        stats = comparison.parse_stats
        if stats:
            self._parsed.setText(f"{stats.total_rows:,}")
            self._junk.setText(
                f"{stats.junk_rows:,}  "
                f"(metadata lines skipped: {stats.metadata_skipped_rows:,})"
            )
            self._deprecated.setText(f"{stats.deprecated_rows:,}")
        else:
            self._parsed.setText("—")
            self._junk.setText("—")
            self._deprecated.setText("—")

        self._items.setText(f"{len(comparison.lines):,}")
        self._matches.setText(f"{comparison.exact_matches:,}")
        self._variances.setText(f"{len(comparison.variance_lines):,}")
        self._shrinkage.setText(f"R {comparison.shrinkage_value:,.2f}")
        self._overage.setText(f"R {comparison.overage_value:,.2f}")
        self._period.setText(period)

        preview_rows = []
        for line in comparison.variance_lines[:100]:
            preview_rows.append(
                [
                    line.sku,
                    line.name[:50],
                    f"{line.baseline_qty:g}",
                    f"{line.counted_qty:g}",
                    f"{line.variance:+g}",
                    f"R {line.variance_value:,.2f}",
                    line.line_type.replace("_", " "),
                ]
            )
        self._variance_table.set_rows(preview_rows)
        self._reconcile_btn.setEnabled(len(comparison.lines) > 0)

    def _reconcile(self) -> None:
        if not self._selected_path:
            return
        if not self._comparison:
            return

        variance_count = len(self._comparison.variance_lines)
        reply = QMessageBox.question(
            self,
            "Confirm Reconcile",
            f"Update baseline quantities for items in this stock take?\n\n"
            f"Variances: {variance_count:,}\n"
            f"Shrinkage: R {self._comparison.shrinkage_value:,.2f}\n"
            f"Overage: R {self._comparison.overage_value:,.2f}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        parsed = parse_stockholding_file(self._selected_path)
        if not confirm_ongoing_stockhold(self, parsed):
            return

        try:
            path = self._selected_path

            def operation():
                with get_session() as session:
                    return apply_stock_take_reconcile(session, path)

            def on_success(result) -> None:
                QMessageBox.information(
                    self,
                    "Reconcile Complete",
                    f"Baseline updated to v{result.baseline_version}.\n"
                    f"{result.items_updated:,} quantities changed, "
                    f"{result.new_items:,} new items added.",
                )
                self.accept()

            def on_error(message: str) -> None:
                self._reconcile_btn.setEnabled(True)
                QMessageBox.critical(self, "Reconcile Failed", message)

            self._reconcile_btn.setEnabled(False)
            run_in_background(
                self,
                operation,
                title="Reconciling stock take…",
                on_success=on_success,
                on_error=on_error,
            )
        except Exception as exc:
            QMessageBox.critical(self, "Reconcile Failed", str(exc))


def run_stock_take_wizard(parent=None) -> bool:
    wizard = StockTakeImportWizard(parent)
    return wizard.exec() == QDialog.DialogCode.Accepted
