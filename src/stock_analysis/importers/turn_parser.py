"""Parser for IQ Retail Stock Turn (Over Stocking) exports."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from stock_analysis.importers.item_filters import should_skip_item
from stock_analysis.importers.iq_retail_parser import (
    ParseStats,
    extract_period,
    is_deprecated_description,
    parse_float,
    part_at,
    read_export_lines,
    should_skip_line,
)

# Column indices from IQStockTurn.csv layout
TURN_IDX = {
    "dept": 0,
    "supplier": 2,
    "code": 3,
    "description": 4,
    "on_hand": 5,
    "qty_30": 10,
    "qty_90": 14,
    "qty_180": 15,
    "avg_3mo": 17,
    "avg_6mo": 18,
    "last_unit_cost": 28,
    "over_qty_3mo": 31,
    "over_qty_6mo": 32,
    "over_value_3mo": 33,
    "over_value_6mo": 37,
}


@dataclass
class TurnRow:
    dept: str
    supplier: str
    code: str
    description: str
    on_hand: float
    qty_sold_30: float
    qty_sold_90: float
    qty_sold_180: float
    avg_monthly_sales_3mo: float
    avg_monthly_sales_6mo: float
    last_unit_cost: float
    over_stock_qty_3mo: float
    over_stock_qty_6mo: float
    over_stock_value_3mo: float
    over_stock_value_6mo: float
    is_deprecated: bool = False
    under_stock_qty_3mo: float = 0.0
    under_stock_qty_6mo: float = 0.0
    under_stock_value_3mo: float = 0.0
    under_stock_value_6mo: float = 0.0


@dataclass
class TurnParseResult:
    rows: list[TurnRow] = field(default_factory=list)
    period_start: str | None = None
    period_end: str | None = None
    stats: ParseStats = field(default_factory=ParseStats)


def _is_header_row(parts: list[str]) -> bool:
    return part_at(parts, 0).lower() == "dept" or part_at(parts, 3).lower() == "code"


def _parse_turn_row(parts: list[str]) -> TurnRow | None:
    code = part_at(parts, TURN_IDX["code"])
    if not code or code.lower() == "code":
        return None
    description = part_at(parts, TURN_IDX["description"])
    if should_skip_item(code, description):
        return None
    return TurnRow(
        dept=part_at(parts, TURN_IDX["dept"]),
        supplier=part_at(parts, TURN_IDX["supplier"]),
        code=code,
        description=description,
        on_hand=parse_float(part_at(parts, TURN_IDX["on_hand"])),
        qty_sold_30=parse_float(part_at(parts, TURN_IDX["qty_30"])),
        qty_sold_90=parse_float(part_at(parts, TURN_IDX["qty_90"])),
        qty_sold_180=parse_float(part_at(parts, TURN_IDX["qty_180"])),
        avg_monthly_sales_3mo=parse_float(part_at(parts, TURN_IDX["avg_3mo"])),
        avg_monthly_sales_6mo=parse_float(part_at(parts, TURN_IDX["avg_6mo"])),
        last_unit_cost=parse_float(part_at(parts, TURN_IDX["last_unit_cost"])),
        over_stock_qty_3mo=parse_float(part_at(parts, TURN_IDX["over_qty_3mo"])),
        over_stock_qty_6mo=parse_float(part_at(parts, TURN_IDX["over_qty_6mo"])),
        over_stock_value_3mo=parse_float(part_at(parts, TURN_IDX["over_value_3mo"])),
        over_stock_value_6mo=parse_float(part_at(parts, TURN_IDX["over_value_6mo"])),
        is_deprecated=is_deprecated_description(description),
    )


def parse_turn_file(path: Path) -> TurnParseResult:
    lines = read_export_lines(path)
    period_start, period_end = extract_period(lines)
    rows: list[TurnRow] = []
    stats = ParseStats()
    seen: set[str] = set()

    for line in lines:
        if should_skip_line(line):
            stats.skipped_rows += 1
            continue
        parts = line.split(",")
        if _is_header_row(parts):
            stats.skipped_rows += 1
            continue
        row = _parse_turn_row(parts)
        if row is None:
            stats.skipped_rows += 1
            continue
        if row.code in seen:
            continue
        seen.add(row.code)
        rows.append(row)
        stats.total_rows += 1
        if row.is_deprecated:
            stats.deprecated_rows += 1

    return TurnParseResult(
        rows=rows,
        period_start=period_start,
        period_end=period_end,
        stats=stats,
    )
