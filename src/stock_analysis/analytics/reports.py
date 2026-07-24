"""Slow-moving and ABC classification reports."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from stock_analysis.analytics.dashboard import build_period_summary, get_period_lines
from stock_analysis.db.models import BaselineItem, ImportBatch, Item


def _baseline_qty_map(session: Session, item_ids: list[int]) -> dict[int, float]:
    if not item_ids:
        return {}
    rows = session.scalars(
        select(BaselineItem).where(BaselineItem.item_id.in_(item_ids))
    ).all()
    return {row.item_id: row.qty_on_hand for row in rows}


def slow_moving_report(session: Session, batch_id: int | None = None) -> list[dict]:
    lines = get_period_lines(session, batch_id)
    if not lines:
        return []

    baseline_map = _baseline_qty_map(session, [item.id for _, item in lines])
    report: list[dict] = []
    for line, item in lines:
        qty = baseline_map.get(item.id, line.on_hand)
        if line.qty_sold_90 != 0 or qty <= 0:
            continue
        cost = line.last_unit_cost or item.unit_cost or 0
        report.append(
            {
                "sku": item.sku,
                "name": item.name[:80],
                "dept": line.dept or item.department or "—",
                "on_hand": qty,
                "unit_cost": cost,
                "stock_value": qty * cost,
                "qty_sold_90": line.qty_sold_90,
            }
        )

    report.sort(key=lambda row: row["stock_value"], reverse=True)
    return report


def abc_report(session: Session, batch_id: int | None = None) -> list[dict]:
    lines = get_period_lines(session, batch_id)
    if not lines:
        return []

    baseline_map = _baseline_qty_map(session, [item.id for _, item in lines])
    rows: list[dict] = []
    for line, item in lines:
        cost = line.last_unit_cost or item.unit_cost or 0
        sales_value = line.qty_sold_90 * cost
        qty = baseline_map.get(item.id, line.on_hand)
        rows.append(
            {
                "sku": item.sku,
                "name": item.name[:80],
                "dept": line.dept or item.department or "—",
                "qty_sold_90": line.qty_sold_90,
                "sales_value": sales_value,
                "on_hand": qty,
                "stock_value": qty * cost,
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
        previous = cumulative
        cumulative += row["sales_value"]
        row["cumulative_pct"] = (cumulative / total_sales) * 100
        if previous / total_sales < 0.80:
            row["abc_class"] = "A"
        elif cumulative / total_sales <= 0.95:
            row["abc_class"] = "B"
        else:
            row["abc_class"] = "C"
    return rows


def abc_summary(
    session: Session, batch_id: int | None = None, report: list[dict] | None = None
) -> dict[str, int]:
    rows = report if report is not None else abc_report(session, batch_id)
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
