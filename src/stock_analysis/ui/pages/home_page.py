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
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from stock_analysis.analytics.cache import get_period_summary_cached, load_summaries
from stock_analysis.analytics.dashboard import (
    _format_delta,
    build_inventory_list_summary,
    build_period_comparison,
    filter_stock_rows,
)
from stock_analysis.analytics.metrics import pct_in_range
from stock_analysis.analytics.lookback import (
    lookback_label,
    over_qty_label,
    qty_column_label,
    sales_period_label,
    sales_value_label,
    under_qty_label,
    units_sold_label,
)
from stock_analysis.analytics.dashboard_config import get_dashboard_config
from stock_analysis.analytics.department_names import display_dept, load_nickname_map
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
from stock_analysis.ui.widgets.holding_weeks import create_holding_weeks, sync_holding_weeks
from stock_analysis.ui.widgets.sales_period_weeks import (
    create_sales_period_weeks,
    sync_sales_period_weeks,
)

_STOCK_ALERT_MODES = {
    "understock": {
        "title": "Understock Alerts",
        "headers": ["SKU", "Name", "On Hand", "Under Qty", "Under Value"],
        "filename": "understock_alerts",
    },
    "overstock": {
        "title": "Overstock Items",
        "headers": ["SKU", "Name", "On Hand", "Over Qty", "Over Value"],
        "filename": "overstock_items",
    },
    "gross_margin": {
        "title": "Gross Profit %",
        "headers": ["SKU", "Name", "Qty Sold", "GP %", "Gross Profit"],
        "filename": "gross_profit_alerts",
    },
    "markup": {
        "title": "Markup %",
        "headers": ["SKU", "Name", "On Hand", "Unit Cost", "Markup %"],
        "filename": "markup_alerts",
    },
}

_ALERT_TYPE_LABELS = {
    "understock": "Understock",
    "overstock": "Overstock",
    "gross_margin": "Gross Profit %",
    "markup": "Markup %",
}

_ALERT_LABEL_TO_TYPE = {label: key for key, label in _ALERT_TYPE_LABELS.items()}

_RANGE_ALERT_TYPES = frozenset({"gross_margin", "markup"})

_SLOW_MODE = {
    "title": "Slow Moving Items",
    "headers": ["SKU", "Name", "On Hand", "Sales", "Over Qty", "Weeks Cover", "Excess Value", "Dept"],
    "filename": "slow_moving",
}

_DEAD_MODE = {
    "title": "Dead Stock",
    "headers": ["SKU", "Name", "On Hand", "Sales", "Unit Cost", "Stock Value", "Dept"],
    "filename": "dead_stock",
}

_SALES_MODE = {
    "title": "Sales",
    "headers": ["SKU", "Name", "Dept", "Qty", "Sales Value", "Gross Profit"],
    "filename": "sales",
}

_OVERVIEW_TAB = 0
_STOCK_ALERTS_TAB = 1
_SALES_TAB = 2
_SLOW_TAB = 3
_DEAD_TAB = 4
_IMPORT_TAB = 5

_SCROLLABLE_TABS = {_STOCK_ALERTS_TAB, _SALES_TAB, _SLOW_TAB, _DEAD_TAB}


@dataclass
class HomeNavState:
    tab: int
    alert_type: str
    dept: str | None
    range_mode: str = "inside"
    range_min: int = 0
    range_max: int = 100


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
        self._period: dict = {}
        self._departments: list[str] = []
        self._nickname_map: dict[str, str] = {}
        self._alert_type = "understock"
        self._dept: str | None = None
        self._enriched = False
        self._understock_rows: list[dict] = []
        self._overstock_rows: list[dict] = []
        self._margin_rows: list[dict] = []
        self._markup_rows: list[dict] = []
        self._slow_rows: list[dict] = []
        self._dead_rows: list[dict] = []
        self._sales_rows: list[dict] = []
        self._alerts_display_rows: list[list] = []
        self._sales_display_rows: list[list] = []
        self._slow_display_rows: list[list] = []
        self._dead_display_rows: list[list] = []
        self._lookback_weeks = 1
        self._comparison: dict = {}
        self._show_overview_kpis = True
        self._show_overview_charts = True
        self._show_overview_health = True

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

        self._dashboard_header = ReportHeader("Home", "")
        self._lookback_label = QLabel("Sales period:")
        self._lookback_spin = create_sales_period_weeks(
            self, on_changed=self._on_lookback_changed
        )
        self._dashboard_header.add_control(self._lookback_label)
        self._dashboard_header.add_control(self._lookback_spin)
        self._holding_label = QLabel("Hold stock for:")
        self._holding_spin = create_holding_weeks(
            self, on_changed=self._on_holding_changed
        )
        self._dashboard_header.add_control(self._holding_label)
        self._dashboard_header.add_control(self._holding_spin)
        self._dept_label = QLabel("Department:")
        self._dept_combo = QComboBox()
        self._dept_combo.setMinimumWidth(160)
        self._dept_combo.currentIndexChanged.connect(self._on_dept_changed)
        self._dashboard_header.add_control(self._dept_label)
        self._dashboard_header.add_control(self._dept_combo)

        overview_tab = QWidget()
        overview_tab.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        overview_layout = QGridLayout(overview_tab)
        overview_layout.setContentsMargins(4, 8, 4, 4)
        overview_layout.setSpacing(12)

        self._kpi_value = KpiCard("Stock Value")
        self._kpi_overstock = KpiCard("Overstocked", filter_key="overstock")
        self._kpi_understock = KpiCard("Understocked", filter_key="understock")
        self._kpi_slow = KpiCard("Slow Moving", filter_key="slow")
        self._kpi_sales = KpiCard("Sales Value", filter_key="sales")
        self._kpi_dead = KpiCard("Dead Stock", filter_key="dead")
        self._kpi_value.set_accent("stock-value")
        self._kpi_overstock.set_accent("stock-over")
        self._kpi_understock.set_accent("stock-under")
        self._kpi_slow.set_accent("stock-slow")
        self._kpi_sales.set_accent("success")
        self._kpi_dead.set_accent("stock-dead")
        for card in (
            self._kpi_overstock,
            self._kpi_understock,
            self._kpi_slow,
            self._kpi_sales,
            self._kpi_dead,
        ):
            card.clicked.connect(lambda checked=False, k=card.filter_key: self._on_kpi_filter(k))

        self._kpis = [
            self._kpi_value,
            self._kpi_overstock,
            self._kpi_understock,
            self._kpi_slow,
            self._kpi_sales,
            self._kpi_dead,
        ]
        for i, kpi in enumerate(self._kpis):
            overview_layout.addWidget(kpi, 0, i)

        self._dept_chart = ChartTile("Stock Value by Dept")
        self._dept_chart.point_clicked.connect(self._on_dept_chart_click)
        overview_layout.addWidget(self._dept_chart, 1, 0, 1, 6)

        self._sellers_tile = DashboardTile("Top Sellers")
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
        overview_layout.addWidget(self._sellers_tile, 2, 0, 1, 3)
        overview_layout.addWidget(self._health_chart, 2, 3, 1, 3)

        overview_layout.setRowMinimumHeight(1, 220)
        overview_layout.setRowMinimumHeight(2, 240)
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
        self._alert_type_combo.addItems(list(_ALERT_TYPE_LABELS.values()))
        self._alert_type_combo.currentTextChanged.connect(self._on_alert_type_changed)
        alerts_filters.addWidget(QLabel("Alert type:"))
        alerts_filters.addWidget(self._alert_type_combo)

        self._alert_range_mode_label = QLabel("Range:")
        self._alert_range_mode = QComboBox()
        self._alert_range_mode.addItems(["Inside", "Outside"])
        self._alert_range_mode.currentTextChanged.connect(self._on_alert_range_changed)
        alerts_filters.addWidget(self._alert_range_mode_label)
        alerts_filters.addWidget(self._alert_range_mode)

        self._alert_range_min_label = QLabel("Min %:")
        self._alert_range_min = QSpinBox()
        self._alert_range_min.setRange(0, 100)
        self._alert_range_min.setValue(0)
        self._alert_range_min.valueChanged.connect(self._on_alert_range_changed)
        alerts_filters.addWidget(self._alert_range_min_label)
        alerts_filters.addWidget(self._alert_range_min)

        self._alert_range_max_label = QLabel("Max %:")
        self._alert_range_max = QSpinBox()
        self._alert_range_max.setRange(0, 100)
        self._alert_range_max.setValue(100)
        self._alert_range_max.valueChanged.connect(self._on_alert_range_changed)
        alerts_filters.addWidget(self._alert_range_max_label)
        alerts_filters.addWidget(self._alert_range_max)

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
        self._alerts_table.enable_compact_rows()
        self._alerts_table.doubleClicked.connect(
            lambda index: self._on_table_double_click(self._alerts_table, index)
        )
        alerts_panel, self._alerts_footer = self._build_table_panel(self._alerts_table)
        self._alerts_tile.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._alerts_tile.set_content(alerts_panel)
        alerts_layout.addWidget(self._alerts_tile, stretch=1)
        self._update_alert_range_filter_visibility()

        sales_tab = QWidget()
        sales_tab.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        sales_layout = QVBoxLayout(sales_tab)
        sales_layout.setContentsMargins(4, 8, 4, 4)
        sales_layout.setSpacing(12)

        self._sales_tile = DashboardTile("Units Sold")
        sales_export_xlsx = QPushButton("Export Excel…")
        sales_export_pdf = QPushButton("Export PDF…")
        sales_export_xlsx.clicked.connect(lambda: self._export_table("sales", "excel"))
        sales_export_pdf.clicked.connect(lambda: self._export_table("sales", "pdf"))
        self._sales_tile.add_action(sales_export_xlsx)
        self._sales_tile.add_action(sales_export_pdf)
        self._sales_table = DataTable()
        self._sales_table.enable_viewport_scrolling()
        self._sales_table.enable_compact_rows()
        self._sales_table.doubleClicked.connect(
            lambda index: self._on_table_double_click(self._sales_table, index)
        )
        sales_panel, self._sales_footer = self._build_table_panel(self._sales_table)
        self._sales_tile.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._sales_tile.set_content(sales_panel)
        sales_layout.addWidget(self._sales_tile, stretch=1)

        slow_tab = QWidget()
        slow_tab.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        slow_layout = QVBoxLayout(slow_tab)
        slow_layout.setContentsMargins(4, 8, 4, 4)
        slow_layout.setSpacing(12)

        self._slow_tile = DashboardTile("Slow Moving Items")
        slow_export_xlsx = QPushButton("Export Excel…")
        slow_export_pdf = QPushButton("Export PDF…")
        slow_export_xlsx.clicked.connect(lambda: self._export_table("slow", "excel"))
        slow_export_pdf.clicked.connect(lambda: self._export_table("slow", "pdf"))
        self._slow_tile.add_action(slow_export_xlsx)
        self._slow_tile.add_action(slow_export_pdf)
        self._slow_table = DataTable()
        self._slow_table.enable_viewport_scrolling()
        self._slow_table.enable_compact_rows()
        self._slow_table.doubleClicked.connect(
            lambda index: self._on_table_double_click(self._slow_table, index)
        )
        slow_panel, self._slow_footer = self._build_table_panel(self._slow_table)
        self._slow_tile.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._slow_tile.set_content(slow_panel)
        slow_layout.addWidget(self._slow_tile, stretch=1)

        dead_tab = QWidget()
        dead_tab.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        dead_layout = QVBoxLayout(dead_tab)
        dead_layout.setContentsMargins(4, 8, 4, 4)
        dead_layout.setSpacing(12)

        self._dead_tile = DashboardTile("Dead Stock")
        dead_export_xlsx = QPushButton("Export Excel…")
        dead_export_pdf = QPushButton("Export PDF…")
        dead_export_xlsx.clicked.connect(lambda: self._export_table("dead", "excel"))
        dead_export_pdf.clicked.connect(lambda: self._export_table("dead", "pdf"))
        self._dead_tile.add_action(dead_export_xlsx)
        self._dead_tile.add_action(dead_export_pdf)
        self._dead_table = DataTable()
        self._dead_table.enable_viewport_scrolling()
        self._dead_table.enable_compact_rows()
        self._dead_table.doubleClicked.connect(
            lambda index: self._on_table_double_click(self._dead_table, index)
        )
        dead_panel, self._dead_footer = self._build_table_panel(self._dead_table)
        self._dead_tile.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._dead_tile.set_content(dead_panel)
        dead_layout.addWidget(self._dead_tile, stretch=1)

        import_tab = QWidget()
        import_tab.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        import_layout = QVBoxLayout(import_tab)
        import_layout.setContentsMargins(4, 8, 4, 4)
        import_layout.setSpacing(16)

        movement_header = QHBoxLayout()
        movement_title = QLabel("Movement Report")
        movement_title.setObjectName("pageTitle")
        self._import_btn = QPushButton("Import Movement Report")
        self._import_btn.clicked.connect(self.import_period_requested.emit)
        movement_header.addWidget(movement_title)
        movement_header.addStretch()
        movement_header.addWidget(self._import_btn)
        import_layout.addLayout(movement_header)

        self._enrichment_banner = QFrame()
        self._enrichment_banner.setObjectName("banner")
        banner_layout = QHBoxLayout(self._enrichment_banner)
        banner_text = QLabel(
            "Step 2: Import Sales_Detail and PurchasesDetailed reports for your movement period to enrich item data and unlock full analytics."
        )
        banner_text.setWordWrap(True)
        enrich_btn = QPushButton("Import Movement Period")
        enrich_btn.clicked.connect(self.import_enrichment_requested.emit)
        banner_layout.addWidget(banner_text, stretch=1)
        banner_layout.addWidget(enrich_btn)
        import_layout.addWidget(self._enrichment_banner)

        self._partial_note = QLabel(
            "Import Sales_Detail and PurchasesDetailed reports (Step 2) to unlock charts and full analytics."
        )
        self._partial_note.setObjectName("placeholder")
        self._partial_note.setWordWrap(True)
        import_layout.addWidget(self._partial_note)

        self._stock_take = StockTakePage()
        self._stock_take.data_changed.connect(self.data_changed.emit)
        import_layout.addWidget(self._stock_take, stretch=1)

        self._tabs.addTab(overview_tab, "Overview")
        self._tabs.addTab(alerts_tab, "Stock Alerts")
        self._tabs.addTab(sales_tab, "Sales")
        self._tabs.addTab(slow_tab, "Slow Moving")
        self._tabs.addTab(dead_tab, "Dead Stock")
        self._tabs.addTab(import_tab, "Import")
        self._tabs.currentChanged.connect(self._on_home_tab_changed)
        self._dash_layout = dash
        dash.addWidget(self._dashboard_header, 0, 0, 1, 6)
        dash.addWidget(self._tabs, 1, 0, 1, 6)
        dash.setRowStretch(0, 0)
        dash.setRowStretch(1, 1)

        self._dashboard.hide()
        self._layout.addWidget(self._dashboard, 0, 0, 1, 6)

    def _on_lookback_changed(self) -> None:
        self._lookback_weeks = self._lookback_spin.value()
        self._update_lookback_labels()
        self._load_period_data()

    def _on_holding_changed(self) -> None:
        self._update_lookback_labels()
        self._load_period_data()

    def _sync_lookback_combos(self) -> None:
        sync_sales_period_weeks(self._lookback_spin)
        sync_holding_weeks(self._holding_spin)
        self._lookback_weeks = self._lookback_spin.value()
        self._update_lookback_labels()

    def _update_lookback_labels(self) -> None:
        weeks = self._lookback_weeks
        label = lookback_label(weeks)
        self._kpi_sales.set_title(sales_value_label(weeks))
        self._sellers_tile.set_title(f"Top Sellers ({label})")
        self._sales_tile.set_title(units_sold_label(weeks))
        _SLOW_MODE["headers"][3] = sales_period_label(weeks)
        _DEAD_MODE["headers"][3] = sales_period_label(weeks)
        _SALES_MODE["title"] = units_sold_label(weeks)
        _SALES_MODE["headers"][3] = qty_column_label(weeks)
        _SALES_MODE["filename"] = f"sales_{label}"
        _STOCK_ALERT_MODES["understock"]["headers"][3] = under_qty_label(
            self._holding_spin.value()
        )
        _STOCK_ALERT_MODES["overstock"]["headers"][3] = over_qty_label(
            self._holding_spin.value()
        )

    def _on_home_tab_changed(self, index: int) -> None:
        on_import = index == _IMPORT_TAB
        on_data_tab = index in _SCROLLABLE_TABS
        if on_import or on_data_tab:
            self._tabs.setMinimumHeight(0)
            self._tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            self._dash_layout.setRowStretch(0, 0)
            self._dash_layout.setRowStretch(1, 1)
        else:
            self._tabs.setMinimumHeight(0)
            self._tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            self._dash_layout.setRowStretch(0, 0)
            self._dash_layout.setRowStretch(1, 1)

        self._update_header_control_visibility(index)

        self._scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            if on_data_tab
            else Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )

        if index == _OVERVIEW_TAB:
            self._apply_overview_filter()
        elif index == _STOCK_ALERTS_TAB:
            self._apply_stock_alerts_filter()
        elif index == _SALES_TAB:
            self._apply_sales_filter()
        elif index == _SLOW_TAB:
            self._apply_slow_filter()
        elif index == _DEAD_TAB:
            self._apply_dead_filter()

        if on_import:
            self._stock_take.refresh()

        canvas = self._scroll.widget()
        if canvas is not None:
            canvas.updateGeometry()

    def _update_header_control_visibility(self, tab_index: int | None = None) -> None:
        if tab_index is None:
            tab_index = self._tabs.currentIndex()
        dept_visible = self._enriched and tab_index != _IMPORT_TAB
        self._lookback_label.setVisible(self._enriched)
        self._lookback_spin.setVisible(self._enriched)
        self._holding_label.setVisible(self._enriched)
        self._holding_spin.setVisible(self._enriched)
        self._dept_label.setVisible(dept_visible)
        self._dept_combo.setVisible(dept_visible)

    def show_stock_alerts(
        self, alert_type: str = "understock", dept: str | None = None
    ) -> None:
        self._show_stock_alerts_tab(alert_type, dept)

    def show_slow_moving(self, dept: str | None = None) -> None:
        self._show_slow_moving_tab(dept)

    def show_dead_stock(self, dept: str | None = None) -> None:
        self._show_dead_stock_tab(dept)

    def _show_stock_alerts_tab(
        self, alert_type: str = "understock", dept: str | None = None
    ) -> None:
        self._alert_type = alert_type
        self._alert_type_combo.blockSignals(True)
        self._alert_type_combo.setCurrentText(_ALERT_TYPE_LABELS.get(alert_type, "Understock"))
        self._alert_type_combo.blockSignals(False)
        self._update_alert_range_filter_visibility()
        if dept is not None:
            self._dept = dept
            self._set_dept_combo(self._dept_combo, self._departments, dept)
        self._apply_stock_alerts_filter()
        self._tabs.setCurrentIndex(_STOCK_ALERTS_TAB)

    def _show_sales_tab(self, dept: str | None = None) -> None:
        if dept is not None:
            self._dept = dept
            self._set_dept_combo(self._dept_combo, self._departments, dept)
        self._apply_sales_filter()
        self._tabs.setCurrentIndex(_SALES_TAB)

    def _show_slow_moving_tab(self, dept: str | None = None) -> None:
        if dept is not None:
            self._dept = dept
            self._set_dept_combo(self._dept_combo, self._departments, dept)
        self._apply_slow_filter()
        self._tabs.setCurrentIndex(_SLOW_TAB)

    def _show_dead_stock_tab(self, dept: str | None = None) -> None:
        if dept is not None:
            self._dept = dept
            self._set_dept_combo(self._dept_combo, self._departments, dept)
        self._apply_dead_filter()
        self._tabs.setCurrentIndex(_DEAD_TAB)

    def _on_kpi_filter(self, key: str | None) -> None:
        if key == "understock":
            self.show_stock_alerts("understock")
        elif key == "overstock":
            self.show_stock_alerts("overstock")
        elif key == "slow":
            self.show_slow_moving()
        elif key == "dead":
            self.show_dead_stock()
        elif key == "sales":
            self._show_sales_tab()

    def _on_dept_chart_click(self, dept: str) -> None:
        self._dept = dept
        self._set_dept_combo(self._dept_combo, self._departments, dept)
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
        self._alert_type = _ALERT_LABEL_TO_TYPE.get(text, "understock")
        self._update_alert_range_filter_visibility()
        self._apply_stock_alerts_filter()

    def _on_alert_range_changed(self, *_args) -> None:
        self._apply_stock_alerts_filter()

    def _update_alert_range_filter_visibility(self) -> None:
        visible = self._alert_type in _RANGE_ALERT_TYPES
        for widget in (
            self._alert_range_mode_label,
            self._alert_range_mode,
            self._alert_range_min_label,
            self._alert_range_min,
            self._alert_range_max_label,
            self._alert_range_max,
        ):
            widget.setVisible(visible)

    def _alert_rows_for_type(self) -> list[dict]:
        if self._alert_type == "understock":
            return self._understock_rows
        if self._alert_type == "overstock":
            return self._overstock_rows
        if self._alert_type == "gross_margin":
            return self._margin_rows
        if self._alert_type == "markup":
            return self._markup_rows
        return []

    def _apply_pct_range_filter(self, rows: list[dict], pct_key: str) -> list[dict]:
        mode = self._alert_range_mode.currentText().lower()
        min_pct = float(self._alert_range_min.value())
        max_pct = float(self._alert_range_max.value())
        return [
            row
            for row in rows
            if pct_in_range(float(row[pct_key]), min_pct, max_pct, mode)
        ]

    def _on_dept_changed(self, _index: int) -> None:
        self._dept = self._dept_combo.currentData()
        self._apply_dept_filters()

    def _apply_dept_filters(self) -> None:
        self._apply_overview_filter()
        self._apply_stock_alerts_filter()
        self._apply_sales_filter()
        self._apply_slow_filter()
        self._apply_dead_filter()

    @staticmethod
    def _filter_dept_dict(values: dict[str, float], dept: str | None) -> dict[str, float]:
        if not dept:
            return values
        return {dept: values.get(dept, 0.0)}

    def _apply_overview_filter(self) -> None:
        if not self._period:
            return

        with get_session() as session:
            enriched = has_enrichment(session)
            inventory_summary = build_inventory_list_summary(
                session,
                search="",
                status="Active",
                has_enrichment=enriched,
                dept=self._dept,
                lookback_weeks=self._lookback_weeks,
            )

        if self._show_overview_kpis:
            self._kpi_value.set_value(f"R {inventory_summary['total_value']:,.2f}")
            self._kpi_overstock.set_value(
                f"R {inventory_summary.get('overstock_value', 0):,.2f}"
            )
            self._kpi_understock.set_value(
                f"R {inventory_summary.get('understock_value', 0):,.2f}"
            )
            self._kpi_slow.set_value(
                f"R {inventory_summary.get('slow_moving_value', 0):,.2f}"
            )
            self._kpi_dead.set_value(
                f"R {inventory_summary.get('dead_stock_value', 0):,.2f}"
            )
            filtered_sales = filter_stock_rows(self._sales_rows, dept=self._dept)
            total_sales_value = sum(r["sales_value"] for r in filtered_sales)
            self._kpi_sales.set_value(f"R {total_sales_value:,.2f}")

            for key, card in (
                ("overstock_value", self._kpi_overstock),
                ("understock_value", self._kpi_understock),
                ("slow_moving_value", self._kpi_slow),
                ("dead_stock_value", self._kpi_dead),
                ("total_sales_value", self._kpi_sales),
            ):
                text, direction = _format_delta(self._comparison.get(f"{key}_delta_pct"))
                card.set_delta(text, direction)

        if self._show_overview_charts:
            dept_values = self._filter_dept_dict(
                inventory_summary.get("dept_values", {}), self._dept
            )
            overstock_values = self._filter_dept_dict(
                inventory_summary.get("dept_overstock_values", {}), self._dept
            )
            slow_moving_values = self._filter_dept_dict(
                inventory_summary.get("dept_slow_moving_values", {}), self._dept
            )
            dept_view, dept_labels = build_dept_values_chart(
                dept_values,
                self._nickname_map,
                overstock_values=overstock_values,
                slow_moving_values=slow_moving_values,
            )
            self._dept_chart.set_chart_view(dept_view, dept_labels)

            filtered_sales = filter_stock_rows(self._sales_rows, dept=self._dept)
            top_sellers = sorted(
                filtered_sales,
                key=lambda row: (row.get("gross_profit", 0.0), row["qty_sold"]),
                reverse=True,
            )[:20]
            top_seller_data = [
                {
                    "code": row["code"],
                    "name": row["name"],
                    "qty_sold": row["qty_sold"],
                    "gross_profit": row.get("gross_profit", 0.0),
                }
                for row in top_sellers
            ]
            self._populate_sellers_table(top_seller_data)

            if self._show_overview_health:
                health = inventory_summary.get("stock_health") or {}
                self._health_chart.set_chart_view(
                    build_stock_health_chart(health, embedded=True), list(health.keys())
                )

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

    def _populate_dept_combo(self, departments: list[str]) -> None:
        self._dept_combo.blockSignals(True)
        self._dept_combo.clear()
        self._dept_combo.addItem("All departments", None)
        for dept in sorted(departments):
            self._dept_combo.addItem(display_dept(dept, self._nickname_map), dept)
        self._set_dept_combo(self._dept_combo, departments, self._dept)
        self._dept_combo.blockSignals(False)

    def _build_table_panel(self, table: DataTable) -> tuple[QWidget, QLabel]:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(table, stretch=1)
        footer = QLabel("")
        footer.setObjectName("tableFooter")
        footer.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        footer.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        footer.setMinimumHeight(footer.fontMetrics().height() + 4)
        layout.addWidget(footer)
        return container, footer

    @staticmethod
    def _format_total(label: str, amount: float) -> str:
        return f"Total {label}: R {amount:,.2f}"

    def _configure_alerts_table_columns(self) -> None:
        header = self._alerts_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.resizeSection(0, 100)
        if self._alert_type in _RANGE_ALERT_TYPES:
            header.resizeSection(2, 80)
            header.resizeSection(3, 80)
            header.resizeSection(4, 100)
        else:
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
        header.resizeSection(5, 100)

    def _configure_slow_table_columns(self) -> None:
        header = self._slow_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.resizeSection(0, 100)
        header.resizeSection(2, 72)
        header.resizeSection(3, 80)
        header.resizeSection(4, 80)
        header.resizeSection(5, 90)
        header.resizeSection(6, 90)
        header.resizeSection(7, 80)

    def _configure_dead_table_columns(self) -> None:
        header = self._dead_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.resizeSection(0, 100)
        header.resizeSection(2, 72)
        header.resizeSection(3, 80)
        header.resizeSection(4, 90)
        header.resizeSection(5, 90)
        header.resizeSection(6, 80)

    def _configure_sellers_table_columns(self) -> None:
        header = self._sellers_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        header.resizeSection(0, 37)
        header.resizeSection(1, 88)
        header.resizeSection(3, 64)
        header.resizeSection(4, 90)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._sellers_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._sellers_table.setTextElideMode(Qt.TextElideMode.ElideRight)

    def _populate_sellers_table(self, top_sellers: list[dict]) -> None:
        qty_header = qty_column_label(self._lookback_weeks)
        self._sellers_table.set_headers(["#", "Code", "Product", qty_header, "Gross Profit"])
        rows = [
            [
                str(i),
                s["code"],
                s["name"],
                f"{s['qty_sold']:g}",
                f"R {s.get('gross_profit', 0.0):,.2f}",
            ]
            for i, s in enumerate(top_sellers, start=1)
        ]
        self._sellers_table.set_rows(rows)
        self._configure_sellers_table_columns()
        self._sellers_table.scrollToTop()

    def _apply_stock_alerts_filter(self) -> None:
        cfg = _STOCK_ALERT_MODES[self._alert_type]
        rows = self._alert_rows_for_type()
        filtered = filter_stock_rows(rows, dept=self._dept)
        if self._alert_type == "gross_margin":
            filtered = self._apply_pct_range_filter(filtered, "gross_margin_pct")
        elif self._alert_type == "markup":
            filtered = self._apply_pct_range_filter(filtered, "markup_pct")
        self._alerts_display_rows = self._rows_to_display(filtered, self._alert_type)
        self._alerts_tile.set_title(cfg["title"])
        self._alerts_table.set_headers(cfg["headers"])
        self._alerts_table.set_rows(self._alerts_display_rows)
        self._configure_alerts_table_columns()
        sub = f"Showing {len(filtered):,} of {len(rows):,}"
        if self._dept:
            sub += f" · Dept: {display_dept(self._dept, self._nickname_map)}"
        self._alerts_tile.set_subtitle(sub)
        if self._alert_type == "understock":
            total = sum(r["under_value"] for r in filtered)
            self._alerts_footer.setText(self._format_total("Under Value", total))
        elif self._alert_type == "overstock":
            total = sum(r["over_value"] for r in filtered)
            self._alerts_footer.setText(self._format_total("Over Value", total))
        elif self._alert_type == "gross_margin":
            total = sum(r["gross_profit"] for r in filtered)
            self._alerts_footer.setText(self._format_total("Gross Profit", total))
        else:
            self._alerts_footer.setText("")

    def _apply_sales_filter(self) -> None:
        filtered = filter_stock_rows(self._sales_rows, dept=self._dept)
        self._sales_display_rows = [
            [
                r["code"],
                r["name"],
                display_dept(r.get("dept", "—"), self._nickname_map),
                f"{r['qty_sold']:g}",
                f"R {r['sales_value']:,.2f}",
                f"R {r.get('gross_profit', 0.0):,.2f}",
            ]
            for r in filtered
        ]
        self._sales_table.set_headers(_SALES_MODE["headers"])
        self._sales_table.set_rows(self._sales_display_rows)
        self._configure_sales_table_columns()
        sub = f"Showing {len(filtered):,} of {len(self._sales_rows):,}"
        if self._dept:
            sub += f" · Dept: {display_dept(self._dept, self._nickname_map)}"
        self._sales_tile.set_subtitle(sub)
        total_sales = sum(r["sales_value"] for r in filtered)
        total_profit = sum(r.get("gross_profit", 0.0) for r in filtered)
        self._sales_footer.setText(
            f"{self._format_total('Sales Value', total_sales)} · "
            f"{self._format_total('Gross Profit', total_profit)}"
        )

    def _apply_slow_filter(self) -> None:
        filtered = filter_stock_rows(self._slow_rows, dept=self._dept)
        self._slow_display_rows = [
            [
                r["code"],
                r["name"],
                f"{r['on_hand']:g}",
                f"{r['qty_sold']:g}",
                f"{r['over_qty']:g}",
                f"{r['weeks_cover']:.1f}",
                f"R {r['excess_value']:,.2f}",
                display_dept(r.get("dept", "—"), self._nickname_map),
            ]
            for r in filtered
        ]
        self._slow_table.set_headers(_SLOW_MODE["headers"])
        self._slow_table.set_rows(self._slow_display_rows)
        self._configure_slow_table_columns()
        sub = f"Showing {len(filtered):,} of {len(self._slow_rows):,}"
        if self._dept:
            sub += f" · Dept: {display_dept(self._dept, self._nickname_map)}"
        self._slow_tile.set_subtitle(sub)
        total = sum(r["excess_value"] for r in filtered)
        self._slow_footer.setText(self._format_total("Excess Value", total))

    def _apply_dead_filter(self) -> None:
        filtered = filter_stock_rows(self._dead_rows, dept=self._dept)
        self._dead_display_rows = [
            [
                r["code"],
                r["name"],
                f"{r['on_hand']:g}",
                f"{r['qty_sold']:g}",
                f"R {r['unit_cost']:,.2f}",
                f"R {r['stock_value']:,.2f}",
                display_dept(r.get("dept", "—"), self._nickname_map),
            ]
            for r in filtered
        ]
        self._dead_table.set_headers(_DEAD_MODE["headers"])
        self._dead_table.set_rows(self._dead_display_rows)
        self._configure_dead_table_columns()
        sub = f"Showing {len(filtered):,} of {len(self._dead_rows):,}"
        if self._dept:
            sub += f" · Dept: {display_dept(self._dept, self._nickname_map)}"
        self._dead_tile.set_subtitle(sub)
        total = sum(r["stock_value"] for r in filtered)
        self._dead_footer.setText(self._format_total("Stock Value", total))

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
        if mode == "gross_margin":
            return [
                [
                    r["code"],
                    r["name"],
                    f"{r['qty_sold']:g}",
                    f"{r['gross_margin_pct']:.1f}%",
                    f"R {r['gross_profit']:,.2f}",
                ]
                for r in rows
            ]
        if mode == "markup":
            return [
                [
                    r["code"],
                    r["name"],
                    f"{r['on_hand']:g}",
                    f"R {r['unit_cost']:,.2f}",
                    f"{r['markup_pct']:.1f}%",
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
        elif table_key == "dead":
            cfg = _DEAD_MODE
            headers = cfg["headers"]
            rows = self._dead_display_rows
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

    def _load_period_data(self) -> None:
        with get_session() as session:
            config = get_dashboard_config(session)
            summaries = load_summaries(session)
            summary = summaries.baseline
            enriched = has_enrichment(session)
            self._lookback_weeks = self._lookback_spin.value()
            period = get_period_summary_cached(session, self._lookback_weeks)
            inventory_summary = build_inventory_list_summary(
                session,
                search="",
                status="Active",
                has_enrichment=enriched,
                lookback_weeks=self._lookback_weeks,
            )
            comparison = build_period_comparison(session, self._lookback_weeks)
            self._nickname_map = load_nickname_map(session)

        self._sync_lookback_combos()

        self._period = period or {}

        show_kpis = config.get("show_kpis", True)
        show_charts = config.get("show_charts", True)
        show_alerts = config.get("show_alerts", True)
        show_sales = config.get("show_sales_tab", True)
        show_slow = config.get("show_slow_moving_tab", True)
        show_dead = config.get("show_dead_stock_tab", True)
        show_health = config.get("show_stock_health", True)
        self._show_overview_kpis = show_kpis
        self._show_overview_charts = show_charts
        self._show_overview_health = show_charts and show_health
        self._comparison = comparison if period else {}

        for kpi in self._kpis:
            kpi.setVisible(show_kpis)
        self._dept_chart.setVisible(show_charts)
        self._sellers_tile.setVisible(show_charts)
        self._health_chart.setVisible(show_charts and show_health)
        self._tabs.setTabVisible(_STOCK_ALERTS_TAB, show_alerts)
        self._tabs.setTabVisible(_SALES_TAB, show_sales)
        self._tabs.setTabVisible(_SLOW_TAB, show_slow)
        self._tabs.setTabVisible(_DEAD_TAB, show_dead)
        overview_visible = show_kpis or show_charts
        self._tabs.setTabVisible(_OVERVIEW_TAB, overview_visible)
        self._tabs.setTabVisible(_IMPORT_TAB, True)

        if period:
            self._departments = list(inventory_summary.get("dept_values", {}).keys())
            self._populate_dept_combo(self._departments)

            self._understock_rows = period.get("reorder_alerts", [])
            self._overstock_rows = period.get("overstock_alerts", [])
            self._margin_rows = period.get("margin_alerts", [])
            self._markup_rows = period.get("markup_alerts", [])
            self._slow_rows = period.get("slow_moving_items", [])
            self._dead_rows = period.get("dead_stock_items", [])
            self._sales_rows = period.get("sales_items", [])

            if period.get("period_start") and period.get("period_end"):
                label = f"{period['period_start']} – {period['period_end']}"
            else:
                label = "Enriched — ready for period imports"
            self._dashboard_header.set_subtitle(label)

            self._apply_dept_filters()
        else:
            for card in (
                self._kpi_overstock,
                self._kpi_understock,
                self._kpi_slow,
                self._kpi_sales,
                self._kpi_dead,
            ):
                card.set_value("—")
                card.set_delta("—")
            self._kpi_value.set_value(f"R {summary['total_value']:,.2f}")
            self._departments = []
            self._understock_rows = []
            self._overstock_rows = []
            self._margin_rows = []
            self._markup_rows = []
            self._slow_rows = []
            self._dead_rows = []
            self._sales_rows = []
            self._populate_dept_combo([])
            self._populate_sellers_table([])
            self._dashboard_header.set_subtitle("Awaiting enrichment (Step 2)")

    def refresh(self) -> None:
        with get_session() as session:
            has_initial = has_initial_baseline(session)
            enriched = has_enrichment(session)

        self._enriched = enriched

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
        self._tabs.setVisible(True)
        self._update_header_control_visibility()

        if enriched:
            self._load_period_data()
        else:
            with get_session() as session:
                summary = load_summaries(session).baseline
            self._kpi_value.set_value(f"R {summary['total_value']:,.2f}")
            for card in (
                self._kpi_overstock,
                self._kpi_understock,
                self._kpi_slow,
                self._kpi_sales,
                self._kpi_dead,
            ):
                card.set_value("—")
            self._dept_chart.setVisible(False)
            self._sellers_tile.setVisible(False)
            self._health_chart.setVisible(False)
            self._tabs.setTabVisible(_STOCK_ALERTS_TAB, False)
            self._tabs.setTabVisible(_SALES_TAB, False)
            self._tabs.setTabVisible(_SLOW_TAB, False)
            self._tabs.setTabVisible(_DEAD_TAB, False)
            self._dashboard_header.set_subtitle("Awaiting enrichment (Step 2)")

        self._stock_take.refresh()

    def capture_nav_state(self) -> HomeNavState:
        return HomeNavState(
            tab=self._tabs.currentIndex(),
            alert_type=self._alert_type,
            dept=self._dept,
            range_mode=self._alert_range_mode.currentText().lower(),
            range_min=self._alert_range_min.value(),
            range_max=self._alert_range_max.value(),
        )

    def restore_nav_state(self, state: object | None) -> None:
        if not isinstance(state, HomeNavState):
            return

        self._alert_type = state.alert_type
        self._dept = state.dept

        self._alert_type_combo.blockSignals(True)
        self._alert_type_combo.setCurrentText(
            _ALERT_TYPE_LABELS.get(state.alert_type, "Understock")
        )
        self._alert_type_combo.blockSignals(False)

        self._alert_range_mode.blockSignals(True)
        self._alert_range_mode.setCurrentText(
            state.range_mode.capitalize() if state.range_mode in ("inside", "outside") else "Inside"
        )
        self._alert_range_mode.blockSignals(False)
        self._alert_range_min.blockSignals(True)
        self._alert_range_min.setValue(state.range_min)
        self._alert_range_min.blockSignals(False)
        self._alert_range_max.blockSignals(True)
        self._alert_range_max.setValue(state.range_max)
        self._alert_range_max.blockSignals(False)
        self._update_alert_range_filter_visibility()

        self._set_dept_combo(self._dept_combo, self._departments, state.dept)

        if state.tab == _OVERVIEW_TAB:
            self._apply_overview_filter()
        elif state.tab == _STOCK_ALERTS_TAB:
            self._apply_stock_alerts_filter()
        elif state.tab == _SALES_TAB:
            self._apply_sales_filter()
        elif state.tab == _SLOW_TAB:
            self._apply_slow_filter()
        elif state.tab == _DEAD_TAB:
            self._apply_dead_filter()

        self._tabs.setCurrentIndex(state.tab)
        self._on_home_tab_changed(state.tab)

    def reset_to_base(self) -> None:
        self._tabs.setCurrentIndex(_OVERVIEW_TAB)
        self._on_home_tab_changed(_OVERVIEW_TAB)
