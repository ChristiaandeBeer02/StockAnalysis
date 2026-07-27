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
    sales_value,
    stock_position_from_line,
    stock_health_category,
    stock_value,
)
from stock_analysis.analytics.lookback import (
    DEFAULT_LOOKBACK,
    build_prior_qty_map,
    qty_sold,
)
from stock_analysis.analytics.queries import baseline_qty_map, get_optimum_stock_months
from stock_analysis.db.models import AnalysisResult, BaselineItem, ImportBatch, Item, PeriodTurnLine
from stock_analysis.importers.item_filters import should_skip_item


def _latest_turn_batch(session: Session) -> ImportBatch | None:
    return session.scalar(
        select(ImportBatch)
        .where(ImportBatch.import_type.in_(["baseline_enrichment", "period_turn"]))
        .order_by(desc(ImportBatch.imported_at))
        .limit(1)
    )


def list_period_batches(session: Session) -> list[dict]:
    batches = session.scalars(
        select(ImportBatch)
        .where(ImportBatch.import_type.in_(["baseline_enrichment", "period_turn"]))
        .order_by(desc(ImportBatch.imported_at))
    ).all()
    result = []
    for batch in batches:
        label = batch.file_name
        if batch.period_start and batch.period_end:
            label = f"{batch.period_start} – {batch.period_end}"
        if batch.import_type == "baseline_enrichment":
            label = f"{label} (enrichment)"
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


def get_period_lines(
    session: Session, batch_id: int | None = None
) -> list[tuple[PeriodTurnLine, Item]]:
    if batch_id is None:
        batch = _latest_turn_batch(session)
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


def get_latest_period_lines(session: Session) -> list[tuple[PeriodTurnLine, Item]]:
    return get_period_lines(session, None)


def _line_stock_levels(
    line: PeriodTurnLine,
    on_hand: float,
    optimum_months: float,
) -> tuple[float, float]:
    return stock_position_from_line(line, on_hand, optimum_months)


def build_period_summary(
    session: Session,
    batch_id: int | None = None,
    lookback_days: int = DEFAULT_LOOKBACK,
) -> dict:
    if batch_id is None:
        batch = _latest_turn_batch(session)
    else:
        batch = session.get(ImportBatch, batch_id)

    lines = get_period_lines(session, batch.id if batch else None)
    if not batch or not lines:
        return {}

    item_ids = [item.id for _, item in lines]
    baseline_map = baseline_qty_map(session, item_ids)
    optimum_months = get_optimum_stock_months(session)
    prior_map, lookback_60_source = build_prior_qty_map(session, batch.id)
    use_two_period_60 = lookback_days == 60 and lookback_60_source == "two_period"

    def _item_qty(line: PeriodTurnLine, item_id: int) -> float:
        return qty_sold(
            line,
            lookback_days,
            prior_qty_30=prior_map.get(item_id, 0.0),
            use_two_period_60=use_two_period_60,
        )

    total_sales = 0.0
    total_sales_value = 0.0
    overstock_items = 0
    understock_items = 0
    slow_moving = 0
    overstock_value = 0.0
    understock_value = 0.0
    slow_moving_value = 0.0
    slow_moving_items: list[dict] = []
    for line, item in lines:
        on_hand = effective_on_hand(baseline_map, item.id, line.on_hand)
        over_qty, under_qty = _line_stock_levels(line, on_hand, optimum_months)
        cost = effective_unit_cost(line, item)
        sold = _item_qty(line, item.id)
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
            slow_moving_value += on_hand * cost
            slow_moving_items.append(
                {
                    "code": item.sku,
                    "name": item.name[:40],
                    "on_hand": on_hand,
                    "qty_sold": sold,
                    "dept": line.dept or item.department or "Unknown",
                    "stock_value": stock_value(on_hand, cost),
                }
            )
        elif category == "overstocked":
            overstock_items += 1
            overstock_value += over_qty * cost

    dept_values: dict[str, float] = {}
    for line, item in lines:
        dept = line.dept or "Unknown"
        on_hand = effective_on_hand(baseline_map, item.id, line.on_hand)
        cost = effective_unit_cost(line, item)
        dept_values[dept] = dept_values.get(dept, 0) + stock_value(on_hand, cost)

    top_sellers = sorted(
        lines, key=lambda x: _item_qty(x[0], x[1].id), reverse=True
    )[:20]
    top_seller_data = [
        {
            "code": item.sku,
            "name": item.name[:40],
            "qty_sold": _item_qty(line, item.id),
        }
        for line, item in top_sellers
        if _item_qty(line, item.id) > 0
    ]

    reorder_alerts = []
    for line, item in lines:
        on_hand = effective_on_hand(baseline_map, item.id, line.on_hand)
        over_qty, under_qty = _line_stock_levels(line, on_hand, optimum_months)
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
        over_qty, under_qty = _line_stock_levels(line, on_hand, optimum_months)
        sold = _item_qty(line, item.id)
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

    sales_items = [
        {
            "code": item.sku,
            "name": item.name[:40],
            "dept": line.dept or item.department or "Unknown",
            "qty_sold": _item_qty(line, item.id),
            "sales_value": sales_value(_item_qty(line, item.id), effective_unit_cost(line, item)),
        }
        for line, item in lines
        if _item_qty(line, item.id) > 0
    ]
    sales_items.sort(key=lambda x: x["qty_sold"], reverse=True)

    return {
        "batch_id": batch.id,
        "period_start": batch.period_start,
        "period_end": batch.period_end,
        "import_type": batch.import_type,
        "lookback_days": lookback_days,
        "lookback_60_source": lookback_60_source if lookback_days == 60 else None,
        "total_sales": total_sales,
        "total_sales_value": total_sales_value,
        "overstock_items": overstock_items,
        "understock_items": understock_items,
        "slow_moving": slow_moving,
        "overstock_value": overstock_value,
        "understock_value": understock_value,
        "slow_moving_value": slow_moving_value,
        "dept_values": dept_values,
        "top_sellers": top_seller_data,
        "sales_items": sales_items,
        "reorder_alerts": reorder_alerts,
        "overstock_alerts": overstock_alerts,
        "slow_moving_items": slow_moving_items,
        "stock_health": build_stock_health_breakdown_from_lines(
            lines, baseline_map, optimum_months, lookback_days, prior_map, use_two_period_60
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


def get_item_turn_history(session: Session, item_id: int) -> list[PeriodTurnLine]:
    return list(
        session.scalars(
            select(PeriodTurnLine)
            .where(PeriodTurnLine.item_id == item_id)
            .order_by(desc(PeriodTurnLine.id))
        ).all()
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


def build_item_chart_data(session: Session, item_id: int) -> dict:
    history = get_item_turn_history_with_batches(session, item_id)
    if not history:
        return {}

    labels: list[str] = []
    qty_30: list[float] = []
    qty_90: list[float] = []
    qty_180: list[float] = []
    over_qty: list[float] = []
    under_qty: list[float] = []

    for line, batch in reversed(history):
        if batch and batch.period_start and batch.period_end:
            labels.append(f"{batch.period_start} – {batch.period_end}")
        else:
            labels.append(f"Import #{line.import_batch_id}")
        qty_30.append(line.qty_sold_30)
        qty_90.append(line.qty_sold_90)
        qty_180.append(line.qty_sold_180)
        over_qty.append(line.over_stock_qty_3mo)
        under_qty.append(line.under_stock_qty_3mo)

    return {
        "labels": labels,
        "qty_30": qty_30,
        "qty_90": qty_90,
        "qty_180": qty_180,
        "over_qty": over_qty,
        "under_qty": under_qty,
    }


def build_stock_health_breakdown_from_lines(
    lines: list[tuple[PeriodTurnLine, Item]],
    baseline_map: dict[int, float],
    optimum_months: float = DEFAULT_OPTIMUM_STOCK_MONTHS,
    lookback_days: int = DEFAULT_LOOKBACK,
    prior_map: dict[int, float] | None = None,
    use_two_period_60: bool = False,
) -> dict[str, int]:
    prior_map = prior_map or {}
    counts = {"Understocked": 0, "Overstocked": 0, "Slow Moving": 0, "Healthy": 0}
    for line, item in lines:
        on_hand = effective_on_hand(baseline_map, item.id, line.on_hand)
        over_qty, under_qty = _line_stock_levels(line, on_hand, optimum_months)
        sold = qty_sold(
            line,
            lookback_days,
            prior_qty_30=prior_map.get(item.id, 0.0),
            use_two_period_60=use_two_period_60,
        )
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


def build_stock_health_breakdown(
    session: Session,
    batch_id: int | None = None,
    lookback_days: int = DEFAULT_LOOKBACK,
) -> dict[str, int]:
    lines = get_period_lines(session, batch_id)
    if not lines:
        return {}
    item_ids = [item.id for _, item in lines]
    baseline_map = baseline_qty_map(session, item_ids)
    optimum_months = get_optimum_stock_months(session)
    prior_map, lookback_60_source = build_prior_qty_map(session, batch_id)
    use_two_period_60 = lookback_days == 60 and lookback_60_source == "two_period"
    return build_stock_health_breakdown_from_lines(
        lines,
        baseline_map,
        optimum_months,
        lookback_days,
        prior_map,
        use_two_period_60,
    )


def build_period_comparison(
    session: Session,
    batch_id: int | None,
    lookback_days: int = DEFAULT_LOOKBACK,
) -> dict:
    batches = [b for b in list_period_batches(session) if b["import_type"] == "period_turn"]
    if not batch_id or not batches:
        return {}
    ids = [b["id"] for b in batches]
    if batch_id not in ids:
        return {}
    idx = ids.index(batch_id)
    if idx + 1 >= len(ids):
        return {}
    current = build_period_summary(session, batch_id, lookback_days)
    previous = build_period_summary(session, ids[idx + 1], lookback_days)
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
    batch_id: int | None,
    lookback_days: int = DEFAULT_LOOKBACK,
) -> str | None:
    from stock_analysis.analytics.reports import abc_report

    for row in abc_report(session, batch_id, lookback_days):
        if row["sku"] == sku:
            return row["abc_class"]
    return None


def _period_label(line: PeriodTurnLine, batch: ImportBatch | None) -> str:
    if batch and batch.period_start and batch.period_end:
        return f"{batch.period_start} – {batch.period_end}"
    return f"Import #{line.import_batch_id}"


def build_item_summary(
    session: Session,
    item_id: int,
    batch_id: int | None = None,
    nickname_map: dict[str, str] | None = None,
    lookback_days: int = DEFAULT_LOOKBACK,
) -> dict:
    item = session.get(Item, item_id)
    baseline = session.scalar(
        select(BaselineItem).where(BaselineItem.item_id == item_id)
    )
    if not item or not baseline:
        return {}

    history = get_item_turn_history_with_batches(session, item_id)
    chart_data = build_item_chart_data(session, item_id)

    if batch_id is None and history:
        batch_id = history[0][0].import_batch_id

    selected_line = None
    selected_batch = None
    for line, batch in history:
        if line.import_batch_id == batch_id:
            selected_line = line
            selected_batch = batch
            break
    if selected_line is None and history:
        selected_line, selected_batch = history[0]

    batches = []
    seen: set[int] = set()
    for line, batch in history:
        if line.import_batch_id in seen:
            continue
        seen.add(line.import_batch_id)
        batches.append(
            {
                "id": line.import_batch_id,
                "label": _period_label(line, batch),
            }
        )

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
                "qty_30": line.qty_sold_30,
                "qty_90": line.qty_sold_90,
                "qty_180": line.qty_sold_180,
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
    qty_sold_30 = over_qty = under_qty = 0.0
    selected_qty_sold = 0.0

    if selected_line:
        qty_sold_30 = selected_line.qty_sold_30
        qty_sold_90 = selected_line.qty_sold_90
        qty_sold_180 = selected_line.qty_sold_180
        over_qty, under_qty = _line_stock_levels(selected_line, on_hand, optimum_months)
        prior_map, lookback_60_source = build_prior_qty_map(session, batch_id)
        use_two_period_60 = lookback_days == 60 and lookback_60_source == "two_period"
        selected_qty_sold = qty_sold(
            selected_line,
            lookback_days,
            prior_qty_30=prior_map.get(item_id, 0.0),
            use_two_period_60=use_two_period_60,
        )
        if qty_sold_30 <= qty_sold_90 <= qty_sold_180:
            mid = max(0.0, selected_line.qty_sold_90 - selected_line.qty_sold_30)
            tail = max(0.0, selected_line.qty_sold_180 - selected_line.qty_sold_90)
            sales_mix = {
                "30d": selected_line.qty_sold_30,
                "31–90d": mid,
                "91–180d": tail,
            }
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
        "supplier": item.supplier or "—",
        "is_deprecated": item.is_deprecated,
        "not_in_turn_report": item.not_in_turn_report,
        "on_hand": baseline.qty_on_hand,
        "unit_cost": unit_cost,
        "stock_value": stock_val,
        "qty_sold_30": qty_sold_30,
        "qty_sold": selected_qty_sold,
        "lookback_days": lookback_days,
        "over_qty": over_qty,
        "under_qty": under_qty,
        "abc_class": _item_abc_class(session, item.sku, batch_id, lookback_days)
        if history
        else None,
        "sales_mix_pie": sales_mix,
        "stock_position_pie": stock_position,
        "period_health_pie": period_health,
        "chart_data": chart_data,
        "history_rows": history_rows,
        "available_batches": batches,
        "selected_batch_id": batch_id,
        "period_label": _period_label(selected_line, selected_batch) if selected_line else "",
    }


def build_inventory_list_summary(
    session: Session,
    *,
    search: str,
    status: str,
    batch_id: int | None,
    has_enrichment: bool,
    dept: str | None = None,
    lookback_days: int = DEFAULT_LOOKBACK,
) -> dict:
    from stock_analysis.analytics.inventory_queries import iter_filtered_items

    lines = get_period_lines(session, batch_id) if has_enrichment else []
    turn_by_item = {item.id: line for line, item in lines}

    item_count = 0
    total_value = 0.0
    understock_count = 0
    overstock_count = 0
    slow_moving_count = 0
    understock_value = 0.0
    overstock_value = 0.0
    slow_moving_value = 0.0
    dept_values: dict[str, float] = {}
    health = {"Understocked": 0, "Overstocked": 0, "Slow Moving": 0, "Healthy": 0}

    optimum_months = get_optimum_stock_months(session)
    prior_map, lookback_60_source = build_prior_qty_map(session, batch_id)
    use_two_period_60 = lookback_days == 60 and lookback_60_source == "two_period"

    for item, baseline in iter_filtered_items(
        session, search, status, has_enrichment, dept=dept
    ):
        if should_skip_item(item.sku, item.name):
            continue
        item_count += 1
        turn = turn_by_item.get(item.id)
        cost = effective_unit_cost(turn, item)
        qty = baseline.qty_on_hand
        value = stock_value(qty, cost)
        total_value += value
        dept_key = item.department or "Unknown"
        dept_values[dept_key] = dept_values.get(dept_key, 0) + value

        if not turn:
            continue
        over_qty, under_qty = _line_stock_levels(turn, qty, optimum_months)
        sold = qty_sold(
            turn,
            lookback_days,
            prior_qty_30=prior_map.get(item.id, 0.0),
            use_two_period_60=use_two_period_60,
        )
        category = stock_health_category(
            under_qty=under_qty, over_qty=over_qty, sold=sold, on_hand=qty
        )
        if category == "understocked":
            understock_count += 1
            understock_value += abs(under_qty) * cost
            health["Understocked"] += 1
        elif category == "slow_moving":
            slow_moving_count += 1
            slow_moving_value += qty * cost
            health["Slow Moving"] += 1
        elif category == "overstocked":
            overstock_count += 1
            overstock_value += over_qty * cost
            health["Overstocked"] += 1
        elif category == "healthy":
            health["Healthy"] += 1

    return {
        "item_count": item_count,
        "total_value": total_value,
        "understock_count": understock_count,
        "overstock_count": overstock_count,
        "slow_moving_count": slow_moving_count,
        "understock_value": understock_value,
        "overstock_value": overstock_value,
        "slow_moving_value": slow_moving_value,
        "dept_values": dept_values,
        "stock_health": health,
    }
