"""Project on-hand quantities after applying movement without persisting."""

from __future__ import annotations

from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from stock_analysis.db.models import BaselineItem, Item
from stock_analysis.importers.item_filters import should_skip_item
from stock_analysis.importers.movement_parser import MovementRow


def compute_new_qty(
    opening: float,
    row: MovementRow,
    *,
    direction: Literal["forward", "backward"],
) -> float:
    if direction == "backward":
        return opening + row.net_sales_qty - row.net_purchases_qty
    return opening - row.net_sales_qty + row.net_purchases_qty


def project_post_movement_on_hand(
    session: Session,
    rows: list[MovementRow],
    *,
    direction: Literal["forward", "backward"] = "forward",
) -> dict[str, tuple[float, str]]:
    sku_map = {item.sku: item for item in session.scalars(select(Item))}
    baseline_map = {row.item_id: row for row in session.scalars(select(BaselineItem))}

    projected: dict[str, tuple[float, str]] = {}
    for item in sku_map.values():
        if should_skip_item(item.sku, item.name) or item.is_deprecated:
            continue
        baseline = baseline_map.get(item.id)
        if baseline is not None:
            projected[item.sku] = (baseline.qty_on_hand, item.name)

    for row in rows:
        if should_skip_item(row.code, row.description):
            continue
        item = sku_map.get(row.code)
        opening = baseline_map[item.id].qty_on_hand if item and item.id in baseline_map else 0.0
        name = row.description or (item.name if item else "")
        new_qty = compute_new_qty(opening, row, direction=direction)
        projected[row.code] = (new_qty, name)

    return projected
