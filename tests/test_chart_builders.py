"""Tests for chart builder helpers."""

import pytest
from PySide6.QtCharts import QBarSeries, QValueAxis
from PySide6.QtCore import QMargins
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from stock_analysis.ui.widgets.chart_builders import (
    DEPT_CHART_OVERSTOCK_COLOR,
    DEPT_CHART_SLOW_MOVING_COLOR,
    DEPT_CHART_TOTAL_COLOR,
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


def test_dept_values_chart_multi_series_uses_kpi_colors_and_axis_max(qapp):
    dept = {"A1": 100.0, "B1": 50.0}
    overstock = {"A1": 20.0, "B1": 5.0}
    slow_moving = {"A1": 15.0, "B1": 30.0}
    view, click_labels = build_dept_values_chart(
        dept,
        overstock_values=overstock,
        slow_moving_values=slow_moving,
    )
    assert click_labels == ["A1", "B1"]

    chart = view.chart()
    series = next(s for s in chart.series() if isinstance(s, QBarSeries))
    bar_sets = list(series.barSets())
    assert len(bar_sets) == 3
    assert [bar_set.label() for bar_set in bar_sets] == [
        "Total Value",
        "Total Overstock",
        "Total Slow Moving",
    ]
    assert bar_sets[0].color().name().upper() == QColor(DEPT_CHART_TOTAL_COLOR).name().upper()
    assert bar_sets[1].color().name().upper() == QColor(DEPT_CHART_OVERSTOCK_COLOR).name().upper()
    assert bar_sets[2].color().name().upper() == QColor(DEPT_CHART_SLOW_MOVING_COLOR).name().upper()
    assert chart.legend().isVisible()

    axis_y = next(axis for axis in chart.axes() if isinstance(axis, QValueAxis))
    assert float(axis_y.max()) >= 100.0
    assert float(axis_y.max()) == _nice_axis_max(100.0)


def test_item_sales_chart_y_axis_covers_all_period_totals(qapp):
    chart_data = {
        "labels": ["21/07/2026\n27/07/2026"],
        "period_keys": ["21/07/2026 - 27/07/2026"],
        "qty_sold": [794.0],
    }
    view, labels = build_item_sales_chart(chart_data)
    assert labels == ["21/07/2026 - 27/07/2026"]

    chart = view.chart()
    assert not chart.legend().isVisible()
    series = next(s for s in chart.series() if isinstance(s, QBarSeries))
    assert len(list(series.barSets())) == 1
    assert list(series.barSets())[0].label() == "Qty Sold"
    axis_y = next(axis for axis in chart.axes() if isinstance(axis, QValueAxis))
    assert float(axis_y.max()) >= 794.0
    assert float(axis_y.max()) == _nice_axis_max(794.0)
