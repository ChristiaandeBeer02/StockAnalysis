"""Dashboard analytics from period turn data."""

from __future__ import annotations

import json

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from stock_analysis.analytics.department_names import display_dept
from stock_analysis.analytics.metrics import (
    DEFAULT_OPTIMUM_STOCK_MONTHS,
    effective_on_hand,
    effective_unit_cost,
    gross_margin_pct,
    markup_pct,
    sales_value,
    stock_position_from_line,
    stock_position_from_weekly_sales,
    stock_health_category,
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
from stock_analysis.analytics.queries import baseline_qty_map, get_optimum_stock_months
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


def _line_stock_levels(
    line: PeriodTurnLine,
    on_hand: float,
    optimum_months: float,
) -> tuple[float, float]:
    return stock_position_from_line(line, on_hand, optimum_months)


def _weekly_under_qty(
    on_hand: float,
    sold: float,
    lookback_weeks: int,
    optimum_months: float,
) -> float:
    _, under_qty = stock_position_from_weekly_sales(
        on_hand, sold, lookback_weeks, optimum_months
    )
    return under_qty


def _item_dept(line: PeriodTurnLine | None, item: Item) -> str:
    return (line.dept if line else None) or item.department or "Unknown"


def _build_ranked_sales_rows(
    session: Session,
    lines: list[tuple[PeriodTurnLine, Item]],
    qty_map: dict[int, float],
    *,
    limit: int | None = None,
) -> list[tuple[PeriodTurnLine | None, Item, float]]:
    line_by_item = {item.id: (line, item) for line, item in lines}
    ranked_ids = sorted(
        (item_id for item_id, qty in qty_map.items() if qty > 0),
        key=lambda item_id: qty_map[item_id],
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
    optimum_months = get_optimum_stock_months(session)
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
    overstock_value = 0.0
    understock_value = 0.0
    slow_moving_value = 0.0
    slow_moving_items: list[dict] = []
    dept_overstock_values: dict[str, float] = {}
    dept_slow_moving_values: dict[str, float] = {}
    for line, item in lines:
        on_hand = effective_on_hand(baseline_map, item.id, line.on_hand)
        over_qty, _ = _line_stock_levels(line, on_hand, optimum_months)
        cost = effective_unit_cost(line, item)
        sold = _item_qty(line, item.id)
        under_qty = _weekly_under_qty(on_hand, sold, lookback_weeks, optimum_months)
        dept = _item_dept(line, item)
        total_sales += sold
        total_sales_value += sales_value(sold, cost)
        category = stock_health_category(
            under_qty=under_qty, over_qty=over_qty, sold=sold, on_hand=on_hand
        )
        if category == "understocked":
            understock_items += 1
            understock_value += abs(under_qty) * cost
        elif category == "slow_moving":
            slow_moving += 1
            item_slow_value = on_hand * cost
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
                    "dept": line.dept or item.department or "Unknown",
                    "unit_cost": cost,
                    "stock_value": stock_value(on_hand, cost),
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
        }
        for _line, item, qty in _build_ranked_sales_rows(
            session, lines, qty_map, limit=20
        )
    ]

    reorder_alerts = []
    for line, item in lines:
        on_hand = effective_on_hand(baseline_map, item.id, line.on_hand)
        sold = _item_qty(line, item.id)
        under_qty = _weekly_under_qty(on_hand, sold, lookback_weeks, optimum_months)
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
        over_qty, _ = _line_stock_levels(line, on_hand, optimum_months)
        sold = _item_qty(line, item.id)
        under_qty = _weekly_under_qty(on_hand, sold, lookback_weeks, optimum_months)
        if over_qty <= 0 or under_qty < 0 or sold == 0:
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
        revenue, profit = item_sales_totals(sales_totals, item.id)
        if revenue <= 0:
            continue
        margin_alerts.append(
            {
                "code": item.sku,
                "name": item.name[:40],
                "dept": line.dept or item.department or "Unknown",
                "qty_sold": _item_qty(line, item.id),
                "gross_margin_pct": gross_margin_pct(profit, revenue),
                "gross_profit": profit,
            }
        )
    margin_alerts.sort(key=lambda x: x["gross_margin_pct"])

    markup_alerts = []
    for line, item in lines:
        revenue, profit = item_sales_totals(sales_totals, item.id)
        if revenue <= 0:
            continue
        cost = revenue - profit
        if cost <= 0:
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
                "markup_pct": markup_pct(profit, cost),
            }
        )
    markup_alerts.sort(key=lambda x: x["markup_pct"])

    sales_items = [
        {
            "code": item.sku,
            "name": item.name[:40],
            "dept": (line.dept if line else None) or item.department or "Unknown",
            "qty_sold": qty,
            "sales_value": sales_value(qty, effective_unit_cost(line, item)),
        }
        for line, item, qty in _build_ranked_sales_rows(session, lines, qty_map)
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
        "overstock_value": overstock_value,
        "understock_value": understock_value,
        "slow_moving_value": slow_moving_value,
        "dept_values": dept_values,
        "dept_overstock_values": dept_overstock_values,
        "dept_slow_moving_values": dept_slow_moving_values,
        "top_sellers": top_seller_data,
        "sales_items": sales_items,
        "reorder_alerts": reorder_alerts,
        "overstock_alerts": overstock_alerts,
        "margin_alerts": margin_alerts,
        "markup_alerts": markup_alerts,
        "slow_moving_items": slow_moving_items,
        "stock_health": build_stock_health_breakdown_from_lines(
            lines, baseline_map, optimum_months, qty_map, lookback_weeks
        ),
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
    optimum_months: float = DEFAULT_OPTIMUM_STOCK_MONTHS,
    qty_map: dict[int, float] | None = None,
    lookback_weeks: int = DEFAULT_LOOKBACK_WEEKS,
) -> dict[str, int]:
    qty_map = qty_map or {}
    counts = {"Understocked": 0, "Overstocked": 0, "Slow Moving": 0, "Healthy": 0}
    for line, item in lines:
        on_hand = effective_on_hand(baseline_map, item.id, line.on_hand)
        over_qty, _ = _line_stock_levels(line, on_hand, optimum_months)
        sold = item_qty_sold(qty_map, item.id)
        under_qty = _weekly_under_qty(on_hand, sold, lookback_weeks, optimum_months)
        category = stock_health_category(
            under_qty=under_qty, over_qty=over_qty, sold=sold, on_hand=on_hand
        )
        if category == "understocked":
            counts["Understocked"] += 1
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

    if len(_stock_batches_for_position(session)) < 2:
        return {}
    current = get_period_summary_cached(
        session, lookback_weeks, stock_batch_offset=0, sales_batch_offset=0
    )
    previous = get_period_summary_cached(
        session, lookback_weeks, stock_batch_offset=1, sales_batch_offset=1
    )
    result = {}
    for key in ("overstock_value", "understock_value", "slow_moving_value", "total_sales_value"):
        cur = current.get(key, 0)
        prev = previous.get(key, 0)
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


def _format_delta(pct: float | None) -> tuple[str, str]:
    if pct is None:
        return "—", "neutral"
    arrow = "▲" if pct > 0 else "▼" if pct < 0 else "—"
    direction = "up" if pct > 0 else "down" if pct < 0 else "neutral"
    return f"{arrow} {abs(pct):.1f}% vs prior", direction


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
    optimum_months = get_optimum_stock_months(session)
    on_hand = baseline.qty_on_hand
    for line, batch in history:
        label = _period_label(line, batch)
        line_over, line_under = _line_stock_levels(line, on_hand, optimum_months)
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
        over_qty, under_qty = stock_position_from_weekly_sales(
            on_hand, selected_qty_sold, lookback_weeks, optimum_months
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
