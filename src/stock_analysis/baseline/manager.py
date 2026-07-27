"""Baseline state management."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete, desc, func, select, update
from sqlalchemy.orm import Session

from stock_analysis.analytics.dashboard import build_period_summary, save_analysis_result
from stock_analysis.analytics.metrics import DEFAULT_OPTIMUM_STOCK_MONTHS, effective_unit_cost_expr
from stock_analysis.analytics.stock_take import StockTakeComparison, compare_stock_take
from stock_analysis.baseline.change_log import log_change
from stock_analysis.db.models import (
    AnalysisResult,
    AppState,
    BaselineChangeLog,
    BaselineItem,
    BaselineVersion,
    ImportBatch,
    Item,
    PeriodTurnLine,
    StockTakeLine,
    StockTakeSession,
)
from stock_analysis.db.session import get_latest_baseline_version, has_initial_baseline, set_app_state
from stock_analysis.importers.iq_retail_parser import extract_optimum_months, read_export_lines
from stock_analysis.importers.item_filters import should_skip_item
from stock_analysis.importers.stockholding_parser import (
    StockholdingParseResult,
    parse_stockholding_file,
)
from stock_analysis.importers.turn_parser import TurnRow
from stock_analysis.importers.turnunder_parser import merge_turn_reports


class ImportCancelledError(Exception):
    """Raised when a long-running import is cancelled by the user."""


_IMPORT_STATE_KEYS = ("initial_baseline_complete", "enrichment_complete")


@dataclass
class InitialImportSummary:
    import_batch_id: int
    baseline_version: int
    items_imported: int
    deprecated_count: int
    total_stock_value: float
    period_start: str | None
    period_end: str | None


def _next_version_number(session: Session) -> int:
    current = session.scalar(select(func.max(BaselineVersion.version_number)))
    return (current or 0) + 1


def _check_cancelled(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise ImportCancelledError("Import cancelled")


def reset_import_data(session: Session) -> None:
    """Delete all import-related data. Preserves dashboard and other app settings."""
    session.execute(delete(StockTakeLine))
    session.execute(delete(AnalysisResult))
    session.execute(delete(StockTakeSession))
    session.execute(delete(PeriodTurnLine))
    session.execute(delete(BaselineChangeLog))
    session.execute(delete(BaselineItem))
    session.execute(delete(BaselineVersion))
    session.execute(delete(Item))
    session.execute(delete(ImportBatch))
    session.execute(delete(AppState).where(AppState.key.in_(_IMPORT_STATE_KEYS)))
    session.flush()


def apply_initial_baseline(
    session: Session,
    path: Path,
    *,
    parsed: StockholdingParseResult | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> InitialImportSummary:
    if has_initial_baseline(session):
        reset_import_data(session)

    if parsed is None:
        parsed = parse_stockholding_file(path)

    batch = ImportBatch(
        import_type="initial_baseline",
        file_name=path.name,
        period_start=parsed.period_start,
        period_end=parsed.period_end,
        row_count=parsed.stats.total_rows,
        deprecated_rows=parsed.stats.deprecated_rows,
        status="applied",
    )
    session.add(batch)
    session.flush()

    version_number = _next_version_number(session)
    version = BaselineVersion(
        version_number=version_number,
        source_type="initial_import",
        source_import_id=batch.id,
        notes=f"Initial baseline from {path.name}",
    )
    session.add(version)
    session.flush()

    sku_map = {item.sku: item for item in session.scalars(select(Item))}
    baseline_map = {row.item_id: row for row in session.scalars(select(BaselineItem))}

    eligible_rows = [
        row for row in parsed.rows if not should_skip_item(row.code, row.description)
    ]
    total_rows = len(eligible_rows)
    total_value = 0.0

    if progress_callback is not None:
        progress_callback(0, total_rows)

    for index, row in enumerate(eligible_rows):
        _check_cancelled(cancel_event)

        item = sku_map.get(row.code)
        if item is None:
            item = Item(
                sku=row.code,
                name=row.description,
                unit_cost=row.unit_cost,
                is_deprecated=row.is_deprecated,
            )
            session.add(item)
            session.flush()
            sku_map[row.code] = item
        else:
            item.name = row.description
            item.unit_cost = row.unit_cost
            item.is_deprecated = row.is_deprecated

        baseline = baseline_map.get(item.id)
        if baseline is None:
            baseline = BaselineItem(
                item_id=item.id,
                qty_on_hand=row.on_hand,
                baseline_version_id=version.id,
                last_update_source="initial_import",
            )
            session.add(baseline)
            baseline_map[item.id] = baseline
            log_change(
                session,
                item_id=item.id,
                baseline_version_id=version.id,
                field_changed="qty_on_hand",
                old_value=None,
                new_value=str(row.on_hand),
                change_reason="initial_import",
                source_type="initial_import",
                source_import_id=batch.id,
            )
        else:
            old_qty = baseline.qty_on_hand
            if old_qty != row.on_hand:
                log_change(
                    session,
                    item_id=item.id,
                    baseline_version_id=version.id,
                    field_changed="qty_on_hand",
                    old_value=str(old_qty),
                    new_value=str(row.on_hand),
                    change_reason="initial_import",
                    source_type="initial_import",
                    source_import_id=batch.id,
                )
            baseline.qty_on_hand = row.on_hand
            baseline.baseline_version_id = version.id
            baseline.last_update_source = "initial_import"

        total_value += row.stock_value

        if (index + 1) % 500 == 0:
            session.flush()

        if progress_callback is not None:
            progress_callback(index + 1, total_rows)

    set_app_state(session, "initial_baseline_complete", "true")
    set_app_state(session, "enrichment_complete", "false")

    return InitialImportSummary(
        import_batch_id=batch.id,
        baseline_version=version_number,
        items_imported=total_rows,
        deprecated_count=parsed.stats.deprecated_rows,
        total_stock_value=total_value,
        period_start=parsed.period_start,
        period_end=parsed.period_end,
    )


@dataclass
class TurnImportSummary:
    import_batch_id: int
    baseline_version: int
    items_processed: int
    new_items: int
    qty_changes: int
    deprecated_count: int
    period_start: str | None
    period_end: str | None
    import_type: str


def _store_turn_line(session: Session, batch_id: int, item_id: int, row: TurnRow) -> None:
    session.add(
        PeriodTurnLine(
            import_batch_id=batch_id,
            item_id=item_id,
            dept=row.dept,
            supplier=row.supplier,
            on_hand=row.on_hand,
            qty_sold_30=row.qty_sold_30,
            qty_sold_90=row.qty_sold_90,
            qty_sold_180=row.qty_sold_180,
            avg_monthly_sales_3mo=row.avg_monthly_sales_3mo,
            avg_monthly_sales_6mo=row.avg_monthly_sales_6mo,
            last_unit_cost=row.last_unit_cost,
            over_stock_qty_3mo=row.over_stock_qty_3mo,
            over_stock_qty_6mo=row.over_stock_qty_6mo,
            over_stock_value_3mo=row.over_stock_value_3mo,
            over_stock_value_6mo=row.over_stock_value_6mo,
            under_stock_qty_3mo=row.under_stock_qty_3mo,
            under_stock_qty_6mo=row.under_stock_qty_6mo,
            under_stock_value_3mo=row.under_stock_value_3mo,
            under_stock_value_6mo=row.under_stock_value_6mo,
        )
    )


def apply_turn_import(
    session: Session,
    turn_path: Path,
    turnunder_path: Path,
    *,
    import_type: str,
) -> TurnImportSummary:
    merged, period_start, period_end = merge_turn_reports(turn_path, turnunder_path)
    deprecated = sum(1 for r in merged if r.is_deprecated)

    turn_lines = read_export_lines(turn_path)
    parsed_optimum = extract_optimum_months(turn_lines)
    optimum_months = (
        parsed_optimum if parsed_optimum and parsed_optimum > 0 else DEFAULT_OPTIMUM_STOCK_MONTHS
    )

    batch = ImportBatch(
        import_type=import_type,
        file_name=turn_path.name,
        companion_file_name=turnunder_path.name,
        period_start=period_start,
        period_end=period_end,
        row_count=len(merged),
        deprecated_rows=deprecated,
        status="applied",
    )
    session.add(batch)
    session.flush()

    version_number = _next_version_number(session)
    source_type = "enrichment" if import_type == "baseline_enrichment" else "period_turn"
    version = BaselineVersion(
        version_number=version_number,
        source_type=source_type,
        source_import_id=batch.id,
        notes=f"{import_type} from {turn_path.name}",
    )
    session.add(version)
    session.flush()

    turn_codes = {r.code for r in merged}
    new_items = 0
    qty_changes = 0

    sku_map = {item.sku: item for item in session.scalars(select(Item))}
    baseline_map = {row.item_id: row for row in session.scalars(select(BaselineItem))}

    for index, row in enumerate(merged):
        if should_skip_item(row.code, row.description):
            continue
        item = sku_map.get(row.code)
        if item is None:
            item = Item(sku=row.code, name=row.description)
            session.add(item)
            session.flush()
            sku_map[row.code] = item
            new_items += 1
        else:
            item.name = row.description or item.name
            item.department = row.dept or item.department
            item.supplier = row.supplier or item.supplier
            if row.last_unit_cost > 0:
                item.unit_cost = row.last_unit_cost
            item.is_deprecated = row.is_deprecated
            item.not_in_turn_report = False

        _store_turn_line(session, batch.id, item.id, row)

        baseline = baseline_map.get(item.id)
        if baseline is None:
            baseline = BaselineItem(
                item_id=item.id,
                qty_on_hand=row.on_hand,
                baseline_version_id=version.id,
                last_update_source=source_type,
            )
            session.add(baseline)
            baseline_map[item.id] = baseline
            log_change(
                session,
                item_id=item.id,
                baseline_version_id=version.id,
                field_changed="qty_on_hand",
                old_value=None,
                new_value=str(row.on_hand),
                change_reason=source_type,
                source_type=source_type,
                source_import_id=batch.id,
            )
            qty_changes += 1
        elif baseline.qty_on_hand != row.on_hand:
            log_change(
                session,
                item_id=item.id,
                baseline_version_id=version.id,
                field_changed="qty_on_hand",
                old_value=str(baseline.qty_on_hand),
                new_value=str(row.on_hand),
                change_reason=source_type,
                source_type=source_type,
                source_import_id=batch.id,
            )
            baseline.qty_on_hand = row.on_hand
            baseline.baseline_version_id = version.id
            baseline.last_update_source = source_type
            qty_changes += 1
        else:
            baseline.baseline_version_id = version.id
            baseline.last_update_source = source_type

        if (index + 1) % 500 == 0:
            session.flush()

    if turn_codes:
        session.execute(
            update(Item).where(Item.sku.notin_(turn_codes)).values(not_in_turn_report=True)
        )

    if import_type == "baseline_enrichment":
        set_app_state(session, "enrichment_complete", "true")

    set_app_state(session, "optimum_stock_months", str(optimum_months))

    summary = build_period_summary(session)
    if summary:
        save_analysis_result(session, batch.id, import_type, summary)

    return TurnImportSummary(
        import_batch_id=batch.id,
        baseline_version=version_number,
        items_processed=len(merged),
        new_items=new_items,
        qty_changes=qty_changes,
        deprecated_count=deprecated,
        period_start=period_start,
        period_end=period_end,
        import_type=import_type,
    )


def apply_enrichment(session: Session, turn_path: Path, turnunder_path: Path) -> TurnImportSummary:
    return apply_turn_import(
        session, turn_path, turnunder_path, import_type="baseline_enrichment"
    )


def apply_period_import(session: Session, turn_path: Path, turnunder_path: Path) -> TurnImportSummary:
    return apply_turn_import(session, turn_path, turnunder_path, import_type="period_turn")


def remove_deprecated_items(session: Session) -> int:
    """Delete all deprecated items and their related records. Returns count removed."""
    deprecated_ids = list(
        session.scalars(select(Item.id).where(Item.is_deprecated.is_(True))).all()
    )
    if not deprecated_ids:
        return 0

    session.execute(delete(PeriodTurnLine).where(PeriodTurnLine.item_id.in_(deprecated_ids)))
    session.execute(delete(BaselineChangeLog).where(BaselineChangeLog.item_id.in_(deprecated_ids)))
    session.execute(delete(BaselineItem).where(BaselineItem.item_id.in_(deprecated_ids)))
    result = session.execute(delete(Item).where(Item.id.in_(deprecated_ids)))
    return result.rowcount or 0


@dataclass
class StockTakeReconcileSummary:
    session_id: int
    import_batch_id: int
    baseline_version: int
    items_updated: int
    new_items: int
    variance_count: int
    shrinkage_value: float
    overage_value: float


def preview_stock_take(session: Session, path: Path) -> StockTakeComparison:
    parsed = parse_stockholding_file(path)
    comparison = compare_stock_take(session, parsed)
    comparison.file_name = path.name
    comparison.parse_stats = parsed.stats
    return comparison


def apply_stock_take_reconcile(session: Session, path: Path) -> StockTakeReconcileSummary:
    parsed = parse_stockholding_file(path)
    comparison = compare_stock_take(session, parsed)
    comparison.file_name = path.name

    batch = ImportBatch(
        import_type="stock_take",
        file_name=path.name,
        period_start=parsed.period_start,
        period_end=parsed.period_end,
        row_count=len(comparison.lines),
        deprecated_rows=parsed.stats.deprecated_rows,
        status="applied",
    )
    session.add(batch)
    session.flush()

    take_session = StockTakeSession(
        import_batch_id=batch.id,
        stock_take_date=parsed.period_end or parsed.period_start,
        applied_at=datetime.now(),
        total_items=len(comparison.lines),
        variance_count=len(comparison.variance_lines),
        shrinkage_value=comparison.shrinkage_value,
        overage_value=comparison.overage_value,
        status="applied",
    )
    session.add(take_session)
    session.flush()

    version_number = _next_version_number(session)
    version = BaselineVersion(
        version_number=version_number,
        source_type="stock_take",
        source_import_id=batch.id,
        notes=f"Stock take reconcile from {path.name}",
    )
    session.add(version)
    session.flush()

    items_updated = 0
    new_items = 0

    sku_map = {item.sku: item for item in session.scalars(select(Item))}
    baseline_map = {row.item_id: row for row in session.scalars(select(BaselineItem))}

    for index, line in enumerate(comparison.lines):
        item = sku_map.get(line.sku)
        if line.line_type == "new_in_stock_take":
            item = Item(sku=line.sku, name=line.name, unit_cost=line.unit_cost)
            session.add(item)
            session.flush()
            sku_map[line.sku] = item
            new_items += 1

        item_id = item.id if item else None

        session.add(
            StockTakeLine(
                session_id=take_session.id,
                item_id=item_id,
                sku=line.sku,
                name=line.name,
                baseline_qty=line.baseline_qty,
                counted_qty=line.counted_qty,
                variance=line.variance,
                variance_value=line.variance_value,
                line_type=line.line_type,
            )
        )

        if line.line_type == "missing_from_stock_take" or item is None:
            continue

        baseline = baseline_map.get(item.id)
        new_qty = line.counted_qty

        if baseline is None:
            baseline = BaselineItem(
                item_id=item.id,
                qty_on_hand=new_qty,
                baseline_version_id=version.id,
                last_update_source="stock_take",
            )
            session.add(baseline)
            baseline_map[item.id] = baseline
            log_change(
                session,
                item_id=item.id,
                baseline_version_id=version.id,
                field_changed="qty_on_hand",
                old_value=None,
                new_value=str(new_qty),
                change_reason="stock_take_variance",
                source_type="stock_take",
                source_import_id=batch.id,
            )
            items_updated += 1
        elif baseline.qty_on_hand != new_qty:
            log_change(
                session,
                item_id=item.id,
                baseline_version_id=version.id,
                field_changed="qty_on_hand",
                old_value=str(baseline.qty_on_hand),
                new_value=str(new_qty),
                change_reason="stock_take_variance",
                source_type="stock_take",
                source_import_id=batch.id,
            )
            baseline.qty_on_hand = new_qty
            baseline.baseline_version_id = version.id
            baseline.last_update_source = "stock_take"
            items_updated += 1
        else:
            baseline.baseline_version_id = version.id
            baseline.last_update_source = "stock_take"

        if line.unit_cost and item.unit_cost != line.unit_cost:
            item.unit_cost = line.unit_cost

        if (index + 1) % 500 == 0:
            session.flush()

    session.add(
        AnalysisResult(
            analysis_type="stock_take_variance",
            import_batch_id=batch.id,
            stock_take_session_id=take_session.id,
            period_start=parsed.period_start,
            period_end=parsed.period_end,
            summary_json=json.dumps(comparison.summary_dict()),
        )
    )

    session.flush()

    return StockTakeReconcileSummary(
        session_id=take_session.id,
        import_batch_id=batch.id,
        baseline_version=version_number,
        items_updated=items_updated,
        new_items=new_items,
        variance_count=len(comparison.variance_lines),
        shrinkage_value=comparison.shrinkage_value,
        overage_value=comparison.overage_value,
    )


def get_stock_take_history(session: Session) -> list[dict]:
    sessions = session.scalars(
        select(StockTakeSession).order_by(desc(StockTakeSession.applied_at))
    ).all()
    history = []
    for s in sessions:
        batch = session.get(ImportBatch, s.import_batch_id)
        history.append(
            {
                "id": s.id,
                "date": s.stock_take_date or "—",
                "file": batch.file_name if batch else "—",
                "total_items": s.total_items,
                "variances": s.variance_count,
                "shrinkage": s.shrinkage_value,
                "overage": s.overage_value,
                "applied_at": s.applied_at.strftime("%Y-%m-%d %H:%M") if s.applied_at else "—",
            }
        )
    return history


def get_stock_take_variance_lines(session: Session, session_id: int) -> list[dict]:
    lines = session.scalars(
        select(StockTakeLine)
        .where(StockTakeLine.session_id == session_id)
        .where(StockTakeLine.variance != 0)
        .order_by(StockTakeLine.variance_value)
    ).all()
    return [
        {
            "sku": line.sku,
            "name": line.name[:60],
            "baseline_qty": line.baseline_qty,
            "counted_qty": line.counted_qty,
            "variance": line.variance,
            "variance_value": line.variance_value,
            "line_type": line.line_type,
        }
        for line in lines
    ]


def get_baseline_summary(session: Session) -> dict:
    item_count = session.scalar(select(func.count(Item.id))) or 0
    active_count = (
        session.scalar(select(func.count(Item.id)).where(Item.is_deprecated.is_(False))) or 0
    )
    total_qty = session.scalar(select(func.sum(BaselineItem.qty_on_hand))) or 0.0

    latest_turn_subq = (
        select(
            PeriodTurnLine.item_id,
            func.max(PeriodTurnLine.id).label("latest_line_id"),
        )
        .group_by(PeriodTurnLine.item_id)
        .subquery()
    )

    total_value = session.scalar(
        select(
            func.sum(
                BaselineItem.qty_on_hand
                * effective_unit_cost_expr(PeriodTurnLine.last_unit_cost, Item.unit_cost)
            )
        )
        .select_from(BaselineItem)
        .join(Item, Item.id == BaselineItem.item_id)
        .outerjoin(latest_turn_subq, latest_turn_subq.c.item_id == BaselineItem.item_id)
        .outerjoin(PeriodTurnLine, PeriodTurnLine.id == latest_turn_subq.c.latest_line_id)
    ) or 0.0

    version = get_latest_baseline_version(session)
    return {
        "item_count": item_count,
        "active_count": active_count,
        "deprecated_count": item_count - active_count,
        "total_qty": total_qty,
        "total_value": total_value,
        "baseline_version": version.version_number if version else 0,
    }
