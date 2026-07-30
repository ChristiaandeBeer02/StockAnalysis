"""Tests for manual item on-hand updates."""

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from stock_analysis.baseline.change_log import log_change
from stock_analysis.baseline.manager import update_item_on_hand
from stock_analysis.db.models import (
    Base,
    BaselineChangeLog,
    BaselineItem,
    BaselineVersion,
    ImportBatch,
    Item,
)
from stock_analysis.db.session import set_app_state


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


def _seed_baseline(session: Session, sku: str, qty: float) -> Item:
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

    item = Item(sku=sku, name=f"Item {sku}")
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
    return item


def test_update_item_on_hand_sets_value(session: Session) -> None:
    item = _seed_baseline(session, "SKU001", 10.0)

    changed = update_item_on_hand(session, item.id, 15)
    session.commit()

    baseline = session.scalar(select(BaselineItem).where(BaselineItem.item_id == item.id))
    assert changed is True
    assert baseline is not None
    assert baseline.qty_on_hand == pytest.approx(15.0)
    assert baseline.last_update_source == "manual_edit"


def test_update_item_on_hand_overwrites_existing(session: Session) -> None:
    item = _seed_baseline(session, "SKU002", 8.0)

    update_item_on_hand(session, item.id, 3)
    session.commit()

    baseline = session.scalar(select(BaselineItem).where(BaselineItem.item_id == item.id))
    assert baseline is not None
    assert baseline.qty_on_hand == pytest.approx(3.0)


def test_update_item_on_hand_no_op_on_same_value(session: Session) -> None:
    item = _seed_baseline(session, "SKU003", 12.0)

    changed = update_item_on_hand(session, item.id, 12)
    session.commit()

    assert changed is False
    logs = session.scalars(
        select(BaselineChangeLog).where(
            BaselineChangeLog.item_id == item.id,
            BaselineChangeLog.change_reason == "manual_edit",
        )
    ).all()
    assert logs == []


def test_update_item_on_hand_rejects_negative(session: Session) -> None:
    item = _seed_baseline(session, "SKU004", 5.0)

    with pytest.raises(ValueError, match="negative"):
        update_item_on_hand(session, item.id, -1)


def test_update_item_on_hand_unknown_item(session: Session) -> None:
    with pytest.raises(ValueError, match="Item not found"):
        update_item_on_hand(session, 999, 1)


def test_update_item_on_hand_no_baseline(session: Session) -> None:
    item = Item(sku="SKU005", name="No baseline")
    session.add(item)
    session.flush()

    with pytest.raises(ValueError, match="no baseline"):
        update_item_on_hand(session, item.id, 1)
