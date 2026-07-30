"""Pivot table exploration over period turn data."""

from __future__ import annotations

import pandas as pd
from sqlalchemy.orm import Session

from stock_analysis.analytics.dashboard import get_lookback_period_lines
from stock_analysis.analytics.lookback import (
    DEFAULT_LOOKBACK_WEEKS,
    build_multi_batch_qty_map,
    item_qty_sold,
    pivot_qty_field_label,
)
from stock_analysis.analytics.metrics import effective_on_hand, effective_unit_cost, stock_value
from stock_analysis.analytics.queries import baseline_qty_map
from stock_analysis.analytics.reports import _line_department

ROW_FIELDS = {
    "Department": "dept",
    "Supplier": "supplier",
    "SKU": "sku",
}

STATIC_VALUE_FIELDS = {
    "Stock Value": "stock_value",
    "On Hand": "on_hand",
    "Overstock Qty (3mo)": "over_stock_qty_3mo",
    "Understock Qty (3mo)": "under_stock_qty_3mo",
}


def value_fields_for_lookback(lookback_weeks: int = DEFAULT_LOOKBACK_WEEKS) -> dict[str, str]:
    fields = {pivot_qty_field_label(lookback_weeks): "qty_sold"}
    fields.update(STATIC_VALUE_FIELDS)
    return fields


VALUE_FIELDS = value_fields_for_lookback()


def build_pivot(
    session: Session,
    row_field: str,
    value_field: str,
    nickname_map: dict[str, str] | None = None,
    lookback_weeks: int = DEFAULT_LOOKBACK_WEEKS,
    *,
    dept_filter: str | None = None,
) -> tuple[list[str], list[list[str]]]:
    del nickname_map  # reserved for row label display in UI exports
    lines = get_lookback_period_lines(session, lookback_weeks)
    if not lines:
        return [], []

    row_key = ROW_FIELDS.get(row_field, "dept")
    value_fields = value_fields_for_lookback(lookback_weeks)
    value_key = value_fields.get(value_field, "qty_sold")
    baseline_map = baseline_qty_map(session, [item.id for _, item in lines])
    qty_map = build_multi_batch_qty_map(session, lookback_weeks)

    records = []
    for line, item in lines:
        if dept_filter and _line_department(line, item) != dept_filter:
            continue
        cost = effective_unit_cost(line, item)
        on_hand = effective_on_hand(baseline_map, item.id, line.on_hand)
        records.append(
            {
                "dept": _line_department(line, item),
                "supplier": line.supplier or item.supplier or "Unknown",
                "sku": item.sku,
                "qty_sold": item_qty_sold(qty_map, item.id),
                "stock_value": stock_value(on_hand, cost),
                "on_hand": on_hand,
                "over_stock_qty_3mo": line.over_stock_qty_3mo,
                "under_stock_qty_3mo": line.under_stock_qty_3mo,
            }
        )

    frame = pd.DataFrame(records)
    if frame.empty:
        return [], []

    grouped = frame.groupby(row_key, dropna=False)[value_key].sum().sort_values(ascending=False)
    headers = [row_field, value_field]
    rows = [[str(index), f"{value:g}" if isinstance(value, float) else str(value)] for index, value in grouped.items()]
    return headers, rows
