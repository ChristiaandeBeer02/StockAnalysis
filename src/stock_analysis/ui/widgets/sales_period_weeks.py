"""Shared sales lookback weeks spin box."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QMessageBox, QSpinBox, QWidget

from stock_analysis.analytics.cache import invalidate_period_summaries
from stock_analysis.analytics.lookback import (
    DEFAULT_LOOKBACK_WEEKS,
    get_lookback_weeks,
    get_available_sales_weeks,
    resolve_lookback_weeks,
    set_lookback_weeks,
)
from stock_analysis.db.session import get_session


def create_sales_period_weeks(
    parent: QWidget | None,
    on_changed: Callable[[], None] | None = None,
) -> QSpinBox:
    spin = QSpinBox(parent)
    spin.setMinimum(1)
    spin.setMaximum(999)
    spin.setMinimumWidth(80)
    spin.setSuffix(" week(s)")

    def _handle_change(_value: int) -> None:
        requested = spin.value()
        with get_session() as session:
            effective, was_clamped = resolve_lookback_weeks(session, requested)
            if was_clamped:
                available = get_available_sales_weeks(session)
                if available < 1:
                    available = DEFAULT_LOOKBACK_WEEKS
                QMessageBox.information(
                    parent,
                    "Sales period",
                    (
                        f"We currently only have {available} week(s) worth of sales reports. "
                        "Please import backdating to view this far back."
                    ),
                )
                spin.blockSignals(True)
                spin.setValue(effective)
                spin.blockSignals(False)
            set_lookback_weeks(session, effective)
        invalidate_period_summaries()
        if on_changed is not None:
            on_changed()

    spin.valueChanged.connect(_handle_change)
    return spin


def sync_sales_period_weeks(spin: QSpinBox) -> None:
    with get_session() as session:
        stored = get_lookback_weeks(session)
        effective, was_clamped = resolve_lookback_weeks(session, stored)
        if was_clamped:
            set_lookback_weeks(session, effective)
    spin.blockSignals(True)
    spin.setValue(effective)
    spin.blockSignals(False)
