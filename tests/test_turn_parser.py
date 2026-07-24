"""Tests for turn report parsers."""

from pathlib import Path

import pytest

from stock_analysis.importers.turn_parser import parse_turn_file
from stock_analysis.importers.turnunder_parser import merge_turn_reports

TURN = Path(__file__).resolve().parents[1] / "IQStockTurn.csv"
UNDER = Path(__file__).resolve().parents[1] / "IQStockTurnunder.csv"


@pytest.mark.skipif(not TURN.exists(), reason="sample turn file not present")
def test_parse_turn_sample():
    result = parse_turn_file(TURN)
    assert result.stats.total_rows > 1000
    codes = {r.code for r in result.rows}
    assert "03MAD0067" in codes
    row = next(r for r in result.rows if r.code == "03MAD0067")
    assert row.on_hand == pytest.approx(2.0)
    assert row.last_unit_cost == pytest.approx(0.07)


@pytest.mark.skipif(not (TURN.exists() and UNDER.exists()), reason="sample files not present")
def test_merge_turn_reports():
    merged, _, _ = merge_turn_reports(TURN, UNDER)
    assert len(merged) >= 12000
    understock = next(r for r in merged if r.code == "1003")
    assert understock.under_stock_qty_3mo < 0
