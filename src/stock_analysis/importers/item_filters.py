"""Rules for skipping junk / system rows from IQ Retail exports."""

from __future__ import annotations

import re

_END_OF_REPORT = re.compile(r"end\s+of\s+report", re.IGNORECASE)


def is_summary_sku(code: str) -> bool:
    return code.strip().lower().startswith("totals")


def is_junk_sku(code: str) -> bool:
    code = code.strip()
    if not code or code == ".":
        return True
    if is_summary_sku(code):
        return True
    if _END_OF_REPORT.search(code) or "***" in code:
        return True
    if code.startswith("*"):
        return True
    if len(code) == 1 and not code.isalnum():
        return True
    return False


def is_junk_description(description: str) -> bool:
    desc = description.strip().lower()
    if desc == "open item for quotation":
        return True
    return False


def should_skip_item(code: str, description: str = "") -> bool:
    return is_junk_sku(code) or is_junk_description(description)


def item_status(*, is_deprecated: bool, not_in_turn_report: bool, has_enrichment: bool) -> str:
    if is_deprecated:
        return "Deprecated"
    if has_enrichment and not_in_turn_report:
        return "No turn data"
    return "Active"
