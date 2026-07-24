"""Qt Charts panel (native, no WebEngine)."""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from stock_analysis.ui.widgets.chart_builders import (
    build_abc_chart,
    build_dept_values_chart,
    build_item_sales_chart,
    build_item_stock_trend_chart,
    build_stock_health_chart,
    build_top_sellers_chart,
)


class ChartPanel(QWidget):
    """Legacy multi-chart container; prefer ChartTile on dashboard pages."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)

    def _clear(self) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def show_html(self, html: str) -> None:
        self._clear()
        label = QLabel(html)
        label.setWordWrap(True)
        self._layout.addWidget(label)

    def render_dashboard(self, summary: dict) -> None:
        dept = summary.get("dept_values", {})
        top = summary.get("top_sellers", [])
        if not dept and not top:
            self.show_html("Import turn reports to see charts.")
            return
        self._clear()
        row = QHBoxLayout()
        row_widget = QWidget()
        row_widget.setLayout(row)
        self._layout.addWidget(row_widget)
        if dept:
            view, _ = build_dept_values_chart(dept)
            row.addWidget(view, 1)
        if top:
            view, _ = build_top_sellers_chart(top)
            row.addWidget(view, 1)

    def render_item_history(self, chart_data: dict) -> None:
        if not chart_data.get("labels"):
            self.show_html("No period history available for this item.")
            return
        self._clear()
        sales, _ = build_item_sales_chart(chart_data)
        trend = build_item_stock_trend_chart(chart_data)
        self._layout.addWidget(sales, 1)
        self._layout.addWidget(trend, 1)

    def render_abc_chart(self, summary: dict[str, int]) -> None:
        if not summary:
            self.show_html("No ABC data available.")
            return
        self._clear()
        self._layout.addWidget(build_abc_chart(summary))

    def render_stock_health(self, breakdown: dict[str, int]) -> None:
        self._clear()
        self._layout.addWidget(build_stock_health_chart(breakdown))
