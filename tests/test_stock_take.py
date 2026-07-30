"""Tests for stock take compare and reconcile."""

import pytest
from sqlalchemy import select

from stock_analysis.analytics.stock_take import compare_stock_take
from stock_analysis.baseline.manager import apply_stock_take_reconcile, preview_stock_take
from stock_analysis.db.models import BaselineItem, Item, StockTakeSession
from stock_analysis.importers.iq_retail_parser import ParseStats
from stock_analysis.importers.stockholding_parser import StockholdingParseResult, StockholdingRow


@pytest.fixture
def db_session(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("stock_analysis.db.session.get_database_path", lambda: db_path)

    import stock_analysis.db.session as session_mod

    session_mod._engine = None
    session_mod._SessionLocal = None

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from stock_analysis.db.models import Base

    engine = create_engine(f"sqlite:///{db_path}", echo=False)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _make_parsed(rows: list[tuple[str, str, float, float | None]]) -> StockholdingParseResult:
    parsed_rows = [
        StockholdingRow(
            code=code,
            description=name,
            on_hand=qty,
            stock_value=qty * (cost or 0),
            unit_cost=cost,
            is_deprecated=False,
        )
        for code, name, qty, cost in rows
    ]
    return StockholdingParseResult(
        rows=parsed_rows,
        period_start="01/01/2026",
        period_end="31/01/2026",
        date_printed=None,
        stats=ParseStats(total_rows=len(parsed_rows), deprecated_rows=0, skipped_rows=0),
    )


def _seed_baseline(session, items: list[tuple[str, str, float, float | None]]) -> None:
    from stock_analysis.baseline.change_log import log_change
    from stock_analysis.db.models import BaselineVersion, ImportBatch
    from stock_analysis.db.session import set_app_state

    batch = ImportBatch(import_type="initial_baseline", file_name="seed.csv", status="applied")
    session.add(batch)
    session.flush()
    version = BaselineVersion(
        version_number=1,
        source_type="initial_import",
        source_import_id=batch.id,
    )
    session.add(version)
    session.flush()

    for code, name, qty, cost in items:
        item = Item(sku=code, name=name, unit_cost=cost)
        session.add(item)
        session.flush()
        session.add(
            BaselineItem(
                item_id=item.id,
                qty_on_hand=qty,
                baseline_version_id=version.id,
                last_update_source="initial_import",
            )
        )
        log_change(
            session,
            item_id=item.id,
            baseline_version_id=version.id,
            field_changed="qty_on_hand",
            old_value=None,
            new_value=str(qty),
            change_reason="initial_import",
            source_type="initial_import",
            source_import_id=batch.id,
        )

    set_app_state(session, "initial_baseline_complete", "true")
    session.flush()


def test_compare_stock_take_detects_shortage_and_new_item(db_session):
    _seed_baseline(
        db_session,
        [
            ("SKU001", "Widget A", 10.0, 5.0),
            ("SKU002", "Widget B", 4.0, 2.0),
        ],
    )

    parsed = _make_parsed(
        [
            ("SKU001", "Widget A", 8.0, 5.0),
            ("SKU003", "Widget C", 3.0, 1.0),
        ]
    )
    comparison = compare_stock_take(db_session, parsed)

    by_sku = {line.sku: line for line in comparison.lines}
    assert by_sku["SKU001"].line_type == "shortage"
    assert by_sku["SKU001"].variance == pytest.approx(-2.0)
    assert by_sku["SKU003"].line_type == "new_in_stock_take"
    assert by_sku["SKU002"].line_type == "missing_from_stock_take"
    assert comparison.shrinkage_value < 0
    assert comparison.overage_value > 0


def test_compare_stock_take_exact_match(db_session):
    _seed_baseline(db_session, [("SKU001", "Widget A", 5.0, 10.0)])
    parsed = _make_parsed([("SKU001", "Widget A", 5.0, 10.0)])
    comparison = compare_stock_take(db_session, parsed)
    assert comparison.exact_matches == 1
    assert len(comparison.variance_lines) == 0


def test_apply_stock_take_reconcile_updates_baseline(tmp_path, db_session):
    _seed_baseline(
        db_session,
        [
            ("SKU001", "Widget A", 10.0, 5.0),
            ("SKU002", "Widget B", 4.0, 2.0),
        ],
    )

    stock_take_csv = tmp_path / "stock_take.csv"
    stock_take_csv.write_text(
        "SKU001,,Widget A,,,,,,,8.00,,,,,R40.00\n"
        "SKU003,,Widget C,,,,,,,3.00,,,,,R3.00\n",
        encoding="latin-1",
    )

    result = apply_stock_take_reconcile(db_session, stock_take_csv)
    assert result.items_updated >= 1
    assert result.new_items == 1
    assert result.baseline_version == 2

    item1 = db_session.scalar(select(Item).where(Item.sku == "SKU001"))
    baseline1 = db_session.scalar(select(BaselineItem).where(BaselineItem.item_id == item1.id))
    assert baseline1.qty_on_hand == pytest.approx(8.0)

    item3 = db_session.scalar(select(Item).where(Item.sku == "SKU003"))
    assert item3 is not None
    baseline3 = db_session.scalar(select(BaselineItem).where(BaselineItem.item_id == item3.id))
    assert baseline3.qty_on_hand == pytest.approx(3.0)

    item2 = db_session.scalar(select(Item).where(Item.sku == "SKU002"))
    baseline2 = db_session.scalar(select(BaselineItem).where(BaselineItem.item_id == item2.id))
    assert baseline2.qty_on_hand == pytest.approx(4.0)

    sessions = db_session.scalars(select(StockTakeSession)).all()
    assert len(sessions) == 1


def test_preview_stock_take_from_file(tmp_path, db_session):
    _seed_baseline(db_session, [("SKU001", "Widget A", 10.0, 5.0)])

    stock_take_csv = tmp_path / "preview.csv"
    stock_take_csv.write_text(
        "SKU001,,Widget A,,,,,,,7.00,,,,,R35.00\n",
        encoding="latin-1",
    )

    comparison = preview_stock_take(db_session, stock_take_csv)
    assert comparison.file_name == "preview.csv"
    assert len(comparison.variance_lines) == 1
    assert comparison.variance_lines[0].sku == "SKU001"
