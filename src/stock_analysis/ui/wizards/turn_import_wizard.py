"""Shared Turn + Turnunder import wizard."""

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

from stock_analysis.importers.turnunder_parser import merge_turn_reports
from stock_analysis.ui.workers.import_worker import run_in_background


class TurnImportWizard(QDialog):
    def __init__(
        self,
        title: str,
        intro: str,
        confirm_label: str,
        on_import,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(560)
        self._on_import = on_import
        self._turn_path: Path | None = None
        self._under_path: Path | None = None

        layout = QVBoxLayout(self)
        intro_label = QLabel(intro)
        intro_label.setWordWrap(True)
        layout.addWidget(intro_label)

        self._turn_label = QLabel("Turn file: not selected")
        turn_btn = QPushButton("Select Turn CSV…")
        turn_btn.clicked.connect(self._browse_turn)
        layout.addWidget(self._turn_label)
        layout.addWidget(turn_btn)

        self._under_label = QLabel("Turnunder file: not selected")
        under_btn = QPushButton("Select Turnunder CSV…")
        under_btn.clicked.connect(self._browse_under)
        layout.addWidget(self._under_label)
        layout.addWidget(under_btn)

        self._preview = QFormLayout()
        self._items = QLabel("—")
        self._deprecated = QLabel("—")
        self._period = QLabel("—")
        self._preview.addRow("Items:", self._items)
        self._preview.addRow("Deprecated:", self._deprecated)
        self._preview.addRow("Report period:", self._period)
        layout.addLayout(self._preview)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self._confirm = QPushButton(confirm_label)
        self._confirm.setEnabled(False)
        self._confirm.clicked.connect(self._do_import)
        buttons.addWidget(cancel)
        buttons.addWidget(self._confirm)
        layout.addLayout(buttons)

    def _browse_turn(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select Turn CSV", "", "CSV Files (*.csv)")
        if path:
            self._turn_path = Path(path)
            self._turn_label.setText(f"Turn file: {self._turn_path.name}")
            self._update_preview()

    def _browse_under(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Turnunder CSV", "", "CSV Files (*.csv)"
        )
        if path:
            self._under_path = Path(path)
            self._under_label.setText(f"Turnunder file: {self._under_path.name}")
            self._update_preview()

    def _update_preview(self) -> None:
        if not self._turn_path or not self._under_path:
            self._confirm.setEnabled(False)
            return
        try:
            merged, p_start, p_end = merge_turn_reports(self._turn_path, self._under_path)
        except Exception as exc:
            QMessageBox.critical(self, "Parse Error", str(exc))
            self._confirm.setEnabled(False)
            return
        deprecated = sum(1 for r in merged if r.is_deprecated)
        self._items.setText(f"{len(merged):,}")
        self._deprecated.setText(str(deprecated))
        period = "—"
        if p_start and p_end:
            period = f"{p_start} to {p_end}"
        self._period.setText(period)
        self._confirm.setEnabled(len(merged) > 0)

    def _do_import(self) -> None:
        if not self._turn_path or not self._under_path:
            return

        turn_path = self._turn_path
        under_path = self._under_path
        self._confirm.setEnabled(False)

        def operation():
            return self._on_import(turn_path, under_path)

        def on_success(result) -> None:
            QMessageBox.information(
                self,
                "Import Complete",
                f"Processed {result.items_processed:,} items.\n"
                f"New SKUs: {result.new_items}\n"
                f"Qty changes: {result.qty_changes}\n"
                f"Baseline v{result.baseline_version}",
            )
            self.accept()

        def on_error(message: str) -> None:
            self._confirm.setEnabled(True)
            QMessageBox.critical(self, "Import Failed", message)

        run_in_background(
            self,
            operation,
            title="Importing reports…",
            on_success=on_success,
            on_error=on_error,
        )


def run_enrichment_wizard(parent) -> bool:
    from stock_analysis.baseline.manager import apply_enrichment
    from stock_analysis.db.session import get_session

    def do_import(turn_path: Path, under_path: Path):
        with get_session() as session:
            return apply_enrichment(session, turn_path, under_path)

    wizard = TurnImportWizard(
        title="Step 2: Enrichment Import",
        intro=(
            "Select the Stock Turn (Over Stocking) and Stock Turn (Under Stocking) reports. "
            "This enriches department, supplier, unit costs, and sales metrics."
        ),
        confirm_label="Apply Enrichment",
        on_import=do_import,
        parent=parent,
    )
    return wizard.exec()


def run_period_import_wizard(parent) -> bool:
    from stock_analysis.baseline.manager import apply_period_import
    from stock_analysis.db.session import get_session

    def do_import(turn_path: Path, under_path: Path):
        with get_session() as session:
            return apply_period_import(session, turn_path, under_path)

    wizard = TurnImportWizard(
        title="Import Movement Report",
        intro="Select the latest Turn and Turnunder CSV exports for this period.",
        confirm_label="Apply Period Import",
        on_import=do_import,
        parent=parent,
    )
    return wizard.exec()
