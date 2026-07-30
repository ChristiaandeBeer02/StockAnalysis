"""Parser for IQ Retail Detailed Stockholding exports (sthold2 format)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from stock_analysis.config import SKIP_STOCKHOLDING_CODES
from stock_analysis.importers.item_filters import should_skip_item
from stock_analysis.importers.iq_retail_parser import (
    ParseStats,
    extract_date_printed,
    extract_period,
    is_deprecated_description,
    parse_currency,
    parse_float,
    part_at,
    read_export_lines,
    should_skip_line,
)


@dataclass
class StockholdingRow:
    code: str
    description: str
    on_hand: float
    stock_value: float
    unit_cost: float | None
    is_deprecated: bool


@dataclass
class StockholdingParseResult:
    rows: list[StockholdingRow]
    period_start: str | None
    period_end: str | None
    date_printed: datetime | None
    stats: ParseStats


def _is_header_row(parts: list[str]) -> bool:
    if not parts:
        return False
    first = parts[0].strip().lower()
    return first == "code" or first.startswith("code")


def _parse_data_row(parts: list[str]) -> StockholdingRow | None:
    code = parts[0].strip() if parts else ""
    description = part_at(parts, 2) or part_at(parts, 3)
    if not code or code in SKIP_STOCKHOLDING_CODES or code.lower() == "code":
        return None
    if should_skip_item(code, description):
        return None

    # Positional: onhand ~col 10, stock value ~col 14 (sparse CSV)
    on_hand = 0.0
    stock_value = 0.0
    for i, part in enumerate(parts):
        val = part.strip()
        if not val:
            continue
        if val.upper().startswith("R"):
            stock_value = parse_currency(val)
        elif i >= 8 and on_hand == 0.0:
            try:
                f = parse_float(val)
                if "R" not in val.upper():
                    on_hand = f
            except ValueError:
                pass

    if on_hand == 0.0:
        for i in range(6, min(len(parts), 14)):
            val = parts[i].strip()
            if val and not val.upper().startswith("R"):
                f = parse_float(val)
                if f != 0.0 or val == "0.00":
                    on_hand = f
                    break

    if stock_value == 0.0:
        for part in parts:
            val = part.strip()
            if val.upper().startswith("R"):
                stock_value = parse_currency(val)
                break

    unit_cost = None
    if on_hand > 0 and stock_value > 0:
        unit_cost = round(stock_value / on_hand, 4)

    deprecated = is_deprecated_description(description)
    return StockholdingRow(
        code=code,
        description=description,
        on_hand=on_hand,
        stock_value=stock_value,
        unit_cost=unit_cost,
        is_deprecated=deprecated,
    )


def parse_stockholding_file(path: Path) -> StockholdingParseResult:
    lines = read_export_lines(path)
    period_start, period_end = extract_period(lines)
    date_printed = extract_date_printed(lines)

    rows: list[StockholdingRow] = []
    stats = ParseStats()
    seen_codes: set[str] = set()

    for line in lines:
        if should_skip_line(line):
            stats.skipped_rows += 1
            continue

        parts = line.split(",")
        if _is_header_row(parts):
            stats.skipped_rows += 1
            continue

        code = parts[0].strip() if parts else ""
        description = part_at(parts, 2) or part_at(parts, 3)
        if code and should_skip_item(code, description):
            stats.junk_rows += 1
            stats.skipped_rows += 1
            continue

        row = _parse_data_row(parts)
        if row is None:
            stats.skipped_rows += 1
            continue

        if row.code in seen_codes:
            continue
        seen_codes.add(row.code)

        rows.append(row)
        stats.total_rows += 1
        if row.is_deprecated:
            stats.deprecated_rows += 1

    return StockholdingParseResult(
        rows=rows,
        period_start=period_start,
        period_end=period_end,
        date_printed=date_printed,
        stats=stats,
    )
