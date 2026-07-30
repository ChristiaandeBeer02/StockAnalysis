"""Application configuration."""

from pathlib import Path

APP_NAME = "Stock Analysis"
APP_VERSION = "0.4.0"

DEPRECATED_PATTERN = r"z{4,}"

SKIP_STOCKHOLDING_CODES = {".", ""}


def get_app_data_dir() -> Path:
    """Return the per-user application data directory."""
    base = Path.home() / "AppData" / "Local" / "stockAnalysis"
    base.mkdir(parents=True, exist_ok=True)
    return base


def get_database_path() -> Path:
    return get_app_data_dir() / "stock_data.db"
