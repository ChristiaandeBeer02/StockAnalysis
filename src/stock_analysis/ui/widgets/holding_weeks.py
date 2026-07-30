"""Shared holding weeks spin box."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QSpinBox, QWidget

from stock_analysis.analytics.cache import invalidate_period_summaries
from stock_analysis.analytics.metrics import DEFAULT_HOLDING_WEEKS
from stock_analysis.analytics.queries import get_holding_weeks, set_holding_weeks
from stock_analysis.db.session import get_session


def create_holding_weeks(
    parent: QWidget | None,
    on_changed: Callable[[], None] | None = None,
) -> QSpinBox:
    spin = QSpinBox(parent)
    spin.setMinimum(1)
    spin.setMaximum(999)
    spin.setMinimumWidth(80)
    spin.setSuffix(" week(s)")

    def _handle_change(_value: int) -> None:
        weeks = max(1, spin.value())
        with get_session() as session:
            set_holding_weeks(session, weeks)
        invalidate_period_summaries()
        if on_changed is not None:
            on_changed()

    spin.valueChanged.connect(_handle_change)
    return spin


def sync_holding_weeks(spin: QSpinBox) -> None:
    with get_session() as session:
        weeks = get_holding_weeks(session)
    spin.blockSignals(True)
    spin.setValue(weeks if weeks >= 1 else DEFAULT_HOLDING_WEEKS)
    spin.blockSignals(False)
