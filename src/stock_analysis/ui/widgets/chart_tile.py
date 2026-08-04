"""Chart visual inside a dashboard tile."""

from PySide6.QtCharts import QBarSeries, QHorizontalBarSeries, QPieSeries
from PySide6.QtCharts import QChartView
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QSizePolicy, QWidget

from stock_analysis.ui.widgets.dashboard_tile import DashboardTile


class ChartTile(DashboardTile):
    point_clicked = Signal(str)

    def __init__(self, title: str, subtitle: str = "", parent=None):
        super().__init__(title, subtitle, parent)
        self._click_labels: list[str] = []
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(280)

    def set_chart_view(
        self,
        view: QChartView | QWidget,
        click_labels: list[str] | None = None,
    ) -> None:
        self._click_labels = click_labels or []
        self.set_content(view)
        chart_view = view if isinstance(view, QChartView) else view.findChild(QChartView)
        if chart_view is not None:
            self._wire_clicks(chart_view)

    def _wire_clicks(self, view: QChartView) -> None:
        chart = view.chart()
        for series in chart.series():
            if isinstance(series, (QBarSeries, QHorizontalBarSeries)):
                for bar_set in series.barSets():
                    bar_set.clicked.connect(self._on_bar_clicked)
            elif isinstance(series, QPieSeries):
                for sl in series.slices():
                    sl.clicked.connect(self._on_pie_clicked)

    def _on_bar_clicked(self, index: int) -> None:
        if 0 <= index < len(self._click_labels):
            self.point_clicked.emit(self._click_labels[index])

    def _on_pie_clicked(self) -> None:
        sl = self.sender()
        if sl and hasattr(sl, "label"):
            text = sl.label()
            if "\n" in text:
                text = text.split("\n", 1)[0]
            elif " (" in text:
                text = text.rsplit(" (", 1)[0]
            self.point_clicked.emit(text)
