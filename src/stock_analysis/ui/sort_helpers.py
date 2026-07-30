"""Helpers for consistent table column sorting."""

from __future__ import annotations

import re
from typing import Any

from PySide6.QtCore import Qt

_SORT_NUMERIC = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")

SORT_ROLE = int(Qt.ItemDataRole.UserRole) + 1


def cell_sort_value(value: Any) -> str | float:
    """Return a comparable sort key for a table cell value."""
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return str(value)

    text = value.strip()
    if text in ("", "—"):
        return text

    numeric_text = text
    if numeric_text.startswith("R "):
        numeric_text = numeric_text[2:].strip()
    numeric_text = numeric_text.replace(",", "")
    if numeric_text.endswith("%"):
        numeric_text = numeric_text[:-1].strip()
    if numeric_text.startswith("+") and len(numeric_text) > 1:
        numeric_text = numeric_text[1:]

    if _SORT_NUMERIC.match(numeric_text):
        return float(numeric_text)

    return text.lower()
