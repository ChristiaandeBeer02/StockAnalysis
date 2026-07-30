"""Shared Sales_Detail + PurchasesDetailed movement import wizard."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QDateEdit,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from stock_analysis.analytics.lookback import list_sales_batches
from stock_analysis.analytics.movement_periods import (
    format_report_date,
    is_catch_up_pending,
    suggest_next_movement_period,
    weekday_name,
)
from stock_analysis.baseline.manager import BackdateValidationError
from stock_analysis.db.session import (
    get_baseline_anchor_date,
    get_movement_closing_weekday,
    get_session,
    has_initial_baseline,
)
from stock_analysis.importers.iq_retail_parser import parse_report_date
from stock_analysis.importers.movement_parser import merge_movement_reports
from stock_analysis.ui.workers.import_worker import run_in_background


@dataclass(frozen=True)
class MovementWizardConfig:
    title: str
    intro: str
    confirm_label: str
    import_type: str
    direction: str
    require_baseline: bool = False
    backdate_confirm: bool = False
    initial_from: date | None = None
    initial_to: date | None = None
    intro_override: str | None = None


def _format_date(value: date) -> str:
    return value.strftime("%d/%m/%Y")


def _qdate_to_date(value: QDate) -> date:
    return date(value.year(), value.month(), value.day())


def _date_to_qdate(value: date) -> QDate:
    return QDate(value.year, value.month, value.day)


def _resolve_default_period(session) -> tuple[date | None, date | None, str | None]:
    closing = get_movement_closing_weekday(session)
    anchor = get_baseline_anchor_date(session)
    if closing is None or anchor is None:
        return None, None, None

    batches = list_sales_batches(session)
    last_end = None
    if batches:
        last_end = parse_report_date(batches[0].period_end or "")

    suggested = suggest_next_movement_period(anchor, closing, last_end)
    if suggested is None:
        return None, None, None

    start, end = suggested
    intro = None
    if is_catch_up_pending(anchor, closing, last_end):
        intro = (
            f"Import movement for the catch-up period {format_report_date(start)} to "
            f"{format_report_date(end)} to roll your baseline forward to your first "
            f"weekly close ({weekday_name(closing)})."
        )
    return start, end, intro


def _build_config(
    *,
    title: str,
    intro: str,
    confirm_label: str,
    import_type: str,
    direction: str,
    require_baseline: bool = False,
    backdate_confirm: bool = False,
    initial_from: date | None = None,
    initial_to: date | None = None,
    intro_override: str | None = None,
    use_closing_defaults: bool = False,
) -> MovementWizardConfig:
    resolved_from = initial_from
    resolved_to = initial_to
    resolved_intro = intro_override

    if use_closing_defaults and resolved_from is None and resolved_to is None:
        with get_session() as session:
            default_from, default_to, default_intro = _resolve_default_period(session)
        if default_from is not None and default_to is not None:
            resolved_from = default_from
            resolved_to = default_to
        if resolved_intro is None and default_intro is not None:
            resolved_intro = default_intro

    return MovementWizardConfig(
        title=title,
        intro=intro,
        confirm_label=confirm_label,
        import_type=import_type,
        direction=direction,
        require_baseline=require_baseline,
        backdate_confirm=backdate_confirm,
        initial_from=resolved_from,
        initial_to=resolved_to,
        intro_override=resolved_intro,
    )


class MovementImportWizard(QDialog):
    def __init__(self, config: MovementWizardConfig, on_import, parent=None):
        super().__init__(parent)
        self.setWindowTitle(config.title)
        self.setMinimumWidth(620)
        self._config = config
        self._on_import = on_import
        self._sales_path: Path | None = None
        self._purchases_path: Path | None = None

        layout = QVBoxLayout(self)
        self._intro = QLabel(config.intro)
        self._intro.setWordWrap(True)
        layout.addWidget(self._intro)

        date_row = QFormLayout()
        self._from_date = QDateEdit(calendarPopup=True)
        self._from_date.setDisplayFormat("dd/MM/yyyy")
        self._to_date = QDateEdit(calendarPopup=True)
        self._to_date.setDisplayFormat("dd/MM/yyyy")
        if config.initial_from is not None and config.initial_to is not None:
            self._from_date.setDate(_date_to_qdate(config.initial_from))
            self._to_date.setDate(_date_to_qdate(config.initial_to))
        else:
            self._from_date.setDate(QDate.currentDate().addDays(-7))
            self._to_date.setDate(QDate.currentDate().addDays(-1))
        self._from_date.dateChanged.connect(self._update_intro)
        self._to_date.dateChanged.connect(self._update_intro)
        date_row.addRow("From:", self._from_date)
        date_row.addRow("To:", self._to_date)
        layout.addLayout(date_row)

        self._sales_label = QLabel("Sales_Detail file: not selected")
        sales_btn = QPushButton("Select Sales_Detail CSV…")
        sales_btn.clicked.connect(self._browse_sales)
        layout.addWidget(self._sales_label)
        layout.addWidget(sales_btn)

        self._purchases_label = QLabel("PurchasesDetailed file: not selected")
        purchases_btn = QPushButton("Select PurchasesDetailed CSV…")
        purchases_btn.clicked.connect(self._browse_purchases)
        layout.addWidget(self._purchases_label)
        layout.addWidget(purchases_btn)

        self._preview = QFormLayout()
        self._items = QLabel("—")
        self._deprecated = QLabel("—")
        self._net_sales = QLabel("—")
        self._net_purchases = QLabel("—")
        self._preview.addRow("Items:", self._items)
        self._preview.addRow("Deprecated:", self._deprecated)
        self._preview.addRow("Net sales qty:", self._net_sales)
        self._preview.addRow("Net purchases qty:", self._net_purchases)
        layout.addLayout(self._preview)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self._confirm = QPushButton(config.confirm_label)
        self._confirm.setEnabled(False)
        self._confirm.clicked.connect(self._do_import)
        buttons.addWidget(cancel)
        buttons.addWidget(self._confirm)
        layout.addLayout(buttons)

        self._update_intro()

    def period_start(self) -> str:
        return _format_date(_qdate_to_date(self._from_date.date()))

    def period_end(self) -> str:
        return _format_date(_qdate_to_date(self._to_date.date()))

    def _intro_body(self) -> str:
        if self._config.intro_override:
            return self._config.intro_override
        return self._config.intro

    def _update_intro(self) -> None:
        start = self.period_start()
        end = self.period_end()
        self._intro.setText(
            f"Please upload the files for {start} to {end}.\n\n{self._intro_body()}"
        )
        self._update_preview()

    def _browse_sales(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Sales_Detail CSV", "", "CSV Files (*.csv)"
        )
        if path:
            self._sales_path = Path(path)
            self._sales_label.setText(f"Sales_Detail file: {self._sales_path.name}")
            self._update_preview()

    def _browse_purchases(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select PurchasesDetailed CSV", "", "CSV Files (*.csv)"
        )
        if path:
            self._purchases_path = Path(path)
            self._purchases_label.setText(f"PurchasesDetailed file: {self._purchases_path.name}")
            self._update_preview()

    def _update_preview(self) -> None:
        if not self._sales_path or not self._purchases_path:
            self._confirm.setEnabled(False)
            return
        if self._from_date.date() > self._to_date.date():
            self._confirm.setEnabled(False)
            return
        try:
            parsed = merge_movement_reports(self._sales_path, self._purchases_path)
        except Exception as exc:
            QMessageBox.critical(self, "Parse Error", str(exc))
            self._confirm.setEnabled(False)
            return

        deprecated = sum(1 for row in parsed.rows if row.is_deprecated)
        net_sales = sum(row.net_sales_qty for row in parsed.rows)
        net_purchases = sum(row.net_purchases_qty for row in parsed.rows)
        self._items.setText(f"{len(parsed.rows):,}")
        self._deprecated.setText(str(deprecated))
        self._net_sales.setText(f"{net_sales:,.2f}")
        self._net_purchases.setText(f"{net_purchases:,.2f}")
        self._confirm.setEnabled(len(parsed.rows) > 0)

    def _do_import(self) -> None:
        if not self._sales_path or not self._purchases_path:
            return

        start = self.period_start()
        end = self.period_end()
        if self._from_date.date() > self._to_date.date():
            QMessageBox.warning(self, "Invalid Dates", "The start date must be on or before the end date.")
            return

        confirm = QMessageBox.warning(
            self,
            "Confirm Period",
            f"Please make sure the files dates are from {start} to {end}.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        if self._config.backdate_confirm:
            backdate_confirm = QMessageBox.warning(
                self,
                "Confirm Backdate Import",
                (
                    f"This will reverse-apply movement for {start} to {end}, rolling your "
                    "baseline backward in time. Manual adjustments during that period will "
                    "not be reversed.\n\nContinue?"
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if backdate_confirm != QMessageBox.StandardButton.Yes:
                return

        sales_path = self._sales_path
        purchases_path = self._purchases_path
        period_start = start
        period_end = end
        self._confirm.setEnabled(False)

        def operation():
            return self._on_import(sales_path, purchases_path, period_start, period_end)

        def on_success(result) -> None:
            QMessageBox.information(
                self,
                "Import Complete",
                (
                    f"Processed {result.items_processed:,} items "
                    f"({result.qty_changes:,} quantity updates)."
                ),
            )
            self.accept()

        def on_error(message: str) -> None:
            self._confirm.setEnabled(True)
            QMessageBox.critical(self, "Import Failed", message)

        run_in_background(
            self,
            operation_builder=lambda _progress, _cancel: operation,
            title="Importing movement data…",
            maximum=0,
            on_success=on_success,
            on_error=on_error,
        )


def _run_wizard(parent, config: MovementWizardConfig, on_import) -> bool:
    if config.require_baseline:
        with get_session() as session:
            if not has_initial_baseline(session):
                QMessageBox.warning(
                    parent,
                    "Baseline Required",
                    "Import an initial baseline (sthold2) before importing movement data.",
                )
                return False

    wizard = MovementImportWizard(config, on_import, parent=parent)
    return wizard.exec() == QDialog.DialogCode.Accepted


def run_enrichment_wizard(
    parent,
    *,
    initial_from: date | None = None,
    initial_to: date | None = None,
    intro_override: str | None = None,
) -> bool:
    from stock_analysis.baseline.manager import apply_enrichment

    def on_import(sales_path, purchases_path, period_start, period_end):
        with get_session() as session:
            return apply_enrichment(
                session,
                sales_path,
                purchases_path,
                period_start=period_start,
                period_end=period_end,
            )

    config = _build_config(
        title="Import Movement Period (Step 2)",
        intro="This applies sales and purchase movement to roll your baseline forward.",
        confirm_label="Import Movement",
        import_type="baseline_enrichment",
        direction="forward",
        require_baseline=True,
        initial_from=initial_from,
        initial_to=initial_to,
        intro_override=intro_override,
        use_closing_defaults=True,
    )
    return _run_wizard(parent, config, on_import)


def run_period_import_wizard(parent) -> bool:
    from stock_analysis.baseline.manager import apply_period_import

    def on_import(sales_path, purchases_path, period_start, period_end):
        with get_session() as session:
            return apply_period_import(
                session,
                sales_path,
                purchases_path,
                period_start=period_start,
                period_end=period_end,
            )

    config = _build_config(
        title="Import Movement Period",
        intro="Import sales and purchase movement for the selected date range.",
        confirm_label="Import Movement",
        import_type="period_turn",
        direction="forward",
        require_baseline=True,
        use_closing_defaults=True,
    )
    return _run_wizard(parent, config, on_import)


def run_backdate_import_wizard(parent) -> bool:
    from stock_analysis.baseline.manager import apply_backdate_import

    def on_import(sales_path, purchases_path, period_start, period_end):
        with get_session() as session:
            try:
                return apply_backdate_import(
                    session,
                    sales_path,
                    purchases_path,
                    period_start=period_start,
                    period_end=period_end,
                )
            except BackdateValidationError as exc:
                raise RuntimeError(str(exc)) from exc

    return _run_wizard(
        parent,
        MovementWizardConfig(
            title="Backdate Import",
            intro="Roll your current baseline backward to estimate on-hand before the selected period.",
            confirm_label="Backdate Import",
            import_type="period_turn_backdate",
            direction="backward",
            require_baseline=True,
            backdate_confirm=True,
        ),
        on_import,
    )
