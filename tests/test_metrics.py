"""Tests for shared inventory metric calculations."""

import pytest

from stock_analysis.analytics.metrics import (
    DEFAULT_OPTIMUM_STOCK_MONTHS,
    compute_base_target_qty,
    compute_healthy_band,
    computed_overstock_qty,
    computed_understock_qty,
    effective_on_hand,
    effective_unit_cost,
    gross_margin_pct,
    markup_pct,
    pct_in_range,
    slow_moving_cover_threshold_weeks,
    stock_health_category,
    stock_position_from_holding_policy,
    weeks_of_cover,
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


def test_stock_health_category_zero_sales_is_dead():
    assert (
        stock_health_category(under_qty=0, over_qty=20, sold=0, on_hand=20) == "dead"
    )


def test_stock_health_category_slow_when_cover_exceeds_twice_hold():
    assert slow_moving_cover_threshold_weeks(2) == pytest.approx(4.0)
    assert weeks_of_cover(300.0, 5.0, 2) == pytest.approx(120.0)
    assert (
        stock_health_category(
            under_qty=0,
            over_qty=20,
            sold=5.0,
            on_hand=300,
            weeks_of_cover=120.0,
            holding_weeks=2,
        )
        == "slow_moving"
    )


def test_stock_health_category_overstock_when_cover_within_twice_hold():
    assert (
        stock_health_category(
            under_qty=0,
            over_qty=5,
            sold=10.0,
            on_hand=35,
            weeks_of_cover=3.5,
            holding_weeks=2,
        )
        == "overstocked"
    )


def test_stock_health_category_understock_takes_priority():
    assert (
        stock_health_category(under_qty=-5, over_qty=0, sold=0, on_hand=10) == "understocked"
    )


def test_compute_base_target_qty():
    assert compute_base_target_qty(120.0, 4, 2) == pytest.approx(60.0)
    assert compute_base_target_qty(100.0, 1, 2) == pytest.approx(200.0)


def test_compute_healthy_band():
    min_h, max_h = compute_healthy_band(100.0, 20.0, 30.0)
    assert min_h == pytest.approx(120.0)
    assert max_h == pytest.approx(130.0)


def test_stock_position_from_holding_policy_bands():
    over_qty, under_qty, min_h, max_h = stock_position_from_holding_policy(
        55.0, 50.0, 1, 1, 20.0, 30.0
    )
    assert min_h == pytest.approx(60.0)
    assert max_h == pytest.approx(65.0)
    assert under_qty == pytest.approx(-5.0)
    assert over_qty == pytest.approx(0.0)

    over_qty, under_qty, _, max_h = stock_position_from_holding_policy(
        70.0, 50.0, 1, 1, 20.0, 30.0
    )
    assert over_qty == pytest.approx(5.0)
    assert under_qty == pytest.approx(0.0)


def test_markup_pct_on_cost():
    assert markup_pct(40.0, 100.0) == pytest.approx(40.0)


def test_gross_profit_from_margin():
    from stock_analysis.analytics.metrics import gross_profit_from_margin

    assert gross_profit_from_margin(200.0, 40.0) == pytest.approx(80.0)
    assert gross_profit_from_margin(200.0, None) == pytest.approx(0.0)


def test_gross_margin_pct_on_revenue():
    assert gross_margin_pct(40.0, 100.0) == pytest.approx(40.0)


def test_pct_in_range_inside():
    assert pct_in_range(35.0, 30.0, 40.0, "inside") is True
    assert pct_in_range(25.0, 30.0, 40.0, "inside") is False


def test_pct_in_range_outside():
    assert pct_in_range(25.0, 30.0, 40.0, "outside") is True
    assert pct_in_range(35.0, 30.0, 40.0, "outside") is False
