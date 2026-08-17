"""Export, validate, import, and restore the SQLite application database."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

from stock_analysis.config import get_database_path
from stock_analysis.db.models import Base

SQLITE_HEADER = b"SQLite format 3\x00"
CORE_TABLES = ("items", "app_state", "baseline_versions", "import_batches")
_INTERNAL_TABLES = {"sqlite_sequence"}
PREV_NAME = "stock_data.prev.db"


class DatabaseBackupError(Exception):
    """Raised when export, import, or restore cannot complete safely."""


@dataclass(frozen=True)
class DatabaseValidation:
    ok: bool
    error: str | None = None
    newer_schema: bool = False
    warning: str | None = None


def get_previous_database_path() -> Path:
    return get_database_path().with_name(PREV_NAME)


def previous_database_exists() -> bool:
    return get_previous_database_path().is_file()


def _sqlite_literal(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace("'", "''")


def _sidecar_paths(db_path: Path) -> list[Path]:
    return [
        db_path.with_name(db_path.name + "-wal"),
        db_path.with_name(db_path.name + "-shm"),
    ]


def _remove_sidecars(db_path: Path) -> None:
    for sidecar in _sidecar_paths(db_path):
        sidecar.unlink(missing_ok=True)


def _remove_db_and_sidecars(db_path: Path) -> None:
    _remove_sidecars(db_path)
    db_path.unlink(missing_ok=True)


def _move_db_group(src: Path, dest: Path) -> None:
    _remove_db_and_sidecars(dest)
    if src.exists():
        src.replace(dest)
    for sidecar in _sidecar_paths(src):
        dest_sidecar = dest.with_name(dest.name + sidecar.name[len(src.name) :])
        if sidecar.exists():
            sidecar.replace(dest_sidecar)


def _model_tables() -> dict[str, set[str]]:
    return {
        table.name: {column.name for column in table.columns}
        for table in Base.metadata.tables.values()
    }


def _has_sqlite_header(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(len(SQLITE_HEADER)) == SQLITE_HEADER
    except OSError:
        return False


def validate_database(path: Path) -> DatabaseValidation:
    """Check that path is a readable Stock Analysis SQLite database."""
    if not path.is_file():
        return DatabaseValidation(ok=False, error="File does not exist.")
    if not _has_sqlite_header(path):
        return DatabaseValidation(ok=False, error="File is not a SQLite database.")

    engine = create_engine(f"sqlite:///{path}", echo=False)
    try:
        with engine.connect() as conn:
            integrity = conn.execute(text("PRAGMA integrity_check")).scalar()
            if integrity != "ok":
                return DatabaseValidation(
                    ok=False,
                    error=f"Database failed integrity check: {integrity}",
                )
            names = {
                row[0]
                for row in conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                )
            }
        missing = [name for name in CORE_TABLES if name not in names]
        if missing:
            return DatabaseValidation(
                ok=False,
                error="File is not a Stock Analysis database (missing core tables: "
                + ", ".join(missing)
                + ").",
            )

        expected = _model_tables()
        extra_tables = names - set(expected) - _INTERNAL_TABLES
        extra_columns: list[str] = []
        inspector = inspect(engine)
        for table_name, columns in expected.items():
            if table_name not in names:
                continue
            found = {column["name"] for column in inspector.get_columns(table_name)}
            extras = found - columns
            extra_columns.extend(f"{table_name}.{column}" for column in sorted(extras))

        if extra_tables or extra_columns:
            details: list[str] = []
            if extra_tables:
                details.append("extra tables: " + ", ".join(sorted(extra_tables)))
            if extra_columns:
                details.append("extra columns: " + ", ".join(extra_columns))
            return DatabaseValidation(
                ok=True,
                newer_schema=True,
                warning=(
                    "This database looks like it came from a newer app version ("
                    + "; ".join(details)
                    + "). Import anyway?"
                ),
            )
        return DatabaseValidation(ok=True)
    except Exception as exc:
        return DatabaseValidation(ok=False, error=f"Could not read database: {exc}")
    finally:
        engine.dispose()


def export_database(dest: Path) -> None:
    """Write a consistent compact copy of the live database to dest."""
    from stock_analysis.db.session import _get_engine

    dest = dest.resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()

    engine = _get_engine()
    quoted = _sqlite_literal(dest)
    try:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
            conn.execute(text(f"VACUUM INTO '{quoted}'"))
    except Exception as exc:
        dest.unlink(missing_ok=True)
        raise DatabaseBackupError(f"Could not export database: {exc}") from exc

    check = validate_database(dest)
    if not check.ok:
        dest.unlink(missing_ok=True)
        raise DatabaseBackupError(check.error or "Exported file failed validation.")


def _reopen_live_database() -> None:
    from stock_analysis.analytics.cache import invalidate_summaries
    from stock_analysis.db.session import init_db

    init_db()
    invalidate_summaries()


def _swap_in_validated_file(incoming: Path) -> None:
    from stock_analysis.db.session import dispose_engine

    live = get_database_path()
    prev = get_previous_database_path()
    dispose_engine()

    live_stash: Path | None = None
    try:
        if live.exists():
            _move_db_group(live, prev)
            live_stash = prev
        shutil.copy2(incoming, live)
        _remove_sidecars(live)
        _reopen_live_database()
    except Exception as exc:
        _remove_db_and_sidecars(live)
        if live_stash is not None and live_stash.exists():
            _move_db_group(live_stash, live)
        try:
            _reopen_live_database()
        except Exception:
            pass
        raise DatabaseBackupError(f"Could not replace database: {exc}") from exc


def import_database(src: Path) -> DatabaseValidation:
    """Copy src, validate, then replace the live database. Keeps one previous copy."""
    src = src.resolve()
    live = get_database_path()
    if src == live.resolve():
        raise DatabaseBackupError("Cannot import the live database file onto itself.")

    temp_dir = live.parent / "_db_import_tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_copy = temp_dir / "incoming.db"
    try:
        shutil.copy2(src, temp_copy)
        _remove_sidecars(temp_copy)
        result = validate_database(temp_copy)
        if not result.ok:
            raise DatabaseBackupError(result.error or "Database failed validation.")
        _swap_in_validated_file(temp_copy)
        return result
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def restore_previous_database() -> DatabaseValidation:
    """Swap the live database with the rolling previous copy."""
    from stock_analysis.db.session import dispose_engine

    live = get_database_path()
    prev = get_previous_database_path()
    if not prev.is_file():
        raise DatabaseBackupError("No previous database is available to restore.")

    result = validate_database(prev)
    if not result.ok:
        raise DatabaseBackupError(result.error or "Previous database failed validation.")

    hold = live.with_name(live.name + ".swap")
    dispose_engine()
    try:
        _move_db_group(live, hold)
        _move_db_group(prev, live)
        _move_db_group(hold, prev)
        _reopen_live_database()
        return result
    except Exception as exc:
        if hold.exists() and not live.exists():
            _move_db_group(hold, live)
        elif live.exists() and not prev.exists() and hold.exists():
            _move_db_group(live, prev)
            _move_db_group(hold, live)
        try:
            _reopen_live_database()
        except Exception:
            pass
        raise DatabaseBackupError(f"Could not restore previous database: {exc}") from exc
