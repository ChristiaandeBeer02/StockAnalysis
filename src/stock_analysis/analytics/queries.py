"""Shared analytics query helpers."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from stock_analysis.analytics.metrics import DEFAULT_OPTIMUM_STOCK_MONTHS
from stock_analysis.db.models import AppState, BaselineItem


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
