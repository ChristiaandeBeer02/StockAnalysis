"""Inventory list and item drill-down shell."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
)

from sqlalchemy import func, select

from stock_analysis.analytics.cache import get_period_summary_cached, invalidate_summaries
from stock_analysis.analytics.dashboard import (
    build_inventory_list_summary,
    build_item_summary,
    list_period_batches,
)
from stock_analysis.analytics.department_names import display_dept, load_nickname_map
from stock_analysis.analytics.lookback import (
    get_lookback_days,
    lookback_label,
    qty_column_label,
    sales_period_label,
)
from stock_analysis.db.models import Item
from stock_analysis.db.session import get_session, has_enrichment, has_initial_baseline
from stock_analysis.ui.export_dialog import prompt_export_excel
from stock_analysis.ui.models.inventory_table_model import InventoryTableModel
from stock_analysis.ui.widgets.chart_builders import (
    build_dept_values_chart,
    build_item_sales_chart,
    build_item_stock_trend_chart,
    build_pie_chart,
    build_stock_health_chart,
)
from stock_analysis.ui.widgets.chart_tile import ChartTile
from stock_analysis.ui.widgets.dashboard_tile import DashboardTile
from stock_analysis.ui.widgets.data_table import DataTable
from stock_analysis.ui.widgets.empty_state import EmptyState
from stock_analysis.ui.widgets.kpi_card import KpiCard
from stock_analysis.ui.widgets.report_header import ReportHeader
from stock_analysis.ui.widgets.sales_period_combo import (
    create_sales_period_combo,
    sync_sales_period_combo,
    update_lookback_tooltip,
)

STATUS_OPTIONS = ["Active", "No turn data", "Deprecated", "All"]

_OVERVIEW_TAB = 0
_ITEMS_TAB = 1


@dataclass
class InventoryNavState:
    tab: int
    dept_filter: str | None
    search_text: str
    status_filter: str
    selected_batch_id: int | None


class ItemDetailPage(QWidget):
    back_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("dashboardCanvas")
        self._item_id: int | None = None
        self._sku = ""
        self._batch_id: int | None = None
        self._all_history_rows: list[dict] = []
        self._history_filter_status: str | None = None
        self._export_title = "Item History"
        self._history_display: list[list] = []

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        body = QWidget()
        body.setObjectName("dashboardCanvas")
        layout = QGridLayout(body)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        scroll.setWidget(body)

        back_btn = QPushButton("← Back")
        back_btn.clicked.connect(self.back_requested.emit)
        self._header = ReportHeader("Item Detail", "")
        self._header.add_control(back_btn)
        self._period_combo = QComboBox()
        self._period_combo.setMinimumWidth(200)
        self._period_combo.currentIndexChanged.connect(self._on_period_changed)
        self._header.add_control(QLabel("Period:"))
        self._header.add_control(self._period_combo)
        self._detail_lookback = create_sales_period_combo(
            self, on_changed=self._on_detail_lookback_changed
        )
        self._header.add_control(QLabel("Sales period:"))
        self._header.add_control(self._detail_lookback)
        layout.addWidget(self._header, 0, 0, 1, 6)

        self._kpi_on_hand = KpiCard("On Hand")
        self._kpi_value = KpiCard("Stock Value")
        self._kpi_sales = KpiCard("Sales")
        self._kpi_over = KpiCard("Over Qty (3mo)")
        self._kpi_under = KpiCard("Under Qty (3mo)")
        self._kpi_abc = KpiCard("ABC Class")
        self._kpi_over.set_accent("warning")
        self._kpi_under.set_accent("danger")
        self._kpis = [
            self._kpi_on_hand,
            self._kpi_value,
            self._kpi_sales,
            self._kpi_over,
            self._kpi_under,
            self._kpi_abc,
        ]
        for i, kpi in enumerate(self._kpis):
            layout.addWidget(kpi, 1, i)

        self._tabs = QTabWidget()
        self._tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._tabs.setMinimumHeight(340)

        sales_tab = QWidget()
        sales_layout = QHBoxLayout(sales_tab)
        sales_layout.setContentsMargins(0, 0, 0, 0)
        sales_layout.setSpacing(8)
        self._sales_chart = ChartTile("Sales by Period")
        self._sales_chart.point_clicked.connect(self._on_period_bar_click)
        self._sales_mix_chart = ChartTile("Sales Mix")
        sales_layout.addWidget(self._sales_chart, 1)
        sales_layout.addWidget(self._sales_mix_chart, 1)

        stock_tab = QWidget()
        stock_layout = QHBoxLayout(stock_tab)
        stock_layout.setContentsMargins(0, 0, 0, 0)
        stock_layout.setSpacing(8)
        self._stock_trend_chart = ChartTile("Stock Trend")
        self._stock_pos_chart = ChartTile("Stock Position")
        stock_layout.addWidget(self._stock_trend_chart, 1)
        stock_layout.addWidget(self._stock_pos_chart, 1)

        summary_tab = QWidget()
        summary_layout = QHBoxLayout(summary_tab)
        summary_layout.setContentsMargins(0, 0, 0, 0)
        summary_layout.setSpacing(8)
        self._health_chart = ChartTile("Period Health")
        self._health_chart.point_clicked.connect(self._on_health_slice_click)
        self._summary_label = QLabel("")
        self._summary_label.setObjectName("placeholder")
        self._summary_label.setWordWrap(True)
        summary_layout.addWidget(self._health_chart, 2)
        summary_layout.addWidget(self._summary_label, 1)

        self._tabs.addTab(sales_tab, "Sales")
        self._tabs.addTab(stock_tab, "Stock")
        self._tabs.addTab(summary_tab, "Summary")
        layout.addWidget(self._tabs, 2, 0, 1, 6)

        self._history_tile = DashboardTile("Period History", "All imported periods")
        export_btn = QPushButton("Export Excel…")
        export_btn.clicked.connect(self._export_history)
        clear_btn = QPushButton("Show all")
        clear_btn.clicked.connect(self._clear_history_filter)
        self._history_tile.add_action(clear_btn)
        self._history_tile.add_action(export_btn)
        self._history = DataTable()
        self._history.setMaximumHeight(280)
        self._history_tile.set_content(self._history)
        layout.addWidget(self._history_tile, 3, 0, 1, 6)

        layout.setRowMinimumHeight(2, 340)
        layout.setRowStretch(2, 3)
        layout.setRowStretch(3, 1)

        self._batch_ids: list[int] = []
        self._nickname_map: dict[str, str] = {}
        self._lookback_days = 90

    def _on_detail_lookback_changed(self) -> None:
        self._lookback_days = self._detail_lookback.currentData() or 90
        self._kpi_sales.set_title(sales_period_label(self._lookback_days))
        self._reload_item()

    def _on_period_changed(self, index: int) -> None:
        if self._item_id is None or index < 0 or index >= len(self._batch_ids):
            return
        self._batch_id = self._batch_ids[index]
        self._reload_item()

    def _on_period_bar_click(self, label: str) -> None:
        for i, row in enumerate(self._all_history_rows):
            if row["period"] == label:
                self._history.selectRow(i)
                break

    def _on_health_slice_click(self, status: str) -> None:
        if self._history_filter_status == status:
            self._clear_history_filter()
            return
        self._history_filter_status = status
        self._apply_history_filter()

    def _clear_history_filter(self) -> None:
        self._history_filter_status = None
        self._apply_history_filter()

    def _apply_history_filter(self) -> None:
        rows = self._all_history_rows
        if self._history_filter_status:
            rows = [r for r in rows if r["status"] == self._history_filter_status]
        self._history_display = [
            [
                r["period"],
                f"{r['qty_30']:g}",
                f"{r['qty_90']:g}",
                f"{r['qty_180']:g}",
                f"{r['over_qty']:g}",
                f"{r['under_qty']:.2f}",
                f"{r['unit_cost']:.2f}" if r["unit_cost"] else "—",
            ]
            for r in rows
        ]
        self._history.set_rows(self._history_display)
        sub = f"Showing {len(rows)} of {len(self._all_history_rows)} periods"
        if self._history_filter_status:
            sub += f" · {self._history_filter_status}"
        self._history_tile.set_subtitle(sub)

    def _export_history(self) -> None:
        headers = [
            "Period",
            "Qty 30d",
            "Qty 90d",
            "Qty 180d",
            "Over Qty",
            "Under Qty",
            "Unit Cost",
        ]
        prompt_export_excel(self, self._export_title, headers, self._history_display, "item_history.xlsx")

    def _populate_period_combo(self, batches: list[dict]) -> None:
        self._period_combo.blockSignals(True)
        self._period_combo.clear()
        self._batch_ids = []
        for batch in batches:
            self._period_combo.addItem(batch["label"])
            self._batch_ids.append(batch["id"])
        if self._batch_id in self._batch_ids:
            self._period_combo.setCurrentIndex(self._batch_ids.index(self._batch_id))
        elif self._batch_ids:
            self._period_combo.setCurrentIndex(0)
            self._batch_id = self._batch_ids[0]
        self._period_combo.blockSignals(False)

    def _reload_item(self) -> None:
        if self._item_id is None:
            return
        with get_session() as session:
            self._nickname_map = load_nickname_map(session)
            self._lookback_days = get_lookback_days(session)
            sync_sales_period_combo(self._detail_lookback)
            data = build_item_summary(
                session,
                self._item_id,
                self._batch_id,
                self._nickname_map,
                self._lookback_days,
            )
        if not data:
            return
        self._apply_summary(data)

    def show_item(self, sku: str) -> None:
        with get_session() as session:
            self._nickname_map = load_nickname_map(session)
            self._lookback_days = get_lookback_days(session)
            sync_sales_period_combo(self._detail_lookback)
            item = session.scalar(select(Item).where(Item.sku == sku))
            if not item:
                self._header.set_subtitle("Item not found")
                self._history.clear_data()
                return
            self._item_id = item.id
            self._sku = sku
            data = build_item_summary(
                session, item.id, None, self._nickname_map, self._lookback_days
            )

        if not data:
            self._header.set_subtitle("Item not found")
            return

        self._batch_id = data.get("selected_batch_id")
        self._populate_period_combo(data.get("available_batches", []))
        self._export_title = f"Item History — {data['sku']}"
        chips = [data["department"], data["supplier"]]
        if data.get("is_deprecated"):
            chips.append("Deprecated")
        if data.get("not_in_turn_report"):
            chips.append("No turn data")
        self._header.set_subtitle(
            f"{data['sku']} — {data['name']}  ·  " + "  ·  ".join(c for c in chips if c and c != "—")
        )
        self._apply_summary(data)

    def _apply_summary(self, data: dict) -> None:
        self._kpi_on_hand.set_value(f"{data['on_hand']:g}")
        self._kpi_value.set_value(f"R {data['stock_value']:,.2f}")
        self._kpi_sales.set_title(sales_period_label(self._lookback_days))
        self._kpi_sales.set_value(f"{data['qty_sold']:g}" if data.get("chart_data") else "—")
        self._kpi_over.set_value(f"{data['over_qty']:g}" if data.get("chart_data") else "—")
        self._kpi_under.set_value(f"{data['under_qty']:.2f}" if data.get("chart_data") else "—")
        abc = data.get("abc_class") or "—"
        self._kpi_abc.set_value(abc)
        accent = {"A": "success", "B": "warning", "C": "amber"}.get(abc)
        self._kpi_abc.set_accent(accent)

        chart_data = data.get("chart_data") or {}
        if chart_data:
            sales_view, labels = build_item_sales_chart(chart_data)
            self._sales_chart.set_chart_view(sales_view, labels)
            self._stock_trend_chart.set_chart_view(build_item_stock_trend_chart(chart_data))
            mix = data.get("sales_mix_pie") or {}
            self._sales_mix_chart.set_chart_view(
                build_pie_chart(mix, "Sales Mix (selected period)"),
                list(mix.keys()),
            )
            pos = data.get("stock_position_pie") or {}
            self._stock_pos_chart.set_chart_view(
                build_pie_chart(pos, "Stock Position", donut=True),
                list(pos.keys()),
            )
            health = data.get("period_health_pie") or {}
            self._health_chart.set_chart_view(
                build_pie_chart(health, "Period Health"),
                list(health.keys()),
            )
            self._summary_label.setText(
                f"Selected period: {data.get('period_label', '—')}\n"
                f"Total history periods: {len(data.get('history_rows', []))}"
            )
        else:
            msg = "Import turn reports for item analytics."
            self._sales_chart.set_chart_view(build_pie_chart({}, msg))
            self._sales_mix_chart.set_chart_view(build_pie_chart({}, msg))
            self._stock_trend_chart.set_chart_view(build_pie_chart({}, msg))
            self._stock_pos_chart.set_chart_view(build_pie_chart({}, msg))
            self._health_chart.set_chart_view(build_pie_chart({}, msg))
            self._summary_label.setText(msg)

        self._all_history_rows = data.get("history_rows", [])
        self._history_filter_status = None
        self._apply_history_filter()


class InventoryPage(QWidget):
    item_selected = Signal(str)
    item_detail_requested = Signal(str)
    stock_alert_requested = Signal(str, object)
    slow_moving_requested = Signal(object)
    data_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._selected_batch_id: int | None = None
        self._batch_ids: list[int] = []
        self._dept_filter: str | None = None
        self._nickname_map: dict[str, str] = {}
        self._lookback_days = 90

        outer = QVBoxLayout(self)
        self._stack = QStackedWidget()
        outer.addWidget(self._stack)

        self._list_view = QWidget()
        self._list_view.setObjectName("dashboardCanvas")
        list_layout = QVBoxLayout(self._list_view)
        list_layout.setContentsMargins(0, 0, 0, 0)

        self._empty = EmptyState(
            "No inventory data",
            "Complete Step 1 (initial baseline import) to populate the inventory list.",
        )
        list_layout.addWidget(self._empty)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._content = QWidget()
        self._content.setObjectName("dashboardCanvas")
        content = QVBoxLayout(self._content)
        content.setContentsMargins(16, 16, 16, 16)
        content.setSpacing(12)
        scroll.setWidget(self._content)

        self._list_header = ReportHeader("Inventory", "")
        self._list_header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._list_period = QComboBox()
        self._list_period.setMinimumWidth(200)
        self._list_period.currentIndexChanged.connect(self._on_list_period_changed)
        self._list_header.add_control(QLabel("Period:"))
        self._list_header.add_control(self._list_period)
        self._list_lookback = create_sales_period_combo(
            self, on_changed=self._on_list_lookback_changed
        )
        self._list_header.add_control(QLabel("Sales period:"))
        self._list_header.add_control(self._list_lookback)
        content.addWidget(self._list_header)

        self._tabs = QTabWidget()
        self._tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        overview_tab = QWidget()
        overview_tab.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        overview_layout = QGridLayout(overview_tab)
        overview_layout.setContentsMargins(4, 8, 4, 4)
        overview_layout.setSpacing(12)

        self._inv_kpi_count = KpiCard("Items in Filter")
        self._inv_kpi_value = KpiCard("Stock Value")
        self._inv_kpi_under = KpiCard("Understocked", filter_key="understock")
        self._inv_kpi_over = KpiCard("Overstocked", filter_key="overstock")
        self._inv_kpi_slow = KpiCard("Slow Moving", filter_key="slow")
        self._inv_kpi_under.set_accent("danger")
        self._inv_kpi_over.set_accent("warning")
        self._inv_kpi_slow.set_accent("amber")
        inv_kpis = [
            self._inv_kpi_count,
            self._inv_kpi_value,
            self._inv_kpi_under,
            self._inv_kpi_over,
            self._inv_kpi_slow,
        ]
        for i, kpi in enumerate(inv_kpis):
            overview_layout.addWidget(kpi, 0, i)
        for card in (self._inv_kpi_under, self._inv_kpi_over, self._inv_kpi_slow):
            card.clicked.connect(lambda checked=False, k=card.filter_key: self._on_kpi_filter(k))

        self._inv_dept_chart = ChartTile("Stock Value by Dept")
        self._inv_dept_chart.point_clicked.connect(self._on_inv_dept_click)
        self._inv_health_chart = ChartTile("Stock Health")
        overview_layout.addWidget(self._inv_dept_chart, 1, 0, 1, 3)
        overview_layout.addWidget(self._inv_health_chart, 1, 3, 1, 3)
        overview_layout.setRowMinimumHeight(1, 320)
        overview_layout.setRowStretch(1, 2)

        items_tab = QWidget()
        items_tab.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        items_layout = QVBoxLayout(items_tab)
        items_layout.setContentsMargins(4, 8, 4, 4)
        items_layout.setSpacing(12)

        toolbar = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search by code or description…")
        self._remove_deprecated_btn = QPushButton("Remove Deprecated")
        self._remove_deprecated_btn.clicked.connect(self._remove_deprecated)
        self._dept_filter_combo = QComboBox()
        self._dept_filter_combo.setMinimumWidth(140)
        self._dept_filter_combo.currentIndexChanged.connect(self._on_dept_combo_changed)
        self._status_filter = QComboBox()
        self._status_filter.addItems(STATUS_OPTIONS)
        self._status_filter.setCurrentText("Active")
        toolbar.addWidget(self._search, stretch=1)
        toolbar.addWidget(self._remove_deprecated_btn)
        toolbar.addWidget(QLabel("Dept:"))
        toolbar.addWidget(self._dept_filter_combo)
        toolbar.addWidget(QLabel("Status:"))
        toolbar.addWidget(self._status_filter)
        toolbar_w = QWidget()
        toolbar_w.setLayout(toolbar)
        items_layout.addWidget(toolbar_w)

        self._table_tile = DashboardTile("Inventory", "")
        self._inventory_model = InventoryTableModel(self)
        self._table = DataTable()
        self._table.enable_viewport_scrolling()
        self._table.set_external_model(self._inventory_model)
        self._configure_inventory_table_columns()
        self._table.doubleClicked.connect(self._on_row_double_click)
        self._table_tile.set_content(self._table)
        items_layout.addWidget(self._table_tile, stretch=1)

        self._tabs.addTab(overview_tab, "Overview")
        self._tabs.addTab(items_tab, "Items")
        self._tabs.currentChanged.connect(self._on_inventory_tab_changed)
        content.addWidget(self._tabs)
        self._content_layout = content

        self._scroll = scroll
        list_layout.addWidget(scroll)

        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(250)
        self._filter_timer.timeout.connect(self._apply_filters)
        self._search.textChanged.connect(self._schedule_filter)
        self._status_filter.currentTextChanged.connect(self._schedule_filter)

        self._detail = ItemDetailPage()

        self._stack.addWidget(self._list_view)
        self._stack.addWidget(self._detail)

    def _configure_inventory_table_columns(self) -> None:
        header = self._table.horizontalHeader()
        header.setStretchLastSection(False)
        for column in range(self._inventory_model.columnCount()):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.resizeSection(0, 130)
        header.resizeSection(2, 72)
        header.resizeSection(3, 88)
        header.resizeSection(4, 88)
        header.resizeSection(5, 80)
        header.resizeSection(6, 100)
        self._table.setTextElideMode(Qt.TextElideMode.ElideRight)

    def _on_list_lookback_changed(self) -> None:
        self._lookback_days = self._list_lookback.currentData() or 90
        sync_sales_period_combo(self._list_lookback)
        self._inventory_model.set_lookback_days(self._lookback_days)
        self._refresh_summary()

    def _on_list_period_changed(self, index: int) -> None:
        if 0 <= index < len(self._batch_ids):
            self._selected_batch_id = self._batch_ids[index]
            self._refresh_summary()

    def _on_inventory_tab_changed(self, index: int) -> None:
        on_items = index == _ITEMS_TAB
        if on_items:
            self._tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            self._content_layout.setStretchFactor(self._tabs, 1)
            self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        else:
            self._tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            self._content_layout.setStretchFactor(self._tabs, 0)
            self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self._content.updateGeometry()

    def show_items_tab(self) -> None:
        self._show_items_tab()

    def _show_items_tab(self) -> None:
        self._tabs.setCurrentIndex(_ITEMS_TAB)

    def set_dept_filter(self, dept: str | None) -> None:
        self._dept_filter = dept
        self._dept_filter_combo.blockSignals(True)
        if dept is None:
            self._dept_filter_combo.setCurrentIndex(0)
        else:
            index = self._dept_filter_combo.findData(dept)
            if index >= 0:
                self._dept_filter_combo.setCurrentIndex(index)
        self._dept_filter_combo.blockSignals(False)
        self._inventory_model.set_dept_filter(dept)
        self._update_table_subtitle()

    def _on_dept_combo_changed(self, _index: int) -> None:
        dept = self._dept_filter_combo.currentData()
        self._dept_filter = dept
        self._inventory_model.set_dept_filter(dept)
        self._update_table_subtitle()
        self._refresh_summary()

    def _on_inv_dept_click(self, dept: str) -> None:
        self.set_dept_filter(dept)
        self._show_items_tab()

    def _on_kpi_filter(self, key: str | None) -> None:
        dept = self._dept_filter
        if key == "understock":
            self.stock_alert_requested.emit("understock", dept)
        elif key == "overstock":
            self.stock_alert_requested.emit("overstock", dept)
        elif key == "slow":
            self.slow_moving_requested.emit(dept)

    def _populate_dept_combo(self, departments: list[str]) -> None:
        current = self._dept_filter
        self._dept_filter_combo.blockSignals(True)
        self._dept_filter_combo.clear()
        self._dept_filter_combo.addItem("All departments", None)
        for dept in sorted(departments):
            self._dept_filter_combo.addItem(display_dept(dept, self._nickname_map), dept)
        if current:
            index = self._dept_filter_combo.findData(current)
            if index >= 0:
                self._dept_filter_combo.setCurrentIndex(index)
            else:
                self._dept_filter_combo.setCurrentIndex(0)
                self._dept_filter = None
                self._inventory_model.set_dept_filter(None)
        self._dept_filter_combo.blockSignals(False)

    def _populate_list_period_combo(self, batches: list[dict]) -> None:
        previous = self._selected_batch_id
        self._list_period.blockSignals(True)
        self._list_period.clear()
        self._batch_ids = []
        selected = 0
        for i, batch in enumerate(batches):
            self._list_period.addItem(batch["label"])
            self._batch_ids.append(batch["id"])
            if previous == batch["id"]:
                selected = i
        if batches:
            self._list_period.setCurrentIndex(selected)
            self._selected_batch_id = self._batch_ids[selected]
        self._list_period.blockSignals(False)

    def _refresh_summary(self) -> None:
        with get_session() as session:
            enriched = has_enrichment(session)
            self._nickname_map = load_nickname_map(session)
            self._lookback_days = get_lookback_days(session)
            sync_sales_period_combo(self._list_lookback)
            summary = build_inventory_list_summary(
                session,
                search=self._search.text(),
                status=self._status_filter.currentText(),
                batch_id=self._selected_batch_id,
                has_enrichment=enriched,
                dept=self._dept_filter,
                lookback_days=self._lookback_days,
            )
            if enriched and self._selected_batch_id:
                period = get_period_summary_cached(
                    session, self._selected_batch_id, self._lookback_days
                )
                update_lookback_tooltip(
                    self._list_lookback, period.get("lookback_60_source")
                )
                if period.get("period_start"):
                    self._list_header.set_subtitle(
                        f"{period['period_start']} – {period['period_end']}"
                    )

        self._inv_kpi_count.set_value(f"{summary['item_count']:,}")
        self._inv_kpi_value.set_value(f"R {summary['total_value']:,.2f}")
        if enriched:
            self._inv_kpi_under.set_value(f"R {summary['understock_value']:,.2f}")
            self._inv_kpi_over.set_value(f"R {summary['overstock_value']:,.2f}")
            self._inv_kpi_slow.set_value(f"R {summary['slow_moving_value']:,.2f}")
            dept_view, dept_labels = build_dept_values_chart(
                summary.get("dept_values", {}), self._nickname_map
            )
            self._inv_dept_chart.set_chart_view(dept_view, dept_labels)
            health = summary.get("stock_health", {})
            self._inv_health_chart.set_chart_view(build_stock_health_chart(health))
        else:
            for kpi in (self._inv_kpi_under, self._inv_kpi_over, self._inv_kpi_slow):
                kpi.set_value("—")
            self._inv_dept_chart.set_chart_view(build_stock_health_chart({}))
            self._inv_health_chart.set_chart_view(build_stock_health_chart({}))

        self._populate_dept_combo(list(summary.get("dept_values", {}).keys()))

    def _schedule_filter(self) -> None:
        self._filter_timer.start()

    def _apply_filters(self) -> None:
        self._inventory_model.set_filters(
            self._search.text(), self._status_filter.currentText()
        )
        self._update_table_subtitle()
        self._refresh_summary()

    def _update_table_subtitle(self) -> None:
        total = self._inventory_model.total_count
        self._table_tile.set_subtitle(f"{total:,} items match filters")

    def _remove_deprecated(self) -> None:
        with get_session() as session:
            count = session.scalar(
                select(func.count(Item.id)).where(Item.is_deprecated.is_(True))
            ) or 0

        if count == 0:
            QMessageBox.information(self, "Remove Deprecated", "No deprecated items to remove.")
            return

        answer = QMessageBox.question(
            self,
            "Remove Deprecated",
            f"Permanently remove {count:,} deprecated items from the database?\n\n"
            "This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        with get_session() as session:
            removed = remove_deprecated_items(session)

        invalidate_summaries()
        QMessageBox.information(
            self, "Remove Deprecated", f"Removed {removed:,} deprecated items."
        )
        self.data_changed.emit()
        self.refresh()

    def _on_row_double_click(self, index) -> None:
        sku = self._inventory_model.sku_at(index.row())
        if sku:
            self.item_detail_requested.emit(sku)

    def show_list_view(self) -> None:
        self._stack.setCurrentWidget(self._list_view)

    def is_showing_detail(self) -> bool:
        return self._stack.currentWidget() is self._detail

    def show_item_detail(self, sku: str) -> None:
        self._detail.show_item(sku)
        self._stack.setCurrentWidget(self._detail)

    def refresh(self) -> None:
        with get_session() as session:
            if not has_initial_baseline(session):
                self._empty.show()
                self._scroll.hide()
                return
            enriched = has_enrichment(session)
            batches = list_period_batches(session) if enriched else []
            self._nickname_map = load_nickname_map(session)

        self._empty.hide()
        self._scroll.show()
        self._stack.setCurrentWidget(self._list_view)

        if enriched:
            self._populate_list_period_combo(batches)
            self._list_period.setVisible(True)
            self._list_lookback.setVisible(True)
            self._detail._detail_lookback.setVisible(True)
            self._inv_dept_chart.setVisible(True)
            self._inv_health_chart.setVisible(True)
        else:
            self._list_period.setVisible(False)
            self._list_lookback.setVisible(False)
            self._detail._detail_lookback.setVisible(False)
            self._inv_dept_chart.setVisible(False)
            self._inv_health_chart.setVisible(False)

        self._inventory_model.set_nickname_map(self._nickname_map)
        self._inventory_model.set_batch_id(self._selected_batch_id)
        self._inventory_model.set_lookback_days(self._lookback_days)
        self._inventory_model.reload()
        self._update_table_subtitle()
        self._refresh_summary()

    def capture_nav_state(self) -> InventoryNavState:
        return InventoryNavState(
            tab=self._tabs.currentIndex(),
            dept_filter=self._dept_filter,
            search_text=self._search.text(),
            status_filter=self._status_filter.currentText(),
            selected_batch_id=self._selected_batch_id,
        )

    def restore_nav_state(self, state: object | None, *, needs_refresh: bool = False) -> None:
        if not isinstance(state, InventoryNavState):
            return

        self.show_list_view()

        if not needs_refresh and self.capture_nav_state() == state:
            return

        if state.selected_batch_id != self._selected_batch_id:
            if state.selected_batch_id in self._batch_ids:
                period_index = self._batch_ids.index(state.selected_batch_id)
                self._list_period.blockSignals(True)
                self._list_period.setCurrentIndex(period_index)
                self._list_period.blockSignals(False)
                self._selected_batch_id = state.selected_batch_id

        self._dept_filter = state.dept_filter
        self._dept_filter_combo.blockSignals(True)
        if state.dept_filter is None:
            self._dept_filter_combo.setCurrentIndex(0)
        else:
            index = self._dept_filter_combo.findData(state.dept_filter)
            if index >= 0:
                self._dept_filter_combo.setCurrentIndex(index)
        self._dept_filter_combo.blockSignals(False)

        self._search.blockSignals(True)
        self._search.setText(state.search_text)
        self._search.blockSignals(False)

        self._status_filter.blockSignals(True)
        self._status_filter.setCurrentText(state.status_filter)
        self._status_filter.blockSignals(False)

        self._tabs.blockSignals(True)
        self._tabs.setCurrentIndex(state.tab)
        self._tabs.blockSignals(False)
        self._on_inventory_tab_changed(state.tab)

        if needs_refresh:
            self._inventory_model.apply_filters(
                state.search_text, state.status_filter, state.dept_filter, reload=False
            )
            self.refresh()
            return

        self._inventory_model.apply_filters(
            state.search_text, state.status_filter, state.dept_filter
        )
        self._update_table_subtitle()

        if state.tab == _OVERVIEW_TAB:
            self._refresh_summary()
