"""Tests for movement import delta logic."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from stock_analysis.baseline.manager import (
    BackdateValidationError,
    apply_backdate_import,
    apply_enrichment,
    apply_initial_baseline,
    apply_period_import,
)
from stock_analysis.analytics.inventory_queries import fetch_inventory_rows
from stock_analysis.db.models import Base, BaselineItem, Item, PeriodTurnLine
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
    assert line.gross_margin_pct == pytest.approx(0.0)


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

    apply_backdate_import(
        session,
        fixtures / "Sales_Detail_sample.csv",
        fixtures / "PurchasesDetailed_sample.csv",
        period_start=MOVEMENT_PERIOD_START,
        period_end=MOVEMENT_PERIOD_END,
    )
    session.commit()

    assert _baseline_qty(session, "BASE001") == pytest.approx(10.0)
    assert _baseline_qty(session, "BASE002") == pytest.approx(5.0)
    assert _baseline_qty(session, "BASE004") == pytest.approx(20.0)


def test_backdate_blocks_negative_qty(session: Session, fixtures: Path) -> None:
    apply_initial_baseline(session, fixtures / "sthold2.csv")
    session.commit()

    with pytest.raises(BackdateValidationError):
        apply_backdate_import(
            session,
            fixtures / "Sales_Detail_sample.csv",
            fixtures / "PurchasesDetailed_sample.csv",
            period_start=MOVEMENT_PERIOD_START,
            period_end=MOVEMENT_PERIOD_END,
        )


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
