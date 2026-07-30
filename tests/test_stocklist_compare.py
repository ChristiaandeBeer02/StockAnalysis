"""Tests for StockLists on-hand comparison and override."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from stock_analysis.analytics.movement_projection import project_post_movement_on_hand
from stock_analysis.analytics.stocklist_compare import compare_movement_to_stocklist
from stock_analysis.baseline.manager import (
    apply_enrichment,
    apply_initial_baseline,
    apply_stocklist_override,
)
from stock_analysis.db.models import Base, BaselineItem, Item
from stock_analysis.importers.movement_parser import merge_movement_reports
from stock_analysis.importers.stocklist_parser import (
    StocklistParseResult,
    StocklistParseStats,
    StocklistRow,
    parse_stocklist_file,
)
from tests.helpers.import_snapshot import MOVEMENT_PERIOD_END, MOVEMENT_PERIOD_START, write_fixture_csvs

FIXTURES = Path(__file__).resolve().parents[1] / "test_imports"


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


def _write_movement_stocklist(path: Path) -> None:
    path.write_text(
      '"CODE","DESCRIPT","ONHAND","SUBDEPARTM"\n'
      '"BASE001","Widget Alpha Test Item*1/1/26",8.00,"T001"\n'
      '"BASE002","Widget Beta Test Item*1/1/26",99.00,"T001"\n'
      '"BASE003","Widget Gamma Baseline Only*1/1/26",8.00,"T001"\n'
      '"MOVE001","Movement Only New Item*1/1/26",3.00,"B001"\n'
      '"ONLYSL","Stocklist Only Item",2.00,"B001"\n',
      encoding="utf-8",
  )


def test_project_post_movement_on_hand(session: Session, fixtures: Path) -> None:
    apply_initial_baseline(session, fixtures / "sthold2.csv")
    session.commit()

    parsed = merge_movement_reports(
        fixtures / "Sales_Detail_sample.csv",
        fixtures / "PurchasesDetailed_sample.csv",
    )
    projected = project_post_movement_on_hand(session, parsed.rows, direction="forward")

    assert projected["BASE001"][0] == pytest.approx(8.0)
    assert projected["BASE002"][0] == pytest.approx(3.0)
    assert projected["BASE003"][0] == pytest.approx(8.0)
    assert projected["BASE004"][0] == pytest.approx(10.0)
    assert projected["MOVE001"][0] == pytest.approx(3.0)


def test_compare_movement_to_stocklist_variances(session: Session, fixtures: Path, tmp_path: Path) -> None:
    apply_initial_baseline(session, fixtures / "sthold2.csv")
    session.commit()

    stocklist_path = tmp_path / "stocklist.csv"
    _write_movement_stocklist(stocklist_path)
    stocklist = parse_stocklist_file(stocklist_path, require_on_hand=True)

    parsed = merge_movement_reports(
        fixtures / "Sales_Detail_sample.csv",
        fixtures / "PurchasesDetailed_sample.csv",
    )
    comparison = compare_movement_to_stocklist(session, parsed.rows, stocklist, direction="forward")

    assert comparison.exact_matches >= 2
    variance_by_sku = {line.sku: line for line in comparison.variance_lines}
    assert "BASE002" in variance_by_sku
    assert variance_by_sku["BASE002"].variance == pytest.approx(96.0)
    assert "BASE004" in variance_by_sku
    assert variance_by_sku["BASE004"].line_type == "missing_from_stocklist"
    assert "ONLYSL" in variance_by_sku
    assert variance_by_sku["ONLYSL"].line_type == "new_in_stocklist"


def test_compare_excludes_junk_stocklist_rows(session: Session, fixtures: Path) -> None:
    apply_initial_baseline(session, fixtures / "sthold2.csv")
    session.commit()

    stocklist = StocklistParseResult(
        rows=[
            StocklistRow(code="BASE001", description="Widget", department="T001", on_hand=8.0),
            StocklistRow(code="* 15582", description="junk", department="M001", on_hand=10.0),
        ],
        stats=StocklistParseStats(total_rows=2, junk_rows=0, eligible_rows=2),
    )
    parsed = merge_movement_reports(
        fixtures / "Sales_Detail_sample.csv",
        fixtures / "PurchasesDetailed_sample.csv",
    )
    comparison = compare_movement_to_stocklist(session, parsed.rows, stocklist, direction="forward")
    assert all(line.sku != "* 15582" for line in comparison.lines)


def test_apply_stocklist_override_updates_baseline(session: Session, fixtures: Path, tmp_path: Path) -> None:
    apply_initial_baseline(session, fixtures / "sthold2.csv")
    apply_enrichment(
        session,
        fixtures / "Sales_Detail_sample.csv",
        fixtures / "PurchasesDetailed_sample.csv",
        period_start=MOVEMENT_PERIOD_START,
        period_end=MOVEMENT_PERIOD_END,
    )
    session.commit()

    assert _baseline_qty(session, "BASE002") == pytest.approx(3.0)

    stocklist_path = tmp_path / "stocklist.csv"
    _write_movement_stocklist(stocklist_path)
    stocklist = parse_stocklist_file(stocklist_path, require_on_hand=True)
    parsed = merge_movement_reports(
        fixtures / "Sales_Detail_sample.csv",
        fixtures / "PurchasesDetailed_sample.csv",
    )
    comparison = compare_movement_to_stocklist(session, parsed.rows, stocklist, direction="forward")

    result = apply_stocklist_override(
        session,
        stocklist_path,
        comparison.variance_lines,
        source_import_id=1,
    )
    session.commit()

    assert result.items_updated >= 1
    assert _baseline_qty(session, "BASE002") == pytest.approx(99.0)
    only_sl = session.scalar(select(Item).where(Item.sku == "ONLYSL"))
    assert only_sl is not None
    assert _baseline_qty(session, "ONLYSL") == pytest.approx(2.0)
    assert _baseline_qty(session, "BASE004") == pytest.approx(10.0)


def test_apply_stocklist_override_skips_missing_from_stocklist(
    session: Session, fixtures: Path, tmp_path: Path
) -> None:
    apply_initial_baseline(session, fixtures / "sthold2.csv")
    session.commit()

    stocklist_path = tmp_path / "stocklist.csv"
    stocklist_path.write_text(
        '"CODE","DESCRIPT","ONHAND","SUBDEPARTM"\n'
        '"BASE001","Widget Alpha Test Item*1/1/26",8.00,"T001"\n',
        encoding="utf-8",
    )
    stocklist = parse_stocklist_file(stocklist_path, require_on_hand=True)
    parsed = merge_movement_reports(
        fixtures / "Sales_Detail_sample.csv",
        fixtures / "PurchasesDetailed_sample.csv",
    )
    comparison = compare_movement_to_stocklist(session, parsed.rows, stocklist, direction="forward")
    missing_lines = [
        line for line in comparison.variance_lines if line.line_type == "missing_from_stocklist"
    ]
    assert any(line.sku == "BASE004" for line in missing_lines)

    result = apply_stocklist_override(session, stocklist_path, comparison.variance_lines)
    session.commit()

    assert result.skipped == len(missing_lines)
    assert _baseline_qty(session, "BASE004") == pytest.approx(20.0)
