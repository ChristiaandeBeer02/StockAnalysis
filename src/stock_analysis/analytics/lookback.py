"""Sales lookback period helpers (week-based)."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from stock_analysis.analytics.movement_periods import suggest_next_backdate_period
from stock_analysis.db.models import AppState, ImportBatch, PeriodTurnLine
from stock_analysis.db.session import get_baseline_anchor_date, get_movement_closing_weekday, set_app_state
from stock_analysis.importers.iq_retail_parser import parse_report_date

SALES_BATCH_TYPES = ("baseline_enrichment", "period_turn", "period_turn_backdate")

DEFAULT_LOOKBACK_WEEKS = 1
_STATE_KEY = "sales_lookback_weeks"


def lookback_label(weeks: int) -> str:
    return f"{weeks}w"


def sales_period_label(weeks: int) -> str:
    return f"Sales ({lookback_label(weeks)})"


def over_qty_label(weeks: int) -> str:
    return f"Over Qty ({lookback_label(weeks)})"


def under_qty_label(weeks: int) -> str:
    return f"Under Qty ({lookback_label(weeks)})"


def units_sold_label(weeks: int) -> str:
    return f"Units Sold ({lookback_label(weeks)})"


def sales_value_label(weeks: int) -> str:
    return f"Sales Value ({lookback_label(weeks)})"


def qty_column_label(weeks: int) -> str:
    return f"Qty {lookback_label(weeks)}"


def sold_column_label(weeks: int) -> str:
    return f"Sold {lookback_label(weeks)}"


def pivot_qty_field_label(weeks: int) -> str:
    return f"Qty Sold ({lookback_label(weeks)})"


def get_lookback_weeks(session: Session) -> int:
    state = session.get(AppState, _STATE_KEY)
    if not state or not state.value:
        return DEFAULT_LOOKBACK_WEEKS
    try:
        weeks = int(state.value)
    except ValueError:
        return DEFAULT_LOOKBACK_WEEKS
    if weeks < 1:
        return DEFAULT_LOOKBACK_WEEKS
    return weeks


def set_lookback_weeks(session: Session, weeks: int) -> None:
    if weeks < 1:
        weeks = DEFAULT_LOOKBACK_WEEKS
    set_app_state(session, _STATE_KEY, str(weeks))


def list_sales_batches(session: Session) -> list[ImportBatch]:
    if "_sales_batches" in session.info:
        return session.info["_sales_batches"]

    batches = session.scalars(
        select(ImportBatch).where(ImportBatch.import_type.in_(SALES_BATCH_TYPES))
    ).all()

    def sort_key(batch: ImportBatch) -> tuple:
        end = parse_report_date(batch.period_end or "") if batch.period_end else None
        start = parse_report_date(batch.period_start or "") if batch.period_start else None
        return (
            end or start,
            start,
            batch.imported_at,
            batch.id,
        )

    result = sorted(batches, key=sort_key, reverse=True)
    session.info["_sales_batches"] = result
    return result


def resolve_backdate_default_period(
    session: Session,
) -> tuple[date | None, date | None, str | None]:
    """Suggest the next historical backdate import range from stored movement history."""
    closing_weekday = get_movement_closing_weekday(session)
    if closing_weekday is None:
        return None, None, None

    batches = list_sales_batches(session)
    if batches:
        oldest = batches[-1]
        if not oldest.period_start:
            return None, None, None
        reference = parse_report_date(oldest.period_start)
        intro = "Suggested period is the week before your earliest imported movement."
    else:
        reference = get_baseline_anchor_date(session)
        if reference is None:
            return None, None, None
        intro = "Suggested period is the week before your baseline date."

    start, end = suggest_next_backdate_period(reference, closing_weekday)
    return start, end, intro


def get_available_sales_weeks(session: Session) -> int:
    return len(list_sales_batches(session))


def get_batch_ids_for_weeks(
    session: Session, weeks: int, *, offset: int = 0
) -> list[int]:
    if weeks < 1:
        return []
    batches = list_sales_batches(session)
    start = offset
    end = offset + weeks
    return [batch.id for batch in batches[start:end]]


def resolve_lookback_weeks(session: Session, requested: int) -> tuple[int, bool]:
    available = get_available_sales_weeks(session)
    if available < 1:
        return DEFAULT_LOOKBACK_WEEKS, requested > DEFAULT_LOOKBACK_WEEKS
    if requested < 1:
        return DEFAULT_LOOKBACK_WEEKS, True
    if requested > available:
        return available, True
    return requested, False


def build_multi_batch_qty_map(
    session: Session, weeks: int, *, offset: int = 0
) -> dict[int, float]:
    batch_ids = get_batch_ids_for_weeks(session, weeks, offset=offset)
    if not batch_ids:
        return {}

    lines = session.scalars(
        select(PeriodTurnLine).where(PeriodTurnLine.import_batch_id.in_(batch_ids))
    ).all()
    qty_map: dict[int, float] = {}
    for line in lines:
        qty_map[line.item_id] = qty_map.get(line.item_id, 0.0) + line.qty_sold_90
    return qty_map


def item_qty_sold(qty_map: dict[int, float], item_id: int) -> float:
    return qty_map.get(item_id, 0.0)


def build_multi_batch_sales_totals(
    session: Session, weeks: int, *, offset: int = 0
) -> dict[int, tuple[float, float]]:
    """Map item_id -> (total_revenue, total_profit) across the lookback window."""
    batch_ids = get_batch_ids_for_weeks(session, weeks, offset=offset)
    if not batch_ids:
        return {}

    lines = session.scalars(
        select(PeriodTurnLine).where(PeriodTurnLine.import_batch_id.in_(batch_ids))
    ).all()
    totals: dict[int, tuple[float, float]] = {}
    for line in lines:
        revenue, profit = totals.get(line.item_id, (0.0, 0.0))
        totals[line.item_id] = (
            revenue + line.net_sales_revenue,
            profit + line.gross_profit,
        )
    return totals


def item_sales_totals(
    totals_map: dict[int, tuple[float, float]], item_id: int
) -> tuple[float, float]:
    return totals_map.get(item_id, (0.0, 0.0))
