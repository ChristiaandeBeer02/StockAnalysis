"""Tests for movement import delta logic."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from stock_analysis.analytics.lookback import resolve_backdate_default_period
from stock_analysis.analytics.movement_periods import suggest_next_movement_period
from stock_analysis.baseline.manager import (
    apply_backdate_import,
    apply_baseline_alignment,
    apply_enrichment,
    apply_initial_baseline,
    apply_period_import,
    find_negative_qty_skus,
)
from stock_analysis.importers.movement_parser import merge_movement_reports
from stock_analysis.analytics.inventory_queries import fetch_inventory_rows
from stock_analysis.db.models import Base, BaselineItem, Item, PeriodTurnLine
from stock_analysis.db.session import get_baseline_anchor_date, has_enrichment, set_movement_closing_weekday
from tests.helpers.import_snapshot import (
    MOVEMENT_PERIOD_END,
    MOVEMENT_PERIOD_START,
    build_purchases_row,
    build_sales_detail_row,
    write_fixture_csvs,
)


@pytest.fixture
def session(tmp_path) -> Session:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", echo=False)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    sess = factory()
    try:
        yield sess
    finally:
        sess.close()


@pytest.fixture
def fixtures(tmp_path: Path) -> Path:
    write_fixture_csvs(tmp_path)
    return tmp_path


def _baseline_qty(session: Session, sku: str) -> float:
    item = session.scalar(select(Item).where(Item.sku == sku))
    assert item is not None
    baseline = session.scalar(select(BaselineItem).where(BaselineItem.item_id == item.id))
    assert baseline is not None
    return baseline.qty_on_hand


def test_forward_movement_delta(session: Session, fixtures: Path) -> None:
    apply_initial_baseline(session, fixtures / "sthold2.csv")
    apply_enrichment(
        session,
        fixtures / "Sales_Detail_sample.csv",
        fixtures / "PurchasesDetailed_sample.csv",
        period_start=MOVEMENT_PERIOD_START,
        period_end=MOVEMENT_PERIOD_END,
    )
    session.commit()

    assert _baseline_qty(session, "BASE001") == pytest.approx(8.0)
    assert _baseline_qty(session, "BASE002") == pytest.approx(3.0)
    assert _baseline_qty(session, "BASE003") == pytest.approx(8.0)
    assert _baseline_qty(session, "BASE004") == pytest.approx(10.0)
    assert _baseline_qty(session, "MOVE001") == pytest.approx(3.0)

    base001 = session.scalar(select(Item).where(Item.sku == "BASE001"))
    assert base001 is not None
    assert base001.department is None
    turn = session.scalar(
        select(PeriodTurnLine).where(PeriodTurnLine.item_id == base001.id)
    )
    assert turn is not None
    assert turn.dept == "A1"


def test_enrichment_stores_revenue_and_profit(session: Session, fixtures: Path) -> None:
    apply_initial_baseline(session, fixtures / "sthold2.csv")
    apply_enrichment(
        session,
        fixtures / "Sales_Detail_sample.csv",
        fixtures / "PurchasesDetailed_sample.csv",
        period_start=MOVEMENT_PERIOD_START,
        period_end=MOVEMENT_PERIOD_END,
    )
    session.commit()

    lines = session.scalars(
        select(PeriodTurnLine).join(Item).where(Item.sku == "BASE001")
    ).all()
    assert len(lines) == 1
    line = lines[0]
    assert line.net_sales_revenue == pytest.approx(10.0)
    assert line.gross_profit == pytest.approx(0.0)


def test_backdate_movement_delta(session: Session, fixtures: Path) -> None:
    apply_initial_baseline(session, fixtures / "sthold2.csv")
    apply_enrichment(
        session,
        fixtures / "Sales_Detail_sample.csv",
        fixtures / "PurchasesDetailed_sample.csv",
        period_start=MOVEMENT_PERIOD_START,
        period_end=MOVEMENT_PERIOD_END,
    )
    session.commit()

    anchor_before = get_baseline_anchor_date(session)

    apply_backdate_import(
        session,
        fixtures / "Sales_Detail_sample.csv",
        fixtures / "PurchasesDetailed_sample.csv",
        period_start=MOVEMENT_PERIOD_START,
        period_end=MOVEMENT_PERIOD_END,
    )
    session.commit()

    assert _baseline_qty(session, "BASE001") == pytest.approx(8.0)
    assert _baseline_qty(session, "BASE002") == pytest.approx(3.0)
    assert _baseline_qty(session, "BASE004") == pytest.approx(10.0)
    assert get_baseline_anchor_date(session) == anchor_before


def test_enrichment_advances_anchor(session: Session, fixtures: Path) -> None:
    apply_initial_baseline(session, fixtures / "sthold2.csv")
    apply_enrichment(
        session,
        fixtures / "Sales_Detail_sample.csv",
        fixtures / "PurchasesDetailed_sample.csv",
        period_start=MOVEMENT_PERIOD_START,
        period_end=MOVEMENT_PERIOD_END,
    )
    session.commit()

    assert get_baseline_anchor_date(session) == date(2026, 1, 31)


def test_baseline_alignment_backdates_and_completes_step2(session: Session, fixtures: Path) -> None:
    apply_initial_baseline(session, fixtures / "sthold2.csv")
    set_movement_closing_weekday(session, 5)  # Saturday; anchor 01/01/2026 is Thursday
    session.commit()

    assert not has_enrichment(session)

    sales_path, purchases_path = _write_alignment_csvs(fixtures)
    apply_baseline_alignment(
        session,
        sales_path,
        purchases_path,
        period_start="28/12/2025",
        period_end="01/01/2026",
    )
    session.commit()

    assert has_enrichment(session)
    assert get_baseline_anchor_date(session) == date(2025, 12, 27)
    assert _baseline_qty(session, "BASE001") == pytest.approx(12.0)


def test_baseline_alignment_allows_negative_qty(session: Session, fixtures: Path) -> None:
    apply_initial_baseline(session, fixtures / "sthold2.csv")
    set_movement_closing_weekday(session, 5)
    session.commit()

    apply_baseline_alignment(
        session,
        fixtures / "Sales_Detail_sample.csv",
        fixtures / "PurchasesDetailed_sample.csv",
        period_start=MOVEMENT_PERIOD_START,
        period_end=MOVEMENT_PERIOD_END,
    )
    session.commit()

    assert _baseline_qty(session, "MOVE001") == pytest.approx(-3.0)


def test_find_negative_qty_skus(session: Session, fixtures: Path) -> None:
    apply_initial_baseline(session, fixtures / "sthold2.csv")
    session.commit()

    parsed = merge_movement_reports(
        fixtures / "Sales_Detail_sample.csv",
        fixtures / "PurchasesDetailed_sample.csv",
    )
    negative = find_negative_qty_skus(session, parsed.rows, direction="backward")

    assert "MOVE001" in negative
    assert "BASE001" not in negative


def test_backdate_import_does_not_change_suggested_alignment_period(
    session: Session, fixtures: Path
) -> None:
    apply_initial_baseline(session, fixtures / "sthold2.csv")
    set_movement_closing_weekday(session, 5)
    session.commit()

    assert get_baseline_anchor_date(session) == date(2026, 1, 1)

    apply_backdate_import(
        session,
        fixtures / "Sales_Detail_sample.csv",
        fixtures / "PurchasesDetailed_sample.csv",
        period_start="01/12/2025",
        period_end="31/12/2025",
    )
    session.commit()

    anchor = get_baseline_anchor_date(session)
    assert anchor == date(2026, 1, 1)
    assert suggest_next_movement_period(anchor, 5) == (
        date(2025, 12, 28),
        date(2026, 1, 1),
    )


def test_resolve_backdate_default_period_uses_baseline_when_no_batches(
    session: Session, fixtures: Path
) -> None:
    apply_initial_baseline(session, fixtures / "sthold2.csv")
    set_movement_closing_weekday(session, 5)
    session.commit()

    start, end, intro = resolve_backdate_default_period(session)

    assert start == date(2025, 12, 21)
    assert end == date(2025, 12, 27)
    assert intro is not None
    assert "baseline date" in intro


def test_resolve_backdate_default_period_uses_oldest_batch(
    session: Session, fixtures: Path
) -> None:
    apply_initial_baseline(session, fixtures / "sthold2.csv")
    set_movement_closing_weekday(session, 5)
    apply_enrichment(
        session,
        fixtures / "Sales_Detail_sample.csv",
        fixtures / "PurchasesDetailed_sample.csv",
        period_start=MOVEMENT_PERIOD_START,
        period_end=MOVEMENT_PERIOD_END,
    )
    session.commit()

    start, end, intro = resolve_backdate_default_period(session)

    assert start == date(2025, 12, 21)
    assert end == date(2025, 12, 27)
    assert intro is not None
    assert "earliest imported movement" in intro


def test_period_import_rolls_forward(session: Session, fixtures: Path) -> None:
    apply_initial_baseline(session, fixtures / "sthold2.csv")
    apply_enrichment(
        session,
        fixtures / "Sales_Detail_sample.csv",
        fixtures / "PurchasesDetailed_sample.csv",
        period_start=MOVEMENT_PERIOD_START,
        period_end=MOVEMENT_PERIOD_END,
    )
    session.commit()

    apply_period_import(
        session,
        fixtures / "Sales_Detail_sample.csv",
        fixtures / "PurchasesDetailed_sample.csv",
        period_start="01/02/2026",
        period_end="07/02/2026",
    )
    session.commit()

    assert _baseline_qty(session, "BASE001") == pytest.approx(6.0)
    assert get_baseline_anchor_date(session) == date(2026, 2, 7)


def _write_alignment_csvs(target_dir: Path) -> tuple[Path, Path]:
    sales_header = (
        '"CODE","DEPARTMENT","MAINITEM","Descript","AvrgCost","GenCode","PurchaseOr","OnHand",'
        '"Regular_SU","SalesOrder","WIPQty","LBOnhand","Subdepartm","Category","Range","Cycle",'
        '"Sales","SalesQty","SalesCost","Refunds","RefundsQty","RefundsCost","NettSales",'
        '"NettSalesQuantity","NettCost","Profit","Purchases","Returns","VAT"'
    )
    purchases_header = (
        '"Code","Department","MainItem","Sales","Units","SalesCost","Refunds","RefundsQty",'
        '"RefundsCost","NettSales","NettCost","Profit","Purchases","RETURNS","PurchasesQT",'
        '"RETURNSQT","NettPurchases","NettPurchases_VAT","VAT"'
    )
    sales_path = target_dir / "Sales_Detail_alignment.csv"
    purchases_path = target_dir / "PurchasesDetailed_alignment.csv"
    sales_path.write_text(
        "\n".join(
            [
                sales_header,
                build_sales_detail_row(
                    "BASE001",
                    "Widget Alpha Test Item*1/1/26",
                    subdepartm="A1",
                    avg_cost=5.0,
                    on_hand=10.0,
                    sales_qty=2.0,
                    net_sales_qty=2.0,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    purchases_path.write_text(
        "\n".join(
            [
                purchases_header,
                build_purchases_row("BASE001", department="A1", sales_qty=2.0, avg_cost=5.0),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return sales_path, purchases_path


def _write_small_period_csvs(target_dir: Path) -> tuple[Path, Path]:
    sales_header = (
        '"CODE","DEPARTMENT","MAINITEM","Descript","AvrgCost","GenCode","PurchaseOr","OnHand",'
        '"Regular_SU","SalesOrder","WIPQty","LBOnhand","Subdepartm","Category","Range","Cycle",'
        '"Sales","SalesQty","SalesCost","Refunds","RefundsQty","RefundsCost","NettSales",'
        '"NettSalesQuantity","NettCost","Profit","Purchases","Returns","VAT"'
    )
    purchases_header = (
        '"Code","Department","MainItem","Sales","Units","SalesCost","Refunds","RefundsQty",'
        '"RefundsCost","NettSales","NettCost","Profit","Purchases","RETURNS","PurchasesQT",'
        '"RETURNSQT","NettPurchases","NettPurchases_VAT","VAT"'
    )
    sales_path = target_dir / "Sales_Detail_small.csv"
    purchases_path = target_dir / "PurchasesDetailed_small.csv"
    sales_path.write_text(
        "\n".join(
            [
                sales_header,
                build_sales_detail_row(
                    "BASE001",
                    "Widget Alpha Test Item*1/1/26",
                    subdepartm="A1",
                    avg_cost=5.0,
                    on_hand=6.0,
                    sales_qty=2.0,
                    net_sales_qty=2.0,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    purchases_path.write_text(
        "\n".join(
            [
                purchases_header,
                build_purchases_row("BASE001", department="A1", sales_qty=2.0, avg_cost=5.0),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return sales_path, purchases_path


def test_active_inventory_includes_items_without_turn_data(
    session: Session, fixtures: Path
) -> None:
    apply_initial_baseline(session, fixtures / "sthold2.csv")
    apply_enrichment(
        session,
        fixtures / "Sales_Detail_sample.csv",
        fixtures / "PurchasesDetailed_sample.csv",
        period_start=MOVEMENT_PERIOD_START,
        period_end=MOVEMENT_PERIOD_END,
    )
    apply_backdate_import(
        session,
        fixtures / "Sales_Detail_sample.csv",
        fixtures / "PurchasesDetailed_sample.csv",
        period_start=MOVEMENT_PERIOD_START,
        period_end=MOVEMENT_PERIOD_END,
    )
    small_sales, small_purchases = _write_small_period_csvs(fixtures)
    apply_period_import(
        session,
        small_sales,
        small_purchases,
        period_start="01/02/2026",
        period_end="07/02/2026",
    )
    session.commit()

    base003 = session.scalar(select(Item).where(Item.sku == "BASE003"))
    base002 = session.scalar(select(Item).where(Item.sku == "BASE002"))
    assert base003 is not None and base003.not_in_turn_report is True
    assert base002 is not None and base002.not_in_turn_report is True

    active_skus = {
        row[0]
        for row in fetch_inventory_rows(
            session,
            search="",
            status="Active",
            has_enrichment=True,
        )
    }
    assert "BASE001" in active_skus
    assert "BASE002" in active_skus
    assert "BASE003" in active_skus

    deprecated_skus = {
        row[0]
        for row in fetch_inventory_rows(
            session,
            search="",
            status="Deprecated",
            has_enrichment=True,
        )
    }
    assert "DEP001" in deprecated_skus
    assert "BASE003" not in deprecated_skus


def test_inventory_summary_includes_all_baseline_dept_values(
    session: Session, fixtures: Path
) -> None:
    from stock_analysis.analytics.dashboard import build_inventory_list_summary

    apply_initial_baseline(session, fixtures / "sthold2.csv")
    apply_enrichment(
        session,
        fixtures / "Sales_Detail_sample.csv",
        fixtures / "PurchasesDetailed_sample.csv",
        period_start=MOVEMENT_PERIOD_START,
        period_end=MOVEMENT_PERIOD_END,
    )
    session.commit()

    summary = build_inventory_list_summary(
        session,
        search="",
        status="Active",
        has_enrichment=True,
    )

    assert summary["item_count"] >= 5
    assert "A1" in summary["dept_values"]
    assert summary["dept_values"]["A1"] > 0
    assert summary["stock_health"]["No movement data"] >= 2


def test_inventory_dept_filter_uses_movement_line_department(
    session: Session, fixtures: Path
) -> None:
    from stock_analysis.analytics.inventory_queries import load_inventory_view_data

    apply_initial_baseline(session, fixtures / "sthold2.csv")
    apply_enrichment(
        session,
        fixtures / "Sales_Detail_sample.csv",
        fixtures / "PurchasesDetailed_sample.csv",
        period_start=MOVEMENT_PERIOD_START,
        period_end=MOVEMENT_PERIOD_END,
    )
    session.commit()

    _, summary = load_inventory_view_data(
        session,
        search="",
        status="Active",
        has_enrichment=True,
        dept="A1",
    )

    assert summary["item_count"] > 0
    assert summary["dept_values"] == {"A1": summary["dept_values"]["A1"]}
