"""Dashboard analytics from period turn data."""

from __future__ import annotations

import json

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from stock_analysis.analytics.department_names import display_dept
from stock_analysis.analytics.metrics import (
    effective_on_hand,
    effective_unit_cost,
    item_stock_health,
    resolve_sales_value,
    stock_position_from_holding_policy,
    stock_value,
)
from stock_analysis.analytics.lookback import (
    DEFAULT_LOOKBACK_WEEKS,
    SALES_BATCH_TYPES,
    build_multi_batch_qty_map,
    build_multi_batch_sales_totals,
    get_batch_ids_for_weeks,
    item_qty_sold,
    item_sales_totals,
    list_sales_batches,
)
from stock_analysis.analytics.queries import (
    baseline_qty_map,
    get_holding_weeks,
    get_stock_buffer_pct_range,
)
from stock_analysis.db.models import AnalysisResult, BaselineItem, ImportBatch, Item, PeriodTurnLine
from stock_analysis.importers.item_filters import should_skip_item


def _latest_turn_batch(session: Session) -> ImportBatch | None:
    return session.scalar(
        select(ImportBatch)
        .where(ImportBatch.import_type.in_(SALES_BATCH_TYPES))
        .order_by(desc(ImportBatch.imported_at))
        .limit(1)
    )


PERIOD_STOCK_BATCH_TYPES = ("period_turn", "period_turn_backdate")


def _stock_batches_for_position(session: Session) -> list[ImportBatch]:
    """Movement batches used for stock-level lines; prefer period imports over enrichment."""
    batches = list_sales_batches(session)
    period_batches = [batch for batch in batches if batch.import_type in PERIOD_STOCK_BATCH_TYPES]
    return period_batches if period_batches else batches


def _stock_batch(session: Session, offset: int = 0) -> ImportBatch | None:
    batches = _stock_batches_for_position(session)
    if offset < len(batches):
        return batches[offset]
    return None


def list_period_batches(session: Session) -> list[dict]:
    batches = session.scalars(
        select(ImportBatch)
        .where(
            ImportBatch.import_type.in_(
                ["baseline_enrichment", "period_turn", "period_turn_backdate"]
            )
        )
        .order_by(desc(ImportBatch.imported_at))
    ).all()
    result = []
    for batch in batches:
        label = batch.file_name
        if batch.period_start and batch.period_end:
            label = f"{batch.period_start} – {batch.period_end}"
        if batch.import_type == "baseline_enrichment":
            label = f"{label} (enrichment)"
        elif batch.import_type == "period_turn_backdate":
            label = f"{label} (backdate)"
        result.append(
            {
                "id": batch.id,
                "label": label,
                "import_type": batch.import_type,
                "period_start": batch.period_start,
                "period_end": batch.period_end,
            }
        )
    return result


def get_lookback_period_lines(
    session: Session,
    lookback_weeks: int = DEFAULT_LOOKBACK_WEEKS,
    *,
    batch_offset: int = 0,
) -> list[tuple[PeriodTurnLine, Item]]:
    """Merge period turn lines across the sales lookback window (newest batch wins per item)."""
    batch_ids = get_batch_ids_for_weeks(session, lookback_weeks, offset=batch_offset)
    if not batch_ids:
        return get_period_lines(session, None, stock_batch_offset=batch_offset)

    rows = list(
        session.execute(
            select(PeriodTurnLine, Item)
            .join(Item, Item.id == PeriodTurnLine.item_id)
            .where(PeriodTurnLine.import_batch_id.in_(batch_ids))
            .where(Item.is_deprecated.is_(False))
        ).all()
    )
    batch_rank = {batch_id: index for index, batch_id in enumerate(batch_ids)}
    merged: dict[int, tuple[PeriodTurnLine, Item]] = {}
    for line, item in rows:
        existing = merged.get(item.id)
        if existing is None:
            merged[item.id] = (line, item)
            continue
        if batch_rank.get(line.import_batch_id, len(batch_ids)) < batch_rank.get(
            existing[0].import_batch_id, len(batch_ids)
        ):
            merged[item.id] = (line, item)

    return list(merged.values())


def get_period_lines(
    session: Session,
    batch_id: int | None = None,
    *,
    stock_batch_offset: int = 0,
) -> list[tuple[PeriodTurnLine, Item]]:
    if batch_id is None:
        batch = _stock_batch(session, stock_batch_offset) or _latest_turn_batch(session)
        if not batch:
            return []
        batch_id = batch.id

    return list(
        session.execute(
            select(PeriodTurnLine, Item)
            .join(Item, Item.id == PeriodTurnLine.item_id)
            .where(PeriodTurnLine.import_batch_id == batch_id)
            .where(Item.is_deprecated.is_(False))
        ).all()
    )


def _holding_position(
    on_hand: float,
    sold: float,
    period_weeks: int,
    holding_weeks: int,
    min_buffer_pct: float,
    max_buffer_pct: float,
) -> tuple[float, float]:
    over_qty, under_qty, _, _ = stock_position_from_holding_policy(
        on_hand,
        sold,
        period_weeks,
        holding_weeks,
        min_buffer_pct,
        max_buffer_pct,
    )
    return over_qty, under_qty


def _item_dept(line: PeriodTurnLine | None, item: Item) -> str:
    return (line.dept if line else None) or item.department or "Unknown"


def _sales_row_sort_key(
    item: Item,
    item_id: int,
    qty_map: dict[int, float],
    sales_totals: dict[int, tuple[float, float]],
    sort_by: str,
) -> float | tuple[float, float]:
    qty = qty_map[item_id]
    if sort_by == "profit":
        _revenue, profit = item_sales_totals(sales_totals, item_id)
        return (profit, qty)
    return qty


def _build_ranked_sales_rows(
    session: Session,
    lines: list[tuple[PeriodTurnLine, Item]],
    qty_map: dict[int, float],
    *,
    sales_totals: dict[int, tuple[float, float]] | None = None,
    sort_by: str = "qty",
    limit: int | None = None,
) -> list[tuple[PeriodTurnLine | None, Item, float]]:
    line_by_item = {item.id: (line, item) for line, item in lines}
    totals = sales_totals or {}
    item_by_id = {item.id: item for _, item in lines}
    missing_item_ids = [item_id for item_id in qty_map if item_id not in item_by_id]
    if missing_item_ids:
        for item in session.scalars(select(Item).where(Item.id.in_(missing_item_ids))):
            item_by_id[item.id] = item

    def _rank_key(item_id: int):
        item = item_by_id.get(item_id)
        if item is None:
            return 0.0
        return _sales_row_sort_key(item, item_id, qty_map, totals, sort_by)

    ranked_ids = sorted(
        (item_id for item_id, qty in qty_map.items() if qty > 0),
        key=_rank_key,
        reverse=True,
    )
    if limit is not None:
        ranked_ids = ranked_ids[:limit]

    missing_ids = [item_id for item_id in ranked_ids if item_id not in line_by_item]
    extra_items: dict[int, Item] = {}
    if missing_ids:
        for item in session.scalars(select(Item).where(Item.id.in_(missing_ids))):
            extra_items[item.id] = item

    rows: list[tuple[PeriodTurnLine | None, Item, float]] = []
    for item_id in ranked_ids:
        qty = qty_map[item_id]
        if item_id in line_by_item:
            line, item = line_by_item[item_id]
        else:
            item = extra_items.get(item_id)
            if item is None:
                continue
            line = None
        rows.append((line, item, qty))
    return rows


def build_period_summary(
    session: Session,
    lookback_weeks: int = DEFAULT_LOOKBACK_WEEKS,
    *,
    stock_batch_offset: int = 0,
    sales_batch_offset: int = 0,
) -> dict:
    batch = _stock_batch(session, stock_batch_offset) or _latest_turn_batch(session)
    lines = get_lookback_period_lines(
        session, lookback_weeks, batch_offset=sales_batch_offset
    )
    if not batch or not lines:
        return {}

    item_ids = [item.id for _, item in lines]
    baseline_map = baseline_qty_map(session, item_ids)
    holding_weeks = get_holding_weeks(session)
    min_buffer_pct, max_buffer_pct = get_stock_buffer_pct_range(session)
    qty_map = build_multi_batch_qty_map(
        session, lookback_weeks, offset=sales_batch_offset
    )
    sales_totals = build_multi_batch_sales_totals(
        session, lookback_weeks, offset=sales_batch_offset
    )

    def _item_qty(_line: PeriodTurnLine, item_id: int) -> float:
        return item_qty_sold(qty_map, item_id)

    total_sales = 0.0
    total_sales_value = 0.0
    overstock_items = 0
    understock_items = 0
    slow_moving = 0
    dead_stock = 0
    overstock_value = 0.0
    understock_value = 0.0
    slow_moving_value = 0.0
    dead_stock_value = 0.0
    slow_moving_items: list[dict] = []
    dead_stock_items: list[dict] = []
    dept_overstock_values: dict[str, float] = {}
    dept_slow_moving_values: dict[str, float] = {}
    dept_dead_stock_values: dict[str, float] = {}
    for line, item in lines:
        on_hand = effective_on_hand(baseline_map, item.id, line.on_hand)
        sold = _item_qty(line, item.id)
        category, over_qty, under_qty, cover = item_stock_health(
            on_hand,
            sold,
            lookback_weeks=lookback_weeks,
            holding_weeks=holding_weeks,
            min_buffer_pct=min_buffer_pct,
            max_buffer_pct=max_buffer_pct,
        )
        cost = effective_unit_cost(line, item)
        dept = _item_dept(line, item)
        total_sales += sold
        revenue, _profit = item_sales_totals(sales_totals, item.id)
        total_sales_value += resolve_sales_value(
            revenue, sold, unit_price=item.unit_price
        )
        if category == "understocked":
            understock_items += 1
            understock_value += abs(under_qty) * cost
        elif category == "dead":
            dead_stock += 1
            item_dead_value = stock_value(on_hand, cost)
            dead_stock_value += item_dead_value
            dept_dead_stock_values[dept] = (
                dept_dead_stock_values.get(dept, 0) + item_dead_value
            )
            dead_stock_items.append(
                {
                    "code": item.sku,
                    "name": item.name[:40],
                    "on_hand": on_hand,
                    "qty_sold": sold,
                    "dept": line.dept or item.department or "Unknown",
                    "unit_cost": cost,
                    "stock_value": item_dead_value,
                }
            )
        elif category == "slow_moving":
            slow_moving += 1
            item_slow_value = over_qty * cost
            slow_moving_value += item_slow_value
            dept_slow_moving_values[dept] = (
                dept_slow_moving_values.get(dept, 0) + item_slow_value
            )
            slow_moving_items.append(
                {
                    "code": item.sku,
                    "name": item.name[:40],
                    "on_hand": on_hand,
                    "qty_sold": sold,
                    "over_qty": over_qty,
                    "weeks_cover": cover,
                    "dept": line.dept or item.department or "Unknown",
                    "unit_cost": cost,
                    "excess_value": item_slow_value,
                }
            )
        elif category == "overstocked":
            overstock_items += 1
            item_over_value = over_qty * cost
            overstock_value += item_over_value
            dept_overstock_values[dept] = (
                dept_overstock_values.get(dept, 0) + item_over_value
            )

    dept_values: dict[str, float] = {}
    for line, item in lines:
        dept = _item_dept(line, item)
        on_hand = effective_on_hand(baseline_map, item.id, line.on_hand)
        cost = effective_unit_cost(line, item)
        dept_values[dept] = dept_values.get(dept, 0) + stock_value(on_hand, cost)

    top_seller_data = [
        {
            "code": item.sku,
            "name": item.name[:40],
            "qty_sold": qty,
            "gross_profit": item_sales_totals(sales_totals, item.id)[1],
        }
        for _line, item, qty in _build_ranked_sales_rows(
            session,
            lines,
            qty_map,
            sales_totals=sales_totals,
            sort_by="profit",
            limit=20,
        )
    ]

    reorder_alerts = []
    for line, item in lines:
        on_hand = effective_on_hand(baseline_map, item.id, line.on_hand)
        sold = _item_qty(line, item.id)
        over_qty, under_qty = _holding_position(
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
        reorder_alerts.append(
            {
                "code": item.sku,
                "name": item.name[:40],
                "on_hand": on_hand,
                "under_qty": under_qty,
                "under_value": under_qty * cost,
                "dept": line.dept or item.department or "Unknown",
                "qty_sold": _item_qty(line, item.id),
            }
        )
    reorder_alerts.sort(key=lambda x: x["under_value"])

    overstock_alerts = []
    for line, item in lines:
        on_hand = effective_on_hand(baseline_map, item.id, line.on_hand)
        sold = _item_qty(line, item.id)
        over_qty, under_qty = _holding_position(
            on_hand,
            sold,
            lookback_weeks,
            holding_weeks,
            min_buffer_pct,
            max_buffer_pct,
        )
        if over_qty <= 0 or under_qty < 0 or sold == 0:
            continue
        category, _, _, cover = item_stock_health(
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
        overstock_alerts.append(
            {
                "code": item.sku,
                "name": item.name[:40],
                "on_hand": on_hand,
                "over_qty": over_qty,
                "over_value": over_qty * cost,
                "dept": line.dept or item.department or "Unknown",
                "qty_sold": _item_qty(line, item.id),
            }
        )
    overstock_alerts.sort(key=lambda x: x["over_value"], reverse=True)

    margin_alerts = []
    for line, item in lines:
        if item.gross_margin_pct is None:
            continue
        revenue, profit = item_sales_totals(sales_totals, item.id)
        if revenue <= 0:
            continue
        margin_alerts.append(
            {
                "code": item.sku,
                "name": item.name[:40],
                "dept": line.dept or item.department or "Unknown",
                "qty_sold": _item_qty(line, item.id),
                "gross_margin_pct": item.gross_margin_pct,
                "gross_profit": profit,
            }
        )
    margin_alerts.sort(key=lambda x: x["gross_margin_pct"])

    markup_alerts = []
    for line, item in lines:
        if item.markup_pct is None:
            continue
        on_hand = effective_on_hand(baseline_map, item.id, line.on_hand)
        unit_cost = effective_unit_cost(line, item)
        markup_alerts.append(
            {
                "code": item.sku,
                "name": item.name[:40],
                "dept": line.dept or item.department or "Unknown",
                "on_hand": on_hand,
                "unit_cost": unit_cost,
                "markup_pct": item.markup_pct,
            }
        )
    markup_alerts.sort(key=lambda x: x["markup_pct"])

    sales_items = [
        {
            "code": item.sku,
            "name": item.name[:40],
            "dept": (line.dept if line else None) or item.department or "Unknown",
            "qty_sold": qty,
            "sales_value": resolve_sales_value(
                item_sales_totals(sales_totals, item.id)[0],
                qty,
                unit_price=item.unit_price,
            ),
            "gross_profit": item_sales_totals(sales_totals, item.id)[1],
        }
        for line, item, qty in _build_ranked_sales_rows(
            session, lines, qty_map, sales_totals=sales_totals, sort_by="qty"
        )
    ]

    return {
        "batch_id": batch.id,
        "period_start": batch.period_start,
        "period_end": batch.period_end,
        "import_type": batch.import_type,
        "lookback_weeks": lookback_weeks,
        "total_sales": total_sales,
        "total_sales_value": total_sales_value,
        "overstock_items": overstock_items,
        "understock_items": understock_items,
        "slow_moving": slow_moving,
        "dead_stock": dead_stock,
        "overstock_value": overstock_value,
        "understock_value": understock_value,
        "slow_moving_value": slow_moving_value,
        "dead_stock_value": dead_stock_value,
        "dept_values": dept_values,
        "dept_overstock_values": dept_overstock_values,
        "dept_slow_moving_values": dept_slow_moving_values,
        "dept_dead_stock_values": dept_dead_stock_values,
        "top_sellers": top_seller_data,
        "sales_items": sales_items,
        "reorder_alerts": reorder_alerts,
        "overstock_alerts": overstock_alerts,
        "margin_alerts": margin_alerts,
        "markup_alerts": markup_alerts,
        "slow_moving_items": slow_moving_items,
        "dead_stock_items": dead_stock_items,
        "stock_health": build_stock_health_breakdown_from_lines(
            lines,
            baseline_map,
            qty_map,
            lookback_weeks,
            holding_weeks,
            min_buffer_pct,
            max_buffer_pct,
        ),
        "holding_weeks": holding_weeks,
        "stock_buffer_min_pct": min_buffer_pct,
        "stock_buffer_max_pct": max_buffer_pct,
    }


def save_analysis_result(session: Session, batch_id: int, import_type: str, summary: dict) -> None:
    batch = session.get(ImportBatch, batch_id)
    session.add(
        AnalysisResult(
            analysis_type="period_summary" if import_type == "period_turn" else "enrichment_summary",
            import_batch_id=batch_id,
            period_start=batch.period_start if batch else None,
            period_end=batch.period_end if batch else None,
            summary_json=json.dumps(summary),
        )
    )


def get_item_turn_history_with_batches(
    session: Session, item_id: int
) -> list[tuple[PeriodTurnLine, ImportBatch | None]]:
    return list(
        session.execute(
            select(PeriodTurnLine, ImportBatch)
            .outerjoin(ImportBatch, ImportBatch.id == PeriodTurnLine.import_batch_id)
            .where(PeriodTurnLine.item_id == item_id)
            .order_by(desc(PeriodTurnLine.id))
        ).all()
    )


def _chart_period_label(line: PeriodTurnLine, batch: ImportBatch | None) -> str:
    if batch and batch.period_start and batch.period_end:
        return f"{batch.period_start}\n{batch.period_end}"
    return f"Import #{line.import_batch_id}"


def build_item_sales_chart_data(
    session: Session, item_id: int, lookback_weeks: int
) -> dict:
    history = get_item_turn_history_with_batches(session, item_id)
    if not history:
        return {}

    batch_ids = get_batch_ids_for_weeks(session, lookback_weeks)
    if not batch_ids:
        return {}

    by_batch = {line.import_batch_id: (line, batch) for line, batch in history}
    labels: list[str] = []
    period_keys: list[str] = []
    qty_sold: list[float] = []

    for batch_id in reversed(batch_ids):
        if batch_id not in by_batch:
            continue
        line, batch = by_batch[batch_id]
        labels.append(_chart_period_label(line, batch))
        period_keys.append(_period_label(line, batch))
        qty_sold.append(line.qty_sold_90)

    if not labels:
        return {}

    return {
        "labels": labels,
        "period_keys": period_keys,
        "qty_sold": qty_sold,
    }


def build_item_stock_chart_data(session: Session, item_id: int) -> dict:
    history = get_item_turn_history_with_batches(session, item_id)
    if not history:
        return {}

    labels: list[str] = []
    over_qty: list[float] = []
    under_qty: list[float] = []

    for line, batch in reversed(history):
        labels.append(_chart_period_label(line, batch))
        over_qty.append(line.over_stock_qty_3mo)
        under_qty.append(line.under_stock_qty_3mo)

    return {
        "labels": labels,
        "over_qty": over_qty,
        "under_qty": under_qty,
    }


def build_stock_health_breakdown_from_lines(
    lines: list[tuple[PeriodTurnLine, Item]],
    baseline_map: dict[int, float],
    qty_map: dict[int, float] | None = None,
    lookback_weeks: int = DEFAULT_LOOKBACK_WEEKS,
    holding_weeks: int = 2,
    min_buffer_pct: float = 20.0,
    max_buffer_pct: float = 30.0,
) -> dict[str, int]:
    qty_map = qty_map or {}
    counts = {
        "Understocked": 0,
        "Dead Stock": 0,
        "Overstocked": 0,
        "Slow Moving": 0,
        "Healthy": 0,
    }
    for line, item in lines:
        on_hand = effective_on_hand(baseline_map, item.id, line.on_hand)
        sold = item_qty_sold(qty_map, item.id)
        category, _, _, _ = item_stock_health(
            on_hand,
            sold,
            lookback_weeks=lookback_weeks,
            holding_weeks=holding_weeks,
            min_buffer_pct=min_buffer_pct,
            max_buffer_pct=max_buffer_pct,
        )
        if category == "understocked":
            counts["Understocked"] += 1
        elif category == "dead":
            counts["Dead Stock"] += 1
        elif category == "slow_moving":
            counts["Slow Moving"] += 1
        elif category == "overstocked":
            counts["Overstocked"] += 1
        elif category == "healthy":
            counts["Healthy"] += 1
    return counts


def build_period_comparison(
    session: Session,
    lookback_weeks: int = DEFAULT_LOOKBACK_WEEKS,
) -> dict:
    from stock_analysis.analytics.cache import get_period_summary_cached

    weeks = max(1, lookback_weeks)
    if weeks < 2 or len(list_sales_batches(session)) < weeks:
        return {}
    older = get_period_summary_cached(
        session, 1, stock_batch_offset=weeks - 1, sales_batch_offset=weeks - 1
    )
    newer = get_period_summary_cached(
        session, 1, stock_batch_offset=weeks - 2, sales_batch_offset=weeks - 2
    )
    if not older or not newer:
        return {}
    result = {}
    for key in (
        "overstock_value",
        "understock_value",
        "slow_moving_value",
        "dead_stock_value",
        "total_sales_value",
    ):
        cur = newer.get(key, 0)
        prev = older.get(key, 0)
        if prev:
            result[f"{key}_delta_pct"] = ((cur - prev) / prev) * 100
        else:
            result[f"{key}_delta_pct"] = None
    return result


def filter_stock_rows(
    rows: list[dict],
    *,
    dept: str | None = None,
    sku: str | None = None,
) -> list[dict]:
    result = rows
    if dept:
        result = [r for r in result if r.get("dept") == dept]
    if sku:
        result = [r for r in result if r.get("code") == sku]
    return result


def _format_delta(
    pct: float | None, lookback_weeks: int | None = None
) -> tuple[str, str]:
    if pct is None:
        return "—", "neutral"
    arrow = "▲" if pct > 0 else "▼" if pct < 0 else "—"
    direction = "up" if pct > 0 else "down" if pct < 0 else "neutral"
    vs = f"vs {lookback_weeks}w ago" if lookback_weeks else "vs prior"
    return f"{arrow} {abs(pct):.1f}% {vs}", direction


def _item_abc_class(
    session: Session,
    sku: str,
    lookback_weeks: int = DEFAULT_LOOKBACK_WEEKS,
) -> str | None:
    cache_key = f"_abc_class_map_{lookback_weeks}"
    if cache_key not in session.info:
        from stock_analysis.analytics.reports import abc_report

        report = abc_report(session, lookback_weeks)
        session.info[cache_key] = {row["sku"]: row["abc_class"] for row in report}
    return session.info[cache_key].get(sku)


def _period_label(line: PeriodTurnLine, batch: ImportBatch | None) -> str:
    if batch and batch.period_start and batch.period_end:
        return f"{batch.period_start} – {batch.period_end}"
    return f"Import #{line.import_batch_id}"


def build_item_summary(
    session: Session,
    item_id: int,
    nickname_map: dict[str, str] | None = None,
    lookback_weeks: int = DEFAULT_LOOKBACK_WEEKS,
) -> dict:
    item = session.get(Item, item_id)
    baseline = session.scalar(
        select(BaselineItem).where(BaselineItem.item_id == item_id)
    )
    if not item or not baseline:
        return {}

    history = get_item_turn_history_with_batches(session, item_id)
    sales_chart_data = build_item_sales_chart_data(session, item_id, lookback_weeks)
    stock_chart_data = build_item_stock_chart_data(session, item_id)

    latest_batch = _latest_turn_batch(session)
    selected_line = None
    selected_batch = None
    if latest_batch:
        for line, batch in history:
            if line.import_batch_id == latest_batch.id:
                selected_line = line
                selected_batch = batch
                break
    if selected_line is None and history:
        selected_line, selected_batch = history[0]

    history_rows = []
    period_health = {"Balanced": 0, "Overstocked": 0, "Understocked": 0}
    holding_weeks = get_holding_weeks(session)
    min_buffer_pct, max_buffer_pct = get_stock_buffer_pct_range(session)
    on_hand = baseline.qty_on_hand
    for line, batch in history:
        label = _period_label(line, batch)
        line_over, line_under = _holding_position(
            on_hand,
            line.qty_sold_90,
            period_weeks=1,
            holding_weeks=holding_weeks,
            min_buffer_pct=min_buffer_pct,
            max_buffer_pct=max_buffer_pct,
        )
        if line_under < 0:
            status = "Understocked"
        elif line_over > 0:
            status = "Overstocked"
        else:
            status = "Balanced"
        history_rows.append(
            {
                "period": label,
                "qty_sold": line.qty_sold_90,
                "over_qty": line_over,
                "under_qty": line_under,
                "unit_cost": line.last_unit_cost,
                "status": status,
            }
        )
        period_health[status] += 1

    unit_cost = effective_unit_cost(selected_line, item)
    stock_val = stock_value(baseline.qty_on_hand, unit_cost)

    sales_mix: dict[str, float] = {}
    stock_position: dict[str, float] = {}
    over_qty = under_qty = 0.0
    selected_qty_sold = 0.0

    if selected_line:
        qty_map = build_multi_batch_qty_map(session, lookback_weeks)
        selected_qty_sold = item_qty_sold(qty_map, item_id)
        over_qty, under_qty = _holding_position(
            on_hand,
            selected_qty_sold,
            lookback_weeks,
            holding_weeks,
            min_buffer_pct,
            max_buffer_pct,
        )
        by_batch = {line.import_batch_id: (line, batch) for line, batch in history}
        for batch_id in reversed(get_batch_ids_for_weeks(session, lookback_weeks)):
            if batch_id not in by_batch:
                continue
            line, batch = by_batch[batch_id]
            qty = line.qty_sold_90
            if qty > 0:
                sales_mix[_period_label(line, batch)] = qty
        if over_qty > 0:
            stock_position = {"At target": on_hand - over_qty, "Excess": over_qty}
        elif under_qty < 0:
            stock_position = {"On hand": on_hand, "Shortfall": abs(under_qty)}
        else:
            stock_position = {"On target": on_hand}

    dept_raw = item.department or (selected_line.dept if selected_line else None)

    return {
        "sku": item.sku,
        "name": item.name[:80],
        "department": display_dept(dept_raw, nickname_map),
        "department_code": item.department,
        "supplier": item.supplier or "—",
        "is_deprecated": item.is_deprecated,
        "not_in_turn_report": item.not_in_turn_report,
        "on_hand": baseline.qty_on_hand,
        "unit_cost": unit_cost,
        "stock_value": stock_val,
        "qty_sold": selected_qty_sold,
        "lookback_weeks": lookback_weeks,
        "over_qty": over_qty,
        "under_qty": under_qty,
        "abc_class": _item_abc_class(session, item.sku, lookback_weeks)
        if history
        else None,
        "sales_mix_pie": sales_mix,
        "stock_position_pie": stock_position,
        "period_health_pie": period_health,
        "sales_chart_data": sales_chart_data,
        "stock_chart_data": stock_chart_data,
        "history_rows": history_rows,
        "period_label": _period_label(selected_line, selected_batch) if selected_line else "",
    }


def build_inventory_list_summary(
    session: Session,
    *,
    search: str,
    status: str,
    has_enrichment: bool,
    dept: str | None = None,
    lookback_weeks: int = DEFAULT_LOOKBACK_WEEKS,
) -> dict:
    from stock_analysis.analytics.inventory_queries import load_inventory_view_data

    _, summary = load_inventory_view_data(
        session,
        search=search,
        status=status,
        has_enrichment=has_enrichment,
        dept=dept,
        lookback_weeks=lookback_weeks,
    )
    return summary
