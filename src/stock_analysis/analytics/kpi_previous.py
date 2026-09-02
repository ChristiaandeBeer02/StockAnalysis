"""Previous-window KPI subtitle helpers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from stock_analysis.analytics.lookback import lookback_label


def previous_lookback_weeks(weeks: int) -> int | None:
    n = max(1, int(weeks))
    if n < 2:
        return None
    return n - 1


def previous_window_tag(weeks: int) -> str | None:
    prev = previous_lookback_weeks(weeks)
    if prev is None:
        return None
    return lookback_label(prev)


def format_rand(value: float) -> str:
    return f"R {value:,.2f}"


def format_int_count(value: int | float) -> str:
    return f"{int(value):,}"


def format_qty(value: float) -> str:
    return f"{value:g}"


def format_qty_2dp(value: float) -> str:
    return f"{value:.2f}"


def format_previous_kpi(formatted_amount: str | None, window_tag: str | None) -> str:
    if not formatted_amount or formatted_amount == "—" or not window_tag:
        return ""
    return f"{formatted_amount} · {window_tag}"


def previous_kpi_direction(
    current: float | int | None,
    previous: float | int | None,
    *,
    compare: bool = True,
) -> str:
    if not compare or current is None or previous is None:
        return "neutral"
    if current > previous:
        return "up"
    if current < previous:
        return "down"
    return "neutral"


def apply_previous_amount(
    card: Any,
    current: float | int | None,
    previous: float | int | None,
    *,
    formatter: Callable[[Any], str],
    tag: str | None,
    compare: bool = True,
) -> None:
    if previous is None or not tag:
        card.set_delta("")
        return
    text = format_previous_kpi(formatter(previous), tag)
    card.set_delta(text, previous_kpi_direction(current, previous, compare=compare))


def apply_previous_text(
    card: Any,
    previous_text: str | None,
    *,
    tag: str | None,
) -> None:
    if not previous_text or previous_text == "—" or not tag:
        card.set_delta("")
        return
    card.set_delta(format_previous_kpi(previous_text, tag), "neutral")
