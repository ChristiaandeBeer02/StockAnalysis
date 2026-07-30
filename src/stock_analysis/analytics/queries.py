"""Shared analytics query helpers."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from stock_analysis.analytics.metrics import (
    DEFAULT_HOLDING_WEEKS,
    DEFAULT_OPTIMUM_STOCK_MONTHS,
    DEFAULT_STOCK_BUFFER_MAX_PCT,
    DEFAULT_STOCK_BUFFER_MIN_PCT,
)
from stock_analysis.db.models import AppState, BaselineItem
from stock_analysis.db.session import set_app_state


def baseline_qty_map(session: Session, item_ids: list[int]) -> dict[int, float]:
    if not item_ids:
        return {}
    rows = session.scalars(
        select(BaselineItem).where(BaselineItem.item_id.in_(item_ids))
    ).all()
    return {row.item_id: row.qty_on_hand for row in rows}


def get_optimum_stock_months(session: Session) -> float:
    state = session.get(AppState, "optimum_stock_months")
    if state is None:
        return DEFAULT_OPTIMUM_STOCK_MONTHS
    try:
        value = float(state.value)
    except ValueError:
        return DEFAULT_OPTIMUM_STOCK_MONTHS
    return value if value > 0 else DEFAULT_OPTIMUM_STOCK_MONTHS


def _read_float_state(session: Session, key: str, default: float) -> float:
    state = session.get(AppState, key)
    if state is None:
        return default
    try:
        value = float(state.value)
    except ValueError:
        return default
    return value


def _read_int_state(session: Session, key: str, default: int) -> int:
    state = session.get(AppState, key)
    if state is None:
        return default
    try:
        value = int(state.value)
    except ValueError:
        return default
    return value


def get_holding_weeks(session: Session) -> int:
    weeks = _read_int_state(session, "holding_weeks", DEFAULT_HOLDING_WEEKS)
    return weeks if weeks >= 1 else DEFAULT_HOLDING_WEEKS


def set_holding_weeks(session: Session, weeks: int) -> None:
    if weeks < 1:
        weeks = DEFAULT_HOLDING_WEEKS
    set_app_state(session, "holding_weeks", str(weeks))


def get_stock_buffer_pct_range(session: Session) -> tuple[float, float]:
    min_pct = _read_float_state(session, "stock_buffer_min_pct", DEFAULT_STOCK_BUFFER_MIN_PCT)
    max_pct = _read_float_state(session, "stock_buffer_max_pct", DEFAULT_STOCK_BUFFER_MAX_PCT)
    if min_pct < 0:
        min_pct = DEFAULT_STOCK_BUFFER_MIN_PCT
    if max_pct < min_pct:
        max_pct = min_pct
    return min_pct, max_pct


def set_stock_buffer_pct_range(session: Session, min_pct: float, max_pct: float) -> None:
    if min_pct < 0:
        min_pct = 0.0
    if max_pct < min_pct:
        max_pct = min_pct
    set_app_state(session, "stock_buffer_min_pct", str(min_pct))
    set_app_state(session, "stock_buffer_max_pct", str(max_pct))
