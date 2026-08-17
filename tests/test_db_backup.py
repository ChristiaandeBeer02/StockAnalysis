"""Tests for database export, import, and restore."""

from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from stock_analysis.db.backup import (
    DatabaseBackupError,
    export_database,
    get_previous_database_path,
    import_database,
    restore_previous_database,
    validate_database,
)
from stock_analysis.db.models import AppState, Base
from stock_analysis.db.session import dispose_engine, get_session, init_db, set_app_state


def _patch_db_path(monkeypatch, db_path: Path) -> None:
    monkeypatch.setattr("stock_analysis.config.get_database_path", lambda: db_path)
    monkeypatch.setattr("stock_analysis.db.session.get_database_path", lambda: db_path)
    monkeypatch.setattr("stock_analysis.db.backup.get_database_path", lambda: db_path)


def _marker() -> str | None:
    with get_session() as session:
        state = session.get(AppState, "marker")
        return None if state is None else state.value


def _set_marker(value: str) -> None:
    with get_session() as session:
        set_app_state(session, "marker", value)


def _standalone_db(path: Path, marker: str, extra_sql: str | None = None) -> None:
    engine = create_engine(f"sqlite:///{path}", echo=False)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO app_state (key, value) VALUES ('marker', :v)"), {"v": marker})
        if extra_sql:
            conn.execute(text(extra_sql))
    engine.dispose()


@pytest.fixture
def live_db(tmp_path, monkeypatch):
    db_path = tmp_path / "stock_data.db"
    _patch_db_path(monkeypatch, db_path)
    dispose_engine()
    init_db()
    _set_marker("live-a")
    yield db_path
    dispose_engine()


def test_export_creates_valid_copy(live_db, tmp_path):
    dest = tmp_path / "export.db"
    export_database(dest)
    result = validate_database(dest)
    assert result.ok
    assert not result.newer_schema
    assert dest.is_file()
    assert live_db.is_file()


def test_validate_rejects_non_sqlite(tmp_path):
    junk = tmp_path / "notes.txt"
    junk.write_text("not a database", encoding="utf-8")
    result = validate_database(junk)
    assert not result.ok
    assert "SQLite" in (result.error or "")


def test_validate_rejects_missing_core_tables(tmp_path):
    path = tmp_path / "empty.db"
    engine = create_engine(f"sqlite:///{path}", echo=False)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)"))
    engine.dispose()
    result = validate_database(path)
    assert not result.ok
    assert "core tables" in (result.error or "")


def test_validate_warns_on_extra_tables(tmp_path):
    path = tmp_path / "newer.db"
    _standalone_db(path, "newer", "CREATE TABLE future_feature (id INTEGER PRIMARY KEY)")
    result = validate_database(path)
    assert result.ok
    assert result.newer_schema
    assert result.warning


def test_import_replaces_live_and_keeps_previous(live_db, tmp_path):
    incoming = tmp_path / "incoming.db"
    _standalone_db(incoming, "live-b")
    result = import_database(incoming)
    assert result.ok
    assert _marker() == "live-b"
    prev = get_previous_database_path()
    assert prev.is_file()
    prev_check = validate_database(prev)
    assert prev_check.ok
    engine = create_engine(f"sqlite:///{prev}", echo=False)
    with engine.connect() as conn:
        value = conn.execute(text("SELECT value FROM app_state WHERE key = 'marker'")).scalar()
    engine.dispose()
    assert value == "live-a"


def test_import_newer_schema_proceeds(live_db, tmp_path):
    incoming = tmp_path / "newer.db"
    _standalone_db(incoming, "future", "CREATE TABLE future_feature (id INTEGER PRIMARY KEY)")
    result = import_database(incoming)
    assert result.ok
    assert result.newer_schema
    assert _marker() == "future"


def test_failed_import_leaves_live_unchanged(live_db, tmp_path):
    junk = tmp_path / "bad.db"
    junk.write_bytes(b"SQLite format 3\x00" + b"\x00" * 64)
    with pytest.raises(DatabaseBackupError):
        import_database(junk)
    assert _marker() == "live-a"
    assert not get_previous_database_path().exists()


def test_restore_swaps_live_and_previous(live_db, tmp_path):
    incoming = tmp_path / "incoming.db"
    _standalone_db(incoming, "live-b")
    import_database(incoming)
    assert _marker() == "live-b"
    restore_previous_database()
    assert _marker() == "live-a"
    engine = create_engine(f"sqlite:///{get_previous_database_path()}", echo=False)
    with engine.connect() as conn:
        value = conn.execute(text("SELECT value FROM app_state WHERE key = 'marker'")).scalar()
    engine.dispose()
    assert value == "live-b"


def test_restore_without_previous_raises(live_db):
    with pytest.raises(DatabaseBackupError):
        restore_previous_database()
    assert _marker() == "live-a"
