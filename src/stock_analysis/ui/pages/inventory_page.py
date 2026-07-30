"""Inventory list and item drill-down shell."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
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
)
from stock_analysis.analytics.department_names import (
    display_dept,
    list_item_departments,
    load_nickname_map,
    update_item_department,
)
from stock_analysis.analytics.inventory_queries import list_inventory_departments
from stock_analysis.analytics.lookback import (
    lookback_label,
    over_qty_label,
    sales_period_label,
    under_qty_label,
)
from stock_analysis.baseline.manager import update_item_on_hand
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
from stock_analysis.ui.widgets.sales_period_weeks import (
    create_sales_period_weeks,
    sync_sales_period_weeks,
)

STATUS_OPTIONS = ["Active", "Deprecated", "All"]

_OVERVIEW_TAB = 0
_ITEMS_TAB = 1


@dataclass
class InventoryNavState:
    tab: int
    dept_filter: str | None
    search_text: str
    status_filter: str


class ItemDetailPage(QWidget):
    back_requested = Signal()
    department_changed = Signal()
    on_hand_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("dashboardCanvas")
        self._item_id: int | None = None
        self._sku = ""
        self._all_history_rows: list[dict] = []
        self._history_filter_status: str | None = None
        self._export_title = "Item History"
        self._history_display: list[list] = []

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setSizeAdjustPolicy(QScrollArea.SizeAdjustPolicy.AdjustIgnored)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(scroll, 1)

        body = QWidget()
        body.setObjectName("dashboardCanvas")
        body.setMinimumHeight(0)
        layout = QGridLayout(body)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        scroll.setWidget(body)

        back_btn = QPushButton("← Back")
        back_btn.clicked.connect(self._on_back_clicked)
        self._header = ReportHeader("Item Detail", "")
        self._header.add_control(back_btn)
        self._detail_lookback = create_sales_period_weeks(
            self, on_changed=self._on_detail_lookback_changed
        )
        self._header.add_control(QLabel("Sales period:"))
        self._header.add_control(self._detail_lookback)
        self._dept_combo = QComboBox()
        self._dept_combo.setMinimumWidth(160)
        self._dept_combo.currentIndexChanged.connect(self._on_dept_changed)
        self._header.add_control(QLabel("Department:"))
        self._header.add_control(self._dept_combo)
        layout.addWidget(self._header, 0, 0, 1, 6)

        self._on_hand_card = QFrame()
        self._on_hand_card.setObjectName("kpiCard")
        on_hand_layout = QVBoxLayout(self._on_hand_card)
        on_hand_layout.setContentsMargins(16, 12, 16, 12)
        on_hand_layout.setSpacing(4)
        on_hand_title = QLabel("On Hand")
        on_hand_title.setObjectName("kpiTitle")
        self._on_hand_spin = QSpinBox()
        self._on_hand_spin.setMinimum(0)
        self._on_hand_spin.setMaximum(9999999)
        self._on_hand_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self._on_hand_spin.valueChanged.connect(self._on_on_hand_spin_changed)
        on_hand_layout.addWidget(on_hand_title)
        on_hand_layout.addWidget(self._on_hand_spin)

        self._kpi_value = KpiCard("Stock Value")
        self._kpi_sales = KpiCard("Sales")
        self._kpi_over = KpiCard("Over Qty")
        self._kpi_under = KpiCard("Under Qty")
        self._kpi_abc = KpiCard("ABC Class")
        self._kpi_over.set_accent("warning")
        self._kpi_under.set_accent("danger")
        self._kpis = [
            self._on_hand_card,
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
        self._sales_chart = ChartTile("Sales over Period")
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
        self._save_on_hand_btn = QPushButton("Save")
        self._save_on_hand_btn.setEnabled(False)
        self._save_on_hand_btn.clicked.connect(self._save_on_hand)
        self._history_tile.add_action(clear_btn)
        self._history_tile.add_action(export_btn)
        self._history_tile.add_action(self._save_on_hand_btn)
        self._history = DataTable()
        self._history.set_headers(
            ["Period", "Qty Sold", "Over Qty", "Under Qty", "Unit Cost"]
        )
        self._history.setMaximumHeight(280)
        self._history_tile.set_content(self._history)
        layout.addWidget(self._history_tile, 3, 0, 1, 6)

        layout.setRowMinimumHeight(2, 340)
        layout.setRowStretch(2, 3)
        layout.setRowStretch(3, 1)

        self._nickname_map: dict[str, str] = {}
        self._lookback_weeks = 1
        self._populating_dept = False
        self._saved_on_hand: int | None = None

    def _set_header_subtitle(self, data: dict) -> None:
        chips = [data["department"], data["supplier"]]
        if data.get("is_deprecated"):
            chips.append("Deprecated")
        if data.get("not_in_turn_report"):
            chips.append("No movement data")
        self._header.set_subtitle(
            f"{data['sku']} — {data['name']}  ·  " + "  ·  ".join(c for c in chips if c and c != "—")
        )

    def _populate_dept_combo(self, current_code: str | None) -> None:
        with get_session() as session:
            departments = list_item_departments(session)

        codes = list(departments)
        if current_code and current_code not in codes:
            codes.append(current_code)
        codes.sort()

        self._populating_dept = True
        self._dept_combo.blockSignals(True)
        self._dept_combo.clear()
        self._dept_combo.addItem("—", None)
        for code in codes:
            self._dept_combo.addItem(display_dept(code, self._nickname_map), code)

        if current_code:
            index = self._dept_combo.findData(current_code)
            self._dept_combo.setCurrentIndex(index if index >= 0 else 0)
        else:
            self._dept_combo.setCurrentIndex(0)

        has_departments = bool(codes)
        self._dept_combo.setEnabled(has_departments)
        self._dept_combo.setToolTip("" if has_departments else "Import departments first")
        self._dept_combo.blockSignals(False)
        self._populating_dept = False

    def has_unsaved_on_hand_changes(self) -> bool:
        if self._saved_on_hand is None:
            return False
        return self._on_hand_spin.value() != self._saved_on_hand

    def _update_save_button_state(self) -> None:
        self._save_on_hand_btn.setEnabled(self.has_unsaved_on_hand_changes())

    def _set_on_hand_value(self, qty: int) -> None:
        self._saved_on_hand = qty
        self._on_hand_spin.blockSignals(True)
        self._on_hand_spin.setValue(qty)
        self._on_hand_spin.blockSignals(False)
        self._update_save_button_state()

    def _on_on_hand_spin_changed(self, _value: int) -> None:
        self._update_save_button_state()

    def _on_back_clicked(self) -> None:
        if self.has_unsaved_on_hand_changes():
            answer = QMessageBox.question(
                self,
                "Unsaved Changes",
                "You have unsaved on-hand changes. Leave without saving?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.back_requested.emit()

    def _save_on_hand(self) -> None:
        if self._item_id is None or self._saved_on_hand is None:
            return
        qty = self._on_hand_spin.value()
        try:
            with get_session() as session:
                update_item_on_hand(session, self._item_id, qty)
        except ValueError as exc:
            QMessageBox.critical(self, "Update Failed", str(exc))
            self._reload_item()
            return
        invalidate_summaries()
        self.on_hand_changed.emit()
        self._reload_item()

    def _on_dept_changed(self, _index: int) -> None:
        if self._populating_dept or self._item_id is None:
            return
        new_dept = self._dept_combo.currentData()
        try:
            with get_session() as session:
                update_item_department(session, self._item_id, new_dept)
        except ValueError as exc:
            QMessageBox.critical(self, "Update Failed", str(exc))
            self._reload_item()
            return
        invalidate_summaries()
        self.department_changed.emit()
        self._reload_item()

    def _update_lookback_kpi_titles(self) -> None:
        weeks = self._lookback_weeks
        self._kpi_sales.set_title(sales_period_label(weeks))
        self._kpi_over.set_title(over_qty_label(weeks))
        self._kpi_under.set_title(under_qty_label(weeks))

    def _on_detail_lookback_changed(self) -> None:
        self._lookback_weeks = self._detail_lookback.value()
        self._update_lookback_kpi_titles()
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
                f"{r['qty_sold']:g}",
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
            "Qty Sold",
            "Over Qty",
            "Under Qty",
            "Unit Cost",
        ]
        prompt_export_excel(self, self._export_title, headers, self._history_display, "item_history.xlsx")

    def _reload_item(self) -> None:
        if self._item_id is None:
            return
        with get_session() as session:
            self._nickname_map = load_nickname_map(session)
            self._lookback_weeks = self._detail_lookback.value()
            sync_sales_period_weeks(self._detail_lookback)
            data = build_item_summary(
                session,
                self._item_id,
                self._nickname_map,
                self._lookback_weeks,
            )
        if not data:
            return
        self._set_header_subtitle(data)
        self._populate_dept_combo(data.get("department_code"))
        self._apply_summary(data)

    def show_item(self, sku: str) -> None:
        with get_session() as session:
            self._nickname_map = load_nickname_map(session)
            self._lookback_weeks = self._detail_lookback.value()
            sync_sales_period_weeks(self._detail_lookback)
            item = session.scalar(select(Item).where(Item.sku == sku))
            if not item:
                self._header.set_subtitle("Item not found")
                self._history.clear_data()
                self._saved_on_hand = None
                self._on_hand_spin.setEnabled(False)
                self._save_on_hand_btn.setEnabled(False)
                return
            self._item_id = item.id
            self._sku = sku
            data = build_item_summary(
                session, item.id, self._nickname_map, self._lookback_weeks
            )

        if not data:
            self._header.set_subtitle("Item not found")
            self._saved_on_hand = None
            self._on_hand_spin.setEnabled(False)
            self._save_on_hand_btn.setEnabled(False)
            return

        self._on_hand_spin.setEnabled(True)

        self._export_title = f"Item History — {data['sku']}"
        self._set_header_subtitle(data)
        self._populate_dept_combo(data.get("department_code"))
        self._apply_summary(data)

    def _apply_summary(self, data: dict) -> None:
        self._set_on_hand_value(int(data["on_hand"]))
        self._kpi_value.set_value(f"R {data['stock_value']:,.2f}")
        self._update_lookback_kpi_titles()
        has_charts = bool(data.get("sales_chart_data") or data.get("stock_chart_data"))
        self._kpi_sales.set_value(f"{data['qty_sold']:g}" if has_charts else "—")
        self._kpi_over.set_value(f"{data['over_qty']:g}" if has_charts else "—")
        self._kpi_under.set_value(f"{data['under_qty']:.2f}" if has_charts else "—")
        abc = data.get("abc_class") or "—"
        self._kpi_abc.set_value(abc)
        accent = {"A": "success", "B": "warning", "C": "amber"}.get(abc)
        self._kpi_abc.set_accent(accent)

        sales_chart_data = data.get("sales_chart_data") or {}
        stock_chart_data = data.get("stock_chart_data") or {}
        if has_charts:
            sales_view, labels = build_item_sales_chart(sales_chart_data)
            self._sales_chart.set_chart_view(sales_view, labels)
            self._stock_trend_chart.set_chart_view(build_item_stock_trend_chart(stock_chart_data))
            mix = data.get("sales_mix_pie") or {}
            self._sales_mix_chart.set_chart_view(
                build_pie_chart(mix, f"Sales Mix ({lookback_label(self._lookback_weeks)})"),
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
                f"Stock position: latest import ({data.get('period_label', '—')})\n"
                f"Sales charts: last {lookback_label(self._lookback_weeks)}\n"
                f"Total history periods: {len(data.get('history_rows', []))}"
            )
        else:
            msg = "Import movement reports for item analytics."
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
    dead_stock_requested = Signal(object)
    data_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._dept_filter: str | None = None
        self._nickname_map: dict[str, str] = {}
        self._lookback_weeks = 1

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
        self._list_lookback = create_sales_period_weeks(
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
        self._inv_kpi_dead = KpiCard("Dead Stock", filter_key="dead")
        self._inv_kpi_under.set_accent("danger")
        self._inv_kpi_over.set_accent("warning")
        self._inv_kpi_slow.set_accent("amber")
        self._inv_kpi_dead.set_accent("danger")
        inv_kpis = [
            self._inv_kpi_count,
            self._inv_kpi_value,
            self._inv_kpi_under,
            self._inv_kpi_over,
            self._inv_kpi_slow,
            self._inv_kpi_dead,
        ]
        for i, kpi in enumerate(inv_kpis):
            overview_layout.addWidget(kpi, 0, i)
        for card in (
            self._inv_kpi_under,
            self._inv_kpi_over,
            self._inv_kpi_slow,
            self._inv_kpi_dead,
        ):
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
        self._detail.department_changed.connect(self._on_item_department_changed)
        self._detail.on_hand_changed.connect(self._on_item_on_hand_changed)

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
        self._lookback_weeks = self._list_lookback.value()
        sync_sales_period_weeks(self._list_lookback)
        self._inventory_model.set_lookback_weeks(self._lookback_weeks)
        self._inventory_model.reload()
        self._update_table_subtitle()
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
        elif key == "dead":
            self.dead_stock_requested.emit(dept)

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

    def _refresh_summary(self) -> None:
        with get_session() as session:
            enriched = has_enrichment(session)
            self._nickname_map = load_nickname_map(session)
            self._lookback_weeks = self._list_lookback.value()
            sync_sales_period_weeks(self._list_lookback)
            summary = self._inventory_model.last_summary
            if summary is None:
                summary = build_inventory_list_summary(
                    session,
                    search=self._search.text(),
                    status=self._status_filter.currentText(),
                    has_enrichment=enriched,
                    dept=self._dept_filter,
                    lookback_weeks=self._lookback_weeks,
                )
            if enriched:
                period = get_period_summary_cached(session, self._lookback_weeks)
                if period.get("period_start"):
                    weeks_label = lookback_label(self._lookback_weeks)
                    self._list_header.set_subtitle(
                        f"Last {weeks_label} · {period['period_start']} – {period['period_end']}"
                    )
            departments = list_inventory_departments(
                session,
                search=self._search.text(),
                status=self._status_filter.currentText(),
                has_enrichment=enriched,
                lookback_weeks=self._lookback_weeks,
            )

        self._inv_kpi_count.set_value(f"{summary['item_count']:,}")
        self._inv_kpi_value.set_value(f"R {summary['total_value']:,.2f}")
        if enriched:
            self._inv_kpi_under.set_value(f"R {summary['understock_value']:,.2f}")
            self._inv_kpi_over.set_value(f"R {summary['overstock_value']:,.2f}")
            self._inv_kpi_slow.set_value(f"R {summary['slow_moving_value']:,.2f}")
            self._inv_kpi_dead.set_value(f"R {summary['dead_stock_value']:,.2f}")
            dept_view, dept_labels = build_dept_values_chart(
                summary.get("dept_values", {}), self._nickname_map
            )
            self._inv_dept_chart.set_chart_view(dept_view, dept_labels)
            health = summary.get("stock_health", {})
            self._inv_health_chart.set_chart_view(build_stock_health_chart(health))
        else:
            for kpi in (self._inv_kpi_under, self._inv_kpi_over, self._inv_kpi_slow, self._inv_kpi_dead):
                kpi.set_value("—")
            self._inv_dept_chart.set_chart_view(build_stock_health_chart({}))
            self._inv_health_chart.set_chart_view(build_stock_health_chart({}))

        self._populate_dept_combo(departments)

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

    def _on_item_department_changed(self) -> None:
        self._inventory_model.reload()
        self._update_table_subtitle()
        self._refresh_summary()

    def _on_item_on_hand_changed(self) -> None:
        self._inventory_model.reload()
        self._update_table_subtitle()
        self._refresh_summary()

    def refresh(self) -> None:
        with get_session() as session:
            if not has_initial_baseline(session):
                self._empty.show()
                self._scroll.hide()
                return
            enriched = has_enrichment(session)
            self._nickname_map = load_nickname_map(session)

        self._empty.hide()
        self._scroll.show()
        self._stack.setCurrentWidget(self._list_view)

        if enriched:
            self._list_lookback.setVisible(True)
            self._detail._detail_lookback.setVisible(True)
            self._inv_dept_chart.setVisible(True)
            self._inv_health_chart.setVisible(True)
        else:
            self._list_lookback.setVisible(False)
            self._detail._detail_lookback.setVisible(False)
            self._inv_dept_chart.setVisible(False)
            self._inv_health_chart.setVisible(False)

        self._inventory_model.set_nickname_map(self._nickname_map)
        self._inventory_model.set_lookback_weeks(self._lookback_weeks)
        self._inventory_model.reload()
        self._update_table_subtitle()
        self._refresh_summary()

    def capture_nav_state(self) -> InventoryNavState:
        return InventoryNavState(
            tab=self._tabs.currentIndex(),
            dept_filter=self._dept_filter,
            search_text=self._search.text(),
            status_filter=self._status_filter.currentText(),
        )

    def restore_nav_state(self, state: object | None, *, needs_refresh: bool = False) -> None:
        if not isinstance(state, InventoryNavState):
            return

        self.show_list_view()

        if not needs_refresh and self.capture_nav_state() == state:
            return

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

    def reset_to_base(self) -> None:
        self.show_list_view()
        self._tabs.setCurrentIndex(_OVERVIEW_TAB)
        self._on_inventory_tab_changed(_OVERVIEW_TAB)
