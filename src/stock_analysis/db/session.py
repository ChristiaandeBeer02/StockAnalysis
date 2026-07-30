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

        if "period_turn_lines" in {
            row[0] for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        }:
            turn_columns = _table_columns(conn, "period_turn_lines")
            if "purchases_qty" not in turn_columns:
                conn.execute(
                    text("ALTER TABLE period_turn_lines ADD COLUMN purchases_qty FLOAT DEFAULT 0")
                )
            if "returns_qty" not in turn_columns:
                conn.execute(
                    text("ALTER TABLE period_turn_lines ADD COLUMN returns_qty FLOAT DEFAULT 0")
                )
            if "net_sales_revenue" not in turn_columns:
                conn.execute(
                    text(
                        "ALTER TABLE period_turn_lines ADD COLUMN net_sales_revenue FLOAT DEFAULT 0"
                    )
                )
            if "gross_profit" not in turn_columns:
                conn.execute(
                    text("ALTER TABLE period_turn_lines ADD COLUMN gross_profit FLOAT DEFAULT 0")
                )
            if "gross_margin_pct" not in turn_columns:
                conn.execute(
                    text(
                        "ALTER TABLE period_turn_lines ADD COLUMN gross_margin_pct FLOAT DEFAULT 0"
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


_MOVEMENT_CLOSING_WEEKDAY_KEY = "movement_closing_weekday"
_BASELINE_ANCHOR_DATE_KEY = "baseline_anchor_date"


def get_movement_closing_weekday(session: Session) -> int | None:
    state = session.get(AppState, _MOVEMENT_CLOSING_WEEKDAY_KEY)
    if not state or not state.value:
        return None
    try:
        weekday = int(state.value)
    except ValueError:
        return None
    if 0 <= weekday <= 6:
        return weekday
    return None


def set_movement_closing_weekday(session: Session, weekday: int) -> None:
    if weekday < 0 or weekday > 6:
        raise ValueError("weekday must be 0 (Monday) through 6 (Sunday)")
    set_app_state(session, _MOVEMENT_CLOSING_WEEKDAY_KEY, str(weekday))


def get_baseline_anchor_date(session: Session):
    from stock_analysis.importers.iq_retail_parser import parse_report_date

    state = session.get(AppState, _BASELINE_ANCHOR_DATE_KEY)
    if not state or not state.value:
        return None
    return parse_report_date(state.value)


def set_baseline_anchor_date(session: Session, value) -> None:
    from stock_analysis.analytics.movement_periods import format_report_date

    set_app_state(session, _BASELINE_ANCHOR_DATE_KEY, format_report_date(value))


def get_latest_baseline_version(session: Session) -> BaselineVersion | None:
    return session.scalar(select(BaselineVersion).order_by(BaselineVersion.version_number.desc()))
