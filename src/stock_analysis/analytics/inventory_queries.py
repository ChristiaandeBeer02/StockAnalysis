"""Inventory list queries."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from stock_analysis.analytics.dashboard import get_period_lines
from stock_analysis.analytics.department_names import display_dept
from stock_analysis.analytics.metrics import effective_unit_cost
from stock_analysis.analytics.lookback import (
    DEFAULT_LOOKBACK,
    build_prior_qty_map,
    qty_sold,
    sold_column_label,
)
from stock_analysis.db.models import BaselineItem, Item, PeriodTurnLine
from stock_analysis.importers.item_filters import item_status, should_skip_item


def inventory_headers(lookback_days: int = DEFAULT_LOOKBACK) -> list[str]:
    return [
        "SKU",
        "Description",
        "Dept",
        "On Hand",
        "Unit Cost",
        sold_column_label(lookback_days),
        "Status",
    ]


INVENTORY_HEADERS = inventory_headers()


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
    nickname_map: dict[str, str] | None = None,
    batch_id: int | None = None,
    lookback_days: int = DEFAULT_LOOKBACK,
) -> list[list[str]]:
    lines = get_period_lines(session, batch_id) if has_enrichment else []
    turn_by_item: dict[int, PeriodTurnLine] = {item.id: line for line, item in lines}
    prior_map, lookback_60_source = build_prior_qty_map(session, batch_id)
    use_two_period_60 = lookback_days == 60 and lookback_60_source == "two_period"

    query = _base_query(has_enrichment, search, status, dept)
    rows_db = session.execute(query).all()

    display_rows: list[list[str]] = []
    for item, baseline in rows_db:
        if should_skip_item(item.sku, item.name):
            continue
        turn = turn_by_item.get(item.id)
        unit_cost = effective_unit_cost(turn, item)
        qty = baseline.qty_on_hand
        unit_cost_str = f"{unit_cost:.2f}" if unit_cost else "—"
        dept_label = display_dept(item.department or (turn.dept if turn else None), nickname_map)
        if turn:
            sold = qty_sold(
                turn,
                lookback_days,
                prior_qty_30=prior_map.get(item.id, 0.0),
                use_two_period_60=use_two_period_60,
            )
            sold_label = f"{sold:g}"
        else:
            sold_label = "—"
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
                sold_label,
                status_label,
            ]
        )

    return display_rows
