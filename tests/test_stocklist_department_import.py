"""Tests for Stocklist department import apply logic."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from stock_analysis.baseline.manager import apply_stocklist_departments
from stock_analysis.db.models import (
    Base,
    BaselineItem,
    BaselineVersion,
    ImportBatch,
    Item,
)
from stock_analysis.importers.stocklist_parser import parse_stocklist_file

FIXTURES = Path(__file__).resolve().parents[1] / "test_imports"
SAMPLE = FIXTURES / "Stocklist_sample.csv"


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


def _ensure_baseline_version(session: Session) -> BaselineVersion:
    existing = session.scalar(select(BaselineVersion).limit(1))
    if existing:
        return existing
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
    return version


def _seed_item(
    session: Session,
    sku: str,
    *,
    department: str | None = None,
    is_deprecated: bool = False,
) -> Item:
    version = _ensure_baseline_version(session)
    item = Item(sku=sku, name=f"Item {sku}", department=department, is_deprecated=is_deprecated)
    session.add(item)
    session.flush()
    session.add(
        BaselineItem(
            item_id=item.id,
            qty_on_hand=1.0,
            baseline_version_id=version.id,
            last_update_source="initial_import",
        )
    )
    session.flush()
    return item


def _mark_baseline_complete(session: Session) -> None:
    from stock_analysis.db.session import set_app_state

    set_app_state(session, "initial_baseline_complete", "true")


def test_apply_stocklist_departments_fill_only(session: Session) -> None:
    _seed_item(session, "SKU001")
    _seed_item(session, "SKU002", department="T001")
    _seed_item(session, "SKU003", department="B001")
    _seed_item(session, "DEPRECATED", is_deprecated=True)
    _mark_baseline_complete(session)
    session.commit()

    parsed = parse_stocklist_file(SAMPLE)
    result = apply_stocklist_departments(session, SAMPLE, parsed=parsed)
    session.commit()

    items = {item.sku: item for item in session.scalars(select(Item))}
    assert items["SKU001"].department == "T001"
    assert items["SKU002"].department == "T001"
    assert items["SKU003"].department == "B001"

    assert result.items_updated == 1
    assert result.items_already_set == 2
    assert len(result.discrepancies) == 2
    assert ("SKU002", "T001", "N001") in result.discrepancies
    assert ("SKU003", "B001", "B002") in result.discrepancies
    assert result.csv_unmatched_skus == 1
    assert result.items_without_department == 0

    batch = session.scalar(
        select(ImportBatch).where(ImportBatch.import_type == "stocklist_departments")
    )
    assert batch is not None
    assert batch.file_name == SAMPLE.name


def test_apply_stocklist_requires_baseline(session: Session) -> None:
    item = Item(sku="SKU001", name="Item SKU001")
    session.add(item)
    session.commit()
    with pytest.raises(ValueError, match="baseline"):
        apply_stocklist_departments(session, SAMPLE)
