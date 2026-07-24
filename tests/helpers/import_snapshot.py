"""Helpers for import integration tests and golden snapshot export."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from stock_analysis.analytics.dashboard import build_period_summary
from stock_analysis.analytics.inventory_queries import fetch_inventory_rows
from stock_analysis.baseline.manager import apply_enrichment, apply_initial_baseline
from stock_analysis.db.models import AppState, Base, BaselineItem, Item, PeriodTurnLine
from stock_analysis.db.session import has_enrichment

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "test_imports"


def build_turn_row(
    *,
    dept: str,
    supplier: str,
    code: str,
    description: str,
    on_hand: float,
    qty_30: float = 0.0,
    qty_90: float = 0.0,
    qty_180: float = 0.0,
    avg_3mo: float = 0.0,
    avg_6mo: float = 0.0,
    last_unit_cost: float = 0.0,
    over_qty_3mo: float = 0.0,
    over_qty_6mo: float = 0.0,
    over_value_3mo: float = 0.0,
    over_value_6mo: float = 0.0,
) -> str:
    parts = [""] * 38
    parts[0] = dept
    parts[2] = supplier
    parts[3] = code
    parts[4] = description
    parts[5] = f"{on_hand:.2f}"
    parts[10] = f"{qty_30:.2f}"
    parts[14] = f"{qty_90:.2f}"
    parts[15] = f"{qty_180:.2f}"
    parts[17] = f"{avg_3mo:.2f}"
    parts[18] = f"{avg_6mo:.2f}"
    parts[28] = f"{last_unit_cost:.2f}"
    parts[31] = f"{over_qty_3mo:.2f}"
    parts[32] = f"{over_qty_6mo:.2f}"
    parts[33] = f"{over_value_3mo:.2f}"
    parts[37] = f"{over_value_6mo:.2f}"
    return ",".join(parts)


def build_under_row(
    *,
    dept: str,
    supplier: str,
    code: str,
    description: str,
    on_hand: float,
    qty_30: float = 0.0,
    qty_90: float = 0.0,
    qty_180: float = 0.0,
    avg_3mo: float = 0.0,
    avg_6mo: float = 0.0,
    last_unit_cost: float = 0.0,
    under_qty_3mo: float = 0.0,
    under_qty_6mo: float = 0.0,
    under_value_3mo: float = 0.0,
    under_value_6mo: float = 0.0,
) -> str:
    parts = [""] * 36
    parts[0] = dept
    parts[2] = supplier
    parts[3] = code
    parts[4] = description
    parts[5] = f"{on_hand:.2f}"
    parts[10] = f"{qty_30:.2f}"
    parts[13] = f"{qty_90:.2f}"
    parts[14] = f"{qty_180:.2f}"
    parts[16] = f"{avg_3mo:.2f}"
    parts[17] = f"{avg_6mo:.2f}"
    parts[26] = f"{last_unit_cost:.2f}"
    parts[29] = f"{under_qty_3mo:.2f}"
    parts[30] = f"{under_qty_6mo:.2f}"
    parts[31] = f"{under_value_3mo:.2f}"
    parts[35] = f"{under_value_6mo:.2f}"
    return ",".join(parts)


def build_stockholding_row(code: str, description: str, on_hand: float, stock_value: float) -> str:
    return f"{code},,{description},,,,,,,,{on_hand:.2f},,,R{stock_value:.2f},,"


def write_fixture_csvs(target_dir: Path | None = None) -> None:
    """Write mini IQ Retail fixture CSV files to target_dir (defaults to test_imports/)."""
    target = target_dir or FIXTURES_DIR
    target.mkdir(parents=True, exist_ok=True)

    sthold_lines = [
        "Manta DIY (Pty) Ltd,,,,,,Date Printed :01/01/2026 10:00:00,,,,Page No 1,,",
        "",
        "Detailed Stockholding,,,,,,,,",
        "",
        "Period: 01/01/2026 to 31/01/2026,",
        "",
        "Current Filter: NA,,,,,,,,,,,,,,Currency: (R),,,,",
        "",
        "Code,Description,,,,,Onhand,Stock Value",
        "* 15582,,zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz,,,,,,,,0.00,,,R0.00,,",
        ".,,Open Item For Quotation,,,,,,,,0.00,,,R0.00,,",
        build_stockholding_row("DEP001", "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz", 2.0, 10.0),
        build_stockholding_row("BASE001", "Widget Alpha Test Item*1/1/26", 10.0, 50.0),
        build_stockholding_row("BASE002", "Widget Beta Test Item*1/1/26", 5.0, 25.0),
        build_stockholding_row("BASE003", "Widget Gamma Baseline Only*1/1/26", 8.0, 40.0),
        build_stockholding_row("BASE004", "Widget Delta Fast Seller*1/1/26", 20.0, 100.0),
        build_stockholding_row("BASE005", "Widget Epsilon Slow Mover*1/1/26", 15.0, 75.0),
        "Totals:,,58.00,,,,,,,R300.00,,",
    ]

    turn_header = [
        "Manta DIY (Pty) Ltd,,,,,,,,,,Date Printed: 01/01/2026,,,,,,,,,,1 of 1",
        "",
        "Stock Turn Report - Over Stocking,,,,,,,",
        "",
        "Report Parameters,,,,,,,,,,,,",
        "",
        "Period: 01/01/2026 to 31/01/2026,",
        "",
        "Optimum Stock Holding In Months:,,,,,,,,,,2.00,,,,,",
        ",,,,,",
        "Sort Order:,,,,,,,,Department,,,,",
        "",
        "Qty Sold (Days),,,,,,,,Ave Monthly Sales,,,,,,,Stock Days on Hand,,,,,,Over Stock Quantity ,,,,,,,Over Stock Value ,,,,,,,,,",
        ",,,,,,,,,,,,,,,,,,,,,Last UnitCost,,,,,,,,,,,,,,,,,,",
        ",,",
        "Dept,,Supplier,Code,Description,On Hand,,,,,Last 30,,,,,Last 90,Last 180,,3 Months,6 Months,,,,,,3 Months,,6 Months,,,,,3 Months,6 Months,3 Months,,,,6 Months,,,",
        "",
        ",,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,",
    ]
    turn_data = [
        build_turn_row(
            dept="A1",
            supplier="S001",
            code="BASE001",
            description="Widget Alpha Test Item*1/1/26",
            on_hand=10.0,
            last_unit_cost=5.50,
            over_qty_3mo=5.0,
            over_qty_6mo=4.0,
            over_value_3mo=27.50,
            over_value_6mo=22.00,
        ),
        build_turn_row(
            dept="A1",
            supplier="S001",
            code="BASE002",
            description="Widget Beta Test Item*1/1/26",
            on_hand=5.0,
            qty_90=12.0,
            avg_3mo=4.0,
            last_unit_cost=5.00,
        ),
        build_turn_row(
            dept="A1",
            supplier="S002",
            code="BASE004",
            description="Widget Delta Fast Seller*1/1/26",
            on_hand=20.0,
            qty_30=35.0,
            qty_90=100.0,
            qty_180=150.0,
            avg_3mo=33.33,
            avg_6mo=25.0,
            last_unit_cost=5.00,
        ),
        build_turn_row(
            dept="B1",
            supplier="S003",
            code="BASE005",
            description="Widget Epsilon Slow Mover*1/1/26",
            on_hand=15.0,
            last_unit_cost=5.00,
            over_qty_3mo=15.0,
            over_qty_6mo=15.0,
            over_value_3mo=75.00,
            over_value_6mo=75.00,
        ),
        build_turn_row(
            dept="B1",
            supplier="S004",
            code="TURN001",
            description="Turn Only New Item*1/1/26",
            on_hand=3.0,
            qty_90=6.0,
            last_unit_cost=12.00,
        ),
    ]

    under_header = [
        "Manta DIY (Pty) Ltd,,,,,,,,,,Date Printed: 01/01/2026,,,,,,,,1 of 1",
        "",
        "Stock Turn Report - Under Stocking,,,,,,,",
        "",
        "Report Parameters,,,,,,,,,,",
        "",
        "Period: 01/01/2026 to 31/01/2026,",
        "",
        "Optimum Stock Holding In Months:,,,,,,,,,2.00,,,,",
        ",,,,",
        "Sort Order:,,,,,,,Department,,,",
        "",
        "Qty Sold (Days),,,,,,,Ave Monthly Sales,,,,,,Stock Days on Hand,,,,,,Under Stock Quantity ,,,,,,,Under Stock Value ,,,,,,,,,",
        ",,,,,,,,,,,,,,,,,,,,Last UnitCost,,,,,,,,,,,,,,,,,,",
        ",,",
        "Dept,,Supplier,Code,Description,On Hand,,,,Last 30,,,,Last 90,Last 180,,3 Months,6 Months,,,,,3 Months,,6 Months,,,,,3 Months,6 Months,3 Months,,,,6 Months,,,",
        "",
        ",,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,",
    ]
    under_data = [
        build_under_row(
            dept="A1",
            supplier="S001",
            code="BASE002",
            description="Widget Beta Test Item*1/1/26",
            on_hand=5.0,
            qty_90=12.0,
            avg_3mo=4.0,
            last_unit_cost=5.00,
            under_qty_3mo=-3.0,
            under_qty_6mo=-2.0,
            under_value_3mo=-15.00,
            under_value_6mo=-10.00,
        ),
        build_under_row(
            dept="A1",
            supplier="S002",
            code="BASE004",
            description="Widget Delta Fast Seller*1/1/26",
            on_hand=20.0,
            qty_90=100.0,
            avg_3mo=33.33,
            last_unit_cost=5.00,
            under_qty_3mo=-2.0,
            under_qty_6mo=-1.0,
            under_value_3mo=-10.00,
            under_value_6mo=-5.00,
        ),
        build_under_row(
            dept="C1",
            supplier="S005",
            code="UNDER001",
            description="Under Report Only Item*1/1/26",
            on_hand=0.0,
            qty_90=8.0,
            avg_3mo=2.67,
            last_unit_cost=8.00,
            under_qty_3mo=-5.33,
            under_qty_6mo=-4.0,
            under_value_3mo=-42.64,
            under_value_6mo=-32.00,
        ),
    ]

    (target / "sthold2.csv").write_text("\n".join(sthold_lines) + "\n", encoding="utf-8")
    (target / "IQStockTurn.csv").write_text("\n".join(turn_header + turn_data) + "\n", encoding="utf-8")
    (target / "IQStockTurnunder.csv").write_text(
        "\n".join(under_header + under_data) + "\n", encoding="utf-8"
    )


def _round_float(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 4)
    if isinstance(value, dict):
        return {key: _round_float(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_round_float(item) for item in value]
    return value


def _sort_summary_lists(summary: dict[str, Any]) -> dict[str, Any]:
    result = dict(summary)
    result.pop("batch_id", None)
    for key in ("top_sellers", "sales_items", "reorder_alerts", "overstock_alerts", "slow_moving_items"):
        if key in result and isinstance(result[key], list):
            result[key] = sorted(result[key], key=lambda row: row.get("code", ""))
    if "dept_values" in result:
        result["dept_values"] = dict(sorted(result["dept_values"].items()))
    if "stock_health" in result and isinstance(result["stock_health"], dict):
        result["stock_health"] = dict(sorted(result["stock_health"].items()))
    return result


def export_import_snapshot(session: Session) -> dict[str, Any]:
    """Export a stable, ID-free snapshot of DB state after imports."""
    items = session.scalars(select(Item).order_by(Item.sku)).all()
    turn_lines = {
        line.item_id: line
        for line in session.scalars(select(PeriodTurnLine).order_by(PeriodTurnLine.id)).all()
    }
    baselines = {
        row.item_id: row for row in session.scalars(select(BaselineItem)).all()
    }

    item_rows = []
    for item in items:
        baseline = baselines.get(item.id)
        turn = turn_lines.get(item.id)
        row: dict[str, Any] = {
            "sku": item.sku,
            "name": item.name,
            "department": item.department,
            "supplier": item.supplier,
            "unit_cost": item.unit_cost,
            "is_deprecated": item.is_deprecated,
            "not_in_turn_report": item.not_in_turn_report,
            "qty_on_hand": baseline.qty_on_hand if baseline else None,
        }
        if turn:
            row.update(
                {
                    "qty_sold_90": turn.qty_sold_90,
                    "over_stock_qty_3mo": turn.over_stock_qty_3mo,
                    "under_stock_qty_3mo": turn.under_stock_qty_3mo,
                    "under_stock_value_3mo": turn.under_stock_value_3mo,
                }
            )
        item_rows.append(row)

    app_state = {
        row.key: row.value
        for row in session.scalars(select(AppState).order_by(AppState.key)).all()
    }

    period_summary = _sort_summary_lists(build_period_summary(session))
    inventory = fetch_inventory_rows(
        session,
        search="",
        status="All",
        has_enrichment=has_enrichment(session),
    )

    return _round_float(
        {
            "app_state": app_state,
            "items": item_rows,
            "inventory": inventory,
            "period_summary": period_summary,
        }
    )


def run_import_pipeline(session: Session, fixtures_dir: Path | None = None) -> dict[str, Any]:
    """Run baseline + enrichment imports and return full snapshot with summaries."""
    fixtures = fixtures_dir or FIXTURES_DIR
    baseline_summary = apply_initial_baseline(session, fixtures / "sthold2.csv")
    enrichment_summary = apply_enrichment(
        session, fixtures / "IQStockTurn.csv", fixtures / "IQStockTurnunder.csv"
    )
    session.flush()

    snapshot = export_import_snapshot(session)
    snapshot["initial_baseline"] = {
        "items_imported": baseline_summary.items_imported,
        "deprecated_count": baseline_summary.deprecated_count,
        "total_stock_value": round(baseline_summary.total_stock_value, 4),
        "period_start": baseline_summary.period_start,
        "period_end": baseline_summary.period_end,
    }
    snapshot["enrichment"] = {
        "items_processed": enrichment_summary.items_processed,
        "new_items": enrichment_summary.new_items,
        "qty_changes": enrichment_summary.qty_changes,
        "deprecated_count": enrichment_summary.deprecated_count,
        "import_type": enrichment_summary.import_type,
        "period_start": enrichment_summary.period_start,
        "period_end": enrichment_summary.period_end,
    }
    return snapshot


def create_test_session(db_path: Path) -> Session:
    engine = create_engine(f"sqlite:///{db_path}", echo=False)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def write_golden_snapshot(output_path: Path | None = None) -> Path:
    """Generate fixture CSVs and write expected_output.json golden file."""
    import tempfile

    write_fixture_csvs()
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = Path(tmp.name)

    engine = create_engine(f"sqlite:///{db_path}", echo=False)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = SessionLocal()
    try:
        snapshot = run_import_pipeline(session)
        session.commit()
        target = output_path or (FIXTURES_DIR / "expected_output.json")
        target.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return target
    finally:
        session.close()
        engine.dispose()
        db_path.unlink(missing_ok=True)


if __name__ == "__main__":
    path = write_golden_snapshot()
    print(f"Wrote golden snapshot to {path}")
