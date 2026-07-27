"""Shared sales lookback period combo box."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QComboBox, QWidget

from stock_analysis.analytics.lookback import (
    LOOKBACK_60_INTERPOLATED_TOOLTIP,
    LOOKBACK_OPTIONS,
    get_lookback_days,
    lookback_combo_label,
    set_lookback_days,
)
from stock_analysis.analytics.cache import invalidate_summaries
from stock_analysis.db.session import get_session


def create_sales_period_combo(
    parent: QWidget | None,
    on_changed: Callable[[], None] | None = None,
) -> QComboBox:
    combo = QComboBox(parent)
    combo.setMinimumWidth(140)
    for days in LOOKBACK_OPTIONS:
        combo.addItem(lookback_combo_label(days), days)

    def _handle_change(_index: int) -> None:
        days = combo.currentData()
        if days is None:
            return
        with get_session() as session:
            set_lookback_days(session, int(days))
        invalidate_summaries()
        _update_lookback_tooltip(combo)
        if on_changed is not None:
            on_changed()

    combo.currentIndexChanged.connect(_handle_change)
    return combo


def sync_sales_period_combo(combo: QComboBox) -> None:
    with get_session() as session:
        days = get_lookback_days(session)
    index = combo.findData(days)
    combo.blockSignals(True)
    if index >= 0:
        combo.setCurrentIndex(index)
    combo.blockSignals(False)
    _update_lookback_tooltip(combo)


def update_lookback_tooltip(combo: QComboBox, lookback_60_source: str | None) -> None:
    days = combo.currentData()
    if days == 60 and lookback_60_source == "interpolated":
        combo.setToolTip(LOOKBACK_60_INTERPOLATED_TOOLTIP)
    else:
        combo.setToolTip("")


def _update_lookback_tooltip(combo: QComboBox) -> None:
    days = combo.currentData()
    if days != 60:
        combo.setToolTip("")
        return
    with get_session() as session:
        from stock_analysis.analytics.lookback import build_prior_qty_map
        from stock_analysis.analytics.dashboard import _latest_turn_batch

        batch = _latest_turn_batch(session)
        batch_id = batch.id if batch else None
        _, source = build_prior_qty_map(session, batch_id)
    update_lookback_tooltip(combo, source)
