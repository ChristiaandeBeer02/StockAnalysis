"""Qt Charts builders with Power BI styling."""

from __future__ import annotations

import math

from PySide6.QtCharts import (
    QBarCategoryAxis,
    QBarSeries,
    QBarSet,
    QChart,
    QChartView,
    QLineSeries,
    QPieSeries,
    QPieSlice,
    QValueAxis,
)
from PySide6.QtCore import QLocale, QMargins, Qt
from PySide6.QtGui import QBrush, QColor, QPainter
from PySide6.QtWidgets import QGraphicsView, QSizePolicy

from stock_analysis.analytics.department_names import display_dept

PBI_COLORS = [
    "#118DFF",
    "#12239E",
    "#E66C37",
    "#6B007B",
    "#E044A7",
    "#744EC2",
    "#D9B300",
    "#107C10",
]

# Match kpiCard accent colors in app.py for Home Overview dept chart series.
DEPT_CHART_TOTAL_COLOR = "#118DFF"
DEPT_CHART_OVERSTOCK_COLOR = "#E66C37"
DEPT_CHART_SLOW_MOVING_COLOR = "#D9B300"

# Slices below this share of total hide callout labels (still appear in legend).
PIE_CALLOUT_MIN_PCT = 8.0
# Slices at or above this show the percentage inside the ring.
PIE_INSIDE_LABEL_MIN_PCT = 18.0


def _color(i: int) -> QColor:
    return QColor(PBI_COLORS[i % len(PBI_COLORS)])


def _prepare_chart(chart: QChart) -> None:
    chart.setBackgroundVisible(True)
    chart.setBackgroundBrush(QBrush(QColor("#ffffff")))
    chart.setAnimationOptions(QChart.AnimationOption.NoAnimation)
    chart.setTitle("")
    chart.setMargins(QMargins(8, 4, 8, 4))
    chart.legend().setVisible(True)
    chart.legend().setAlignment(Qt.AlignmentFlag.AlignBottom)


def _nice_axis_max(value: float, tick_count: int = 5) -> float:
    """Round up data max so Y-axis top is a clean round number."""
    if value <= 0:
        return 1.0
    if tick_count < 2:
        tick_count = 2
    rough_step = value / (tick_count - 1)
    magnitude = 10 ** math.floor(math.log10(rough_step))
    normalized = rough_step / magnitude
    if normalized <= 1:
        nice_step = magnitude
    elif normalized <= 2:
        nice_step = 2 * magnitude
    elif normalized <= 5:
        nice_step = 5 * magnitude
    else:
        nice_step = 10 * magnitude
    return nice_step * (tick_count - 1)


def _chart_view(chart: QChart) -> QChartView:
    _prepare_chart(chart)
    view = QChartView(chart)
    view.setRenderHint(QPainter.RenderHint.Antialiasing)
    view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    view.setMinimumSize(160, 180)
    view.setAutoFillBackground(True)
    view.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
    palette = view.palette()
    palette.setColor(view.backgroundRole(), QColor("#ffffff"))
    view.setPalette(palette)
    return view


def build_dept_values_chart(
    dept_values: dict[str, float],
    nickname_map: dict[str, str] | None = None,
    *,
    overstock_values: dict[str, float] | None = None,
    slow_moving_values: dict[str, float] | None = None,
) -> tuple[QChartView, list[str]]:
    if not dept_values:
        return _empty_chart("No department data"), []
    sorted_depts = sorted(dept_values.items(), key=lambda x: x[1], reverse=True)[:10]
    raw_codes = [d[0] for d in sorted_depts]
    labels = [display_dept(code, nickname_map) for code in raw_codes] if nickname_map else raw_codes
    multi_series = overstock_values is not None and slow_moving_values is not None
    if multi_series:
        overstock_values = overstock_values or {}
        slow_moving_values = slow_moving_values or {}
        series_defs = (
            ("Total Value", [d[1] for d in sorted_depts], DEPT_CHART_TOTAL_COLOR),
            (
                "Total Overstock",
                [overstock_values.get(code, 0.0) for code in raw_codes],
                DEPT_CHART_OVERSTOCK_COLOR,
            ),
            (
                "Total Slow Moving",
                [slow_moving_values.get(code, 0.0) for code in raw_codes],
                DEPT_CHART_SLOW_MOVING_COLOR,
            ),
        )
        max_val = max(value for _, values, _ in series_defs for value in values)
    else:
        max_val = max(value for _, value in sorted_depts)
        series_defs = (("Value", [d[1] for d in sorted_depts], _color(0)),)
    series = QBarSeries()
    for name, values, color in series_defs:
        bar_set = QBarSet(name)
        bar_set.setColor(QColor(color))
        bar_set.append(values)
        series.append(bar_set)
    chart = QChart()
    chart.addSeries(series)
    tick_count = 5
    nice_max = _nice_axis_max(max_val, tick_count=tick_count)
    locale = QLocale(QLocale.Language.English, QLocale.Country.UnitedStates)
    chart.setLocale(locale)
    chart.setLocalizeNumbers(True)
    axis_x = QBarCategoryAxis()
    axis_x.append(labels)
    axis_y = QValueAxis()
    axis_y.setRange(0, nice_max)
    axis_y.setLabelFormat("%.0f")
    axis_y.setTickCount(tick_count)
    axis_y.setMinorTickCount(0)
    chart.addAxis(axis_x, Qt.AlignmentFlag.AlignBottom)
    chart.addAxis(axis_y, Qt.AlignmentFlag.AlignLeft)
    series.attachAxis(axis_x)
    series.attachAxis(axis_y)
    view = _chart_view(chart)
    chart.legend().setVisible(multi_series)
    # Qt Charts already reserves space for Y-axis labels; extra left margin double-books it.
    chart.setMargins(QMargins(4, 4, 4, 8))
    view.setMinimumSize(0, 0)
    return view, raw_codes



def _configure_pie_slices(
    series: QPieSeries,
    data: dict[str, float],
    *,
    label_min_pct: float = PIE_CALLOUT_MIN_PCT,
    embedded: bool = False,
) -> bool:
    """Add slices with smart labels. Returns True if legend should be shown."""
    items = sorted(((k, v) for k, v in data.items() if v > 0), key=lambda x: x[1], reverse=True)
    total = sum(v for _, v in items)
    if total <= 0:
        return False

    has_hidden_callouts = False
    for i, (label, value) in enumerate(items):
        pct = 100.0 * value / total
        sl = series.append(label, value)
        sl.setColor(_color(i))
        pct_text = f"{pct:.1f}%"
        legend_text = f"{label} ({pct_text})"
        sl.setLabel(legend_text)

        if embedded:
            sl.setLabelVisible(False)
            has_hidden_callouts = True
        elif pct >= label_min_pct:
            sl.setLabelPosition(QPieSlice.LabelPosition.LabelOutside)
            sl.setLabelArmLengthFactor(0.08 if pct >= PIE_INSIDE_LABEL_MIN_PCT else 0.14)
            sl.setLabelVisible(True)
        else:
            sl.setLabelVisible(False)
            has_hidden_callouts = True

    return has_hidden_callouts or len(items) > 3


def build_pie_chart(
    data: dict[str, float],
    title: str,
    *,
    donut: bool = False,
) -> QChartView:
    if not data or all(v <= 0 for v in data.values()):
        return _empty_chart("No data")
    series = QPieSeries()
    if donut:
        series.setHoleSize(0.55)
    series.setPieSize(0.82)
    show_legend = _configure_pie_slices(series, data)
    chart = QChart()
    chart.addSeries(series)
    view = _chart_view(chart)
    chart.legend().setVisible(show_legend)
    if show_legend:
        chart.legend().setAlignment(Qt.AlignmentFlag.AlignBottom)
    return view


def build_stock_health_chart(breakdown: dict[str, int], *, embedded: bool = False) -> QChartView:
    data = {k: float(v) for k, v in breakdown.items() if v > 0}
    if not data:
        return _empty_chart("No data")
    series = QPieSeries()
    series.setHoleSize(0.55)
    series.setPieSize(0.78)
    _configure_pie_slices(
        series, data, label_min_pct=PIE_CALLOUT_MIN_PCT, embedded=embedded
    )
    chart = QChart()
    chart.addSeries(series)
    chart.setMargins(QMargins(8, 4, 8, 36))
    view = _chart_view(chart)
    chart.legend().setVisible(True)
    chart.legend().setAlignment(Qt.AlignmentFlag.AlignBottom)
    return view


def build_item_sales_chart(chart_data: dict) -> tuple[QChartView, list[str]]:
    labels = chart_data.get("labels", [])
    qty_sold = chart_data.get("qty_sold", [])
    period_keys = chart_data.get("period_keys", labels)
    if not labels:
        return _empty_chart("No period history"), []
    max_val = max(qty_sold, default=0.0)
    tick_count = 5
    nice_max = _nice_axis_max(max_val, tick_count=tick_count)
    chart = QChart()
    series = QBarSeries()
    bar_set = QBarSet("Qty Sold")
    bar_set.setColor(_color(1))
    bar_set.append(qty_sold)
    series.append(bar_set)
    chart.addSeries(series)
    axis_x = QBarCategoryAxis()
    axis_x.append(labels)
    axis_y = QValueAxis()
    axis_y.setRange(0, nice_max)
    axis_y.setLabelFormat("%.0f")
    axis_y.setTickCount(tick_count)
    axis_y.setMinorTickCount(0)
    chart.addAxis(axis_x, Qt.AlignmentFlag.AlignBottom)
    chart.addAxis(axis_y, Qt.AlignmentFlag.AlignLeft)
    series.attachAxis(axis_x)
    series.attachAxis(axis_y)
    chart.legend().setVisible(False)
    chart.setMargins(QMargins(4, 4, 4, 36))
    view = _chart_view(chart)
    chart.legend().setVisible(False)
    return view, period_keys


def build_item_stock_trend_chart(chart_data: dict) -> QChartView:
    labels = chart_data.get("labels", [])
    if not labels:
        return _empty_chart("No stock trend data")
    chart = QChart()
    for i, (name, key) in enumerate((("Overstock", "over_qty"), ("Understock", "under_qty"))):
        line = QLineSeries()
        line.setName(name)
        line.setColor(_color(i + 3))
        for idx, value in enumerate(chart_data.get(key, [])):
            line.append(idx, value)
        chart.addSeries(line)
    axis_x = QBarCategoryAxis()
    axis_x.append(labels)
    axis_y = QValueAxis()
    chart.addAxis(axis_x, Qt.AlignmentFlag.AlignBottom)
    chart.addAxis(axis_y, Qt.AlignmentFlag.AlignLeft)
    for series in chart.series():
        series.attachAxis(axis_x)
        series.attachAxis(axis_y)
    return _chart_view(chart)


def build_abc_chart(summary: dict[str, int]) -> QChartView:
    classes = ["A", "B", "C"]
    bar_set = QBarSet("Items")
    for i, cls in enumerate(classes):
        bar_set.append(summary.get(cls, 0))
    bar_set.setColor(_color(0))
    series = QBarSeries()
    series.append(bar_set)
    chart = QChart()
    chart.addSeries(series)
    axis_x = QBarCategoryAxis()
    axis_x.append(classes)
    axis_y = QValueAxis()
    chart.addAxis(axis_x, Qt.AlignmentFlag.AlignBottom)
    chart.addAxis(axis_y, Qt.AlignmentFlag.AlignLeft)
    series.attachAxis(axis_x)
    series.attachAxis(axis_y)
    return _chart_view(chart)


def _empty_chart(message: str) -> QChartView:
    chart = QChart()
    chart.setBackgroundVisible(True)
    chart.setBackgroundBrush(QBrush(QColor("#ffffff")))
    chart.setAnimationOptions(QChart.AnimationOption.NoAnimation)
    chart.setTitle(message)
    chart.setMargins(QMargins(8, 4, 8, 4))
    chart.legend().setVisible(False)
    view = QChartView(chart)
    view.setRenderHint(QPainter.RenderHint.Antialiasing)
    view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    view.setMinimumSize(160, 180)
    view.setAutoFillBackground(True)
    view.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
    palette = view.palette()
    palette.setColor(view.backgroundRole(), QColor("#ffffff"))
    view.setPalette(palette)
    return view
