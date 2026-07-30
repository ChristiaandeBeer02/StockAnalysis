"""Tests for Stocklist CSV parser."""

from pathlib import Path

import pytest

from stock_analysis.importers.stocklist_parser import parse_stocklist_file

FIXTURES = Path(__file__).resolve().parents[1] / "test_imports"
SAMPLE = FIXTURES / "Stocklist_sample.csv"


def test_parse_stocklist_sample() -> None:
    result = parse_stocklist_file(SAMPLE)
    assert result.stats.total_rows == 5
    assert result.stats.junk_rows == 1
    assert result.stats.eligible_rows == 4

    by_code = {row.code: row for row in result.rows}
    assert by_code["SKU001"].department == "T001"
    assert by_code["SKU002"].department == "N001"
    assert "* 15582" not in by_code


def test_parse_stocklist_missing_subdepartm(tmp_path: Path) -> None:
    path = tmp_path / "bad.csv"
    path.write_text(
        '"CODE","DESCRIPT"\n"SKU001","Item One"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="SUBDEPARTM"):
        parse_stocklist_file(path)


def test_parse_stocklist_missing_code(tmp_path: Path) -> None:
    path = tmp_path / "bad.csv"
    path.write_text(
        '"DESCRIPT","SUBDEPARTM"\n"Item One","T001"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="CODE"):
        parse_stocklist_file(path)
