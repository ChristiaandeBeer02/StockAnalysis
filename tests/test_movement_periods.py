"""Tests for movement period calculations."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from stock_analysis.analytics.movement_periods import (
    backdate_alignment_period,
    baseline_anchor_date,
    format_baseline_as_of_label,
    is_catch_up_pending,
    next_regular_period,
    previous_closing_date,
    previous_regular_period,
    suggest_next_backdate_period,
    suggest_next_movement_period,
)
from stock_analysis.importers.iq_retail_parser import ParseStats
from stock_analysis.importers.stockholding_parser import StockholdingParseResult


def _parsed(
    *,
    period_start: str | None = "01/06/2026",
    period_end: str | None = "30/06/2026",
    date_printed: datetime | None = None,
) -> StockholdingParseResult:
    return StockholdingParseResult(
        rows=[],
        period_start=period_start,
        period_end=period_end,
        date_printed=date_printed,
        stats=ParseStats(),
    )


def test_baseline_anchor_date_uses_period_end_for_closed_period() -> None:
    parsed = _parsed(period_end="30/06/2026")
    assert baseline_anchor_date(parsed) == date(2026, 6, 30)


def test_baseline_anchor_date_uses_date_printed_for_ongoing_stockhold() -> None:
    parsed = _parsed(
        period_start="01/07/2026",
        period_end="31/07/2026",
        date_printed=datetime(2026, 7, 27, 11, 9, 11),
    )
    assert baseline_anchor_date(parsed) == date(2026, 7, 27)


def test_previous_closing_date_tuesday_to_saturday() -> None:
    anchor = date(2026, 6, 30)  # Tuesday
    assert previous_closing_date(anchor, closing_weekday=5) == date(2026, 6, 27)


def test_previous_closing_date_returns_none_on_closing_day() -> None:
    anchor = date(2026, 7, 4)  # Saturday
    assert previous_closing_date(anchor, closing_weekday=5) is None


def test_backdate_alignment_period_tuesday_to_saturday() -> None:
    anchor = date(2026, 6, 30)  # Tuesday
    assert backdate_alignment_period(anchor, closing_weekday=5) == (
        date(2026, 6, 28),
        date(2026, 6, 30),
    )


def test_backdate_alignment_period_returns_none_on_closing_day() -> None:
    anchor = date(2026, 7, 4)  # Saturday
    assert backdate_alignment_period(anchor, closing_weekday=5) is None


def test_backdate_alignment_period_friday_to_saturday() -> None:
    anchor = date(2026, 7, 3)  # Friday
    assert backdate_alignment_period(anchor, closing_weekday=5) == (
        date(2026, 6, 28),
        date(2026, 7, 3),
    )


def test_next_regular_period_after_saturday_close() -> None:
    after = date(2026, 7, 4)  # Saturday
    assert next_regular_period(after, closing_weekday=5) == (
        date(2026, 7, 5),
        date(2026, 7, 11),
    )


def test_previous_regular_period_before_sunday_week_start() -> None:
    before = date(2026, 7, 5)  # Sunday
    assert previous_regular_period(before, closing_weekday=5) == (
        date(2026, 6, 28),
        date(2026, 7, 4),
    )


def test_previous_regular_period_before_baseline_date() -> None:
    before = date(2026, 1, 1)  # Thursday
    assert previous_regular_period(before, closing_weekday=5) == (
        date(2025, 12, 21),
        date(2025, 12, 27),
    )


def test_suggest_next_backdate_period_delegates_to_previous_regular_period() -> None:
    before = date(2026, 7, 5)
    assert suggest_next_backdate_period(before, 5) == previous_regular_period(before, 5)


def test_suggest_next_movement_period_returns_alignment_when_misaligned() -> None:
    anchor = date(2026, 6, 30)
    assert suggest_next_movement_period(anchor, 5) == (
        date(2026, 6, 28),
        date(2026, 6, 30),
    )


def test_suggest_next_movement_period_ignores_batch_history_when_misaligned() -> None:
    anchor = date(2026, 7, 27)  # Monday
    assert suggest_next_movement_period(anchor, 5) == (
        date(2026, 7, 26),
        date(2026, 7, 27),
    )


def test_suggest_next_movement_period_on_closing_day_returns_next_week() -> None:
    anchor = date(2026, 7, 4)
    assert suggest_next_movement_period(anchor, 5) == (
        date(2026, 7, 5),
        date(2026, 7, 11),
    )


def test_suggest_next_movement_period_after_alignment_uses_baseline_date() -> None:
    anchor = date(2026, 6, 27)  # Saturday close after alignment
    assert suggest_next_movement_period(anchor, 5) == (
        date(2026, 6, 28),
        date(2026, 7, 4),
    )


def test_is_catch_up_pending() -> None:
    anchor = date(2026, 6, 30)
    assert is_catch_up_pending(anchor, 5) is True
    assert is_catch_up_pending(date(2026, 7, 4), 5) is False
    assert is_catch_up_pending(None, 5) is False
    assert is_catch_up_pending(anchor, None) is False


def test_format_baseline_as_of_label() -> None:
    anchor = date(2026, 7, 27)
    assert format_baseline_as_of_label(anchor, 5) == "Monday 27/07/2026 (not aligned)"
    assert format_baseline_as_of_label(date(2026, 7, 25), 5) == "Saturday 25/07/2026"
    assert format_baseline_as_of_label(None, 5) == "Not set"
