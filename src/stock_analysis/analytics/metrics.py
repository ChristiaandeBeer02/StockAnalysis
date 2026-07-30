"""Shared inventory metric calculations."""

from __future__ import annotations

from sqlalchemy import case, func

from stock_analysis.db.models import Item, PeriodTurnLine

DEFAULT_OPTIMUM_STOCK_MONTHS = 2.0
DEFAULT_HOLDING_WEEKS = 2
DEFAULT_STOCK_BUFFER_MIN_PCT = 20.0
DEFAULT_STOCK_BUFFER_MAX_PCT = 30.0


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


def gross_margin_pct(profit: float, revenue: float) -> float:
    return (profit / revenue * 100.0) if revenue else 0.0


def gross_profit_from_margin(revenue: float, gross_margin_pct_value: float | None) -> float:
    if gross_margin_pct_value is None:
        return 0.0
    return revenue * (gross_margin_pct_value / 100.0)


def markup_pct(profit: float, cost: float) -> float:
    return (profit / cost * 100.0) if cost else 0.0


def pct_in_range(value: float, min_pct: float, max_pct: float, mode: str) -> bool:
    """Return True when value matches the range filter (mode: 'inside' or 'outside')."""
    inside = min_pct <= value <= max_pct
    return inside if mode == "inside" else not inside


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


def stock_position_from_weekly_sales(
    on_hand: float,
    total_qty_sold: float,
    lookback_weeks: int,
    optimum_months: float = DEFAULT_OPTIMUM_STOCK_MONTHS,
) -> tuple[float, float]:
    """Return (over_qty, under_qty) using average weekly sales across the lookback window."""
    weeks = max(1, lookback_weeks)
    avg_monthly_sales = (total_qty_sold / weeks) * (30.0 / 7.0)
    return (
        computed_overstock_qty(on_hand, avg_monthly_sales, optimum_months),
        computed_understock_qty(on_hand, avg_monthly_sales, optimum_months),
    )


def compute_base_target_qty(
    total_qty_sold: float,
    period_weeks: int,
    holding_weeks: int,
) -> float:
    """Target stock from average weekly sales over the period times holding weeks."""
    period = max(1, period_weeks)
    hold = max(1, holding_weeks)
    avg_weekly = total_qty_sold / period
    return avg_weekly * hold


def compute_healthy_band(
    base_target: float,
    min_buffer_pct: float,
    max_buffer_pct: float,
) -> tuple[float, float]:
    """Return (min_healthy, max_healthy) as base_target × (1 + buffer%)."""
    min_mult = 1.0 + min_buffer_pct / 100.0
    max_mult = 1.0 + max_buffer_pct / 100.0
    return base_target * min_mult, base_target * max_mult


def stock_position_from_holding_policy(
    on_hand: float,
    total_qty_sold: float,
    period_weeks: int,
    holding_weeks: int,
    min_buffer_pct: float,
    max_buffer_pct: float,
) -> tuple[float, float, float, float]:
    """Return (over_qty, under_qty, min_healthy, max_healthy)."""
    base_target = compute_base_target_qty(total_qty_sold, period_weeks, holding_weeks)
    min_healthy, max_healthy = compute_healthy_band(base_target, min_buffer_pct, max_buffer_pct)
    over_qty = max(0.0, on_hand - max_healthy)
    under_qty = min(0.0, on_hand - min_healthy)
    return over_qty, under_qty, min_healthy, max_healthy


def weeks_of_cover(on_hand: float, total_qty_sold: float, period_weeks: int) -> float:
    """Weeks of stock at the item's average weekly sales rate in the lookback window."""
    period = max(1, period_weeks)
    avg_weekly = total_qty_sold / period
    if avg_weekly <= 0:
        return 0.0
    return on_hand / avg_weekly


def slow_moving_cover_threshold_weeks(holding_weeks: int) -> float:
    """Cover above this (when overstocked) classifies as slow moving."""
    return 2.0 * max(1, holding_weeks)


def stock_health_category(
    *,
    under_qty: float,
    over_qty: float,
    sold: float,
    on_hand: float,
    weeks_of_cover: float = 0.0,
    holding_weeks: int = DEFAULT_HOLDING_WEEKS,
) -> str | None:
    """Exclusive stock health bucket: understock, dead, slow_moving, overstock, or healthy."""
    if under_qty < 0:
        return "understocked"
    if sold == 0 and on_hand > 0:
        return "dead"
    if over_qty > 0:
        if weeks_of_cover > slow_moving_cover_threshold_weeks(holding_weeks):
            return "slow_moving"
        return "overstocked"
    if on_hand > 0:
        return "healthy"
    return None


def item_stock_health(
    on_hand: float,
    sold: float,
    *,
    lookback_weeks: int,
    holding_weeks: int,
    min_buffer_pct: float,
    max_buffer_pct: float,
) -> tuple[str | None, float, float, float]:
    """Return (category, over_qty, under_qty, weeks_of_cover)."""
    over_qty, under_qty, _, _ = stock_position_from_holding_policy(
        on_hand,
        sold,
        lookback_weeks,
        holding_weeks,
        min_buffer_pct,
        max_buffer_pct,
    )
    cover = weeks_of_cover(on_hand, sold, lookback_weeks)
    category = stock_health_category(
        under_qty=under_qty,
        over_qty=over_qty,
        sold=sold,
        on_hand=on_hand,
        weeks_of_cover=cover,
        holding_weeks=holding_weeks,
    )
    return category, over_qty, under_qty, cover
