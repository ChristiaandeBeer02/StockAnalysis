"""Baseline import and re-import tests."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from stock_analysis.analytics.dashboard_config import save_dashboard_config
from stock_analysis.baseline.manager import (
    ImportCancelledError,
    apply_enrichment,
    apply_initial_baseline,
    reset_import_data,
)
from stock_analysis.db.models import (
    AppState,
    Base,
    BaselineItem,
    ImportBatch,
    Item,
    PeriodTurnLine,
    StockTakeSession,
)
from stock_analysis.db.session import has_enrichment, has_initial_baseline
from stock_analysis.importers.stockholding_parser import parse_stockholding_file
from tests.helpers.import_snapshot import (
    build_stockholding_row,
    write_fixture_csvs,
)


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


def _write_baseline_csv(path: Path, codes: list[str]) -> None:
    lines = [
        "Manta DIY (Pty) Ltd,,,,,,Date Printed :01/01/2026 10:00:00,,,,Page No 1,,",
        "",
        "Detailed Stockholding,,,,,,,,",
        "",
        "Period: 01/01/2026 to 31/01/2026,",
        "",
        "Current Filter: NA,,,,,,,,,,,,,,Currency: (R),,,,",
        "",
        "Code,Description,,,,,Onhand,Stock Value",
    ]
    for index, code in enumerate(codes):
        lines.append(
            build_stockholding_row(code, f"Item {code}", float(index + 1), float((index + 1) * 5))
        )
    lines.append("Totals:,,0.00,,,,,,,R0.00,,")
    path.write_text("\n".join(lines), encoding="utf-8")


def test_initial_baseline_import_on_empty_db(session: Session, tmp_path: Path) -> None:
    write_fixture_csvs(tmp_path)
    result = apply_initial_baseline(session, tmp_path / "sthold2.csv")
    session.commit()

    assert result.items_imported == 6
    assert has_initial_baseline(session)
    assert not has_enrichment(session)
    assert session.scalar(select(func.count(Item.id))) == 6


def test_reimport_wipes_existing_data_and_starts_clean(session: Session, tmp_path: Path) -> None:
    write_fixture_csvs(tmp_path)
    apply_initial_baseline(session, tmp_path / "sthold2.csv")
    apply_enrichment(
        session,
        tmp_path / "IQStockTurn.csv",
        tmp_path / "IQStockTurnunder.csv",
    )
    session.commit()

    assert session.scalar(select(func.count(PeriodTurnLine.id))) > 0
    assert has_enrichment(session)

    reimport_path = tmp_path / "reimport.csv"
    _write_baseline_csv(reimport_path, ["NEW001", "NEW002"])

    result = apply_initial_baseline(session, reimport_path)
    session.commit()

    skus = set(session.scalars(select(Item.sku)).all())
    assert skus == {"NEW001", "NEW002"}
    assert session.scalar(select(func.count(PeriodTurnLine.id))) == 0
    assert session.scalar(select(func.count(ImportBatch.id))) == 1
    assert not has_enrichment(session)
    assert has_initial_baseline(session)
    assert result.baseline_version == 1


def test_reimport_preserves_dashboard_config(session: Session, tmp_path: Path) -> None:
    write_fixture_csvs(tmp_path)
    save_dashboard_config(session, {"show_kpis": False, "show_charts": True})
    apply_initial_baseline(session, tmp_path / "sthold2.csv")
    session.commit()

    reimport_path = tmp_path / "reimport.csv"
    _write_baseline_csv(reimport_path, ["ONLY001"])
    apply_initial_baseline(session, reimport_path)
    session.commit()

    from stock_analysis.analytics.dashboard_config import get_dashboard_config

    config = get_dashboard_config(session)
    assert config["show_kpis"] is False
    assert config["show_charts"] is True


def test_reset_import_data_clears_import_tables(session: Session, tmp_path: Path) -> None:
    write_fixture_csvs(tmp_path)
    save_dashboard_config(session, {"show_kpis": True, "show_charts": False})
    apply_initial_baseline(session, tmp_path / "sthold2.csv")
    apply_enrichment(
        session,
        tmp_path / "IQStockTurn.csv",
        tmp_path / "IQStockTurnunder.csv",
    )
    session.commit()

    reset_import_data(session)
    session.commit()

    assert session.scalar(select(func.count(Item.id))) == 0
    assert session.scalar(select(func.count(BaselineItem.id))) == 0
    assert session.scalar(select(func.count(ImportBatch.id))) == 0
    assert session.scalar(select(func.count(StockTakeSession.id))) == 0
    assert not has_initial_baseline(session)
    assert not has_enrichment(session)
    assert session.get(AppState, "dashboard_config") is not None


def test_apply_initial_baseline_cancelled(session: Session, tmp_path: Path) -> None:
    codes = [f"SKU{i:04d}" for i in range(200)]
    csv_path = tmp_path / "large.csv"
    _write_baseline_csv(csv_path, codes)

    cancel_event = threading.Event()
    progress_calls = 0

    def progress_callback(current: int, total: int) -> None:
        nonlocal progress_calls
        progress_calls += 1
        if current >= 5:
            cancel_event.set()

    with pytest.raises(ImportCancelledError):
        apply_initial_baseline(
            session,
            csv_path,
            progress_callback=progress_callback,
            cancel_event=cancel_event,
        )

    assert progress_calls >= 5


def test_baseline_import_performance(session: Session, tmp_path: Path) -> None:
    codes = [f"PERF{i:04d}" for i in range(1000)]
    csv_path = tmp_path / "perf.csv"
    _write_baseline_csv(csv_path, codes)

    start = time.perf_counter()
    apply_initial_baseline(session, csv_path)
    session.commit()
    first_elapsed = time.perf_counter() - start

    reimport_path = tmp_path / "perf_reimport.csv"
    _write_baseline_csv(reimport_path, [f"RE{i:03d}" for i in range(1000)])

    start = time.perf_counter()
    apply_initial_baseline(session, reimport_path)
    session.commit()
    reimport_elapsed = time.perf_counter() - start

    assert first_elapsed < 5.0
    assert reimport_elapsed < 5.0
    assert session.scalar(select(func.count(Item.id))) == 1000


def test_apply_initial_baseline_uses_cached_parse(session: Session, tmp_path: Path) -> None:
    write_fixture_csvs(tmp_path)
    path = tmp_path / "sthold2.csv"
    parsed = parse_stockholding_file(path)

    result = apply_initial_baseline(session, path, parsed=parsed)
    session.commit()

    assert result.items_imported == parsed.stats.total_rows


def test_init_db_migrates_legacy_analysis_results_schema(tmp_path: Path) -> None:
    from sqlalchemy import create_engine, text

    from stock_analysis.analytics.dashboard import save_analysis_result
    from stock_analysis.db.models import AnalysisResult
    from stock_analysis.db.session import get_database_path, init_db
    import stock_analysis.db.session as db_session

    db_path = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{db_path}", echo=False)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE import_batches (
                    id INTEGER PRIMARY KEY,
                    import_type VARCHAR(32) NOT NULL,
                    file_name VARCHAR(512) NOT NULL,
                    companion_file_name VARCHAR(512),
                    imported_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    period_start VARCHAR(32),
                    period_end VARCHAR(32),
                    row_count INTEGER DEFAULT 0,
                    deprecated_rows INTEGER DEFAULT 0,
                    status VARCHAR(32) DEFAULT 'applied',
                    notes TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE analysis_results (
                    id INTEGER PRIMARY KEY,
                    analysis_type VARCHAR(64) NOT NULL,
                    import_batch_id INTEGER NOT NULL,
                    period_start VARCHAR(32),
                    period_end VARCHAR(32),
                    generated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    result_json TEXT,
                    summary_json TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                "INSERT INTO import_batches (id, import_type, file_name) VALUES (1, 'baseline_enrichment', 'turn.csv')"
            )
        )

    db_session._engine = None
    db_session._SessionLocal = None
    try:
        db_session.get_database_path = lambda: db_path
        init_db()

        with db_session.get_session() as session:
            save_analysis_result(
                session,
                batch_id=1,
                import_type="baseline_enrichment",
                summary={"batch_id": 1},
            )
            session.flush()
            result = session.scalar(select(AnalysisResult))
            assert result is not None
            assert result.stock_take_session_id is None
    finally:
        db_session.get_database_path = get_database_path
        db_session._engine = None
        db_session._SessionLocal = None
