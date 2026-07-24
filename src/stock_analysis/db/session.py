"""Database engine and session management."""

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from stock_analysis.config import get_database_path
from stock_analysis.db.models import AppState, Base, BaselineVersion

_engine = None
_SessionLocal = None


def _get_engine():
    global _engine, _SessionLocal
    if _engine is None:
        from sqlalchemy import event
        from sqlalchemy.pool import NullPool

        db_path = get_database_path()
        _engine = create_engine(
            f"sqlite:///{db_path}",
            echo=False,
            connect_args={"check_same_thread": False, "timeout": 30},
            poolclass=NullPool,
        )

        @event.listens_for(_engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)
    return _engine


def _table_columns(conn, table_name: str) -> set[str]:
    from sqlalchemy import text

    rows = conn.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
    return {row[1] for row in rows}


def _migrate_schema(engine) -> None:
    """Apply lightweight schema upgrades for existing SQLite databases."""
    from sqlalchemy import text

    with engine.begin() as conn:
        if "analysis_results" not in {
            row[0] for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        }:
            return

        columns = _table_columns(conn, "analysis_results")
        if "stock_take_session_id" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE analysis_results "
                    "ADD COLUMN stock_take_session_id INTEGER "
                    "REFERENCES stock_take_sessions(id)"
                )
            )


def init_db() -> None:
    from sqlalchemy import text

    engine = _get_engine()
    Base.metadata.create_all(engine)
    _migrate_schema(engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_period_turn_lines_batch_item "
                "ON period_turn_lines (import_batch_id, item_id)"
            )
        )


@contextmanager
def get_session() -> Generator[Session, None, None]:
    if _SessionLocal is None:
        _get_engine()
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def has_initial_baseline(session: Session) -> bool:
    state = session.get(AppState, "initial_baseline_complete")
    return state is not None and state.value == "true"


def has_enrichment(session: Session) -> bool:
    state = session.get(AppState, "enrichment_complete")
    return state is not None and state.value == "true"


def set_app_state(session: Session, key: str, value: str) -> None:
    existing = session.get(AppState, key)
    if existing:
        existing.value = value
    else:
        session.add(AppState(key=key, value=value))


def get_latest_baseline_version(session: Session) -> BaselineVersion | None:
    return session.scalar(select(BaselineVersion).order_by(BaselineVersion.version_number.desc()))
