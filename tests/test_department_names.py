"""Tests for department nickname mapping."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from stock_analysis.analytics.department_names import (
    display_dept,
    list_imported_departments,
    load_nickname_map,
    save_nicknames,
)
from stock_analysis.db.models import Base, DepartmentNickname, Item, PeriodTurnLine


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


def test_display_dept_falls_back_to_raw_code():
    assert display_dept("A1", {}) == "A1"
    assert display_dept("A1", {"B1": "Beverages"}) == "A1"


def test_display_dept_uses_nickname():
    assert display_dept("A1", {"A1": "Beverages"}) == "Beverages"


def test_display_dept_handles_empty_values():
    assert display_dept(None, {}) == "—"
    assert display_dept("—", {}) == "—"


def test_save_and_load_nicknames(session: Session):
    save_nicknames(session, {"A1": "Beverages", "B1": "Tobacco"})
    session.commit()

    loaded = load_nickname_map(session)
    assert loaded == {"A1": "Beverages", "B1": "Tobacco"}


def test_save_nicknames_clears_blank_entries(session: Session):
    session.add(DepartmentNickname(code="A1", nickname="Old Name"))
    session.commit()

    save_nicknames(session, {"A1": ""})
    session.commit()

    assert load_nickname_map(session) == {}
    assert session.get(DepartmentNickname, "A1") is None


def test_list_imported_departments_unions_sources(session: Session):
    item = Item(sku="SKU1", name="Item 1", department="A1")
    session.add(item)
    session.flush()
    session.add(
        PeriodTurnLine(
            import_batch_id=1,
            item_id=item.id,
            dept="B1",
        )
    )
    session.commit()

    departments = list_imported_departments(session)
    assert departments == ["A1", "B1"]
