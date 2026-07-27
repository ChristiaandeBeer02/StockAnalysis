"""Slow-moving and ABC classification reports."""

from __future__ import annotations

from sqlalchemy.orm import Session

from stock_analysis.analytics.dashboard import build_period_summary, get_period_lines
from stock_analysis.analytics.lookback import (
    DEFAULT_LOOKBACK,
    build_prior_qty_map,
    qty_sold,
)
from stock_analysis.analytics.metrics import (
    effective_on_hand,
    effective_unit_cost,
    stock_value,
)
from stock_analysis.analytics.queries import baseline_qty_map
from stock_analysis.db.models import ImportBatch


def slow_moving_report(
    session: Session,
    batch_id: int | None = None,
    lookback_days: int = DEFAULT_LOOKBACK,
) -> list[dict]:
    lines = get_period_lines(session, batch_id)
    if not lines:
        return []

    baseline_map = baseline_qty_map(session, [item.id for _, item in lines])
    prior_map, lookback_60_source = build_prior_qty_map(session, batch_id)
    use_two_period_60 = lookback_days == 60 and lookback_60_source == "two_period"
    report: list[dict] = []
    for line, item in lines:
        qty = effective_on_hand(baseline_map, item.id, line.on_hand)
        sold = qty_sold(
            line,
            lookback_days,
            prior_qty_30=prior_map.get(item.id, 0.0),
            use_two_period_60=use_two_period_60,
        )
        if sold != 0 or qty <= 0:
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


def abc_report(
    session: Session,
    batch_id: int | None = None,
    lookback_days: int = DEFAULT_LOOKBACK,
) -> list[dict]:
    lines = get_period_lines(session, batch_id)
    if not lines:
        return []

    baseline_map = baseline_qty_map(session, [item.id for _, item in lines])
    prior_map, lookback_60_source = build_prior_qty_map(session, batch_id)
    use_two_period_60 = lookback_days == 60 and lookback_60_source == "two_period"
    rows: list[dict] = []
    for line, item in lines:
        cost = effective_unit_cost(line, item)
        sold = qty_sold(
            line,
            lookback_days,
            prior_qty_30=prior_map.get(item.id, 0.0),
            use_two_period_60=use_two_period_60,
        )
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
    batch_id: int | None = None,
    report: list[dict] | None = None,
    lookback_days: int = DEFAULT_LOOKBACK,
) -> dict[str, int]:
    rows = report if report is not None else abc_report(session, batch_id, lookback_days)
    summary = {"A": 0, "B": 0, "C": 0}
    for row in rows:
        summary[row["abc_class"]] += 1
    return summary


def report_period_label(session: Session, batch_id: int | None = None) -> str:
    if batch_id:
        batch = session.get(ImportBatch, batch_id)
    else:
        summary = build_period_summary(session)
        if not summary:
            return "Latest period"
        return f"{summary.get('period_start', '')} – {summary.get('period_end', '')}"

    if batch and batch.period_start and batch.period_end:
        return f"{batch.period_start} – {batch.period_end}"
    return batch.file_name if batch else "Latest period"
