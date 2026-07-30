"""Tests for Phase 4 reports and pivot analytics."""

import math

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from stock_analysis.analytics.dashboard import (
    build_inventory_list_summary,
    build_period_comparison,
    build_period_summary,
    build_item_summary,
    get_lookback_period_lines,
    list_period_batches,
)
from stock_analysis.analytics.lookback import build_multi_batch_qty_map, item_qty_sold
from stock_analysis.analytics.metrics import effective_on_hand, stock_position_from_weekly_sales
from stock_analysis.analytics.queries import baseline_qty_map, get_optimum_stock_months
from stock_analysis.analytics.pivot import build_pivot
from stock_analysis.analytics.reports import abc_report, slow_moving_report, understocked_report
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
        period_end="07/01/2026",
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
    report = slow_moving_report(db_session)
    assert len(report) == 1
    assert report[0]["sku"] == "SLOW001"


def test_slow_moving_report_dept_filter(db_session):
    report = slow_moving_report(db_session, dept_filter="B2")
    assert len(report) == 1
    assert report[0]["sku"] == "SLOW001"

    assert slow_moving_report(db_session, dept_filter="A1") == []


def test_understocked_report(db_session):
    report = understocked_report(db_session)
    skus = {row["sku"] for row in report}
    assert "FAST001" in skus
    assert "MID001" in skus
    assert "SLOW001" not in skus

    for row in report:
        assert row["units_under"] > 0
        assert row["units_under"] == int(row["units_under"])
        assert row["purchase_cost"] == pytest.approx(row["units_under"] * row["unit_cost"])

    assert len(report) == 2
    assert report[0]["sku"] == "FAST001"
    assert report[0]["purchase_cost"] > report[1]["purchase_cost"]


def test_understocked_report_units_under_are_ceiled(db_session):
    report = understocked_report(db_session)
    lines = get_lookback_period_lines(db_session, 1)
    baseline_map = baseline_qty_map(db_session, [item.id for _, item in lines])
    qty_map = build_multi_batch_qty_map(db_session, 1)
    optimum_months = get_optimum_stock_months(db_session)

    by_sku = {item.sku: (line, item) for line, item in lines}
    for row in report:
        line, item = by_sku[row["sku"]]
        on_hand = effective_on_hand(baseline_map, item.id, line.on_hand)
        sold = item_qty_sold(qty_map, item.id)
        _, under_qty = stock_position_from_weekly_sales(
            on_hand, sold, 1, optimum_months
        )
        assert row["units_under"] == math.ceil(abs(under_qty))


def test_abc_report_classifies_items(db_session):
    report = abc_report(db_session)
    classes = {row["sku"]: row["abc_class"] for row in report}
    assert classes["FAST001"] == "B"
    assert classes["SLOW001"] == "C"


def test_abc_report_dept_filter(db_session):
    report = abc_report(db_session, dept_filter="A1")
    skus = {row["sku"] for row in report}
    assert skus == {"FAST001", "MID001"}

    classes = {row["sku"]: row["abc_class"] for row in report}
    assert classes["FAST001"] == "B"
    assert classes["MID001"] == "C"


def test_abc_report_respects_lookback_weeks(db_session):
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    older = ImportBatch(
        import_type="period_turn",
        file_name="older.csv",
        period_start="08/12/2025",
        period_end="14/12/2025",
        imported_at=now - timedelta(days=2),
    )
    db_session.add(older)
    db_session.flush()

    fast = db_session.scalar(select(Item).where(Item.sku == "FAST001"))
    db_session.add(
        PeriodTurnLine(
            import_batch_id=older.id,
            item_id=fast.id,
            dept="A1",
            on_hand=10.0,
            qty_sold_90=50.0,
            last_unit_cost=5.0,
        )
    )
    db_session.commit()

    report_1w = abc_report(db_session, lookback_weeks=1)
    by_sku = {row["sku"]: row for row in report_1w}
    assert by_sku["FAST001"]["qty_sold"] == pytest.approx(100.0)

    report_2w = abc_report(db_session, lookback_weeks=2)
    by_sku_2w = {row["sku"]: row for row in report_2w}
    assert by_sku_2w["FAST001"]["qty_sold"] == pytest.approx(150.0)


def test_build_pivot_by_department(db_session):
    headers, rows = build_pivot(db_session, "Department", "Qty Sold (1w)")
    assert headers == ["Department", "Qty Sold (1w)"]
    values = {row[0]: float(row[1]) for row in rows}
    assert values["A1"] == pytest.approx(120.0)
    assert values["B2"] == pytest.approx(0.0)


def test_build_pivot_dept_filter(db_session):
    headers, rows = build_pivot(
        db_session, "Department", "Qty Sold (1w)", dept_filter="A1"
    )
    assert headers == ["Department", "Qty Sold (1w)"]
    values = {row[0]: float(row[1]) for row in rows}
    assert values == {"A1": pytest.approx(120.0)}


def test_build_period_summary(db_session):
    summary = build_period_summary(db_session)
    assert summary["total_sales"] == pytest.approx(120.0)
    assert summary["total_sales_value"] == pytest.approx(580.0)
    assert summary["slow_moving"] == 1
    assert summary["slow_moving_value"] == pytest.approx(40.0)
    slow_items = summary["slow_moving_items"]
    assert len(slow_items) == 1
    assert slow_items[0]["unit_cost"] == pytest.approx(2.0)
    assert slow_items[0]["stock_value"] == pytest.approx(40.0)
    assert summary["overstock_value"] == pytest.approx(0.0)
    assert summary["understock_value"] == pytest.approx(4901.428571428571)


def test_build_inventory_list_summary_value_fields(db_session):
    summary = build_inventory_list_summary(
        db_session,
        search="",
        status="Active",
        has_enrichment=True,
    )
    assert summary["overstock_count"] == 0
    assert summary["overstock_value"] == pytest.approx(0.0)
    assert summary["understock_count"] == 2
    assert summary["understock_value"] == pytest.approx(4901.428571428571)
    assert summary["slow_moving_count"] == 1
    assert summary["slow_moving_value"] == pytest.approx(40.0)

    filtered = build_inventory_list_summary(
        db_session,
        search="SLOW",
        status="Active",
        has_enrichment=True,
    )
    assert filtered["item_count"] == 1
    assert filtered["overstock_count"] == 0
    assert filtered["overstock_value"] == pytest.approx(0.0)
    assert filtered["slow_moving_count"] == 1
    assert filtered["slow_moving_value"] == pytest.approx(40.0)


def test_overstock_value_does_not_exceed_baseline_stock_value(db_session):
    slow_item = db_session.scalar(select(Item).where(Item.sku == "SLOW001"))
    turn = db_session.scalar(
        select(PeriodTurnLine).where(PeriodTurnLine.item_id == slow_item.id)
    )
    turn.last_unit_cost = 0.0
    slow_item.unit_cost = 5.0
    db_session.commit()

    period = build_period_summary(db_session)
    baseline = get_baseline_summary(db_session)
    assert period["overstock_value"] <= baseline["total_value"]
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

    headers, rows = build_pivot(db_session, "Department", "Stock Value")
    values = {row[0]: float(row[1]) for row in rows}
    assert values["A1"] == pytest.approx(15 * 5.0 + 5 * 4.0)


def test_overstock_alerts_exclude_understocked_fast_mover(db_session):
    fast_item = db_session.scalar(select(Item).where(Item.sku == "FAST001"))
    turn = db_session.scalar(
        select(PeriodTurnLine).where(PeriodTurnLine.item_id == fast_item.id)
    )
    turn.over_stock_qty_3mo = 10.0
    turn.under_stock_qty_3mo = -20.0
    turn.avg_monthly_sales_3mo = 30.0
    turn.on_hand = 10.0
    db_session.commit()

    summary = build_period_summary(db_session)
    codes = {row["code"] for row in summary["overstock_alerts"]}
    assert "FAST001" not in codes
    under_codes = {row["code"] for row in summary["reorder_alerts"]}
    assert "FAST001" in under_codes


def test_margin_and_markup_alerts(db_session):
    fast = db_session.scalar(select(Item).where(Item.sku == "FAST001"))
    mid = db_session.scalar(select(Item).where(Item.sku == "MID001"))
    line_fast = db_session.scalar(
        select(PeriodTurnLine).where(PeriodTurnLine.item_id == fast.id)
    )
    line_fast.net_sales_revenue = 200.0
    line_fast.gross_profit = 80.0
    line_mid = db_session.scalar(
        select(PeriodTurnLine).where(PeriodTurnLine.item_id == mid.id)
    )
    line_mid.net_sales_revenue = 100.0
    line_mid.gross_profit = 25.0
    db_session.commit()

    summary = build_period_summary(db_session)
    margin_codes = {row["code"] for row in summary["margin_alerts"]}
    assert margin_codes == {"FAST001", "MID001"}
    assert "SLOW001" not in margin_codes

    markup_codes = {row["code"] for row in summary["markup_alerts"]}
    assert markup_codes == {"FAST001", "MID001"}

    margins = [r["gross_margin_pct"] for r in summary["margin_alerts"]]
    assert margins[0] <= margins[1]

    fast_margin = next(r for r in summary["margin_alerts"] if r["code"] == "FAST001")
    assert fast_margin["gross_margin_pct"] == pytest.approx(40.0)
    assert fast_margin["gross_profit"] == pytest.approx(80.0)

    fast_markup = next(r for r in summary["markup_alerts"] if r["code"] == "FAST001")
    assert fast_markup["markup_pct"] == pytest.approx(66.6666666667)


def test_period_comparison_delta_none_when_prior_zero(db_session):
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    enrichment = db_session.scalar(
        select(ImportBatch).where(ImportBatch.import_type == "baseline_enrichment")
    )
    enrichment.imported_at = now - timedelta(days=10)
    batch_a = ImportBatch(
        import_type="period_turn",
        file_name="a.csv",
        period_start="01/02/2026",
        period_end="07/02/2026",
        status="applied",
        imported_at=now - timedelta(days=1),
    )
    batch_b = ImportBatch(
        import_type="period_turn",
        file_name="b.csv",
        period_start="08/02/2026",
        period_end="14/02/2026",
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

    comparison = build_period_comparison(db_session)
    assert comparison["slow_moving_value_delta_pct"] is None


def test_top_sellers_respects_lookback_weeks(db_session):
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    batch_a = ImportBatch(
        import_type="period_turn",
        file_name="a.csv",
        period_start="01/02/2026",
        period_end="07/02/2026",
        imported_at=now - timedelta(days=1),
    )
    batch_b = ImportBatch(
        import_type="period_turn",
        file_name="b.csv",
        period_start="08/02/2026",
        period_end="14/02/2026",
        imported_at=now,
    )
    db_session.add_all([batch_a, batch_b])
    db_session.flush()

    fast = db_session.scalar(select(Item).where(Item.sku == "FAST001"))
    db_session.add(
        PeriodTurnLine(
            import_batch_id=batch_a.id,
            item_id=fast.id,
            dept="A1",
            on_hand=10.0,
            qty_sold_90=50.0,
            last_unit_cost=5.0,
        )
    )
    db_session.add(
        PeriodTurnLine(
            import_batch_id=batch_b.id,
            item_id=fast.id,
            dept="A1",
            on_hand=10.0,
            qty_sold_90=100.0,
            last_unit_cost=5.0,
        )
    )
    db_session.commit()

    summary_1w = build_period_summary(db_session, lookback_weeks=1)
    summary_2w = build_period_summary(db_session, lookback_weeks=2)
    top_1w = {row["code"]: row["qty_sold"] for row in summary_1w["top_sellers"]}
    top_2w = {row["code"]: row["qty_sold"] for row in summary_2w["top_sellers"]}
    assert top_1w["FAST001"] == pytest.approx(100.0)
    assert top_2w["FAST001"] == pytest.approx(150.0)


def test_reorder_alerts_respects_lookback_weeks(db_session):
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    batch_a = ImportBatch(
        import_type="period_turn",
        file_name="a.csv",
        period_start="01/02/2026",
        period_end="07/02/2026",
        imported_at=now - timedelta(days=1),
    )
    batch_b = ImportBatch(
        import_type="period_turn",
        file_name="b.csv",
        period_start="08/02/2026",
        period_end="14/02/2026",
        imported_at=now,
    )
    db_session.add_all([batch_a, batch_b])
    db_session.flush()

    fast = db_session.scalar(select(Item).where(Item.sku == "FAST001"))
    db_session.add(
        PeriodTurnLine(
            import_batch_id=batch_a.id,
            item_id=fast.id,
            dept="A1",
            on_hand=10.0,
            qty_sold_90=50.0,
            last_unit_cost=5.0,
        )
    )
    db_session.add(
        PeriodTurnLine(
            import_batch_id=batch_b.id,
            item_id=fast.id,
            dept="A1",
            on_hand=10.0,
            qty_sold_90=100.0,
            last_unit_cost=5.0,
        )
    )
    db_session.commit()

    summary_1w = build_period_summary(db_session, lookback_weeks=1)
    summary_2w = build_period_summary(db_session, lookback_weeks=2)
    alerts_1w = {row["code"]: row["under_qty"] for row in summary_1w["reorder_alerts"]}
    alerts_2w = {row["code"]: row["under_qty"] for row in summary_2w["reorder_alerts"]}
    assert "FAST001" in alerts_1w
    assert "FAST001" in alerts_2w
    assert alerts_1w["FAST001"] != alerts_2w["FAST001"]


def test_inventory_summary_uses_lookback_period_lines(db_session):
    from datetime import UTC, datetime, timedelta

    from stock_analysis.analytics.dashboard import build_inventory_list_summary

    now = datetime.now(UTC)
    enrichment = ImportBatch(
        import_type="baseline_enrichment",
        file_name="enrich.csv",
        period_start="01/02/2026",
        period_end="07/02/2026",
        imported_at=now,
    )
    backdate = ImportBatch(
        import_type="period_turn_backdate",
        file_name="backdate.csv",
        period_start="01/02/2026",
        period_end="07/02/2026",
        imported_at=now - timedelta(days=1),
    )
    db_session.add_all([enrichment, backdate])
    db_session.flush()

    fast = db_session.scalar(select(Item).where(Item.sku == "FAST001"))
    slow = db_session.scalar(select(Item).where(Item.sku == "SLOW001"))
    db_session.add(
        PeriodTurnLine(
            import_batch_id=enrichment.id,
            item_id=fast.id,
            dept="A1",
            on_hand=10.0,
            qty_sold_90=1.0,
            last_unit_cost=5.0,
            avg_monthly_sales_3mo=50.0,
        )
    )
    db_session.add(
        PeriodTurnLine(
            import_batch_id=backdate.id,
            item_id=fast.id,
            dept="A1",
            on_hand=10.0,
            qty_sold_90=100.0,
            last_unit_cost=5.0,
            avg_monthly_sales_3mo=50.0,
        )
    )
    db_session.add(
        PeriodTurnLine(
            import_batch_id=backdate.id,
            item_id=slow.id,
            dept="B2",
            on_hand=20.0,
            qty_sold_90=0.0,
            last_unit_cost=2.0,
            avg_monthly_sales_3mo=0.0,
        )
    )
    db_session.commit()

    summary = build_inventory_list_summary(
        db_session,
        search="",
        status="Active",
        has_enrichment=True,
        lookback_weeks=2,
    )
    health = summary["stock_health"]
    assert health["Slow Moving"] == 1
    assert summary["slow_moving_value"] == pytest.approx(40.0)

    period = build_period_summary(db_session, lookback_weeks=2)
    assert period["slow_moving"] == 1
    assert period["total_sales_value"] == pytest.approx(505.0)


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


def test_build_item_summary_sales_mix_uses_weekly_lookback(db_session):
    from datetime import UTC, datetime, timedelta

    fast_item = db_session.scalar(select(Item).where(Item.sku == "FAST001"))
    enrichment = db_session.scalar(
        select(ImportBatch).where(ImportBatch.import_type == "baseline_enrichment")
    )
    enrichment.period_start = "08/01/2026"
    enrichment.period_end = "14/01/2026"
    enrichment.imported_at = datetime.now(UTC)
    now = datetime.now(UTC)
    older_batch = ImportBatch(
        import_type="period_turn_backdate",
        file_name="week1.csv",
        period_start="01/01/2026",
        period_end="07/01/2026",
        imported_at=now - timedelta(days=7),
    )
    db_session.add(older_batch)
    db_session.flush()
    db_session.add(
        PeriodTurnLine(
            import_batch_id=older_batch.id,
            item_id=fast_item.id,
            dept="A1",
            on_hand=10.0,
            qty_sold_90=50.0,
            last_unit_cost=5.0,
        )
    )
    db_session.commit()

    summary_1w = build_item_summary(db_session, fast_item.id, lookback_weeks=1)
    summary_2w = build_item_summary(db_session, fast_item.id, lookback_weeks=2)

    assert summary_1w["qty_sold"] == pytest.approx(100.0)
    assert summary_2w["qty_sold"] == pytest.approx(150.0)
    assert summary_1w["under_qty"] != summary_2w["under_qty"]
    assert summary_2w["sales_mix_pie"] == {
        "01/01/2026 – 07/01/2026": 50.0,
        "08/01/2026 – 14/01/2026": 100.0,
    }
    assert summary_2w["sales_chart_data"]["qty_sold"] == [50.0, 100.0]
