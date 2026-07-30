"""Inventory list queries."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from stock_analysis.analytics.dashboard import (
    _item_dept,
    get_lookback_period_lines,
)
from stock_analysis.analytics.department_names import display_dept
from stock_analysis.analytics.metrics import (
    effective_unit_cost,
    item_stock_health,
    stock_value,
)
from stock_analysis.analytics.lookback import (
    DEFAULT_LOOKBACK_WEEKS,
    build_multi_batch_qty_map,
    item_qty_sold,
    sold_column_label,
)
from stock_analysis.analytics.queries import (
    get_holding_weeks,
    get_stock_buffer_pct_range,
)
from stock_analysis.db.models import BaselineItem, Item, PeriodTurnLine
from stock_analysis.importers.item_filters import item_status, should_skip_item


def inventory_headers(lookback_weeks: int = DEFAULT_LOOKBACK_WEEKS) -> list[str]:
    return [
        "SKU",
        "Description",
        "Dept",
        "On Hand",
        "Unit Cost",
        sold_column_label(lookback_weeks),
        "Status",
    ]


INVENTORY_HEADERS = inventory_headers()


def _status_clause(status: str, has_enrichment: bool):
    if status == "Deprecated":
        return Item.is_deprecated.is_(True)
    if status == "Active":
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


def _empty_inventory_summary() -> dict:
    return {
        "item_count": 0,
        "total_value": 0.0,
        "understock_count": 0,
        "overstock_count": 0,
        "slow_moving_count": 0,
        "dead_stock_count": 0,
        "understock_value": 0.0,
        "overstock_value": 0.0,
        "slow_moving_value": 0.0,
        "dead_stock_value": 0.0,
        "dept_values": {},
        "dept_overstock_values": {},
        "dept_slow_moving_values": {},
        "dept_dead_stock_values": {},
        "stock_health": {
            "Understocked": 0,
            "Dead Stock": 0,
            "Overstocked": 0,
            "Slow Moving": 0,
            "Healthy": 0,
            "No movement data": 0,
        },
    }


def load_inventory_view_data(
    session: Session,
    *,
    search: str,
    status: str,
    has_enrichment: bool,
    dept: str | None = None,
    nickname_map: dict[str, str] | None = None,
    lookback_weeks: int = DEFAULT_LOOKBACK_WEEKS,
) -> tuple[list[list[str]], dict]:
    """Load table rows and overview summary in a single catalog pass."""
    lines = get_lookback_period_lines(session, lookback_weeks) if has_enrichment else []
    turn_by_item: dict[int, PeriodTurnLine] = {item.id: line for line, item in lines}
    qty_map = build_multi_batch_qty_map(session, lookback_weeks) if has_enrichment else {}

    summary = _empty_inventory_summary()
    dept_values = summary["dept_values"]
    dept_overstock_values = summary["dept_overstock_values"]
    dept_slow_moving_values = summary["dept_slow_moving_values"]
    dept_dead_stock_values = summary["dept_dead_stock_values"]
    health = summary["stock_health"]
    holding_weeks = get_holding_weeks(session)
    min_buffer_pct, max_buffer_pct = get_stock_buffer_pct_range(session)

    query = _base_query(has_enrichment, search, status, dept)
    rows_db = session.execute(query).all()

    display_rows: list[list[str]] = []
    for item, baseline in rows_db:
        if should_skip_item(item.sku, item.name):
            continue

        turn = turn_by_item.get(item.id)
        effective_dept = _item_dept(turn, item)
        if dept and effective_dept != dept:
            continue
        unit_cost = effective_unit_cost(turn, item)
        qty = baseline.qty_on_hand
        value = stock_value(qty, unit_cost)
        summary["item_count"] += 1
        summary["total_value"] += value
        dept_key = effective_dept
        dept_values[dept_key] = dept_values.get(dept_key, 0) + value

        unit_cost_str = f"{unit_cost:.2f}" if unit_cost else "—"
        dept_label = display_dept(item.department or (turn.dept if turn else None), nickname_map)
        if turn:
            sold = item_qty_sold(qty_map, item.id)
            sold_label = f"{sold:g}"
        else:
            sold = 0.0
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

        if not turn:
            if has_enrichment:
                health["No movement data"] += 1
            continue

        category, over_qty, under_qty, _cover = item_stock_health(
            qty,
            sold,
            lookback_weeks=lookback_weeks,
            holding_weeks=holding_weeks,
            min_buffer_pct=min_buffer_pct,
            max_buffer_pct=max_buffer_pct,
        )
        if category == "understocked":
            summary["understock_count"] += 1
            summary["understock_value"] += abs(under_qty) * unit_cost
            health["Understocked"] += 1
        elif category == "dead":
            summary["dead_stock_count"] += 1
            item_dead_value = qty * unit_cost
            summary["dead_stock_value"] += item_dead_value
            dept_dead_stock_values[dept_key] = (
                dept_dead_stock_values.get(dept_key, 0) + item_dead_value
            )
            health["Dead Stock"] += 1
        elif category == "slow_moving":
            summary["slow_moving_count"] += 1
            item_slow_value = over_qty * unit_cost
            summary["slow_moving_value"] += item_slow_value
            dept_slow_moving_values[dept_key] = (
                dept_slow_moving_values.get(dept_key, 0) + item_slow_value
            )
            health["Slow Moving"] += 1
        elif category == "overstocked":
            summary["overstock_count"] += 1
            item_over_value = over_qty * unit_cost
            summary["overstock_value"] += item_over_value
            dept_overstock_values[dept_key] = (
                dept_overstock_values.get(dept_key, 0) + item_over_value
            )
            health["Overstocked"] += 1
        elif category == "healthy":
            health["Healthy"] += 1

    return display_rows, summary


def list_inventory_departments(
    session: Session,
    *,
    search: str,
    status: str,
    has_enrichment: bool,
    lookback_weeks: int = DEFAULT_LOOKBACK_WEEKS,
) -> list[str]:
    """Distinct effective department codes for the current search/status filters."""
    lines = get_lookback_period_lines(session, lookback_weeks) if has_enrichment else []
    turn_by_item: dict[int, PeriodTurnLine] = {item.id: line for line, item in lines}
    query = _base_query(has_enrichment, search, status, dept=None)
    departments: set[str] = set()
    for item, _baseline in session.execute(query).all():
        if should_skip_item(item.sku, item.name):
            continue
        turn = turn_by_item.get(item.id)
        departments.add(_item_dept(turn, item))
    return sorted(departments)


def list_item_departments(session: Session, *, status: str = "Active") -> list[str]:
    query = (
        select(Item.department)
        .join(BaselineItem, BaselineItem.item_id == Item.id)
        .where(Item.department.isnot(None))
        .distinct()
        .order_by(Item.department)
    )
    clause = _status_clause(status, has_enrichment=True)
    if clause is not None:
        query = query.where(clause)
    return [dept for dept in session.scalars(query).all() if dept]


def fetch_inventory_rows(
    session: Session,
    *,
    search: str,
    status: str,
    has_enrichment: bool,
    dept: str | None = None,
    nickname_map: dict[str, str] | None = None,
    lookback_weeks: int = DEFAULT_LOOKBACK_WEEKS,
) -> list[list[str]]:
    rows, _ = load_inventory_view_data(
        session,
        search=search,
        status=status,
        has_enrichment=has_enrichment,
        dept=dept,
        nickname_map=nickname_map,
        lookback_weeks=lookback_weeks,
    )
    return rows
