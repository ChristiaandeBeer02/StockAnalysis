"""Sales lookback period helpers."""

from __future__ import annotations

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from stock_analysis.db.models import AppState, ImportBatch, PeriodTurnLine
from stock_analysis.db.session import set_app_state

LOOKBACK_OPTIONS = (30, 60, 90)
DEFAULT_LOOKBACK = 90
_STATE_KEY = "sales_lookback_days"

LOOKBACK_60_INTERPOLATED_TOOLTIP = (
    "Estimated from 30d and 90d data — import a prior period for exact 60-day totals"
)


def lookback_label(days: int) -> str:
    return f"{days}d"


def lookback_combo_label(days: int) -> str:
    return f"Last {days} days"


def sales_period_label(days: int) -> str:
    return f"Sales ({lookback_label(days)})"


def units_sold_label(days: int) -> str:
    return f"Units Sold ({lookback_label(days)})"


def sales_value_label(days: int) -> str:
    return f"Sales Value ({lookback_label(days)})"


def qty_column_label(days: int) -> str:
    return f"Qty {lookback_label(days)}"


def sold_column_label(days: int) -> str:
    return f"Sold {lookback_label(days)}"


def pivot_qty_field_label(days: int) -> str:
    return f"Qty Sold ({lookback_label(days)})"


def get_lookback_days(session: Session) -> int:
    state = session.get(AppState, _STATE_KEY)
    if not state or not state.value:
        return DEFAULT_LOOKBACK
    try:
        days = int(state.value)
    except ValueError:
        return DEFAULT_LOOKBACK
    if days not in LOOKBACK_OPTIONS:
        return DEFAULT_LOOKBACK
    return days


def set_lookback_days(session: Session, days: int) -> None:
    if days not in LOOKBACK_OPTIONS:
        days = DEFAULT_LOOKBACK
    set_app_state(session, _STATE_KEY, str(days))


def get_prior_period_batch_id(session: Session, batch_id: int | None) -> int | None:
    if batch_id is None:
        return None
    batches = session.scalars(
        select(ImportBatch)
        .where(ImportBatch.import_type == "period_turn")
        .order_by(desc(ImportBatch.imported_at))
    ).all()
    ids = [batch.id for batch in batches]
    if batch_id not in ids:
        return None
    idx = ids.index(batch_id)
    if idx + 1 >= len(ids):
        return None
    return ids[idx + 1]


def build_prior_qty_map(session: Session, batch_id: int | None) -> tuple[dict[int, float], str]:
    prior_batch_id = get_prior_period_batch_id(session, batch_id)
    if prior_batch_id is None:
        return {}, "interpolated"
    lines = session.scalars(
        select(PeriodTurnLine).where(PeriodTurnLine.import_batch_id == prior_batch_id)
    ).all()
    return {line.item_id: line.qty_sold_30 for line in lines}, "two_period"


def _interpolate_60(line: PeriodTurnLine) -> float:
    mid = max(0.0, line.qty_sold_90 - line.qty_sold_30)
    return line.qty_sold_30 + mid * 0.5


def qty_sold(
    line: PeriodTurnLine,
    days: int,
    *,
    prior_qty_30: float = 0.0,
    use_two_period_60: bool = False,
) -> float:
    if days == 30:
        return line.qty_sold_30
    if days == 90:
        return line.qty_sold_90
    if days == 60:
        if use_two_period_60:
            return line.qty_sold_30 + prior_qty_30
        return _interpolate_60(line)
    return line.qty_sold_90
