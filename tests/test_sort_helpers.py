"""Tests for table sort helpers."""

import pytest

from stock_analysis.ui.sort_helpers import cell_sort_value


def test_cell_sort_value_parses_percentage():
    assert cell_sort_value("93.9%") == pytest.approx(93.9)
    assert cell_sort_value("9.9%") == pytest.approx(9.9)


def test_cell_sort_value_percentage_sort_order():
    values = ["93.9%", "9.9%", "84.5%", "7.5%"]
    sorted_desc = sorted(values, key=cell_sort_value, reverse=True)
    assert sorted_desc == ["93.9%", "84.5%", "9.9%", "7.5%"]


def test_cell_sort_value_parses_rand():
    assert cell_sort_value("R 1,234.50") == pytest.approx(1234.5)
