"""Home dashboard page."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from stock_analysis.analytics.cache import get_period_summary_cached, invalidate_summaries, load_summaries
from stock_analysis.analytics.dashboard import (
    _format_delta,
    build_period_comparison,
    filter_stock_rows,
    list_period_batches,
)
from stock_analysis.analytics.dashboard_config import get_dashboard_config
from stock_analysis.db.session import get_session, has_enrichment, has_initial_baseline
from stock_analysis.ui.export_dialog import prompt_export_excel, prompt_export_pdf
from stock_analysis.ui.pages.stock_take_page import StockTakePage
from stock_analysis.ui.widgets.chart_builders import (
    build_dept_values_chart,
    build_stock_health_chart,
)
from stock_analysis.ui.widgets.chart_tile import ChartTile
from stock_analysis.ui.widgets.dashboard_tile import DashboardTile
from stock_analysis.ui.widgets.data_table import DataTable
from stock_analysis.ui.widgets.empty_state import EmptyState
from stock_analysis.ui.widgets.kpi_card import KpiCard
from stock_analysis.ui.widgets.report_header import ReportHeader

_STOCK_ALERT_MODES = {
    "understock": {
        "title": "Understock Alerts",
        "headers": ["SKU", "Name", "On Hand", "Under Qty (3mo)", "Under Value"],
        "filename": "understock_alerts",
    },
    "overstock": {
        "title": "Overstock Items",
        "headers": ["SKU", "Name", "On Hand", "Over Qty (3mo)", "Over Value"],
        "filename": "overstock_items",
    },
}

_SLOW_MODE = {
    "title": "Slow Moving Items",
    "headers": ["SKU", "Name", "On Hand", "Sales (90d)", "Dept"],
    "filename": "slow_moving",
}

_SALES_MODE = {
    "title": "Sales (90d)",
    "headers": ["SKU", "Name", "Dept", "Qty (90d)", "Sales Value"],
    "filename": "sales_90d",
}

_OVERVIEW_TAB = 0
_STOCK_ALERTS_TAB = 1
_SALES_TAB = 2
_SLOW_TAB = 3
_IMPORT_TAB = 4

_SCROLLABLE_TABS = {_STOCK_ALERTS_TAB, _SALES_TAB, _SLOW_TAB}


@dataclass
class HomeNavState:
    tab: int
    alert_type: str
    alerts_dept: str | None
    sales_dept: str | None
    slow_dept: str | None
    selected_batch_id: int | None


class HomePage(QWidget):
    import_initial_requested = Signal()
    import_enrichment_requested = Signal()
    import_period_requested = Signal()
    inventory_dept_requested = Signal(str)
    item_detail_requested = Signal(str)
    data_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("dashboardCanvas")
        self._selected_batch_id: int | None = None
        self._batch_ids: list[int] = []
        self._period: dict = {}
        self._departments: list[str] = []
        self._alert_type = "understock"
        self._alerts_dept: str | None = None
        self._sales_dept: str | None = None
        self._slow_dept: str | None = None
        self._understock_rows: list[dict] = []
        self._overstock_rows: list[dict] = []
        self._slow_rows: list[dict] = []
        self._sales_rows: list[dict] = []
        self._alerts_display_rows: list[list] = []
        self._sales_display_rows: list[list] = []
        self._slow_display_rows: list[list] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        viewport = scroll.viewport()
        viewport.setAutoFillBackground(True)
        viewport_palette = viewport.palette()
        viewport_palette.setColor(viewport.backgroundRole(), QColor("#eaeaea"))
        viewport.setPalette(viewport_palette)
        outer.addWidget(scroll)
        self._scroll = scroll

        container = QWidget()
        container.setObjectName("dashboardCanvas")
        self._layout = QGridLayout(container)
        self._layout.setContentsMargins(16, 16, 16, 16)
        self._layout.setSpacing(12)
        scroll.setWidget(container)

        self._empty = EmptyState(
            "No baseline yet",
            "Import your first Detailed Stockholding report (sthold2) to establish opening stock levels.",
            "Import Initial Baseline",
        )
        self._empty.action_clicked.connect(self.import_initial_requested.emit)
        self._layout.addWidget(self._empty, 0, 0, 1, 6)

        self._dashboard = QWidget()
        self._dashboard.setObjectName("dashboardCanvas")
        dash = QGridLayout(self._dashboard)
        dash.setContentsMargins(0, 0, 0, 0)
        dash.setSpacing(12)

        self._tabs = QTabWidget()
        self._tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        overview_tab = QWidget()
        overview_tab.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        overview_layout = QGridLayout(overview_tab)
        overview_layout.setContentsMargins(4, 8, 4, 4)
        overview_layout.setSpacing(12)

        self._overview_header = ReportHeader("Stock Overview", "")
        overview_layout.addWidget(self._overview_header, 0, 0, 1, 6)

        self._kpi_skus = KpiCard("Total SKUs")
        self._kpi_value = KpiCard("Stock Value")
        self._kpi_overstock = KpiCard("Overstocked", filter_key="overstock")
        self._kpi_understock = KpiCard("Understocked", filter_key="understock")
        self._kpi_slow = KpiCard("Slow Moving", filter_key="slow")
        self._kpi_sales = KpiCard("Sales (90d)", filter_key="sales")
        self._kpi_overstock.set_accent("warning")
        self._kpi_understock.set_accent("danger")
        self._kpi_slow.set_accent("amber")
        self._kpi_sales.set_accent("success")
        for card in (
            self._kpi_overstock,
            self._kpi_understock,
            self._kpi_slow,
            self._kpi_sales,
        ):
            card.clicked.connect(lambda checked=False, k=card.filter_key: self._on_kpi_filter(k))

        self._kpis = [
            self._kpi_skus,
            self._kpi_value,
            self._kpi_overstock,
            self._kpi_understock,
            self._kpi_slow,
            self._kpi_sales,
        ]
        for i, kpi in enumerate(self._kpis):
            overview_layout.addWidget(kpi, 1, i)

        self._dept_chart = ChartTile("Stock Value by Dept")
        self._dept_chart.point_clicked.connect(self._on_dept_chart_click)
        overview_layout.addWidget(self._dept_chart, 2, 0, 1, 6)

        self._sellers_tile = DashboardTile("Top Sellers (90d)")
        self._sellers_table = DataTable()
        self._sellers_table.setSortingEnabled(False)
        self._sellers_table.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._sellers_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._sellers_table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._sellers_table.doubleClicked.connect(self._on_sellers_double_click)
        self._configure_sellers_table_columns()
        self._sellers_tile.set_content(self._sellers_table)
        self._health_chart = ChartTile("Stock Health")
        overview_layout.addWidget(self._sellers_tile, 3, 0, 1, 3)
        overview_layout.addWidget(self._health_chart, 3, 3, 1, 3)

        overview_layout.setRowMinimumHeight(2, 220)
        overview_layout.setRowMinimumHeight(3, 240)
        self._dept_chart.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self._dept_chart.setMinimumHeight(240)
        self._dept_chart.setMaximumHeight(280)
        for tile in (self._sellers_tile, self._health_chart):
            tile.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            tile.setMinimumHeight(280)
            tile.setMaximumHeight(300)

        alerts_tab = QWidget()
        alerts_tab.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        alerts_layout = QVBoxLayout(alerts_tab)
        alerts_layout.setContentsMargins(4, 8, 4, 4)
        alerts_layout.setSpacing(12)

        alerts_filters = QHBoxLayout()
        self._alert_type_combo = QComboBox()
        self._alert_type_combo.addItems(["Understock", "Overstock"])
        self._alert_type_combo.currentTextChanged.connect(self._on_alert_type_changed)
        self._alerts_dept_combo = QComboBox()
        self._alerts_dept_combo.setMinimumWidth(160)
        self._alerts_dept_combo.currentIndexChanged.connect(self._on_alerts_dept_changed)
        alerts_filters.addWidget(QLabel("Alert type:"))
        alerts_filters.addWidget(self._alert_type_combo)
        alerts_filters.addSpacing(12)
        alerts_filters.addWidget(QLabel("Department:"))
        alerts_filters.addWidget(self._alerts_dept_combo)
        alerts_filters.addStretch()
        alerts_layout.addLayout(alerts_filters)

        self._alerts_tile = DashboardTile("Stock Alerts")
        export_xlsx = QPushButton("Export Excel…")
        export_pdf = QPushButton("Export PDF…")
        export_xlsx.clicked.connect(lambda: self._export_table("alerts", "excel"))
        export_pdf.clicked.connect(lambda: self._export_table("alerts", "pdf"))
        self._alerts_tile.add_action(export_xlsx)
        self._alerts_tile.add_action(export_pdf)
        self._alerts_table = DataTable()
        self._alerts_table.enable_viewport_scrolling()
        self._alerts_table.doubleClicked.connect(
            lambda index: self._on_table_double_click(self._alerts_table, index)
        )
        self._alerts_tile.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._alerts_tile.set_content(self._alerts_table)
        alerts_layout.addWidget(self._alerts_tile, stretch=1)

        sales_tab = QWidget()
        sales_tab.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        sales_layout = QVBoxLayout(sales_tab)
        sales_layout.setContentsMargins(4, 8, 4, 4)
        sales_layout.setSpacing(12)

        sales_filters = QHBoxLayout()
        self._sales_dept_combo = QComboBox()
        self._sales_dept_combo.setMinimumWidth(160)
        self._sales_dept_combo.currentIndexChanged.connect(self._on_sales_dept_changed)
        sales_filters.addWidget(QLabel("Department:"))
        sales_filters.addWidget(self._sales_dept_combo)
        sales_filters.addStretch()
        sales_layout.addLayout(sales_filters)

        self._sales_tile = DashboardTile("Sales (90d)")
        sales_export_xlsx = QPushButton("Export Excel…")
        sales_export_pdf = QPushButton("Export PDF…")
        sales_export_xlsx.clicked.connect(lambda: self._export_table("sales", "excel"))
        sales_export_pdf.clicked.connect(lambda: self._export_table("sales", "pdf"))
        self._sales_tile.add_action(sales_export_xlsx)
        self._sales_tile.add_action(sales_export_pdf)
        self._sales_table = DataTable()
        self._sales_table.enable_viewport_scrolling()
        self._sales_table.doubleClicked.connect(
            lambda index: self._on_table_double_click(self._sales_table, index)
        )
        self._sales_tile.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._sales_tile.set_content(self._sales_table)
        sales_layout.addWidget(self._sales_tile, stretch=1)

        slow_tab = QWidget()
        slow_tab.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        slow_layout = QVBoxLayout(slow_tab)
        slow_layout.setContentsMargins(4, 8, 4, 4)
        slow_layout.setSpacing(12)

        slow_filters = QHBoxLayout()
        self._slow_dept_combo = QComboBox()
        self._slow_dept_combo.setMinimumWidth(160)
        self._slow_dept_combo.currentIndexChanged.connect(self._on_slow_dept_changed)
        slow_filters.addWidget(QLabel("Department:"))
        slow_filters.addWidget(self._slow_dept_combo)
        slow_filters.addStretch()
        slow_layout.addLayout(slow_filters)

        self._slow_tile = DashboardTile("Slow Moving Items")
        slow_export_xlsx = QPushButton("Export Excel…")
        slow_export_pdf = QPushButton("Export PDF…")
        slow_export_xlsx.clicked.connect(lambda: self._export_table("slow", "excel"))
        slow_export_pdf.clicked.connect(lambda: self._export_table("slow", "pdf"))
        self._slow_tile.add_action(slow_export_xlsx)
        self._slow_tile.add_action(slow_export_pdf)
        self._slow_table = DataTable()
        self._slow_table.enable_viewport_scrolling()
        self._slow_table.doubleClicked.connect(
            lambda index: self._on_table_double_click(self._slow_table, index)
        )
        self._slow_tile.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._slow_tile.set_content(self._slow_table)
        slow_layout.addWidget(self._slow_tile, stretch=1)

        import_tab = QWidget()
        import_tab.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        import_layout = QVBoxLayout(import_tab)
        import_layout.setContentsMargins(4, 8, 4, 4)
        import_layout.setSpacing(16)

        movement_header = QHBoxLayout()
        movement_title = QLabel("Movement Report")
        movement_title.setObjectName("pageTitle")
        self._period_combo = QComboBox()
        self._period_combo.setMinimumWidth(220)
        self._period_combo.currentIndexChanged.connect(self._on_period_changed)
        self._import_btn = QPushButton("Import Movement Report")
        self._import_btn.clicked.connect(self.import_period_requested.emit)
        movement_header.addWidget(movement_title)
        movement_header.addStretch()
        movement_header.addWidget(QLabel("Period:"))
        movement_header.addWidget(self._period_combo)
        movement_header.addWidget(self._import_btn)
        import_layout.addLayout(movement_header)

        self._enrichment_banner = QFrame()
        self._enrichment_banner.setObjectName("banner")
        banner_layout = QHBoxLayout(self._enrichment_banner)
        banner_text = QLabel(
            "Step 2: Import Turn + Turnunder reports to enrich item data and unlock full analytics."
        )
        banner_text.setWordWrap(True)
        enrich_btn = QPushButton("Import Turn Reports")
        enrich_btn.clicked.connect(self.import_enrichment_requested.emit)
        banner_layout.addWidget(banner_text, stretch=1)
        banner_layout.addWidget(enrich_btn)
        import_layout.addWidget(self._enrichment_banner)

        self._partial_note = QLabel(
            "Import Turn + Turnunder reports (Step 2) to unlock charts and full analytics."
        )
        self._partial_note.setObjectName("placeholder")
        self._partial_note.setWordWrap(True)
        import_layout.addWidget(self._partial_note)

        self._stock_take = StockTakePage(embedded=True)
        self._stock_take.data_changed.connect(self.data_changed.emit)
        import_layout.addWidget(self._stock_take, stretch=1)

        self._tabs.addTab(overview_tab, "Overview")
        self._tabs.addTab(alerts_tab, "Stock Alerts")
        self._tabs.addTab(sales_tab, "Sales")
        self._tabs.addTab(slow_tab, "Slow Moving")
        self._tabs.addTab(import_tab, "Import")
        self._tabs.currentChanged.connect(self._on_home_tab_changed)
        self._dash_layout = dash
        dash.addWidget(self._tabs, 0, 0, 1, 6)
        dash.setRowStretch(0, 1)

        self._dashboard.hide()
        self._layout.addWidget(self._dashboard, 0, 0, 1, 6)

    def _on_home_tab_changed(self, index: int) -> None:
        on_import = index == _IMPORT_TAB
        on_data_tab = index in _SCROLLABLE_TABS
        if on_import or on_data_tab:
            self._tabs.setMinimumHeight(0)
            self._tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            self._dash_layout.setRowStretch(0, 1)
        else:
            self._tabs.setMinimumHeight(0)
            self._tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            self._dash_layout.setRowStretch(0, 0)

        self._scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            if on_data_tab
            else Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )

        if on_import:
            self._stock_take.refresh()

        canvas = self._scroll.widget()
        if canvas is not None:
            canvas.updateGeometry()

    def _on_period_changed(self, index: int) -> None:
        if 0 <= index < len(self._batch_ids):
            self._selected_batch_id = self._batch_ids[index]
            invalidate_summaries()
            self._load_period_data()

    def _sync_selected_batch(self, batch_id: int | None) -> None:
        if batch_id is None or batch_id == self._selected_batch_id:
            return
        if batch_id not in self._batch_ids:
            return
        self._selected_batch_id = batch_id
        period_index = self._batch_ids.index(batch_id)
        self._period_combo.blockSignals(True)
        self._period_combo.setCurrentIndex(period_index)
        self._period_combo.blockSignals(False)
        invalidate_summaries()
        self._load_period_data()

    def show_stock_alerts(
        self, alert_type: str = "understock", dept: str | None = None, batch_id: int | None = None
    ) -> None:
        self._sync_selected_batch(batch_id)
        self._show_stock_alerts_tab(alert_type, dept)

    def show_slow_moving(self, dept: str | None = None, batch_id: int | None = None) -> None:
        self._sync_selected_batch(batch_id)
        self._show_slow_moving_tab(dept)

    def _show_stock_alerts_tab(
        self, alert_type: str = "understock", dept: str | None = None
    ) -> None:
        self._alert_type = alert_type
        self._alert_type_combo.blockSignals(True)
        self._alert_type_combo.setCurrentText("Understock" if alert_type == "understock" else "Overstock")
        self._alert_type_combo.blockSignals(False)
        if dept is not None:
            self._alerts_dept = dept
            self._set_dept_combo(self._alerts_dept_combo, self._departments, dept)
        self._apply_stock_alerts_filter()
        self._tabs.setCurrentIndex(_STOCK_ALERTS_TAB)

    def _show_sales_tab(self, dept: str | None = None) -> None:
        if dept is not None:
            self._sales_dept = dept
            self._set_dept_combo(self._sales_dept_combo, self._departments, dept)
        self._apply_sales_filter()
        self._tabs.setCurrentIndex(_SALES_TAB)

    def _show_slow_moving_tab(self, dept: str | None = None) -> None:
        if dept is not None:
            self._slow_dept = dept
            self._set_dept_combo(self._slow_dept_combo, self._departments, dept)
        self._apply_slow_filter()
        self._tabs.setCurrentIndex(_SLOW_TAB)

    def _on_kpi_filter(self, key: str | None) -> None:
        if key == "understock":
            self.show_stock_alerts("understock")
        elif key == "overstock":
            self.show_stock_alerts("overstock")
        elif key == "slow":
            self.show_slow_moving()
        elif key == "sales":
            self._show_sales_tab()

    def _on_dept_chart_click(self, dept: str) -> None:
        self.inventory_dept_requested.emit(dept)

    def _on_sellers_double_click(self, index) -> None:
        model = self._sellers_table.model()
        sku = model.data(model.index(index.row(), 1))
        if sku:
            self.item_detail_requested.emit(str(sku))

    def _on_table_double_click(self, table: DataTable, index) -> None:
        model = table.model()
        sku = model.data(model.index(index.row(), 0))
        if sku:
            self.item_detail_requested.emit(str(sku))

    def _on_alert_type_changed(self, text: str) -> None:
        self._alert_type = "understock" if text == "Understock" else "overstock"
        self._apply_stock_alerts_filter()

    def _on_alerts_dept_changed(self, _index: int) -> None:
        self._alerts_dept = self._alerts_dept_combo.currentData()
        self._apply_stock_alerts_filter()

    def _on_sales_dept_changed(self, _index: int) -> None:
        self._sales_dept = self._sales_dept_combo.currentData()
        self._apply_sales_filter()

    def _on_slow_dept_changed(self, _index: int) -> None:
        self._slow_dept = self._slow_dept_combo.currentData()
        self._apply_slow_filter()

    def _set_dept_combo(
        self, combo: QComboBox, departments: list[str], selected: str | None
    ) -> None:
        combo.blockSignals(True)
        if selected is None:
            combo.setCurrentIndex(0)
        else:
            index = combo.findData(selected)
            if index >= 0:
                combo.setCurrentIndex(index)
        combo.blockSignals(False)

    def _populate_dept_combos(self, departments: list[str]) -> None:
        for combo, current in (
            (self._alerts_dept_combo, self._alerts_dept),
            (self._sales_dept_combo, self._sales_dept),
            (self._slow_dept_combo, self._slow_dept),
        ):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("All departments", None)
            for dept in sorted(departments):
                combo.addItem(dept, dept)
            self._set_dept_combo(combo, departments, current)
            combo.blockSignals(False)

    def _configure_alerts_table_columns(self) -> None:
        header = self._alerts_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.resizeSection(0, 100)
        header.resizeSection(2, 72)
        header.resizeSection(3, 110)
        header.resizeSection(4, 100)

    def _configure_sales_table_columns(self) -> None:
        header = self._sales_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.resizeSection(0, 100)
        header.resizeSection(2, 80)
        header.resizeSection(3, 80)
        header.resizeSection(4, 100)

    def _configure_slow_table_columns(self) -> None:
        header = self._slow_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.resizeSection(0, 100)
        header.resizeSection(2, 72)
        header.resizeSection(3, 80)
        header.resizeSection(4, 80)

    def _configure_sellers_table_columns(self) -> None:
        header = self._sellers_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        header.resizeSection(0, 37)
        header.resizeSection(1, 88)
        header.resizeSection(3, 64)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._sellers_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._sellers_table.setTextElideMode(Qt.TextElideMode.ElideRight)

    def _populate_sellers_table(self, top_sellers: list[dict]) -> None:
        self._sellers_table.set_headers(["#", "Code", "Product", "Qty 90d"])
        rows = [
            [str(i), s["code"], s["name"], f"{s['qty_90']:g}"]
            for i, s in enumerate(top_sellers, start=1)
        ]
        self._sellers_table.set_rows(rows)
        self._configure_sellers_table_columns()
        self._sellers_table.scrollToTop()

    def _apply_stock_alerts_filter(self) -> None:
        cfg = _STOCK_ALERT_MODES[self._alert_type]
        rows = self._understock_rows if self._alert_type == "understock" else self._overstock_rows
        filtered = filter_stock_rows(rows, dept=self._alerts_dept)
        self._alerts_display_rows = self._rows_to_display(filtered, self._alert_type)
        self._alerts_tile.set_title(cfg["title"])
        self._alerts_table.set_headers(cfg["headers"])
        self._alerts_table.set_rows(self._alerts_display_rows)
        self._configure_alerts_table_columns()
        sub = f"Showing {len(filtered):,} of {len(rows):,}"
        if self._alerts_dept:
            sub += f" · Dept: {self._alerts_dept}"
        self._alerts_tile.set_subtitle(sub)

    def _apply_sales_filter(self) -> None:
        filtered = filter_stock_rows(self._sales_rows, dept=self._sales_dept)
        self._sales_display_rows = [
            [
                r["code"],
                r["name"],
                r.get("dept", "—"),
                f"{r['qty_90']:g}",
                f"R {r['sales_value']:,.2f}",
            ]
            for r in filtered
        ]
        self._sales_table.set_headers(_SALES_MODE["headers"])
        self._sales_table.set_rows(self._sales_display_rows)
        self._configure_sales_table_columns()
        sub = f"Showing {len(filtered):,} of {len(self._sales_rows):,}"
        if self._sales_dept:
            sub += f" · Dept: {self._sales_dept}"
        self._sales_tile.set_subtitle(sub)

    def _apply_slow_filter(self) -> None:
        filtered = filter_stock_rows(self._slow_rows, dept=self._slow_dept)
        self._slow_display_rows = [
            [
                r["code"],
                r["name"],
                f"{r['on_hand']:g}",
                f"{r['qty_sold_90']:g}",
                r.get("dept", "—"),
            ]
            for r in filtered
        ]
        self._slow_table.set_headers(_SLOW_MODE["headers"])
        self._slow_table.set_rows(self._slow_display_rows)
        self._configure_slow_table_columns()
        sub = f"Showing {len(filtered):,} of {len(self._slow_rows):,}"
        if self._slow_dept:
            sub += f" · Dept: {self._slow_dept}"
        self._slow_tile.set_subtitle(sub)

    def _rows_to_display(self, rows: list[dict], mode: str) -> list[list]:
        if mode == "overstock":
            return [
                [
                    r["code"],
                    r["name"],
                    f"{r['on_hand']:g}",
                    f"{r['over_qty']:g}",
                    f"R {r['over_value']:,.2f}",
                ]
                for r in rows
            ]
        return [
            [
                r["code"],
                r["name"],
                f"{r['on_hand']:g}",
                f"{r['under_qty']:.2f}",
                f"R {r['under_value']:,.2f}",
            ]
            for r in rows
        ]

    def _export_table(self, table_key: str, fmt: str) -> None:
        if table_key == "alerts":
            cfg = _STOCK_ALERT_MODES[self._alert_type]
            headers = cfg["headers"]
            rows = self._alerts_display_rows
        elif table_key == "sales":
            cfg = _SALES_MODE
            headers = cfg["headers"]
            rows = self._sales_display_rows
        else:
            cfg = _SLOW_MODE
            headers = cfg["headers"]
            rows = self._slow_display_rows

        period = self._period
        period_label = ""
        if period.get("period_start") and period.get("period_end"):
            period_label = f"{period['period_start']} – {period['period_end']}"
        title = f"{cfg['title']} — {period_label}"
        if fmt == "excel":
            prompt_export_excel(
                self, title, headers, rows, f"{cfg['filename']}.xlsx"
            )
        else:
            prompt_export_pdf(
                self, title, headers, rows, f"{cfg['filename']}.pdf"
            )

    def _populate_period_combo(self, batches: list[dict]) -> None:
        previous = self._selected_batch_id
        self._period_combo.blockSignals(True)
        self._period_combo.clear()
        self._batch_ids = []
        selected_index = 0
        for index, batch in enumerate(batches):
            self._period_combo.addItem(batch["label"])
            self._batch_ids.append(batch["id"])
            if previous == batch["id"]:
                selected_index = index
        if batches:
            self._period_combo.setCurrentIndex(selected_index)
            self._selected_batch_id = self._batch_ids[selected_index]
        self._period_combo.blockSignals(False)

    def _load_period_data(self) -> None:
        with get_session() as session:
            config = get_dashboard_config(session)
            summaries = load_summaries(session)
            summary = summaries.baseline
            period = get_period_summary_cached(session, self._selected_batch_id)
            comparison = build_period_comparison(session, self._selected_batch_id)

        self._period = period or {}
        self._kpi_skus.set_value(f"{summary['item_count']:,}")
        self._kpi_value.set_value(f"R {summary['total_value']:,.2f}")

        show_kpis = config.get("show_kpis", True)
        show_charts = config.get("show_charts", True)
        show_alerts = config.get("show_alerts", True)
        show_sales = config.get("show_sales_tab", True)
        show_slow = config.get("show_slow_moving_tab", True)
        show_health = config.get("show_stock_health", True)

        for kpi in self._kpis:
            kpi.setVisible(show_kpis)
        self._dept_chart.setVisible(show_charts)
        self._sellers_tile.setVisible(show_charts)
        self._health_chart.setVisible(show_charts and show_health)
        self._tabs.setTabVisible(_STOCK_ALERTS_TAB, show_alerts)
        self._tabs.setTabVisible(_SALES_TAB, show_sales)
        self._tabs.setTabVisible(_SLOW_TAB, show_slow)
        overview_visible = show_kpis or show_charts
        self._tabs.setTabVisible(_OVERVIEW_TAB, overview_visible)
        self._tabs.setTabVisible(_IMPORT_TAB, True)

        if period:
            self._kpi_overstock.set_value(f"{period.get('overstock_items', 0):,}")
            self._kpi_understock.set_value(f"{period.get('understock_items', 0):,}")
            self._kpi_slow.set_value(f"{period.get('slow_moving', 0):,}")
            self._kpi_sales.set_value(f"{period.get('total_sales_90', 0):,.0f}")

            for key, card in (
                ("overstock_items", self._kpi_overstock),
                ("understock_items", self._kpi_understock),
                ("slow_moving", self._kpi_slow),
                ("total_sales_90", self._kpi_sales),
            ):
                text, direction = _format_delta(comparison.get(f"{key}_delta_pct"))
                card.set_delta(text, direction)

            if show_charts:
                dept_view, dept_labels = build_dept_values_chart(period.get("dept_values", {}))
                self._dept_chart.set_chart_view(dept_view, dept_labels)
                self._populate_sellers_table(period.get("top_sellers", []))

                if show_health:
                    health = period.get("stock_health") or {}
                    self._health_chart.set_chart_view(
                        build_stock_health_chart(health, embedded=True), list(health.keys())
                    )

            self._departments = list(period.get("dept_values", {}).keys())
            self._populate_dept_combos(self._departments)

            self._understock_rows = period.get("reorder_alerts", [])
            self._overstock_rows = period.get("overstock_alerts", [])
            self._slow_rows = period.get("slow_moving_items", [])
            self._sales_rows = period.get("sales_items", [])

            if period.get("period_start") and period.get("period_end"):
                label = f"{period['period_start']} – {period['period_end']}"
            else:
                label = "Enriched — ready for period imports"
            self._overview_header.set_subtitle(label)

            if show_alerts:
                self._apply_stock_alerts_filter()
            if show_sales:
                self._apply_sales_filter()
            if show_slow:
                self._apply_slow_filter()
        else:
            for card in (self._kpi_overstock, self._kpi_understock, self._kpi_slow, self._kpi_sales):
                card.set_value("—")
                card.set_delta("—")
            self._departments = []
            self._understock_rows = []
            self._overstock_rows = []
            self._slow_rows = []
            self._sales_rows = []
            self._populate_dept_combos([])
            self._populate_sellers_table([])
            self._overview_header.set_subtitle("Awaiting enrichment (Step 2)")

    def refresh(self) -> None:
        invalidate_summaries()
        with get_session() as session:
            has_initial = has_initial_baseline(session)
            enriched = has_enrichment(session)
            batches = list_period_batches(session) if enriched else []

        if not has_initial:
            self._empty.show()
            self._dashboard.hide()
            self._stock_take.refresh()
            return

        self._empty.hide()
        self._dashboard.show()

        self._enrichment_banner.setVisible(not enriched)
        self._import_btn.setEnabled(enriched)
        self._partial_note.setVisible(not enriched)
        self._period_combo.setVisible(enriched)
        self._tabs.setVisible(True)

        if enriched:
            self._populate_period_combo(batches)
            self._load_period_data()
        else:
            with get_session() as session:
                summary = load_summaries(session).baseline
            self._kpi_skus.set_value(f"{summary['item_count']:,}")
            self._kpi_value.set_value(f"R {summary['total_value']:,.2f}")
            for card in (self._kpi_overstock, self._kpi_understock, self._kpi_slow, self._kpi_sales):
                card.set_value("—")
            self._dept_chart.setVisible(False)
            self._sellers_tile.setVisible(False)
            self._health_chart.setVisible(False)
            self._tabs.setTabVisible(_STOCK_ALERTS_TAB, False)
            self._tabs.setTabVisible(_SALES_TAB, False)
            self._tabs.setTabVisible(_SLOW_TAB, False)
            self._overview_header.set_subtitle("Awaiting enrichment (Step 2)")

        self._stock_take.refresh()

    def capture_nav_state(self) -> HomeNavState:
        return HomeNavState(
            tab=self._tabs.currentIndex(),
            alert_type=self._alert_type,
            alerts_dept=self._alerts_dept,
            sales_dept=self._sales_dept,
            slow_dept=self._slow_dept,
            selected_batch_id=self._selected_batch_id,
        )

    def restore_nav_state(self, state: object | None) -> None:
        if not isinstance(state, HomeNavState):
            return

        if state.selected_batch_id != self._selected_batch_id:
            self._selected_batch_id = state.selected_batch_id
            if state.selected_batch_id in self._batch_ids:
                period_index = self._batch_ids.index(state.selected_batch_id)
                self._period_combo.blockSignals(True)
                self._period_combo.setCurrentIndex(period_index)
                self._period_combo.blockSignals(False)
                invalidate_summaries()
                self._load_period_data()

        self._alert_type = state.alert_type
        self._alerts_dept = state.alerts_dept
        self._sales_dept = state.sales_dept
        self._slow_dept = state.slow_dept

        self._alert_type_combo.blockSignals(True)
        self._alert_type_combo.setCurrentText(
            "Understock" if state.alert_type == "understock" else "Overstock"
        )
        self._alert_type_combo.blockSignals(False)
        self._set_dept_combo(self._alerts_dept_combo, self._departments, state.alerts_dept)
        self._set_dept_combo(self._sales_dept_combo, self._departments, state.sales_dept)
        self._set_dept_combo(self._slow_dept_combo, self._departments, state.slow_dept)

        if state.tab == _STOCK_ALERTS_TAB:
            self._apply_stock_alerts_filter()
        elif state.tab == _SALES_TAB:
            self._apply_sales_filter()
        elif state.tab == _SLOW_TAB:
            self._apply_slow_filter()

        self._tabs.setCurrentIndex(state.tab)
        self._on_home_tab_changed(state.tab)
