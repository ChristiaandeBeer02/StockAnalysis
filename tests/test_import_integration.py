"""End-to-end import pipeline tests against golden fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.helpers.import_snapshot import (
    FIXTURES_DIR,
    create_test_session,
    run_import_pipeline,
)

GOLDEN = FIXTURES_DIR / "expected_output.json"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


def _load_golden() -> dict:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def test_fixture_files_exist(fixtures_dir: Path):
    for name in ("sthold2.csv", "Sales_Detail_sample.csv", "PurchasesDetailed_sample.csv", "expected_output.json"):
        assert (fixtures_dir / name).exists(), f"Missing fixture: {name}"


@pytest.mark.skipif(not GOLDEN.exists(), reason="golden file not present")
def test_full_import_pipeline_matches_golden(tmp_path, fixtures_dir: Path):
    expected = _load_golden()
    session = create_test_session(tmp_path / "test.db")
    try:
        actual = run_import_pipeline(session, fixtures_dir)
        session.commit()
    finally:
        session.close()

    assert actual["initial_baseline"] == expected["initial_baseline"]
    assert actual["enrichment"] == expected["enrichment"]
    assert actual["app_state"] == expected["app_state"]
    assert actual["items"] == expected["items"]
    assert actual["inventory"] == expected["inventory"]
    assert actual["period_summary"] == expected["period_summary"]
