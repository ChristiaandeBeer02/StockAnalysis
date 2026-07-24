"""Parser for IQ Retail Stock Turn (Under Stocking) exports."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from stock_analysis.importers.item_filters import should_skip_item
from stock_analysis.importers.iq_retail_parser import (
    ParseStats,
    is_deprecated_description,
    parse_float,
    part_at,
    read_export_lines,
    should_skip_line,
)
from stock_analysis.importers.turn_parser import TurnRow, _is_header_row, parse_turn_file

UNDER_IDX = {
    "dept": 0,
    "supplier": 2,
    "code": 3,
    "description": 4,
    "on_hand": 5,
    "qty_30": 10,
    "qty_90": 13,
    "qty_180": 14,
    "avg_3mo": 16,
    "avg_6mo": 17,
    "last_unit_cost": 26,
    "under_qty_3mo": 29,
    "under_qty_6mo": 30,
    "under_value_3mo": 31,
    "under_value_6mo": 35,
}


@dataclass
class TurnunderParseResult:
    rows: dict[str, TurnRow] = field(default_factory=dict)
    stats: ParseStats = field(default_factory=ParseStats)


def _parse_turnunder_row(parts: list[str]) -> TurnRow | None:
    code = part_at(parts, UNDER_IDX["code"])
    if not code or code.lower() == "code":
        return None
    description = part_at(parts, UNDER_IDX["description"])
    if should_skip_item(code, description):
        return None
    return TurnRow(
        dept=part_at(parts, UNDER_IDX["dept"]),
        supplier=part_at(parts, UNDER_IDX["supplier"]),
        code=code,
        description=description,
        on_hand=parse_float(part_at(parts, UNDER_IDX["on_hand"])),
        qty_sold_30=parse_float(part_at(parts, UNDER_IDX["qty_30"])),
        qty_sold_90=parse_float(part_at(parts, UNDER_IDX["qty_90"])),
        qty_sold_180=parse_float(part_at(parts, UNDER_IDX["qty_180"])),
        avg_monthly_sales_3mo=parse_float(part_at(parts, UNDER_IDX["avg_3mo"])),
        avg_monthly_sales_6mo=parse_float(part_at(parts, UNDER_IDX["avg_6mo"])),
        last_unit_cost=parse_float(part_at(parts, UNDER_IDX["last_unit_cost"])),
        over_stock_qty_3mo=0.0,
        over_stock_qty_6mo=0.0,
        over_stock_value_3mo=0.0,
        over_stock_value_6mo=0.0,
        under_stock_qty_3mo=parse_float(part_at(parts, UNDER_IDX["under_qty_3mo"])),
        under_stock_qty_6mo=parse_float(part_at(parts, UNDER_IDX["under_qty_6mo"])),
        under_stock_value_3mo=parse_float(part_at(parts, UNDER_IDX["under_value_3mo"])),
        under_stock_value_6mo=parse_float(part_at(parts, UNDER_IDX["under_value_6mo"])),
        is_deprecated=is_deprecated_description(description),
    )


def parse_turnunder_file(path: Path) -> TurnunderParseResult:
    lines = read_export_lines(path)
    rows: dict[str, TurnRow] = {}
    stats = ParseStats()

    for line in lines:
        if should_skip_line(line):
            stats.skipped_rows += 1
            continue
        parts = line.split(",")
        if _is_header_row(parts):
            stats.skipped_rows += 1
            continue
        row = _parse_turnunder_row(parts)
        if row is None:
            stats.skipped_rows += 1
            continue
        rows[row.code] = row
        stats.total_rows += 1
        if row.is_deprecated:
            stats.deprecated_rows += 1

    return TurnunderParseResult(rows=rows, stats=stats)


def merge_turn_reports(turn_path: Path, turnunder_path: Path) -> tuple[list[TurnRow], str | None, str | None]:
    """Merge Turn and Turnunder files into a single dataset keyed by Code."""
    turn = parse_turn_file(turn_path)
    under = parse_turnunder_file(turnunder_path)

    merged: list[TurnRow] = []
    for row in turn.rows:
        under_row = under.rows.get(row.code)
        if under_row:
            row.under_stock_qty_3mo = under_row.under_stock_qty_3mo
            row.under_stock_qty_6mo = under_row.under_stock_qty_6mo
            row.under_stock_value_3mo = under_row.under_stock_value_3mo
            row.under_stock_value_6mo = under_row.under_stock_value_6mo
        merged.append(row)

    turn_codes = {r.code for r in turn.rows}
    for code, under_row in under.rows.items():
        if code not in turn_codes:
            merged.append(under_row)

    return merged, turn.period_start, turn.period_end
