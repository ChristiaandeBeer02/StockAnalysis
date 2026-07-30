"""Movement period calculations aligned to a weekly closing day."""

from __future__ import annotations

from datetime import date, timedelta

from stock_analysis.importers.iq_retail_parser import (
    is_ongoing_stockhold,
    parse_report_date,
)
from stock_analysis.importers.stockholding_parser import StockholdingParseResult

WEEKDAY_NAMES = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)


def format_report_date(value: date) -> str:
    return value.strftime("%d/%m/%Y")


def weekday_name(weekday: int) -> str:
    return WEEKDAY_NAMES[weekday]


def baseline_anchor_date(parsed: StockholdingParseResult) -> date | None:
    """Snapshot date for baseline stock levels."""
    if is_ongoing_stockhold(parsed.period_start, parsed.period_end, parsed.date_printed):
        if parsed.date_printed is not None:
            return parsed.date_printed.date()
        return None
    if parsed.period_end:
        return parse_report_date(parsed.period_end)
    return None


def previous_closing_date(anchor: date, closing_weekday: int) -> date | None:
    """Most recent closing day on or before ``anchor``. None if ``anchor`` is already on closing day."""
    if anchor.weekday() == closing_weekday:
        return None
    days_since_closing = (anchor.weekday() - closing_weekday) % 7
    return anchor - timedelta(days=days_since_closing)


def backdate_alignment_period(anchor: date, closing_weekday: int) -> tuple[date, date] | None:
    """Day after previous closing through ``anchor`` (inclusive). None if already on closing day."""
    previous_close = previous_closing_date(anchor, closing_weekday)
    if previous_close is None:
        return None
    return previous_close + timedelta(days=1), anchor


def next_regular_period(after: date, closing_weekday: int) -> tuple[date, date]:
    """First full movement week after ``after``: (closing+1) through closing."""
    start = after + timedelta(days=1)
    week_start_weekday = (closing_weekday + 1) % 7
    days_to_week_start = (week_start_weekday - start.weekday()) % 7
    start = start + timedelta(days=days_to_week_start)
    end = start + timedelta(days=6)
    return start, end


def suggest_next_movement_period(
    anchor: date | None,
    closing_weekday: int | None,
) -> tuple[date, date] | None:
    """Suggest the next movement import range from the stored baseline-as-of date."""
    if closing_weekday is None or anchor is None:
        return None

    alignment = backdate_alignment_period(anchor, closing_weekday)
    if alignment is not None:
        return alignment
    return next_regular_period(anchor, closing_weekday)


def is_catch_up_pending(
    anchor: date | None,
    closing_weekday: int | None,
) -> bool:
    if anchor is None or closing_weekday is None:
        return False
    return backdate_alignment_period(anchor, closing_weekday) is not None


def format_baseline_as_of_label(anchor: date | None, closing_weekday: int | None) -> str:
    if anchor is None:
        return "Not set"
    label = f"{weekday_name(anchor.weekday())} {format_report_date(anchor)}"
    if closing_weekday is not None and is_catch_up_pending(anchor, closing_weekday):
        label += " (not aligned)"
    return label
