"""Shared Sales_Detail + PurchasesDetailed movement import wizard."""

from __future__ import annotations

import csv
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

from stock_analysis.analytics.movement_periods import (
    format_report_date,
    is_catch_up_pending,
    previous_closing_date,
    suggest_next_movement_period,
    weekday_name,
)
from stock_analysis.analytics.stocklist_compare import StocklistComparison, compare_movement_to_stocklist
from stock_analysis.baseline.manager import (
    apply_stocklist_override,
    apply_stocklist_pricing,
    find_negative_qty_skus,
)
from stock_analysis.db.session import (
    get_baseline_anchor_date,
    get_movement_closing_weekday,
    get_session,
    has_enrichment,
    has_initial_baseline,
)
from stock_analysis.importers.movement_parser import merge_movement_reports
from stock_analysis.importers.stocklist_parser import parse_stocklist_file
from stock_analysis.ui.widgets.data_table import DataTable
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
    enable_stocklist_compare: bool = False


def _format_date(value: date) -> str:
    return value.strftime("%d/%m/%Y")


def _qdate_to_date(value: QDate) -> date:
    return date(value.year(), value.month(), value.day())


def _date_to_qdate(value: date) -> QDate:
    return QDate(value.year, value.month, value.day)


def _alignment_context(session) -> tuple[bool, date | None, date | None, str | None]:
    closing = get_movement_closing_weekday(session)
    anchor = get_baseline_anchor_date(session)
    if closing is None or anchor is None:
        return False, None, None, None

    pending = is_catch_up_pending(anchor, closing)
    suggested = suggest_next_movement_period(anchor, closing)
    if suggested is None:
        return pending, None, None, None

    start, end = suggested
    anchor_label = format_report_date(anchor)
    intro = None
    if pending:
        intro = (
            f"Your baseline is as of {anchor_label}. "
            f"Import movement for the alignment period {format_report_date(start)} to "
            f"{format_report_date(end)} to backdate your baseline to your previous "
            f"weekly close ({weekday_name(closing)})."
        )
    else:
        intro = (
            f"Your baseline is as of {anchor_label}. "
            f"Import movement for the suggested period below."
        )
    return pending, start, end, intro


def _resolve_default_period(session) -> tuple[date | None, date | None, str | None]:
    _, start, end, intro = _alignment_context(session)
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
    enable_stocklist_compare: bool = False,
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
        enable_stocklist_compare=enable_stocklist_compare,
    )


class MovementImportWizard(QDialog):
    def __init__(self, config: MovementWizardConfig, on_import, parent=None):
        super().__init__(parent)
        self.setWindowTitle(config.title)
        if config.enable_stocklist_compare:
            self.setMinimumSize(720, 600)
        else:
            self.setMinimumWidth(620)
        self._config = config
        self._on_import = on_import
        self._sales_path: Path | None = None
        self._purchases_path: Path | None = None
        self._stocklist_path: Path | None = None
        self._comparison: StocklistComparison | None = None
        self._parsed_movement_rows = None

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

        if config.enable_stocklist_compare:
            self._stocklist_label = QLabel("StockLists file: not selected (optional)")
            stocklist_btn = QPushButton("Select StockLists CSV…")
            stocklist_btn.clicked.connect(self._browse_stocklist)
            layout.addWidget(self._stocklist_label)
            layout.addWidget(stocklist_btn)

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

        if config.enable_stocklist_compare:
            self._stocklist_preview = QFormLayout()
            self._compared = QLabel("—")
            self._matches = QLabel("—")
            self._variances = QLabel("—")
            self._stocklist_preview.addRow("Items compared:", self._compared)
            self._stocklist_preview.addRow("Exact matches:", self._matches)
            self._stocklist_preview.addRow("Variances:", self._variances)
            layout.addLayout(self._stocklist_preview)

            variance_label = QLabel("StockLists variance preview (largest differences first)")
            layout.addWidget(variance_label)
            self._variance_table = DataTable()
            self._variance_table.set_headers(
                ["SKU", "Name", "Projected", "Stocklist", "Variance", "Type"]
            )
            self._variance_table.enable_viewport_scrolling()
            layout.addWidget(self._variance_table)

            export_row = QHBoxLayout()
            export_row.addStretch()
            self._export_btn = QPushButton("Export CSV…")
            self._export_btn.setEnabled(False)
            self._export_btn.clicked.connect(self._export_differences_csv)
            export_row.addWidget(self._export_btn)
            layout.addLayout(export_row)

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

    def _browse_stocklist(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select StockLists CSV", "", "CSV Files (*.csv)"
        )
        if path:
            self._stocklist_path = Path(path)
            self._stocklist_label.setText(f"StockLists file: {self._stocklist_path.name}")
            self._update_preview()

    def _clear_stocklist_comparison(self) -> None:
        self._comparison = None
        if not self._config.enable_stocklist_compare:
            return
        self._compared.setText("—")
        self._matches.setText("—")
        self._variances.setText("—")
        self._variance_table.set_rows([])
        self._export_btn.setEnabled(False)

    def _update_stocklist_comparison(self, movement_rows) -> None:
        if not self._config.enable_stocklist_compare:
            return
        if not self._stocklist_path:
            self._clear_stocklist_comparison()
            return
        try:
            stocklist = parse_stocklist_file(self._stocklist_path, require_on_hand=True)
            with get_session() as session:
                comparison = compare_movement_to_stocklist(
                    session,
                    movement_rows,
                    stocklist,
                    direction=self._config.direction,
                )
            comparison.file_name = self._stocklist_path.name
            self._comparison = comparison
        except Exception as exc:
            QMessageBox.critical(self, "StockLists Compare Error", str(exc))
            self._clear_stocklist_comparison()
            return

        self._compared.setText(f"{len(comparison.lines):,}")
        self._matches.setText(f"{comparison.exact_matches:,}")
        self._variances.setText(f"{len(comparison.variance_lines):,}")

        preview_rows = []
        for line in comparison.variance_lines:
            preview_rows.append(
                [
                    line.sku,
                    line.name[:50],
                    f"{line.projected_qty:g}",
                    f"{line.stocklist_qty:g}",
                    f"{line.variance:+g}",
                    line.line_type.replace("_", " "),
                ]
            )
        self._variance_table.set_rows(preview_rows)
        self._export_btn.setEnabled(len(comparison.variance_lines) > 0)

    def _update_preview(self) -> None:
        if not self._sales_path or not self._purchases_path:
            self._confirm.setEnabled(False)
            self._clear_stocklist_comparison()
            return
        if self._from_date.date() > self._to_date.date():
            self._confirm.setEnabled(False)
            self._clear_stocklist_comparison()
            return
        try:
            parsed = merge_movement_reports(self._sales_path, self._purchases_path)
        except Exception as exc:
            QMessageBox.critical(self, "Parse Error", str(exc))
            self._confirm.setEnabled(False)
            self._clear_stocklist_comparison()
            return

        self._parsed_movement_rows = parsed.rows
        deprecated = sum(1 for row in parsed.rows if row.is_deprecated)
        net_sales = sum(row.net_sales_qty for row in parsed.rows)
        net_purchases = sum(row.net_purchases_qty for row in parsed.rows)
        self._items.setText(f"{len(parsed.rows):,}")
        self._deprecated.setText(str(deprecated))
        self._net_sales.setText(f"{net_sales:,.2f}")
        self._net_purchases.setText(f"{net_purchases:,.2f}")
        self._update_stocklist_comparison(parsed.rows)
        self._confirm.setEnabled(len(parsed.rows) > 0)

    def _export_differences_csv(self) -> None:
        if not self._comparison or not self._comparison.variance_lines:
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export StockLists Variances",
            "stocklist_variances.csv",
            "CSV Files (*.csv)",
        )
        if not path:
            return
        try:
            with Path(path).open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(
                    [
                        "sku",
                        "name",
                        "projected_on_hand",
                        "stocklist_on_hand",
                        "variance",
                        "line_type",
                    ]
                )
                for line in self._comparison.variance_lines:
                    writer.writerow(
                        [
                            line.sku,
                            line.name,
                            line.projected_qty,
                            line.stocklist_qty,
                            line.variance,
                            line.line_type,
                        ]
                    )
        except OSError as exc:
            QMessageBox.critical(self, "Export Failed", str(exc))
            return
        QMessageBox.information(self, "Export Complete", f"Saved to {path}")

    def _export_negative_skus_csv(self, skus: list[str]) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Negative SKUs",
            "negative_skus.csv",
            "CSV Files (*.csv)",
        )
        if not path:
            return
        try:
            with Path(path).open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["sku"])
                for sku in skus:
                    writer.writerow([sku])
        except OSError as exc:
            QMessageBox.critical(self, "Export Failed", str(exc))
            return
        QMessageBox.information(self, "Export Complete", f"Saved to {path}")

    def _confirm_negative_qty_warning(self, skus: list[str]) -> bool:
        preview = ", ".join(skus[:10])
        suffix = f" (+{len(skus) - 10} more)" if len(skus) > 10 else ""
        message = (
            f"{len(skus)} SKU(s) will go below zero after this import: {preview}{suffix}\n\n"
            "Continue anyway?"
        )

        while True:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle("Negative Stock Warning")
            box.setText(message)
            export_btn = box.addButton("Export CSV…", QMessageBox.ButtonRole.ActionRole)
            continue_btn = box.addButton("Continue", QMessageBox.ButtonRole.AcceptRole)
            cancel_btn = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            clicked = box.clickedButton()
            if clicked == continue_btn:
                return True
            if clicked == cancel_btn or clicked is None:
                return False
            if clicked == export_btn:
                self._export_negative_skus_csv(skus)

    def _warn_if_negative_qty(self, rows) -> bool:
        if self._config.direction != "backward":
            return True
        if self._config.import_type == "period_turn_backdate":
            return True
        with get_session() as session:
            negative = find_negative_qty_skus(session, rows, direction="backward")
        if not negative:
            return True
        return self._confirm_negative_qty_warning(negative)

    def _handle_post_import_variances(self, result) -> None:
        comparison = self._comparison
        stocklist_path = self._stocklist_path
        if (
            not self._config.enable_stocklist_compare
            or stocklist_path is None
            or comparison is None
            or not comparison.variance_lines
        ):
            QMessageBox.information(
                self,
                "Import Complete",
                (
                    f"Processed {result.items_processed:,} items "
                    f"({result.qty_changes:,} quantity updates)."
                ),
            )
            self.accept()
            return

        message = (
            f"Movement import complete ({result.items_processed:,} items, "
            f"{result.qty_changes:,} quantity updates).\n\n"
            f"{len(comparison.variance_lines):,} StockLists variance(s) found.\n\n"
            "Override replaces baseline on-hand with StockLists values. "
            "Ignore keeps movement-computed quantities."
        )

        while True:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle("StockLists Variances")
            box.setText(message)
            export_btn = box.addButton("Export CSV…", QMessageBox.ButtonRole.ActionRole)
            override_btn = box.addButton("Override", QMessageBox.ButtonRole.AcceptRole)
            ignore_btn = box.addButton("Ignore", QMessageBox.ButtonRole.DestructiveRole)
            box.exec()
            clicked = box.clickedButton()
            if clicked == ignore_btn or clicked is None:
                QMessageBox.information(
                    self,
                    "Import Complete",
                    (
                        f"Processed {result.items_processed:,} items "
                        f"({result.qty_changes:,} quantity updates)."
                    ),
                )
                self.accept()
                return
            if clicked == export_btn:
                self._export_differences_csv()
                continue
            if clicked == override_btn:
                self._run_stocklist_override(result, stocklist_path, comparison)
                return

    def _run_stocklist_override(self, movement_result, stocklist_path: Path, comparison) -> None:
        variance_lines = list(comparison.variance_lines)
        source_import_id = movement_result.import_batch_id

        def operation():
            with get_session() as session:
                return apply_stocklist_override(
                    session,
                    stocklist_path,
                    variance_lines,
                    source_import_id=source_import_id,
                )

        def on_success(override_result) -> None:
            QMessageBox.information(
                self,
                "Import Complete",
                (
                    f"Processed {movement_result.items_processed:,} movement items "
                    f"({movement_result.qty_changes:,} quantity updates).\n"
                    f"StockLists override applied: {override_result.items_updated:,} quantities "
                    f"updated ({override_result.new_items:,} new items)."
                ),
            )
            self.accept()

        def on_error(message: str) -> None:
            QMessageBox.critical(self, "Override Failed", message)

        run_in_background(
            self,
            operation_builder=lambda _progress, _cancel: operation,
            title="Applying StockLists override…",
            maximum=0,
            on_success=on_success,
            on_error=on_error,
        )

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
            if self._config.import_type == "period_turn_backdate":
                backdate_message = (
                    f"This will import historical sales and purchase data for {start} to {end}. "
                    "Your current on-hand quantities and baseline date will not change.\n\nContinue?"
                )
            else:
                end_date = _qdate_to_date(self._to_date.date())
                with get_session() as session:
                    closing = get_movement_closing_weekday(session)
                aligned = (
                    previous_closing_date(end_date, closing)
                    if closing is not None
                    else None
                )
                if aligned is not None:
                    aligned_label = format_report_date(aligned)
                    backdate_message = (
                        f"Your baseline is as of {end} (your stockholding date). This will "
                        f"reverse-apply movement from {end} back through {start}, rolling your "
                        f"baseline back to your previous weekly close ({aligned_label}). "
                        "Manual adjustments during that period will not be reversed.\n\n"
                        "Continue?"
                    )
                else:
                    backdate_message = (
                        f"Your baseline is as of {end} (your stockholding date). This will "
                        f"reverse-apply movement from {end} back through {start}, rolling your "
                        "baseline backward in time. Manual adjustments during that period will "
                        "not be reversed.\n\nContinue?"
                    )
            backdate_confirm = QMessageBox.warning(
                self,
                "Confirm Backdate Import",
                backdate_message,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if backdate_confirm != QMessageBox.StandardButton.Yes:
                return

        try:
            parsed = merge_movement_reports(self._sales_path, self._purchases_path)
        except Exception as exc:
            QMessageBox.critical(self, "Parse Error", str(exc))
            return
        if not self._warn_if_negative_qty(parsed.rows):
            return

        sales_path = self._sales_path
        purchases_path = self._purchases_path
        period_start = start
        period_end = end
        self._confirm.setEnabled(False)

        def operation():
            return self._on_import(sales_path, purchases_path, period_start, period_end)

        def on_success(result) -> None:
            stocklist_path = self._stocklist_path
            if stocklist_path is not None:
                try:
                    parsed = parse_stocklist_file(stocklist_path)
                    with get_session() as session:
                        apply_stocklist_pricing(session, parsed)
                except Exception as exc:
                    QMessageBox.warning(
                        self,
                        "Stocklist Pricing",
                        f"Movement imported but pricing update failed: {exc}",
                    )
            self._handle_post_import_variances(result)

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
    with get_session() as session:
        needs_closing_day = (
            has_initial_baseline(session)
            and not has_enrichment(session)
            and get_movement_closing_weekday(session) is None
        )

    if needs_closing_day:
        from stock_analysis.ui.wizards.movement_closing_dialog import run_closing_day_dialog

        if run_closing_day_dialog(parent) is None:
            return False

    with get_session() as session:
        alignment_pending, _, _, _ = _alignment_context(session)

    if alignment_pending:
        from stock_analysis.baseline.manager import apply_baseline_alignment

        def on_import(sales_path, purchases_path, period_start, period_end):
            with get_session() as session:
                return apply_baseline_alignment(
                    session,
                    sales_path,
                    purchases_path,
                    period_start=period_start,
                    period_end=period_end,
                )

        config = _build_config(
            title="Import Movement Period (Step 2)",
            intro=(
                "This reverse-applies movement to align your baseline to your previous weekly close."
            ),
            confirm_label="Import Movement",
            import_type="baseline_enrichment",
            direction="backward",
            require_baseline=True,
            backdate_confirm=True,
            initial_from=initial_from,
            initial_to=initial_to,
            intro_override=intro_override,
            use_closing_defaults=True,
        )
    else:
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
            enable_stocklist_compare=True,
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
        enable_stocklist_compare=True,
    )
    return _run_wizard(parent, config, on_import)


def run_backdate_import_wizard(parent) -> bool:
    from stock_analysis.baseline.manager import apply_backdate_import

    def on_import(sales_path, purchases_path, period_start, period_end):
        with get_session() as session:
            return apply_backdate_import(
                session,
                sales_path,
                purchases_path,
                period_start=period_start,
                period_end=period_end,
            )

    return _run_wizard(
        parent,
        MovementWizardConfig(
            title="Backdate Import",
            intro="Import historical sales and purchase data for a past period. Your current on-hand quantities and baseline date will not change.",
            confirm_label="Backdate Import",
            import_type="period_turn_backdate",
            direction="backward",
            require_baseline=True,
            backdate_confirm=True,
        ),
        on_import,
    )
