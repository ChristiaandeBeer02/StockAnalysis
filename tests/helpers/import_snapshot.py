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
MOVEMENT_PERIOD_START = "01/01/2026"
MOVEMENT_PERIOD_END = "31/01/2026"


def build_stockholding_row(code: str, description: str, on_hand: float, stock_value: float) -> str:
    return f"{code},,{description},,,,,,,,{on_hand:.2f},,,R{stock_value:.2f},,"


def build_sales_detail_row(
    code: str,
    description: str,
    *,
    subdepartm: str = "",
    department: str = "249",
    avg_cost: float = 5.0,
    on_hand: float = 0.0,
    sales_qty: float = 0.0,
    refunds_qty: float = 0.0,
    net_sales_qty: float | None = None,
) -> str:
    net = net_sales_qty if net_sales_qty is not None else sales_qty - refunds_qty
    sales_cost = sales_qty * avg_cost
    net_cost = net * avg_cost
    return (
        f'"{code}","{department}","","{description}",{avg_cost:.2f},"",0.00,{on_hand:.2f},'
        f'"",0.00,0.00,0.00,"{subdepartm}","","CAT001","",{sales_cost:.2f},{sales_qty:.2f},'
        f'{sales_cost:.2f},0.00,0.00,0.00,{net * avg_cost:.2f},{net:.2f},{net_cost:.2f},'
        f'0.00,0.00,0.00,0.00'
    )


def build_purchases_row(
    code: str,
    *,
    department: str = "A1",
    sales_qty: float = 0.0,
    refunds_qty: float = 0.0,
    purchases_qty: float = 0.0,
    returns_qty: float = 0.0,
    avg_cost: float = 5.0,
) -> str:
    sales_cost = sales_qty * avg_cost
    net_sales = sales_qty - refunds_qty
    net_cost = net_sales * avg_cost
    net_purchases = purchases_qty * avg_cost
    return (
        f'"{code}","{department}","",{net_sales * avg_cost:.2f},{sales_qty:.2f},{sales_cost:.2f},'
        f'0.00,{refunds_qty:.2f},0.00,{net_sales * avg_cost:.2f},{net_cost:.2f},0.00,'
        f'{net_purchases:.2f},0.00,{purchases_qty:.2f},{returns_qty:.2f},'
        f'{net_purchases:.2f},0.00,0.00'
    )


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

    sales_header = (
        '"CODE","DEPARTMENT","MAINITEM","Descript","AvrgCost","GenCode","PurchaseOr","OnHand",'
        '"Regular_SU","SalesOrder","WIPQty","LBOnhand","Subdepartm","Category","Range","Cycle",'
        '"Sales","SalesQty","SalesCost","Refunds","RefundsQty","RefundsCost","NettSales",'
        '"NettSalesQuantity","NettCost","Profit","Purchases","Returns","VAT"'
    )
    sales_rows = [
        build_sales_detail_row(
            "BASE001",
            "Widget Alpha Test Item*1/1/26",
            subdepartm="A1",
            avg_cost=5.0,
            on_hand=10.0,
            sales_qty=2.0,
            net_sales_qty=2.0,
        ),
        build_sales_detail_row(
            "BASE002",
            "Widget Beta Test Item*1/1/26",
            subdepartm="A1",
            avg_cost=5.0,
            on_hand=5.0,
            sales_qty=5.0,
            net_sales_qty=5.0,
        ),
        build_sales_detail_row(
            "BASE004",
            "Widget Delta Fast Seller*1/1/26",
            subdepartm="A1",
            avg_cost=5.0,
            on_hand=20.0,
            sales_qty=10.0,
            net_sales_qty=10.0,
        ),
        build_sales_detail_row(
            "MOVE001",
            "Movement Only New Item*1/1/26",
            subdepartm="B1",
            avg_cost=12.0,
            on_hand=0.0,
            sales_qty=0.0,
            net_sales_qty=0.0,
        ),
    ]

    purchases_header = (
        '"Code","Department","MainItem","Sales","Units","SalesCost","Refunds","RefundsQty",'
        '"RefundsCost","NettSales","NettCost","Profit","Purchases","RETURNS","PurchasesQT",'
        '"RETURNSQT","NettPurchases","NettPurchases_VAT","VAT"'
    )
    purchases_rows = [
        build_purchases_row("BASE001", department="A1", sales_qty=2.0, avg_cost=5.0),
        build_purchases_row(
            "BASE002",
            department="A1",
            sales_qty=5.0,
            purchases_qty=3.0,
            avg_cost=5.0,
        ),
        build_purchases_row("BASE004", department="A1", sales_qty=10.0, avg_cost=5.0),
        build_purchases_row("MOVE001", department="B1", purchases_qty=3.0, avg_cost=12.0),
    ]

    (target / "sthold2.csv").write_text("\n".join(sthold_lines) + "\n", encoding="utf-8")
    (target / "Sales_Detail_sample.csv").write_text(
        "\n".join([sales_header, *sales_rows]) + "\n",
        encoding="utf-8",
    )
    (target / "PurchasesDetailed_sample.csv").write_text(
        "\n".join([purchases_header, *purchases_rows]) + "\n",
        encoding="utf-8",
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
    for key in ("top_sellers", "sales_items", "reorder_alerts", "overstock_alerts", "margin_alerts", "markup_alerts", "slow_moving_items"):
        if key in result and isinstance(result[key], list):
            result[key] = sorted(result[key], key=lambda row: row.get("code", ""))
    if "dept_values" in result:
        result["dept_values"] = dict(sorted(result["dept_values"].items()))
    for key in ("dept_overstock_values", "dept_slow_moving_values"):
        if key in result:
            result[key] = dict(sorted(result[key].items()))
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
                    "purchases_qty": turn.purchases_qty,
                    "returns_qty": turn.returns_qty,
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
    """Run baseline + movement imports and return full snapshot with summaries."""
    fixtures = fixtures_dir or FIXTURES_DIR
    baseline_summary = apply_initial_baseline(session, fixtures / "sthold2.csv")
    enrichment_summary = apply_enrichment(
        session,
        fixtures / "Sales_Detail_sample.csv",
        fixtures / "PurchasesDetailed_sample.csv",
        period_start=MOVEMENT_PERIOD_START,
        period_end=MOVEMENT_PERIOD_END,
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
