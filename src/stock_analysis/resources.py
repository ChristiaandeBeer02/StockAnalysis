"""Application bundled assets."""

from __future__ import annotations

from importlib.resources import files

_ICON_NAME = "manta-ray.png"


def icon_bytes() -> bytes:
    return (files("stock_analysis") / "resources" / _ICON_NAME).read_bytes()
