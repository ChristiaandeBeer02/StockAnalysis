"""Tests for previous-window KPI subtitles."""

from stock_analysis.analytics.kpi_previous import (
    apply_previous_amount,
    apply_previous_text,
    format_int_count,
    format_previous_kpi,
    format_qty,
    format_qty_2dp,
    format_rand,
    previous_kpi_direction,
    previous_lookback_weeks,
    previous_window_tag,
)


class _FakeCard:
    def __init__(self) -> None:
        self.text = None
        self.direction = None

    def set_delta(self, text: str, direction: str = "neutral") -> None:
        self.text = text
        self.direction = direction


def test_format_previous_kpi_kinds():
    assert format_previous_kpi(format_rand(1100), "4w") == "R 1,100.00 · 4w"
    assert format_previous_kpi(format_int_count(12), "4w") == "12 · 4w"
    assert format_previous_kpi(format_qty(15), "4w") == "15 · 4w"
    assert format_previous_kpi(format_qty_2dp(3.5), "4w") == "3.50 · 4w"
    assert format_previous_kpi("B", "4w") == "B · 4w"
    assert format_previous_kpi(format_rand(250), "12/08/2026") == "R 250.00 · 12/08/2026"
    assert format_previous_kpi("—", "4w") == ""
    assert format_previous_kpi(format_rand(1), None) == ""
    assert "vs" not in format_previous_kpi(format_rand(1), "4w")


def test_previous_window_tag():
    assert previous_lookback_weeks(5) == 4
    assert previous_window_tag(5) == "4w"
    assert previous_lookback_weeks(1) is None


def test_previous_kpi_direction():
    assert previous_kpi_direction(10, 8) == "up"
    assert previous_kpi_direction(8, 10) == "down"
    assert previous_kpi_direction(8, 8) == "neutral"
    assert previous_kpi_direction(10, 8, compare=False) == "neutral"


def test_apply_previous_amount_and_text():
    card = _FakeCard()
    apply_previous_amount(card, 1200, 1100, formatter=format_rand, tag="4w")
    assert card.text == "R 1,100.00 · 4w"
    assert card.direction == "up"

    card = _FakeCard()
    apply_previous_amount(card, 5, None, formatter=format_rand, tag="4w")
    assert card.text == ""

    card = _FakeCard()
    apply_previous_text(card, "B", tag="4w")
    assert card.text == "B · 4w"
    assert card.direction == "neutral"
