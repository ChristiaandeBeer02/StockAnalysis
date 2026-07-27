"""Tests for sales lookback helpers."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from stock_analysis.analytics.lookback import (
    build_prior_qty_map,
    get_lookback_days,
    qty_sold,
    set_lookback_days,
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


def _line(**kwargs) -> PeriodTurnLine:
    defaults = {
        "qty_sold_30": 10.0,
        "qty_sold_90": 40.0,
        "qty_sold_180": 80.0,
    }
    defaults.update(kwargs)
    return PeriodTurnLine(import_batch_id=1, item_id=1, **defaults)


def test_qty_sold_30_and_90():
    line = _line()
    assert qty_sold(line, 30) == 10.0
    assert qty_sold(line, 90) == 40.0


def test_qty_sold_60_interpolated():
    line = _line()
    assert qty_sold(line, 60) == pytest.approx(25.0)


def test_qty_sold_60_two_period():
    line = _line(qty_sold_30=12.0)
    assert qty_sold(line, 60, prior_qty_30=8.0, use_two_period_60=True) == 20.0


def test_build_prior_qty_map_two_period(db_session):
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    batch_a = ImportBatch(
        import_type="period_turn",
        file_name="a.csv",
        imported_at=now - timedelta(days=1),
    )
    batch_b = ImportBatch(
        import_type="period_turn",
        file_name="b.csv",
        imported_at=now,
    )
    db_session.add_all([batch_a, batch_b])
    db_session.flush()

    item = Item(sku="A001", name="Item")
    db_session.add(item)
    db_session.flush()

    db_session.add(
        PeriodTurnLine(
            import_batch_id=batch_a.id,
            item_id=item.id,
            qty_sold_30=5.0,
            qty_sold_90=20.0,
        )
    )
    db_session.add(
        PeriodTurnLine(
            import_batch_id=batch_b.id,
            item_id=item.id,
            qty_sold_30=7.0,
            qty_sold_90=25.0,
        )
    )
    db_session.commit()

    prior_map, source = build_prior_qty_map(db_session, batch_b.id)
    assert source == "two_period"
    assert prior_map[item.id] == 5.0


def test_build_prior_qty_map_interpolated_without_prior(db_session):
    batch = ImportBatch(import_type="baseline_enrichment", file_name="enrich.csv")
    db_session.add(batch)
    db_session.commit()

    prior_map, source = build_prior_qty_map(db_session, batch.id)
    assert source == "interpolated"
    assert prior_map == {}


def test_lookback_days_persistence(db_session):
    set_lookback_days(db_session, 30)
    db_session.commit()
    assert get_lookback_days(db_session) == 30

    set_lookback_days(db_session, 99)
    db_session.commit()
    assert get_lookback_days(db_session) == 90
