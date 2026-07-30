"""Tests for IQ Retail stockholding parser."""

from pathlib import Path

import pytest

from stock_analysis.importers.iq_retail_parser import (
    extract_date_printed,
    is_ongoing_stockhold,
    should_skip_line,
)
from stock_analysis.importers.stockholding_parser import (
    _parse_data_row,
    parse_stockholding_file,
)

SAMPLE = Path(__file__).resolve().parents[1] / "sthold2.csv"


@pytest.mark.skipif(not SAMPLE.exists(), reason="sample file not present")
def test_parse_stockholding_sample():
    result = parse_stockholding_file(SAMPLE)
    assert result.stats.total_rows > 1000
    assert result.period_start is not None
    codes = {r.code for r in result.rows}
    assert "03MAD0067" in codes
    assert "Totals:" not in codes
    item = next(r for r in result.rows if r.code == "03MAD0067")
    assert item.on_hand == pytest.approx(2.0)
    assert item.is_deprecated is False


@pytest.mark.skipif(not SAMPLE.exists(), reason="sample file not present")
def test_parse_stockholding_sample_stats():
    result = parse_stockholding_file(SAMPLE)
    assert result.stats.junk_rows >= 1
    assert result.stats.metadata_skipped_rows > 3000
    assert result.stats.deprecated_rows > 200
    assert result.stats.stock_take_eligible_rows == (
        result.stats.total_rows - result.stats.deprecated_rows
    )


def test_totals_row_not_parsed():
    parts = "Totals:,,339390.22,,,,,,,R7909987.78,,".split(",")
    assert _parse_data_row(parts) is None
    assert should_skip_line("Totals:,,339390.22,,,,,,,R7909987.78,,")


def test_description_from_column_three():
    parts = "YVC500ML,,,Excelsior Varnish 500ml Yatch Clear*24/6/26,,,,,,,,,,3.00,,,R209.70,,".split(
        ","
    )
    row = _parse_data_row(parts)
    assert row is not None
    assert row.code == "YVC500ML"
    assert row.description.startswith("Excelsior Varnish")
    assert row.on_hand == pytest.approx(3.0)


def test_deprecated_detection():
    from stock_analysis.importers.iq_retail_parser import is_deprecated_description

    assert is_deprecated_description("zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz")
    assert not is_deprecated_description("Bosch Laser Level GLL 12-22")


def test_extract_date_printed() -> None:
    lines = [
        'Manta DIY (Pty) Ltd,,,,,,Date Printed :27/07/2026 11:09:11,,,,Page No 1,,',
        "Period: 01/07/2026 to 31/07/2026,",
    ]
    printed = extract_date_printed(lines)
    assert printed is not None
    assert printed.strftime("%d/%m/%Y %H:%M:%S") == "27/07/2026 11:09:11"


def test_is_ongoing_stockhold() -> None:
    from datetime import datetime

    printed = datetime(2026, 7, 27, 11, 9, 11)
    assert is_ongoing_stockhold("01/07/2026", "31/07/2026", printed) is True
    assert is_ongoing_stockhold("01/07/2026", "30/06/2026", printed) is False
    assert is_ongoing_stockhold("01/07/2026", "31/07/2026", None) is False
