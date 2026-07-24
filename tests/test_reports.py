"""Tests for Phase 4 reports and pivot analytics."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from stock_analysis.analytics.dashboard import build_period_summary, list_period_batches
from stock_analysis.analytics.pivot import build_pivot
from stock_analysis.analytics.reports import abc_report, slow_moving_report
from stock_analysis.baseline.change_log import log_change
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
    assert classes["FAST001"] == "A"
    assert classes["SLOW001"] == "C"


def test_build_pivot_by_department(db_session):
    headers, rows = build_pivot(db_session, "Department", "Qty Sold (90d)", None)
    assert headers == ["Department", "Qty Sold (90d)"]
    values = {row[0]: float(row[1]) for row in rows}
    assert values["A1"] == pytest.approx(120.0)
    assert values["B2"] == pytest.approx(0.0)


def test_build_period_summary_with_batch_id(db_session):
    batch_id = list_period_batches(db_session)[0]["id"]
    summary = build_period_summary(db_session, batch_id)
    assert summary["total_sales_90"] == pytest.approx(120.0)
    assert summary["slow_moving"] == 1
