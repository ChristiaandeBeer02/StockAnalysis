"""Compare two stockholding exports (file vs file)."""

from __future__ import annotations

from dataclasses import dataclass

from stock_analysis.importers.item_filters import should_skip_item
from stock_analysis.importers.stockholding_parser import StockholdingParseResult, StockholdingRow

UNKNOWN_DEPARTMENT = "__unknown__"

_ON_HAND_EPSILON = 0.0001
_VALUE_EPSILON = 0.01


@dataclass
class StockholdDiffLine:
    sku: str
    name: str
    on_hand_diff: float
    value_diff: float
    department: str | None = None


def _rows_by_sku(parsed: StockholdingParseResult) -> dict[str, StockholdingRow]:
    by_sku: dict[str, StockholdingRow] = {}
    for row in parsed.rows:
        if should_skip_item(row.code, row.description) or row.is_deprecated:
            continue
        by_sku[row.code] = row
    return by_sku


def compare_stockholdings(
    first: StockholdingParseResult,
    second: StockholdingParseResult,
    *,
    dept_by_sku: dict[str, str | None] | None = None,
) -> list[StockholdDiffLine]:
    """Compare two parsed stockholdings. Deltas are second minus first."""
    first_by_sku = _rows_by_sku(first)
    second_by_sku = _rows_by_sku(second)
    dept_lookup = dept_by_sku or {}

    lines: list[StockholdDiffLine] = []
    for sku in sorted(set(first_by_sku) | set(second_by_sku)):
        left = first_by_sku.get(sku)
        right = second_by_sku.get(sku)
        left_qty = left.on_hand if left else 0.0
        right_qty = right.on_hand if right else 0.0
        left_value = left.stock_value if left else 0.0
        right_value = right.stock_value if right else 0.0
        name = ""
        if right and right.description:
            name = right.description
        elif left and left.description:
            name = left.description

        dept = dept_lookup.get(sku)
        if dept is None or not str(dept).strip():
            dept = UNKNOWN_DEPARTMENT

        lines.append(
            StockholdDiffLine(
                sku=sku,
                name=name,
                on_hand_diff=right_qty - left_qty,
                value_diff=right_value - left_value,
                department=dept,
            )
        )

    lines.sort(key=lambda line: line.value_diff)
    return lines


def has_difference(line: StockholdDiffLine) -> bool:
    return (
        abs(line.on_hand_diff) > _ON_HAND_EPSILON
        or abs(line.value_diff) > _VALUE_EPSILON
    )


def filter_diff_lines(
    lines: list[StockholdDiffLine],
    *,
    differences_only: bool = False,
    dept_filter: str | None = None,
) -> list[StockholdDiffLine]:
    filtered = lines
    if differences_only:
        filtered = [line for line in filtered if has_difference(line)]
    if dept_filter is not None:
        filtered = [line for line in filtered if line.department == dept_filter]
    return filtered


def format_value_diff(value_diff: float) -> str:
    if abs(value_diff) <= _VALUE_EPSILON:
        return "—"
    return f"R {value_diff:+,.2f}"
