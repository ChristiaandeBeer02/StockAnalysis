"""Tests for SKUs that have never received a PeriodTurnLine."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from stock_analysis.analytics.inventory_queries import list_no_movement_items
from stock_analysis.baseline.manager import (
    apply_enrichment,
    apply_initial_baseline,
    apply_period_import,
)
from stock_analysis.db.models import Base, Item, PeriodTurnLine
from tests.helpers.import_snapshot import (
    MOVEMENT_PERIOD_END,
    MOVEMENT_PERIOD_START,
    write_fixture_csvs,
)
from tests.test_movement_import import _write_small_period_csvs


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


def test_list_no_movement_items_never_had_turn_line(session: Session, fixtures: Path) -> None:
    apply_initial_baseline(session, fixtures / "sthold2.csv")
    apply_enrichment(
        session,
        fixtures / "Sales_Detail_sample.csv",
        fixtures / "PurchasesDetailed_sample.csv",
        period_start=MOVEMENT_PERIOD_START,
        period_end=MOVEMENT_PERIOD_END,
    )
    session.commit()

    skus = {row["code"] for row in list_no_movement_items(session)}
    assert "BASE003" in skus
    assert "BASE005" in skus
    assert "BASE001" not in skus
    assert "DEP001" not in skus

    base003 = next(row for row in list_no_movement_items(session) if row["code"] == "BASE003")
    assert base003["qty_sold"] == 0.0
    assert "on_hand" in base003
    assert "unit_cost" in base003
    assert "stock_value" in base003
    assert "dept" in base003


def test_list_no_movement_excludes_dropped_off_latest_report(
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
    small_sales, small_purchases = _write_small_period_csvs(fixtures)
    apply_period_import(
        session,
        small_sales,
        small_purchases,
        period_start="01/02/2026",
        period_end="07/02/2026",
    )
    session.commit()

    base002 = session.scalar(select(Item).where(Item.sku == "BASE002"))
    assert base002 is not None
    assert base002.not_in_turn_report is True
    assert session.scalar(
        select(PeriodTurnLine.id).where(PeriodTurnLine.item_id == base002.id).limit(1)
    )

    skus = {row["code"] for row in list_no_movement_items(session)}
    assert "BASE002" not in skus
    assert "BASE003" in skus
    assert "BASE001" not in skus
