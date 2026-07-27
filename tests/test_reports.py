"""Tests for Phase 4 reports and pivot analytics."""

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from stock_analysis.analytics.dashboard import (
    build_inventory_list_summary,
    build_period_comparison,
    build_period_summary,
    build_item_summary,
    list_period_batches,
)
from stock_analysis.analytics.pivot import build_pivot
from stock_analysis.analytics.reports import abc_report, slow_moving_report
from stock_analysis.baseline.change_log import log_change
from stock_analysis.baseline.manager import get_baseline_summary
from stock_analysis.db.models import (
    Base,
    BaselineItem,
    BaselineVersion,
    ImportBatch,
    Item,
    PeriodTurnLine,
)
from stock_analysis.db.session import set_app_state


@pytest.fixture
def db_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = Session()

    batch = ImportBatch(
        import_type="baseline_enrichment",
        file_name="turn.csv",
        period_start="01/01/2026",
        period_end="31/01/2026",
        status="applied",
    )
    session.add(batch)
    session.flush()

    version = BaselineVersion(
        version_number=1,
        source_type="initial_import",
        source_import_id=batch.id,
    )
    session.add(version)
    session.flush()

    items = [
        ("FAST001", "Fast Seller", "A1", 10.0, 5.0, 100.0),
        ("SLOW001", "Slow Mover", "B2", 20.0, 2.0, 0.0),
        ("MID001", "Mid Seller", "A1", 5.0, 4.0, 20.0),
    ]
    for sku, name, dept, on_hand, cost, sold_90 in items:
        item = Item(sku=sku, name=name, department=dept, unit_cost=cost)
        session.add(item)
        session.flush()
        session.add(
            BaselineItem(
                item_id=item.id,
                qty_on_hand=on_hand,
                baseline_version_id=version.id,
                last_update_source="initial_import",
            )
        )
        session.add(
            PeriodTurnLine(
                import_batch_id=batch.id,
                item_id=item.id,
                dept=dept,
                on_hand=on_hand,
                qty_sold_90=sold_90,
                last_unit_cost=cost,
                over_stock_qty_3mo=0.0,
                under_stock_qty_3mo=0.0,
            )
        )
        log_change(
            session,
            item_id=item.id,
            baseline_version_id=version.id,
            field_changed="qty_on_hand",
            old_value=None,
            new_value=str(on_hand),
            change_reason="initial_import",
            source_type="initial_import",
            source_import_id=batch.id,
        )

    set_app_state(session, "initial_baseline_complete", "true")
    set_app_state(session, "enrichment_complete", "true")
    session.commit()

    yield session
    session.close()


def test_list_period_batches(db_session):
    batches = list_period_batches(db_session)
    assert len(batches) == 1
    assert batches[0]["import_type"] == "baseline_enrichment"


def test_slow_moving_report(db_session):
    report = slow_moving_report(db_session, None)
    assert len(report) == 1
    assert report[0]["sku"] == "SLOW001"


def test_abc_report_classifies_items(db_session):
    report = abc_report(db_session, None)
    classes = {row["sku"]: row["abc_class"] for row in report}
    assert classes["FAST001"] == "B"
    assert classes["SLOW001"] == "C"


def test_abc_report_respects_lookback_days(db_session):
    fast = db_session.scalar(select(Item).where(Item.sku == "FAST001"))
    turn = db_session.scalar(select(PeriodTurnLine).where(PeriodTurnLine.item_id == fast.id))
    turn.qty_sold_30 = 5.0
    turn.qty_sold_90 = 100.0
    db_session.commit()

    report_30 = abc_report(db_session, None, lookback_days=30)
    by_sku = {row["sku"]: row for row in report_30}
    assert by_sku["FAST001"]["qty_sold"] == pytest.approx(5.0)
    assert by_sku["FAST001"]["sales_value"] == pytest.approx(25.0)


def test_build_pivot_by_department(db_session):
    headers, rows = build_pivot(db_session, "Department", "Qty Sold (90d)", None)
    assert headers == ["Department", "Qty Sold (90d)"]
    values = {row[0]: float(row[1]) for row in rows}
    assert values["A1"] == pytest.approx(120.0)
    assert values["B2"] == pytest.approx(0.0)


def test_build_period_summary_with_batch_id(db_session):
    batch_id = list_period_batches(db_session)[0]["id"]
    summary = build_period_summary(db_session, batch_id)
    assert summary["total_sales"] == pytest.approx(120.0)
    assert summary["total_sales_value"] == pytest.approx(580.0)
    assert summary["slow_moving"] == 1
    assert summary["slow_moving_value"] == pytest.approx(40.0)
    slow_items = summary["slow_moving_items"]
    assert len(slow_items) == 1
    assert slow_items[0]["stock_value"] == pytest.approx(40.0)
    assert summary["overstock_value"] == pytest.approx(70.0)
    assert summary["understock_value"] == pytest.approx(0.0)


def test_build_inventory_list_summary_value_fields(db_session):
    batch_id = list_period_batches(db_session)[0]["id"]
    summary = build_inventory_list_summary(
        db_session,
        search="",
        status="Active",
        batch_id=batch_id,
        has_enrichment=True,
    )
    assert summary["overstock_count"] == 2
    assert summary["overstock_value"] == pytest.approx(70.0)
    assert summary["understock_count"] == 0
    assert summary["understock_value"] == pytest.approx(0.0)
    assert summary["slow_moving_count"] == 1
    assert summary["slow_moving_value"] == pytest.approx(40.0)

    filtered = build_inventory_list_summary(
        db_session,
        search="SLOW",
        status="Active",
        batch_id=batch_id,
        has_enrichment=True,
    )
    assert filtered["item_count"] == 1
    assert filtered["overstock_count"] == 0
    assert filtered["overstock_value"] == pytest.approx(0.0)
    assert filtered["slow_moving_count"] == 1
    assert filtered["slow_moving_value"] == pytest.approx(40.0)


def test_overstock_value_does_not_exceed_baseline_stock_value(db_session):
    batch_id = list_period_batches(db_session)[0]["id"]
    slow_item = db_session.scalar(select(Item).where(Item.sku == "SLOW001"))
    turn = db_session.scalar(
        select(PeriodTurnLine).where(PeriodTurnLine.item_id == slow_item.id)
    )
    turn.last_unit_cost = 0.0
    slow_item.unit_cost = 5.0
    db_session.commit()

    period = build_period_summary(db_session, batch_id)
    baseline = get_baseline_summary(db_session)
    assert period["overstock_value"] <= baseline["total_value"]
    batch_id = list_period_batches(db_session)[0]["id"]
    fast_item = db_session.scalar(select(Item).where(Item.sku == "FAST001"))
    fast_baseline = db_session.scalar(
        select(BaselineItem).where(BaselineItem.item_id == fast_item.id)
    )
    fast_baseline.qty_on_hand = 15.0
    turn = db_session.scalar(
        select(PeriodTurnLine).where(PeriodTurnLine.item_id == fast_item.id)
    )
    turn.on_hand = 10.0
    db_session.commit()

    headers, rows = build_pivot(db_session, "Department", "Stock Value", batch_id)
    values = {row[0]: float(row[1]) for row in rows}
    assert values["A1"] == pytest.approx(15 * 5.0 + 5 * 4.0)


def test_overstock_alerts_exclude_understocked_fast_mover(db_session):
    batch_id = list_period_batches(db_session)[0]["id"]
    fast_item = db_session.scalar(select(Item).where(Item.sku == "FAST001"))
    turn = db_session.scalar(
        select(PeriodTurnLine).where(PeriodTurnLine.item_id == fast_item.id)
    )
    turn.over_stock_qty_3mo = 10.0
    turn.under_stock_qty_3mo = -20.0
    turn.avg_monthly_sales_3mo = 30.0
    turn.on_hand = 10.0
    db_session.commit()

    summary = build_period_summary(db_session, batch_id)
    codes = {row["code"] for row in summary["overstock_alerts"]}
    assert "FAST001" not in codes
    under_codes = {row["code"] for row in summary["reorder_alerts"]}
    assert "FAST001" in under_codes


def test_period_comparison_delta_none_when_prior_zero(db_session):
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    batch_a = ImportBatch(
        import_type="period_turn",
        file_name="a.csv",
        period_start="01/02/2026",
        period_end="28/02/2026",
        status="applied",
        imported_at=now - timedelta(days=1),
    )
    batch_b = ImportBatch(
        import_type="period_turn",
        file_name="b.csv",
        period_start="01/03/2026",
        period_end="31/03/2026",
        status="applied",
        imported_at=now,
    )
    db_session.add_all([batch_a, batch_b])
    db_session.flush()

    fast_item = db_session.scalar(select(Item).where(Item.sku == "FAST001"))
    slow_item = db_session.scalar(select(Item).where(Item.sku == "SLOW001"))
    db_session.add(
        PeriodTurnLine(
            import_batch_id=batch_a.id,
            item_id=fast_item.id,
            dept="A1",
            on_hand=10.0,
            qty_sold_90=100.0,
            last_unit_cost=5.0,
            avg_monthly_sales_3mo=33.0,
        )
    )
    db_session.add(
        PeriodTurnLine(
            import_batch_id=batch_b.id,
            item_id=fast_item.id,
            dept="A1",
            on_hand=10.0,
            qty_sold_90=100.0,
            last_unit_cost=5.0,
            avg_monthly_sales_3mo=33.0,
        )
    )
    db_session.add(
        PeriodTurnLine(
            import_batch_id=batch_b.id,
            item_id=slow_item.id,
            dept="B2",
            on_hand=20.0,
            qty_sold_90=0.0,
            last_unit_cost=2.0,
            avg_monthly_sales_3mo=0.0,
        )
    )
    db_session.commit()

    comparison = build_period_comparison(db_session, batch_b.id)
    assert comparison["slow_moving_value_delta_pct"] is None


def test_build_item_summary_uses_turn_unit_cost(db_session):
    fast_item = db_session.scalar(select(Item).where(Item.sku == "FAST001"))
    turn = db_session.scalar(
        select(PeriodTurnLine).where(PeriodTurnLine.item_id == fast_item.id)
    )
    turn.last_unit_cost = 8.0
    fast_item.unit_cost = 5.0
    db_session.commit()

    summary = build_item_summary(db_session, fast_item.id)
    assert summary["stock_value"] == pytest.approx(10.0 * 8.0)
