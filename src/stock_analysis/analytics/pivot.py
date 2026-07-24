"""Pivot table exploration over period turn data."""

from __future__ import annotations

import pandas as pd
from sqlalchemy.orm import Session

from stock_analysis.analytics.dashboard import get_period_lines

ROW_FIELDS = {
    "Department": "dept",
    "Supplier": "supplier",
    "SKU": "sku",
}

VALUE_FIELDS = {
    "Qty Sold (90d)": "qty_sold_90",
    "Stock Value": "stock_value",
    "On Hand": "on_hand",
    "Overstock Qty (3mo)": "over_stock_qty_3mo",
    "Understock Qty (3mo)": "under_stock_qty_3mo",
}


def build_pivot(
    session: Session,
    row_field: str,
    value_field: str,
    batch_id: int | None = None,
) -> tuple[list[str], list[list[str]]]:
    lines = get_period_lines(session, batch_id)
    if not lines:
        return [], []

    row_key = ROW_FIELDS.get(row_field, "dept")
    value_key = VALUE_FIELDS.get(value_field, "qty_sold_90")

    records = []
    for line, item in lines:
        cost = line.last_unit_cost or item.unit_cost or 0
        on_hand = line.on_hand
        records.append(
            {
                "dept": line.dept or item.department or "Unknown",
                "supplier": line.supplier or item.supplier or "Unknown",
                "sku": item.sku,
                "qty_sold_90": line.qty_sold_90,
                "stock_value": on_hand * cost,
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
