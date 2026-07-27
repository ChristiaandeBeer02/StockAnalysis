"""Tests for chart builder helpers."""

import pytest
from PySide6.QtCharts import QValueAxis
from PySide6.QtCore import QMargins
from PySide6.QtWidgets import QApplication

from stock_analysis.ui.widgets.chart_builders import (
    _nice_axis_max,
    build_dept_values_chart,
    build_item_sales_chart,
)

@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_nice_axis_max_large_value():
    assert _nice_axis_max(1_847_293) == 2_000_000


def test_nice_axis_max_medium_value():
    assert _nice_axis_max(850_000) == 2_000_000


def test_nice_axis_max_very_large_value():
    assert _nice_axis_max(3_200_000) == 4_000_000


def test_nice_axis_max_small_value():
    assert _nice_axis_max(42) == 80


def test_nice_axis_max_zero_or_negative():
    assert _nice_axis_max(0) == 1.0
    assert _nice_axis_max(-100) == 1.0


def test_nice_axis_max_always_covers_data():
    values = [1_847_293, 850_000, 3_200_000, 42, 1, 999_999]
    for value in values:
        assert _nice_axis_max(value) >= value


def test_dept_values_chart_does_not_double_reserve_y_axis_space(qapp):
    dept = {"Beverages": 1_900_000, "Tobacco": 400_000, "Pharmacy": 350_000}
    view, click_labels = build_dept_values_chart(dept)
    assert click_labels == ["Beverages", "Tobacco", "Pharmacy"]
    view.resize(492, 270)
    view.show()
    qapp.processEvents()

    chart = view.chart()
    plot_left = chart.plotArea().x()

    chart.setMargins(QMargins(84, 4, 4, 8))
    qapp.processEvents()
    redundant_plot_left = chart.plotArea().x()

    assert plot_left < redundant_plot_left - 40
    assert plot_left < 95


def test_dept_values_chart_uses_nicknames_for_labels(qapp):
    dept = {"A1": 100.0, "B1": 50.0}
    nickname_map = {"A1": "Beverages", "B1": "Tobacco"}
    view, click_labels = build_dept_values_chart(dept, nickname_map)
    assert click_labels == ["A1", "B1"]

    chart = view.chart()
    axis_x = chart.axes()[0]
    assert list(axis_x.categories()) == ["Beverages", "Tobacco"]


def test_item_sales_chart_y_axis_covers_all_period_totals(qapp):
    chart_data = {
        "labels": ["Import #2"],
        "qty_30": [190.0],
        "qty_90": [627.0],
        "qty_180": [794.0],
    }
    view, labels = build_item_sales_chart(chart_data)
    assert labels == ["Import #2"]

    chart = view.chart()
    axis_y = next(axis for axis in chart.axes() if isinstance(axis, QValueAxis))
    assert float(axis_y.max()) >= 794.0
    assert float(axis_y.max()) == _nice_axis_max(794.0)
