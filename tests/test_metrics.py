"""Tests for shared inventory metric calculations."""

import pytest

from stock_analysis.analytics.metrics import (
    DEFAULT_OPTIMUM_STOCK_MONTHS,
    computed_overstock_qty,
    computed_understock_qty,
    effective_on_hand,
    effective_unit_cost,
    stock_health_category,
)


class _Item:
    unit_cost = 5.0


class _Line:
    last_unit_cost = 8.0
    avg_monthly_sales_3mo = 30.0


class _LineZeroTurnCost:
    last_unit_cost = 0.0
    avg_monthly_sales_3mo = 30.0


def test_computed_overstock_for_fast_mover():
    assert computed_overstock_qty(18.0, 30.0, 2.0) == pytest.approx(0.0)


def test_computed_overstock_for_slow_mover():
    assert computed_overstock_qty(20.0, 0.0, 2.0) == pytest.approx(20.0)


def test_computed_understock_for_fast_mover():
    assert computed_understock_qty(18.0, 30.0, 2.0) == pytest.approx(-42.0)


def test_effective_on_hand_prefers_baseline():
    baseline_map = {1: 15.0}
    assert effective_on_hand(baseline_map, 1, 10.0) == pytest.approx(15.0)


def test_effective_unit_cost_prefers_turn_line():
    assert effective_unit_cost(_Line(), _Item()) == pytest.approx(8.0)


def test_effective_unit_cost_zero_turn_cost_falls_back_to_item():
    assert effective_unit_cost(_LineZeroTurnCost(), _Item()) == pytest.approx(5.0)


def test_effective_unit_cost_no_turn_line_uses_item():
    assert effective_unit_cost(None, _Item()) == pytest.approx(5.0)


def test_default_optimum_months_is_two():
    assert DEFAULT_OPTIMUM_STOCK_MONTHS == 2.0


def test_stock_health_category_prioritizes_slow_moving_over_overstock():
    assert (
        stock_health_category(under_qty=0, over_qty=20, sold=0, on_hand=20) == "slow_moving"
    )


def test_stock_health_category_understock_takes_priority():
    assert (
        stock_health_category(under_qty=-5, over_qty=0, sold=0, on_hand=10) == "understocked"
    )
