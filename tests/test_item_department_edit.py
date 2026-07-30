"""Tests for manual item department updates."""

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from stock_analysis.analytics.department_names import update_item_department
from stock_analysis.db.models import Base, Item, PeriodTurnLine


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


def test_update_item_department_sets_value(session: Session) -> None:
    item = Item(sku="SKU001", name="Item One")
    session.add(item)
    session.flush()

    update_item_department(session, item.id, "T001")
    session.commit()
    session.refresh(item)
    assert item.department == "T001"


def test_update_item_department_overwrites_existing(session: Session) -> None:
    item = Item(sku="SKU002", name="Item Two", department="A001")
    session.add(item)
    session.flush()

    update_item_department(session, item.id, "B002")
    session.commit()
    session.refresh(item)
    assert item.department == "B002"


def test_update_item_department_clears_value(session: Session) -> None:
    item = Item(sku="SKU003", name="Item Three", department="C003")
    session.add(item)
    session.flush()

    update_item_department(session, item.id, None)
    session.commit()
    session.refresh(item)
    assert item.department is None


def test_update_item_department_unknown_item(session: Session) -> None:
    with pytest.raises(ValueError, match="not found"):
        update_item_department(session, 999, "T001")


def test_flush_item_departments_clears_all(session: Session) -> None:
    from stock_analysis.analytics.department_names import flush_item_departments

    item_a = Item(sku="A", name="A", department="T001")
    item_b = Item(sku="B", name="B", department="N001")
    session.add(item_a)
    session.add(item_b)
    session.add(Item(sku="C", name="C"))
    session.flush()
    session.add(
        PeriodTurnLine(import_batch_id=1, item_id=item_a.id, dept="P6"),
    )
    session.flush()

    cleared = flush_item_departments(session)
    session.commit()

    assert cleared == 2
    items = {item.sku: item for item in session.scalars(select(Item))}
    assert items["A"].department is None
    assert items["B"].department is None
    assert items["C"].department is None
    turn_line = session.scalars(select(PeriodTurnLine)).first()
    assert turn_line is not None
    assert turn_line.dept is None
