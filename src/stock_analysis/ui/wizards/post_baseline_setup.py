"""Post-baseline setup: closing day selection and optional baseline alignment."""

from __future__ import annotations

from PySide6.QtWidgets import QMessageBox, QWidget

from stock_analysis.analytics.movement_periods import (
    backdate_alignment_period,
    baseline_anchor_date,
    format_report_date,
    weekday_name,
)
from stock_analysis.db.session import get_baseline_anchor_date, get_session
from stock_analysis.importers.stockholding_parser import StockholdingParseResult
from stock_analysis.ui.wizards.movement_closing_dialog import run_closing_day_dialog
from stock_analysis.ui.wizards.movement_import_wizard import run_enrichment_wizard


def run_post_baseline_setup(parent: QWidget, parsed: StockholdingParseResult | None = None) -> bool:
    """Ask for closing day and optionally launch baseline alignment. Returns True if data changed."""
    anchor = None
    if parsed is not None:
        anchor = baseline_anchor_date(parsed)
    if anchor is None:
        with get_session() as session:
            anchor = get_baseline_anchor_date(session)
    if anchor is None:
        return False

    closing_weekday = run_closing_day_dialog(parent)
    if closing_weekday is None:
        return False

    alignment = backdate_alignment_period(anchor, closing_weekday)
    if alignment is None:
        QMessageBox.information(
            parent,
            "Movement Calendar Set",
            (
                f"Your baseline is already on your closing day "
                f"({weekday_name(closing_weekday)}).\n\n"
                "Import movement when ready using the Step 2 banner or Settings."
            ),
        )
        return False

    start, end = alignment
    start_label = format_report_date(start)
    end_label = format_report_date(end)
    return run_enrichment_wizard(
        parent,
        initial_from=start,
        initial_to=end,
        intro_override=(
            f"Your stockholding is from {format_report_date(anchor)} "
            f"({weekday_name(anchor.weekday())}), which is not your closing day.\n\n"
            f"Import movement for the alignment period {start_label} to {end_label} "
            "to backdate your baseline to your previous weekly close."
        ),
    )
