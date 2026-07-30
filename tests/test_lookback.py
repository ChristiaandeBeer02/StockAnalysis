"""Tests for sales lookback helpers."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from stock_analysis.analytics.lookback import (
    build_multi_batch_qty_map,
    build_multi_batch_sales_totals,
    get_available_sales_weeks,
    get_batch_ids_for_weeks,
    get_lookback_weeks,
    list_sales_batches,
    resolve_lookback_weeks,
    set_lookback_weeks,
)
from stock_analysis.db.models import Base, ImportBatch, Item, PeriodTurnLine


@pytest.fixture
def db_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = Session()
    yield session
    session.close()


def _add_batch(session, *, period_end: str, period_start: str, import_type: str = "period_turn"):
    batch = ImportBatch(
        import_type=import_type,
        file_name=f"{period_end}.csv",
        period_start=period_start,
        period_end=period_end,
    )
    session.add(batch)
    session.flush()
    return batch


def test_list_sales_batches_orders_by_period_end_desc(db_session):
    older = _add_batch(db_session, period_start="01/01/2026", period_end="07/01/2026")
    newer = _add_batch(db_session, period_start="08/01/2026", period_end="14/01/2026")
    db_session.commit()

    batches = list_sales_batches(db_session)
    assert [batch.id for batch in batches] == [newer.id, older.id]


def test_build_multi_batch_qty_map_sums_across_batches(db_session):
    batch_a = _add_batch(db_session, period_start="01/01/2026", period_end="07/01/2026")
    batch_b = _add_batch(db_session, period_start="08/01/2026", period_end="14/01/2026")
    item = Item(sku="A001", name="Item")
    db_session.add(item)
    db_session.flush()
    db_session.add(
        PeriodTurnLine(import_batch_id=batch_a.id, item_id=item.id, qty_sold_90=3.0)
    )
    db_session.add(
        PeriodTurnLine(import_batch_id=batch_b.id, item_id=item.id, qty_sold_90=5.0)
    )
    db_session.commit()

    qty_map = build_multi_batch_qty_map(db_session, 2)
    assert qty_map[item.id] == pytest.approx(8.0)

    one_week = build_multi_batch_qty_map(db_session, 1)
    assert one_week[item.id] == pytest.approx(5.0)


def test_build_multi_batch_sales_totals_sums_across_batches(db_session):
    batch_a = _add_batch(db_session, period_start="01/01/2026", period_end="07/01/2026")
    batch_b = _add_batch(db_session, period_start="08/01/2026", period_end="14/01/2026")
    item = Item(sku="A001", name="Item")
    db_session.add(item)
    db_session.flush()
    db_session.add(
        PeriodTurnLine(
            import_batch_id=batch_a.id,
            item_id=item.id,
            net_sales_revenue=10.0,
            gross_profit=2.0,
        )
    )
    db_session.add(
        PeriodTurnLine(
            import_batch_id=batch_b.id,
            item_id=item.id,
            net_sales_revenue=30.0,
            gross_profit=6.0,
        )
    )
    db_session.commit()

    totals = build_multi_batch_sales_totals(db_session, 2)
    revenue, profit = totals[item.id]
    assert revenue == pytest.approx(40.0)
    assert profit == pytest.approx(8.0)


def test_get_batch_ids_for_weeks_offset(db_session):
    batch_a = _add_batch(db_session, period_start="01/01/2026", period_end="07/01/2026")
    batch_b = _add_batch(db_session, period_start="08/01/2026", period_end="14/01/2026")
    db_session.commit()

    assert get_batch_ids_for_weeks(db_session, 1) == [batch_b.id]
    assert get_batch_ids_for_weeks(db_session, 1, offset=1) == [batch_a.id]


def test_resolve_lookback_weeks_clamps(db_session):
    _add_batch(db_session, period_start="01/01/2026", period_end="07/01/2026")
    _add_batch(db_session, period_start="08/01/2026", period_end="14/01/2026")
    db_session.commit()

    assert get_available_sales_weeks(db_session) == 2
    effective, was_clamped = resolve_lookback_weeks(db_session, 4)
    assert effective == 2
    assert was_clamped is True


def test_lookback_weeks_persistence(db_session):
    set_lookback_weeks(db_session, 3)
    db_session.commit()
    assert get_lookback_weeks(db_session) == 3

    set_lookback_weeks(db_session, 0)
    db_session.commit()
    assert get_lookback_weeks(db_session) == 1
