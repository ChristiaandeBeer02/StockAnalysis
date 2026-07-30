"""Slow-moving and ABC classification reports."""

from __future__ import annotations

import math

from sqlalchemy.orm import Session

from stock_analysis.analytics.dashboard import get_lookback_period_lines
from stock_analysis.analytics.lookback import (
    DEFAULT_LOOKBACK_WEEKS,
    build_multi_batch_qty_map,
    item_qty_sold,
)
from stock_analysis.analytics.metrics import (
    effective_on_hand,
    effective_unit_cost,
    item_stock_health,
    stock_position_from_holding_policy,
    stock_value,
)
from stock_analysis.analytics.queries import (
    baseline_qty_map,
    get_holding_weeks,
    get_stock_buffer_pct_range,
)
from stock_analysis.db.models import Item, PeriodTurnLine


def _line_department(line: PeriodTurnLine, item: Item) -> str:
    return (line.dept or item.department) or "Unknown"


def _holding_policy(session: Session) -> tuple[int, float, float]:
    return (
        get_holding_weeks(session),
        *get_stock_buffer_pct_range(session),
    )


def dead_stock_report(
    session: Session,
    lookback_weeks: int = DEFAULT_LOOKBACK_WEEKS,
    *,
    dept_filter: str | None = None,
) -> list[dict]:
    lines = get_lookback_period_lines(session, lookback_weeks)
    if not lines:
        return []

    baseline_map = baseline_qty_map(session, [item.id for _, item in lines])
    qty_map = build_multi_batch_qty_map(session, lookback_weeks)
    holding_weeks, min_buffer_pct, max_buffer_pct = _holding_policy(session)
    report: list[dict] = []
    for line, item in lines:
        if dept_filter and _line_department(line, item) != dept_filter:
            continue
        qty = effective_on_hand(baseline_map, item.id, line.on_hand)
        sold = item_qty_sold(qty_map, item.id)
        category, _, _, _ = item_stock_health(
            qty,
            sold,
            lookback_weeks=lookback_weeks,
            holding_weeks=holding_weeks,
            min_buffer_pct=min_buffer_pct,
            max_buffer_pct=max_buffer_pct,
        )
        if category != "dead":
            continue
        cost = effective_unit_cost(line, item)
        report.append(
            {
                "sku": item.sku,
                "name": item.name[:80],
                "dept": line.dept or item.department or "—",
                "on_hand": qty,
                "unit_cost": cost,
                "stock_value": stock_value(qty, cost),
                "qty_sold": sold,
            }
        )

    report.sort(key=lambda row: row["stock_value"], reverse=True)
    return report


def slow_moving_report(
    session: Session,
    lookback_weeks: int = DEFAULT_LOOKBACK_WEEKS,
    *,
    dept_filter: str | None = None,
) -> list[dict]:
    lines = get_lookback_period_lines(session, lookback_weeks)
    if not lines:
        return []

    baseline_map = baseline_qty_map(session, [item.id for _, item in lines])
    qty_map = build_multi_batch_qty_map(session, lookback_weeks)
    holding_weeks, min_buffer_pct, max_buffer_pct = _holding_policy(session)
    report: list[dict] = []
    for line, item in lines:
        if dept_filter and _line_department(line, item) != dept_filter:
            continue
        qty = effective_on_hand(baseline_map, item.id, line.on_hand)
        sold = item_qty_sold(qty_map, item.id)
        category, over_qty, _, cover = item_stock_health(
            qty,
            sold,
            lookback_weeks=lookback_weeks,
            holding_weeks=holding_weeks,
            min_buffer_pct=min_buffer_pct,
            max_buffer_pct=max_buffer_pct,
        )
        if category != "slow_moving":
            continue
        cost = effective_unit_cost(line, item)
        report.append(
            {
                "sku": item.sku,
                "name": item.name[:80],
                "dept": line.dept or item.department or "—",
                "on_hand": qty,
                "over_qty": over_qty,
                "weeks_cover": cover,
                "unit_cost": cost,
                "excess_value": stock_value(over_qty, cost),
                "qty_sold": sold,
            }
        )

    report.sort(key=lambda row: row["excess_value"], reverse=True)
    return report


def understocked_report(
    session: Session,
    lookback_weeks: int = DEFAULT_LOOKBACK_WEEKS,
    *,
    dept_filter: str | None = None,
) -> list[dict]:
    lines = get_lookback_period_lines(session, lookback_weeks)
    if not lines:
        return []

    baseline_map = baseline_qty_map(session, [item.id for _, item in lines])
    qty_map = build_multi_batch_qty_map(session, lookback_weeks)
    holding_weeks, min_buffer_pct, max_buffer_pct = _holding_policy(session)
    report: list[dict] = []
    for line, item in lines:
        if dept_filter and _line_department(line, item) != dept_filter:
            continue
        on_hand = effective_on_hand(baseline_map, item.id, line.on_hand)
        sold = item_qty_sold(qty_map, item.id)
        if sold == 0:
            continue
        _, under_qty, _, _ = stock_position_from_holding_policy(
            on_hand,
            sold,
            lookback_weeks,
            holding_weeks,
            min_buffer_pct,
            max_buffer_pct,
        )
        if under_qty >= 0:
            continue
        cost = effective_unit_cost(line, item)
        units_under = math.ceil(abs(under_qty))
        report.append(
            {
                "sku": item.sku,
                "name": item.name[:80],
                "dept": line.dept or item.department or "—",
                "on_hand": on_hand,
                "units_under": units_under,
                "unit_cost": cost,
                "purchase_cost": units_under * cost,
            }
        )

    report.sort(key=lambda row: row["purchase_cost"], reverse=True)
    return report


def overstocked_report(
    session: Session,
    lookback_weeks: int = DEFAULT_LOOKBACK_WEEKS,
    *,
    dept_filter: str | None = None,
) -> list[dict]:
    lines = get_lookback_period_lines(session, lookback_weeks)
    if not lines:
        return []

    baseline_map = baseline_qty_map(session, [item.id for _, item in lines])
    qty_map = build_multi_batch_qty_map(session, lookback_weeks)
    holding_weeks, min_buffer_pct, max_buffer_pct = _holding_policy(session)
    report: list[dict] = []
    for line, item in lines:
        if dept_filter and _line_department(line, item) != dept_filter:
            continue
        on_hand = effective_on_hand(baseline_map, item.id, line.on_hand)
        sold = item_qty_sold(qty_map, item.id)
        if sold == 0:
            continue
        category, over_qty, under_qty, _ = item_stock_health(
            on_hand,
            sold,
            lookback_weeks=lookback_weeks,
            holding_weeks=holding_weeks,
            min_buffer_pct=min_buffer_pct,
            max_buffer_pct=max_buffer_pct,
        )
        if category != "overstocked":
            continue
        cost = effective_unit_cost(line, item)
        units_over = math.ceil(over_qty)
        report.append(
            {
                "sku": item.sku,
                "name": item.name[:80],
                "dept": line.dept or item.department or "—",
                "on_hand": on_hand,
                "units_over": units_over,
                "unit_cost": cost,
                "excess_value": units_over * cost,
            }
        )

    report.sort(key=lambda row: row["excess_value"], reverse=True)
    return report


def abc_report(
    session: Session,
    lookback_weeks: int = DEFAULT_LOOKBACK_WEEKS,
    *,
    dept_filter: str | None = None,
) -> list[dict]:
    lines = get_lookback_period_lines(session, lookback_weeks)
    if not lines:
        return []

    baseline_map = baseline_qty_map(session, [item.id for _, item in lines])
    qty_map = build_multi_batch_qty_map(session, lookback_weeks)
    rows: list[dict] = []
    for line, item in lines:
        if dept_filter and _line_department(line, item) != dept_filter:
            continue
        cost = effective_unit_cost(line, item)
        sold = item_qty_sold(qty_map, item.id)
        sales_val = sold * cost
        qty = effective_on_hand(baseline_map, item.id, line.on_hand)
        rows.append(
            {
                "sku": item.sku,
                "name": item.name[:80],
                "dept": line.dept or item.department or "—",
                "qty_sold": sold,
                "sales_value": sales_val,
                "on_hand": qty,
                "stock_value": stock_value(qty, cost),
            }
        )

    rows.sort(key=lambda row: row["sales_value"], reverse=True)
    total_sales = sum(row["sales_value"] for row in rows)
    if total_sales <= 0:
        for row in rows:
            row["cumulative_pct"] = 0.0
            row["abc_class"] = "C"
        return rows

    cumulative = 0.0
    for row in rows:
        cumulative += row["sales_value"]
        share = cumulative / total_sales
        row["cumulative_pct"] = share * 100
        if share <= 0.80:
            row["abc_class"] = "A"
        elif share <= 0.95:
            row["abc_class"] = "B"
        else:
            row["abc_class"] = "C"
    return rows


def abc_summary(
    session: Session,
    report: list[dict] | None = None,
    lookback_weeks: int = DEFAULT_LOOKBACK_WEEKS,
) -> dict[str, int]:
    rows = report if report is not None else abc_report(session, lookback_weeks)
    summary = {"A": 0, "B": 0, "C": 0}
    for row in rows:
        summary[row["abc_class"]] += 1
    return summary


def report_period_label(session: Session, lookback_weeks: int = DEFAULT_LOOKBACK_WEEKS) -> str:
    from stock_analysis.analytics.dashboard import _latest_turn_batch

    batch = _latest_turn_batch(session)
    if not batch:
        return "Latest period"
    if lookback_weeks == 1 and batch.period_end:
        return f"As of {batch.period_end}"
    if batch.period_end:
        return f"Last {lookback_weeks} week(s) (as of {batch.period_end})"
    return f"Last {lookback_weeks} week(s)"