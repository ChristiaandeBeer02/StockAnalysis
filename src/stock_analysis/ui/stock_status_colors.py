"""Shared stock-status colors for charts and KPI accents."""

DEAD_STOCK = "#FF1744"
UNDERSTOCKED = "#FFEA00"
OVERSTOCKED = "#118DFF"
SLOW_MOVING = "#B388FF"
HEALTHY = "#84CC16"
STOCK_VALUE = "#8A8886"
NO_MOVEMENT = "#8A8886"

STOCK_HEALTH_COLORS: dict[str, str] = {
    "Dead Stock": DEAD_STOCK,
    "Understocked": UNDERSTOCKED,
    "Overstocked": OVERSTOCKED,
    "Slow Moving": SLOW_MOVING,
    "Healthy": HEALTHY,
    "No movement data": NO_MOVEMENT,
}

DEPT_CHART_TOTAL_COLOR = STOCK_VALUE
DEPT_CHART_OVERSTOCK_COLOR = OVERSTOCKED
DEPT_CHART_SLOW_MOVING_COLOR = SLOW_MOVING

KPI_ACCENT_COLORS: dict[str, str] = {
    "stock-value": STOCK_VALUE,
    "stock-over": OVERSTOCKED,
    "stock-under": UNDERSTOCKED,
    "stock-slow": SLOW_MOVING,
    "stock-dead": DEAD_STOCK,
}
