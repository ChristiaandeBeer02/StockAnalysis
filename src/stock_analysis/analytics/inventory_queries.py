"""Inventory list queries."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from stock_analysis.analytics.dashboard import get_latest_period_lines
from stock_analysis.db.models import BaselineItem, Item, PeriodTurnLine
from stock_analysis.importers.item_filters import item_status, should_skip_item

INVENTORY_HEADERS = ["SKU", "Description", "Dept", "On Hand", "Unit Cost", "Sold 90d", "Status"]


def _status_clause(status: str, has_enrichment: bool):
    if status == "Deprecated":
        return Item.is_deprecated.is_(True)
    if status == "No turn data":
        if not has_enrichment:
            return Item.id == -1
        return Item.is_deprecated.is_(False), Item.not_in_turn_report.is_(True)
    if status == "Active":
        if has_enrichment:
            return Item.is_deprecated.is_(False), Item.not_in_turn_report.is_(False)
        return Item.is_deprecated.is_(False)
    return None


def base_inventory_query(has_enrichment: bool, search: str, status: str, dept: str | None = None):
    query = (
        select(Item, BaselineItem)
        .join(BaselineItem, BaselineItem.item_id == Item.id)
        .order_by(Item.sku)
    )
    if search:
        pattern = f"%{search}%"
        query = query.where(or_(Item.sku.ilike(pattern), Item.name.ilike(pattern)))
    if dept:
        query = query.where(Item.department == dept)
    clause = _status_clause(status, has_enrichment)
    if clause is not None:
        if isinstance(clause, tuple):
            query = query.where(*clause)
        else:
            query = query.where(clause)
    return query


def iter_filtered_items(
    session: Session,
    search: str,
    status: str,
    has_enrichment: bool,
    dept: str | None = None,
) -> Iterator[tuple[Item, BaselineItem]]:
    query = base_inventory_query(has_enrichment, search, status, dept)
    for row in session.execute(query).all():
        yield row[0], row[1]


def _base_query(has_enrichment: bool, search: str, status: str, dept: str | None = None):
    return base_inventory_query(has_enrichment, search, status, dept)


def fetch_inventory_rows(
    session: Session,
    *,
    search: str,
    status: str,
    has_enrichment: bool,
    dept: str | None = None,
) -> list[list[str]]:
    lines = get_latest_period_lines(session)
    latest_turn: dict[int, PeriodTurnLine] = {item.id: line for line, item in lines}

    query = _base_query(has_enrichment, search, status, dept)
    rows_db = session.execute(query).all()

    display_rows: list[list[str]] = []
    for item, baseline in rows_db:
        if should_skip_item(item.sku, item.name):
            continue
        unit_cost = item.unit_cost
        qty = baseline.qty_on_hand
        unit_cost_str = f"{unit_cost:.2f}" if unit_cost else "—"
        turn = latest_turn.get(item.id)
        dept_label = item.department or (turn.dept if turn else "—")
        sold_90 = f"{turn.qty_sold_90:g}" if turn else "—"
        status_label = item_status(
            is_deprecated=item.is_deprecated,
            not_in_turn_report=item.not_in_turn_report,
            has_enrichment=has_enrichment,
        )
        display_rows.append(
            [
                item.sku,
                item.name[:80],
                dept_label,
                f"{qty:g}",
                unit_cost_str,
                sold_90,
                status_label,
            ]
        )

    return display_rows
