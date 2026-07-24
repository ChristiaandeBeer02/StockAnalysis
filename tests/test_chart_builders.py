"""Tests for chart builder helpers."""

import pytest
from PySide6.QtCore import QMargins
from PySide6.QtWidgets import QApplication

from stock_analysis.ui.widgets.chart_builders import _nice_axis_max, build_dept_values_chart


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
    view, _ = build_dept_values_chart(dept)
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
