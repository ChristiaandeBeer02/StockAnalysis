"""Home dashboard visibility preferences."""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from stock_analysis.db.session import set_app_state

_CONFIG_KEY = "dashboard_config"

DEFAULT_CONFIG = {
    "show_kpis": True,
    "show_charts": True,
    "show_alerts": True,
    "show_sales_tab": True,
    "show_slow_moving_tab": True,
    "show_stock_health": True,
}


def get_dashboard_config(session: Session) -> dict:
    from stock_analysis.db.models import AppState

    state = session.get(AppState, _CONFIG_KEY)
    if not state or not state.value:
        return dict(DEFAULT_CONFIG)
    try:
        data = json.loads(state.value)
    except json.JSONDecodeError:
        return dict(DEFAULT_CONFIG)
    merged = dict(DEFAULT_CONFIG)
    merged.update({k: bool(v) for k, v in data.items() if k in DEFAULT_CONFIG})
    return merged


def save_dashboard_config(session: Session, config: dict) -> None:
    payload = {key: bool(config.get(key, DEFAULT_CONFIG[key])) for key in DEFAULT_CONFIG}
    set_app_state(session, _CONFIG_KEY, json.dumps(payload))
