"""Shared analytics query helpers."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from stock_analysis.db.models import BaselineItem


def baseline_qty_map(session: Session, item_ids: list[int]) -> dict[int, float]:
    if not item_ids:
        return {}
    rows = session.scalars(
        select(BaselineItem).where(BaselineItem.item_id.in_(item_ids))
    ).all()
    return {row.item_id: row.qty_on_hand for row in rows}
