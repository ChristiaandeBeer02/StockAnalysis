"""Tests for movement period calculations."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from stock_analysis.analytics.movement_periods import (
    baseline_anchor_date,
    catch_up_period,
    is_catch_up_pending,
    next_regular_period,
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


def test_catch_up_period_tuesday_to_saturday() -> None:
    anchor = date(2026, 6, 30)  # Tuesday
    assert catch_up_period(anchor, closing_weekday=5) == (
        date(2026, 7, 1),
        date(2026, 7, 4),
    )


def test_catch_up_period_returns_none_on_closing_day() -> None:
    anchor = date(2026, 7, 4)  # Saturday
    assert catch_up_period(anchor, closing_weekday=5) is None


def test_catch_up_period_friday_to_saturday() -> None:
    anchor = date(2026, 7, 3)  # Friday
    assert catch_up_period(anchor, closing_weekday=5) == (
        date(2026, 7, 4),
        date(2026, 7, 4),
    )


def test_next_regular_period_after_saturday_close() -> None:
    after = date(2026, 7, 4)  # Saturday
    assert next_regular_period(after, closing_weekday=5) == (
        date(2026, 7, 5),
        date(2026, 7, 11),
    )


def test_suggest_next_movement_period_returns_catch_up_when_no_imports() -> None:
    anchor = date(2026, 6, 30)
    assert suggest_next_movement_period(anchor, 5, None) == (
        date(2026, 7, 1),
        date(2026, 7, 4),
    )


def test_suggest_next_movement_period_returns_next_week_after_import() -> None:
    anchor = date(2026, 6, 30)
    last_end = date(2026, 7, 4)
    assert suggest_next_movement_period(anchor, 5, last_end) == (
        date(2026, 7, 5),
        date(2026, 7, 11),
    )


def test_suggest_next_movement_period_on_closing_day_without_imports() -> None:
    anchor = date(2026, 7, 4)
    assert suggest_next_movement_period(anchor, 5, None) == (
        date(2026, 7, 5),
        date(2026, 7, 11),
    )


def test_is_catch_up_pending() -> None:
    anchor = date(2026, 6, 30)
    assert is_catch_up_pending(anchor, 5, None) is True
    assert is_catch_up_pending(anchor, 5, date(2026, 7, 4)) is False
    assert is_catch_up_pending(date(2026, 7, 4), 5, None) is False
