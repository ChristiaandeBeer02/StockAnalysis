"""Shared stockholding import warnings."""

from __future__ import annotations

from PySide6.QtWidgets import QMessageBox, QWidget

from stock_analysis.importers.iq_retail_parser import is_ongoing_stockhold
from stock_analysis.importers.stockholding_parser import StockholdingParseResult

_ONGOING_WARNING = (
    "Slight data inaccuracy may occur when using an ongoing stock hold. "
    "Are you sure you want to continue?"
)


def confirm_ongoing_stockhold(parent: QWidget, parsed: StockholdingParseResult) -> bool:
    if not is_ongoing_stockhold(parsed.period_start, parsed.period_end, parsed.date_printed):
        return True
    reply = QMessageBox.warning(
        parent,
        "Ongoing Stock Hold",
        _ONGOING_WARNING,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
    )
    return reply == QMessageBox.StandardButton.Yes
