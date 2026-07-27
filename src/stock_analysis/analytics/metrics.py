"""Shared inventory metric calculations."""

from __future__ import annotations

from sqlalchemy import case, func

from stock_analysis.db.models import Item, PeriodTurnLine

DEFAULT_OPTIMUM_STOCK_MONTHS = 2.0


def effective_on_hand(baseline_map: dict[int, float], item_id: int, line_on_hand: float) -> float:
    """Prefer baseline qty (e.g. after stock take), fall back to turn report on_hand."""
    return baseline_map.get(item_id, line_on_hand)


def effective_unit_cost(line: PeriodTurnLine | None, item: Item) -> float:
    """Resolve unit cost: turn last_unit_cost when > 0, else item.unit_cost."""
    if line is not None and line.last_unit_cost > 0:
        return line.last_unit_cost
    return item.unit_cost or 0.0


def effective_unit_cost_expr(turn_last_unit_cost, item_unit_cost):
    """SQL expression matching effective_unit_cost() for aggregates."""
    return case(
        (turn_last_unit_cost > 0, turn_last_unit_cost),
        else_=func.coalesce(item_unit_cost, 0.0),
    )


def stock_value(qty: float, unit_cost: float) -> float:
    return qty * unit_cost


def sales_value(qty_sold_90: float, unit_cost: float) -> float:
    return qty_sold_90 * unit_cost


def target_stock_qty(avg_monthly_sales_3mo: float, optimum_months: float) -> float:
    return avg_monthly_sales_3mo * optimum_months


def computed_overstock_qty(
    on_hand: float,
    avg_monthly_sales_3mo: float,
    optimum_months: float = DEFAULT_OPTIMUM_STOCK_MONTHS,
) -> float:
    return max(0.0, on_hand - target_stock_qty(avg_monthly_sales_3mo, optimum_months))


def computed_understock_qty(
    on_hand: float,
    avg_monthly_sales_3mo: float,
    optimum_months: float = DEFAULT_OPTIMUM_STOCK_MONTHS,
) -> float:
    return min(0.0, on_hand - target_stock_qty(avg_monthly_sales_3mo, optimum_months))


def stock_position_from_line(
    line: PeriodTurnLine,
    on_hand: float,
    optimum_months: float = DEFAULT_OPTIMUM_STOCK_MONTHS,
) -> tuple[float, float]:
    """Return (over_qty, under_qty) using computed stock levels."""
    over_qty = computed_overstock_qty(on_hand, line.avg_monthly_sales_3mo, optimum_months)
    under_qty = computed_understock_qty(on_hand, line.avg_monthly_sales_3mo, optimum_months)
    return over_qty, under_qty


def stock_health_category(
    *,
    under_qty: float,
    over_qty: float,
    sold: float,
    on_hand: float,
) -> str | None:
    """Exclusive stock health bucket: understock, slow_moving, overstock, or healthy."""
    if under_qty < 0:
        return "understocked"
    if sold == 0 and on_hand > 0:
        return "slow_moving"
    if over_qty > 0:
        return "overstocked"
    if on_hand > 0:
        return "healthy"
    return None
