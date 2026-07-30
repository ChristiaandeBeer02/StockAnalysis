"""Performance-related unit tests."""

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from stock_analysis.analytics.cache import get_period_summary_cached, invalidate_summaries, load_summaries
from stock_analysis.analytics.dashboard import get_item_turn_history_with_batches
from stock_analysis.analytics.reports import abc_report, abc_summary
from stock_analysis.baseline.manager import get_baseline_summary
from stock_analysis.db.models import Base, ImportBatch, Item, PeriodTurnLine


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    sess = factory()
    try:
        yield sess
    finally:
        sess.close()


def _seed_period_data(session: Session) -> None:
    batch_a = ImportBatch(import_type="period_turn", file_name="a.csv", period_start="2025-01", period_end="2025-02")
    batch_b = ImportBatch(import_type="period_turn", file_name="b.csv", period_start="2025-03", period_end="2025-04")
    session.add_all([batch_a, batch_b])
    session.flush()

    item = Item(sku="SKU1", name="Widget", unit_cost=10.0)
    session.add(item)
    session.flush()

    session.add_all(
        [
            PeriodTurnLine(import_batch_id=batch_a.id, item_id=item.id, qty_sold_90=5),
            PeriodTurnLine(import_batch_id=batch_b.id, item_id=item.id, qty_sold_90=12),
        ]
    )
    session.commit()


def test_get_item_turn_history_with_batches_single_query(session: Session) -> None:
    _seed_period_data(session)
    history = get_item_turn_history_with_batches(session, session.scalar(select(Item.id)))
    assert len(history) == 2
    assert history[0][1].file_name == "b.csv"
    assert history[1][1].file_name == "a.csv"


def test_summary_cache_deduplicates_period_summary(session: Session) -> None:
    _seed_period_data(session)
    invalidate_summaries()
    load_summaries(session)
    first = get_period_summary_cached(session)
    second = get_period_summary_cached(session)
    assert first is second


def test_abc_summary_reuses_report(session: Session) -> None:
    _seed_period_data(session)
    report = abc_report(session)
    summary = abc_summary(session, report=report)
    assert sum(summary.values()) == len(report)


def test_get_baseline_summary_uses_sql_aggregate(session: Session) -> None:
    from stock_analysis.db.models import BaselineItem, BaselineVersion

    item = Item(sku="A1", name="A", unit_cost=2.5)
    session.add(item)
    session.flush()
    version = BaselineVersion(version_number=1, source_type="test")
    session.add(version)
    session.flush()
    session.add(
        BaselineItem(
            item_id=item.id,
            qty_on_hand=4,
            baseline_version_id=version.id,
            last_update_source="test",
        )
    )
    session.commit()

    summary = get_baseline_summary(session)
    assert summary["total_value"] == pytest.approx(10.0)
    assert "overstock_items" not in summary


def test_get_baseline_summary_prefers_latest_turn_cost(session: Session) -> None:
    from stock_analysis.db.models import BaselineItem, BaselineVersion

    batch = ImportBatch(import_type="period_turn", file_name="turn.csv", status="applied")
    session.add(batch)
    session.flush()
    item = Item(sku="COST1", name="Cost Test", unit_cost=5.0)
    session.add(item)
    session.flush()
    version = BaselineVersion(version_number=1, source_type="test")
    session.add(version)
    session.flush()
    session.add(
        BaselineItem(
            item_id=item.id,
            qty_on_hand=10,
            baseline_version_id=version.id,
            last_update_source="test",
        )
    )
    session.add(
        PeriodTurnLine(
            import_batch_id=batch.id,
            item_id=item.id,
            on_hand=10.0,
            last_unit_cost=8.0,
        )
    )
    session.commit()

    summary = get_baseline_summary(session)
    assert summary["total_value"] == pytest.approx(80.0)


def test_get_baseline_summary_zero_turn_cost_falls_back_to_item(session: Session) -> None:
    from stock_analysis.db.models import BaselineItem, BaselineVersion

    batch = ImportBatch(import_type="period_turn", file_name="turn.csv", status="applied")
    session.add(batch)
    session.flush()
    item = Item(sku="COST0", name="Zero Turn Cost", unit_cost=5.0)
    session.add(item)
    session.flush()
    version = BaselineVersion(version_number=1, source_type="test")
    session.add(version)
    session.flush()
    session.add(
        BaselineItem(
            item_id=item.id,
            qty_on_hand=10,
            baseline_version_id=version.id,
            last_update_source="test",
        )
    )
    session.add(
        PeriodTurnLine(
            import_batch_id=batch.id,
            item_id=item.id,
            on_hand=10.0,
            last_unit_cost=0.0,
        )
    )
    session.commit()

    summary = get_baseline_summary(session)
    assert summary["total_value"] == pytest.approx(50.0)
