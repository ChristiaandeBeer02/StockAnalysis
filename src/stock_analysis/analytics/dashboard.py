"""Dashboard analytics from period turn data."""

from __future__ import annotations

import json

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from stock_analysis.analytics.queries import baseline_qty_map
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


def build_period_summary(session: Session, batch_id: int | None = None) -> dict:
    if batch_id is None:
        batch = _latest_turn_batch(session)
    else:
        batch = session.get(ImportBatch, batch_id)

    lines = get_period_lines(session, batch.id if batch else None)
    if not batch or not lines:
        return {}

    item_ids = [item.id for _, item in lines]
    baseline_map = baseline_qty_map(session, item_ids)

    total_sales_90 = sum(line.qty_sold_90 for line, _ in lines)
    overstock_items = sum(1 for line, _ in lines if line.over_stock_qty_3mo > 0)
    understock_items = sum(1 for line, _ in lines if line.under_stock_qty_3mo < 0)
    slow_moving = 0
    for line, item in lines:
        qty = baseline_map.get(item.id, line.on_hand)
        if line.qty_sold_90 == 0 and qty > 0:
            slow_moving += 1

    dept_values: dict[str, float] = {}
    for line, item in lines:
        dept = line.dept or "Unknown"
        qty = baseline_map.get(item.id, line.on_hand)
        value = qty * (line.last_unit_cost or item.unit_cost or 0)
        dept_values[dept] = dept_values.get(dept, 0) + value

    top_sellers = sorted(lines, key=lambda x: x[0].qty_sold_90, reverse=True)[:20]
    top_seller_data = [
        {
            "code": item.sku,
            "name": item.name[:40],
            "qty_90": line.qty_sold_90,
        }
        for line, item in top_sellers
        if line.qty_sold_90 > 0
    ]

    reorder_alerts = [
        {
            "code": item.sku,
            "name": item.name[:40],
            "on_hand": line.on_hand,
            "under_qty": line.under_stock_qty_3mo,
            "under_value": line.under_stock_value_3mo,
            "dept": line.dept or item.department or "Unknown",
            "qty_sold_90": line.qty_sold_90,
        }
        for line, item in lines
        if line.under_stock_qty_3mo < 0
    ]
    reorder_alerts.sort(key=lambda x: x["under_value"])

    overstock_alerts = [
        {
            "code": item.sku,
            "name": item.name[:40],
            "on_hand": line.on_hand,
            "over_qty": line.over_stock_qty_3mo,
            "over_value": line.over_stock_qty_3mo
            * (line.last_unit_cost or item.unit_cost or 0),
            "dept": line.dept or item.department or "Unknown",
            "qty_sold_90": line.qty_sold_90,
        }
        for line, item in lines
        if line.over_stock_qty_3mo > 0
    ]
    overstock_alerts.sort(key=lambda x: x["over_value"], reverse=True)

    sales_items = [
        {
            "code": item.sku,
            "name": item.name[:40],
            "dept": line.dept or item.department or "Unknown",
            "qty_90": line.qty_sold_90,
            "sales_value": line.qty_sold_90 * (line.last_unit_cost or item.unit_cost or 0),
        }
        for line, item in lines
        if line.qty_sold_90 > 0
    ]
    sales_items.sort(key=lambda x: x["qty_90"], reverse=True)

    slow_moving_items = [
        {
            "code": item.sku,
            "name": item.name[:40],
            "on_hand": baseline_map.get(item.id, line.on_hand),
            "qty_sold_90": line.qty_sold_90,
            "dept": line.dept or item.department or "Unknown",
        }
        for line, item in lines
        if line.qty_sold_90 == 0 and baseline_map.get(item.id, line.on_hand) > 0
    ]

    return {
        "batch_id": batch.id,
        "period_start": batch.period_start,
        "period_end": batch.period_end,
        "import_type": batch.import_type,
        "total_sales_90": total_sales_90,
        "overstock_items": overstock_items,
        "understock_items": understock_items,
        "slow_moving": slow_moving,
        "dept_values": dept_values,
        "top_sellers": top_seller_data,
        "sales_items": sales_items,
        "reorder_alerts": reorder_alerts,
        "overstock_alerts": overstock_alerts,
        "slow_moving_items": slow_moving_items,
        "stock_health": build_stock_health_breakdown_from_lines(lines, baseline_map),
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
) -> dict[str, int]:
    counts = {"Understocked": 0, "Overstocked": 0, "Slow Moving": 0, "Healthy": 0}
    for line, item in lines:
        qty = baseline_map.get(item.id, line.on_hand)
        if line.under_stock_qty_3mo < 0:
            counts["Understocked"] += 1
        elif line.over_stock_qty_3mo > 0:
            counts["Overstocked"] += 1
        elif line.qty_sold_90 == 0 and qty > 0:
            counts["Slow Moving"] += 1
        elif qty > 0:
            counts["Healthy"] += 1
    return counts


def build_stock_health_breakdown(session: Session, batch_id: int | None = None) -> dict[str, int]:
    lines = get_period_lines(session, batch_id)
    if not lines:
        return {}
    item_ids = [item.id for _, item in lines]
    baseline_map = baseline_qty_map(session, item_ids)
    return build_stock_health_breakdown_from_lines(lines, baseline_map)


def build_period_comparison(session: Session, batch_id: int | None) -> dict:
    batches = [b for b in list_period_batches(session) if b["import_type"] == "period_turn"]
    if not batch_id or not batches:
        return {}
    ids = [b["id"] for b in batches]
    if batch_id not in ids:
        return {}
    idx = ids.index(batch_id)
    if idx + 1 >= len(ids):
        return {}
    current = build_period_summary(session, batch_id)
    previous = build_period_summary(session, ids[idx + 1])
    result = {}
    for key in ("overstock_items", "understock_items", "slow_moving", "total_sales_90"):
        cur = current.get(key, 0)
        prev = previous.get(key, 0)
        if prev:
            result[f"{key}_delta_pct"] = ((cur - prev) / prev) * 100
        elif cur:
            result[f"{key}_delta_pct"] = 100.0
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


def _item_abc_class(session: Session, sku: str, batch_id: int | None) -> str | None:
    from stock_analysis.analytics.reports import abc_report

    for row in abc_report(session, batch_id):
        if row["sku"] == sku:
            return row["abc_class"]
    return None


def _period_label(line: PeriodTurnLine, batch: ImportBatch | None) -> str:
    if batch and batch.period_start and batch.period_end:
        return f"{batch.period_start} – {batch.period_end}"
    return f"Import #{line.import_batch_id}"


def build_item_summary(session: Session, item_id: int, batch_id: int | None = None) -> dict:
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
    for line, batch in history:
        label = _period_label(line, batch)
        history_rows.append(
            {
                "period": label,
                "qty_30": line.qty_sold_30,
                "qty_90": line.qty_sold_90,
                "qty_180": line.qty_sold_180,
                "over_qty": line.over_stock_qty_3mo,
                "under_qty": line.under_stock_qty_3mo,
                "unit_cost": line.last_unit_cost,
                "status": (
                    "Understocked"
                    if line.under_stock_qty_3mo < 0
                    else "Overstocked"
                    if line.over_stock_qty_3mo > 0
                    else "Balanced"
                ),
            }
        )
        period_health[history_rows[-1]["status"]] += 1

    unit_cost = item.unit_cost or 0
    stock_value = baseline.qty_on_hand * unit_cost

    sales_mix: dict[str, float] = {}
    stock_position: dict[str, float] = {}
    qty_sold_30 = qty_sold_90 = over_qty = under_qty = 0.0

    if selected_line:
        qty_sold_30 = selected_line.qty_sold_30
        qty_sold_90 = selected_line.qty_sold_90
        over_qty = selected_line.over_stock_qty_3mo
        under_qty = selected_line.under_stock_qty_3mo
        mid = max(0.0, selected_line.qty_sold_90 - selected_line.qty_sold_30)
        tail = max(0.0, selected_line.qty_sold_180 - selected_line.qty_sold_90)
        sales_mix = {
            "30d": selected_line.qty_sold_30,
            "31–90d": mid,
            "91–180d": tail,
        }
        on_target = baseline.qty_on_hand
        if over_qty > 0:
            stock_position = {"On target": on_target, "Overstock": over_qty}
        elif under_qty < 0:
            stock_position = {"On hand": on_target, "Under gap": abs(under_qty)}
        else:
            stock_position = {"On target": on_target}

    return {
        "sku": item.sku,
        "name": item.name[:80],
        "department": item.department or "—",
        "supplier": item.supplier or "—",
        "is_deprecated": item.is_deprecated,
        "not_in_turn_report": item.not_in_turn_report,
        "on_hand": baseline.qty_on_hand,
        "unit_cost": unit_cost,
        "stock_value": stock_value,
        "qty_sold_30": qty_sold_30,
        "qty_sold_90": qty_sold_90,
        "over_qty": over_qty,
        "under_qty": under_qty,
        "abc_class": _item_abc_class(session, item.sku, batch_id) if history else None,
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
) -> dict:
    from stock_analysis.analytics.inventory_queries import iter_filtered_items

    lines = get_period_lines(session, batch_id) if has_enrichment else []
    turn_by_item = {item.id: line for line, item in lines}

    item_count = 0
    total_value = 0.0
    understock_count = 0
    overstock_count = 0
    slow_moving_count = 0
    dept_values: dict[str, float] = {}
    health = {"Understocked": 0, "Overstocked": 0, "Slow Moving": 0, "Healthy": 0}

    for item, baseline in iter_filtered_items(
        session, search, status, has_enrichment, dept=dept
    ):
        if should_skip_item(item.sku, item.name):
            continue
        item_count += 1
        cost = item.unit_cost or 0
        qty = baseline.qty_on_hand
        value = qty * cost
        total_value += value
        dept = item.department or "Unknown"
        dept_values[dept] = dept_values.get(dept, 0) + value

        turn = turn_by_item.get(item.id)
        if not turn:
            continue
        if turn.under_stock_qty_3mo < 0:
            understock_count += 1
            health["Understocked"] += 1
        elif turn.over_stock_qty_3mo > 0:
            overstock_count += 1
            health["Overstocked"] += 1
        elif turn.qty_sold_90 == 0 and qty > 0:
            slow_moving_count += 1
            health["Slow Moving"] += 1
        elif qty > 0:
            health["Healthy"] += 1

    return {
        "item_count": item_count,
        "total_value": total_value,
        "understock_count": understock_count,
        "overstock_count": overstock_count,
        "slow_moving_count": slow_moving_count,
        "dept_values": dept_values,
        "stock_health": health,
    }
