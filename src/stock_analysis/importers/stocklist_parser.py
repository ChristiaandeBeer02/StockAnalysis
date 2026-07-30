"""Parser for IQ Retail Stocklist exports (department assignment)."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from stock_analysis.importers.iq_retail_parser import parse_float
from stock_analysis.importers.item_filters import should_skip_item


@dataclass
class StocklistRow:
    code: str
    description: str
    department: str
    on_hand: float = 0.0
    gross_margin_pct: float | None = None
    markup_pct: float | None = None


@dataclass
class StocklistParseStats:
    total_rows: int
    junk_rows: int
    eligible_rows: int


@dataclass
class StocklistParseResult:
    rows: list[StocklistRow]
    stats: StocklistParseStats


def _read_csv_dicts(path: Path) -> list[dict[str, str]]:
    for encoding in ("utf-8-sig", "latin-1", "cp1252"):
        try:
            with path.open(newline="", encoding=encoding) as handle:
                return list(csv.DictReader(handle))
        except UnicodeDecodeError:
            continue
    with path.open(newline="", encoding="latin-1", errors="replace") as handle:
        return list(csv.DictReader(handle))


def _field(row: dict[str, str], *names: str) -> str:
    for name in names:
        if name in row and row[name] is not None:
            return str(row[name]).strip()
    return ""


def _has_column(rows: list[dict[str, str]], *names: str) -> bool:
    if not rows:
        return False
    keys = set(rows[0].keys())
    return any(name in keys for name in names)


def _parse_on_hand(raw: dict[str, str]) -> float:
    value = _field(raw, "ONHAND", "OnHand", "Onhand")
    if not value:
        return 0.0
    return parse_float(value)


def _parse_optional_float(raw: dict[str, str], *names: str) -> float | None:
    value = _field(raw, *names)
    if not value:
        return None
    return parse_float(value)


def parse_stocklist_file(path: Path, *, require_on_hand: bool = False) -> StocklistParseResult:
    raw_rows = _read_csv_dicts(path)
    if not raw_rows:
        raise ValueError("Stocklist file is empty.")

    if not _has_column(raw_rows, "CODE", "Code"):
        raise ValueError("Stocklist file is missing required CODE column.")
    if not _has_column(raw_rows, "SUBDEPARTM", "SubDepartm"):
        raise ValueError("Stocklist file is missing required SUBDEPARTM column.")
    if require_on_hand and not _has_column(raw_rows, "ONHAND", "OnHand", "Onhand"):
        raise ValueError("Stocklist file is missing required ONHAND column.")

    parsed: list[StocklistRow] = []
    junk_rows = 0
    for raw in raw_rows:
        code = _field(raw, "CODE", "Code")
        description = _field(raw, "DESCRIPT", "Descript", "Description")
        if should_skip_item(code, description):
            junk_rows += 1
            continue
        department = _field(raw, "SUBDEPARTM", "SubDepartm")
        parsed.append(
            StocklistRow(
                code=code,
                description=description,
                department=department,
                on_hand=_parse_on_hand(raw),
                gross_margin_pct=_parse_optional_float(raw, "GP_1", "Gp_1"),
                markup_pct=_parse_optional_float(raw, "MARKUP_1", "Markup_1"),
            )
        )

    return StocklistParseResult(
        rows=parsed,
        stats=StocklistParseStats(
            total_rows=len(raw_rows),
            junk_rows=junk_rows,
            eligible_rows=len(parsed),
        ),
    )
