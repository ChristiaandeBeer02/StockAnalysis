"""Navigation history types for back-button restore."""

from __future__ import annotations

from dataclasses import dataclass

MAX_NAV_STACK = 10


@dataclass
class NavState:
    sidebar_index: int
    page_state: object | None = None
