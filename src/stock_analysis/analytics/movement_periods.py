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


def catch_up_period(anchor: date, closing_weekday: int) -> tuple[date, date] | None:
    """Day after anchor through the next closing day (inclusive). None if already on closing day."""
    if anchor.weekday() == closing_weekday:
        return None
    start = anchor + timedelta(days=1)
    days_to_closing = (closing_weekday - start.weekday()) % 7
    end = start + timedelta(days=days_to_closing)
    return start, end


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
    last_period_end: date | None,
) -> tuple[date, date] | None:
    """Suggest the next movement import range based on baseline and import history."""
    if closing_weekday is None or anchor is None:
        return None

    if last_period_end is None:
        catch_up = catch_up_period(anchor, closing_weekday)
        if catch_up is not None:
            return catch_up
        return next_regular_period(anchor, closing_weekday)

    return next_regular_period(last_period_end, closing_weekday)


def is_catch_up_pending(
    anchor: date | None,
    closing_weekday: int | None,
    last_period_end: date | None,
) -> bool:
    if anchor is None or closing_weekday is None or last_period_end is not None:
        return False
    return catch_up_period(anchor, closing_weekday) is not None
