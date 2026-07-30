"""Shared IQ Retail CSV parsing utilities."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from stock_analysis.config import DEPRECATED_PATTERN

_PERIOD_RE = re.compile(
    r"Period:\s*(\d{2}/\d{2}/\d{4})\s*to\s*(\d{2}/\d{2}/\d{4})",
    re.IGNORECASE,
)
_DATE_PRINTED_RE = re.compile(
    r"Date Printed\s*:?\s*(\d{2}/\d{2}/\d{4})(?:\s+(\d{2}:\d{2}:\d{2}))?",
    re.IGNORECASE,
)
_OPTIMUM_MONTHS_RE = re.compile(
    r"Optimum Stock Holding In Months:.*?(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_DEPRECATED_RE = re.compile(DEPRECATED_PATTERN, re.IGNORECASE)

_SKIP_PREFIXES = (
    "manta diy",
    "stock turn report",
    "detailed stockholding",
    "report parameters",
    "sort order",
    "qty sold",
    "optimum stock",
    "date printed",
    "page no",
    "current filter",
    "period:",
    "currency:",
    "code,description",
    "dept,,supplier,code",
    "last unitcost",
    "ave monthly sales",
    "stock days on hand",
    "totals:",
)


def is_deprecated_description(description: str) -> bool:
    return bool(_DEPRECATED_RE.search(description))


def parse_currency(value: str) -> float:
    if not value or not str(value).strip():
        return 0.0
    cleaned = str(value).strip().upper().replace("R", "").replace("\u00a0", " ").replace(" ", "")
    if not cleaned:
        return 0.0
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def parse_float(value: str) -> float:
    if not value or not str(value).strip():
        return 0.0
    try:
        return float(str(value).strip().replace(",", ""))
    except ValueError:
        return 0.0


def read_export_lines(path: Path) -> list[str]:
    for encoding in ("utf-8-sig", "latin-1", "cp1252"):
        try:
            return path.read_text(encoding=encoding).splitlines()
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="latin-1", errors="replace").splitlines()


def extract_period(lines: list[str]) -> tuple[str | None, str | None]:
    for line in lines[:30]:
        match = _PERIOD_RE.search(line)
        if match:
            return match.group(1), match.group(2)
    return None, None


def parse_report_date(value: str) -> date | None:
    try:
        return datetime.strptime(value.strip(), "%d/%m/%Y").date()
    except ValueError:
        return None


def extract_date_printed(lines: list[str]) -> datetime | None:
    for line in lines[:30]:
        match = _DATE_PRINTED_RE.search(line)
        if match:
            date_part = match.group(1)
            time_part = match.group(2) or "00:00:00"
            try:
                return datetime.strptime(f"{date_part} {time_part}", "%d/%m/%Y %H:%M:%S")
            except ValueError:
                parsed = parse_report_date(date_part)
                if parsed is not None:
                    return datetime.combine(parsed, datetime.min.time())
    return None


def is_ongoing_stockhold(
    period_start: str | None,
    period_end: str | None,
    date_printed: datetime | None,
) -> bool:
    if not period_start or not period_end or date_printed is None:
        return False
    start = parse_report_date(period_start)
    end = parse_report_date(period_end)
    if start is None or end is None:
        return False
    printed = date_printed.date()
    return start <= printed <= end


def extract_optimum_months(lines: list[str]) -> float | None:
    for line in lines[:30]:
        match = _OPTIMUM_MONTHS_RE.search(line)
        if match:
            return parse_float(match.group(1))
    return None


def should_skip_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped or stripped.replace(",", "") == "":
        return True
    lower = stripped.lower()
    return any(lower.startswith(prefix) for prefix in _SKIP_PREFIXES)


def part_at(parts: list[str], index: int) -> str:
    if index < len(parts):
        return parts[index].strip()
    return ""


@dataclass
class ParseStats:
    total_rows: int = 0
    deprecated_rows: int = 0
    skipped_rows: int = 0
    junk_rows: int = 0

    @property
    def metadata_skipped_rows(self) -> int:
        return self.skipped_rows - self.junk_rows

    @property
    def stock_take_eligible_rows(self) -> int:
        return self.total_rows - self.deprecated_rows
