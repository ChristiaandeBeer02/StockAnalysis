"""Reports: slow-moving, ABC, and pivot exploration."""

from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from stock_analysis.analytics.dashboard import list_period_batches
from stock_analysis.analytics.pivot import ROW_FIELDS, VALUE_FIELDS, build_pivot
from stock_analysis.analytics.reports import abc_report, abc_summary, report_period_label, slow_moving_report
from stock_analysis.db.session import get_session, has_enrichment, has_initial_baseline
from stock_analysis.ui.export_dialog import prompt_export_excel, prompt_export_pdf
from stock_analysis.ui.widgets.chart_builders import build_abc_chart, build_pie_chart
from stock_analysis.ui.widgets.chart_tile import ChartTile
from stock_analysis.ui.widgets.data_table import DataTable
from stock_analysis.ui.widgets.empty_state import EmptyState


class ReportsPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        scroll.setWidget(container)

        self._empty = EmptyState(
            "Reports unavailable",
            "Complete Step 2 (Turn + Turnunder enrichment) to unlock slow-moving, ABC, and pivot reports.",
        )
        layout.addWidget(self._empty)

        self._content = QWidget()
        content_layout = QVBoxLayout(self._content)
        content_layout.setContentsMargins(0, 0, 0, 0)

        period_row = QHBoxLayout()
        period_row.addWidget(QLabel("Period:"))
        self._period_combo = QComboBox()
        self._period_combo.setMinimumWidth(280)
        self._period_combo.currentIndexChanged.connect(self._reload_active_tab)
        period_row.addWidget(self._period_combo)
        period_row.addStretch()
        content_layout.addLayout(period_row)

        self._tabs = QTabWidget()
        self._slow_table = DataTable()
        self._slow_table.set_headers(
            ["SKU", "Name", "Dept", "On Hand", "Unit Cost", "Stock Value"]
        )
        self._abc_table = DataTable()
        self._abc_table.set_headers(
            ["SKU", "Name", "Dept", "Qty 90d", "Sales Value", "ABC", "Cumulative %"]
        )
        self._abc_chart = ChartTile("ABC Classification")
        self._pivot_table = DataTable()

        slow_tab = QWidget()
        slow_layout = QVBoxLayout(slow_tab)
        slow_toolbar = QHBoxLayout()
        slow_export_xlsx = QPushButton("Export Excel…")
        slow_export_pdf = QPushButton("Export PDF…")
        slow_export_xlsx.clicked.connect(lambda: self._export_slow("excel"))
        slow_export_pdf.clicked.connect(lambda: self._export_slow("pdf"))
        slow_toolbar.addStretch()
        slow_toolbar.addWidget(slow_export_xlsx)
        slow_toolbar.addWidget(slow_export_pdf)
        slow_layout.addLayout(slow_toolbar)
        slow_layout.addWidget(self._slow_table)

        abc_tab = QWidget()
        abc_layout = QVBoxLayout(abc_tab)
        abc_toolbar = QHBoxLayout()
        abc_export_xlsx = QPushButton("Export Excel…")
        abc_export_pdf = QPushButton("Export PDF…")
        abc_export_xlsx.clicked.connect(lambda: self._export_abc("excel"))
        abc_export_pdf.clicked.connect(lambda: self._export_abc("pdf"))
        abc_toolbar.addStretch()
        abc_toolbar.addWidget(abc_export_xlsx)
        abc_toolbar.addWidget(abc_export_pdf)
        abc_layout.addLayout(abc_toolbar)
        abc_layout.addWidget(self._abc_chart)
        abc_layout.addWidget(self._abc_table)

        pivot_tab = QWidget()
        pivot_layout = QVBoxLayout(pivot_tab)
        pivot_controls = QHBoxLayout()
        self._pivot_row = QComboBox()
        self._pivot_row.addItems(list(ROW_FIELDS.keys()))
        self._pivot_value = QComboBox()
        self._pivot_value.addItems(list(VALUE_FIELDS.keys()))
        pivot_generate = QPushButton("Generate")
        pivot_generate.clicked.connect(self._load_pivot)
        pivot_export_xlsx = QPushButton("Export Excel…")
        pivot_export_xlsx.clicked.connect(lambda: self._export_pivot("excel"))
        pivot_controls.addWidget(QLabel("Group by:"))
        pivot_controls.addWidget(self._pivot_row)
        pivot_controls.addWidget(QLabel("Value:"))
        pivot_controls.addWidget(self._pivot_value)
        pivot_controls.addWidget(pivot_generate)
        pivot_controls.addStretch()
        pivot_controls.addWidget(pivot_export_xlsx)
        pivot_layout.addLayout(pivot_controls)
        pivot_layout.addWidget(self._pivot_table)

        self._tabs.addTab(slow_tab, "Slow Moving")
        self._tabs.addTab(abc_tab, "ABC Analysis")
        self._tabs.addTab(pivot_tab, "Pivot")
        self._tabs.currentChanged.connect(self._reload_active_tab)
        content_layout.addWidget(self._tabs)

        self._content.hide()
        layout.addWidget(self._content)

        self._slow_rows: list[list] = []
        self._abc_rows: list[list] = []
        self._pivot_headers: list[str] = []
        self._pivot_rows: list[list] = []
        self._batch_ids: list[int | None] = []

    def _configure_slow_table_columns(self) -> None:
        header = self._slow_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.resizeSection(0, 100)
        header.resizeSection(2, 72)
        header.resizeSection(3, 80)
        header.resizeSection(4, 100)
        header.resizeSection(5, 100)

    def _configure_abc_table_columns(self) -> None:
        header = self._abc_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.resizeSection(0, 100)
        header.resizeSection(2, 72)
        header.resizeSection(3, 80)
        header.resizeSection(4, 100)
        header.resizeSection(5, 64)
        header.resizeSection(6, 100)

    def _selected_batch_id(self) -> int | None:
        index = self._period_combo.currentIndex()
        if index < 0 or index >= len(self._batch_ids):
            return None
        return self._batch_ids[index]

    def _reload_active_tab(self) -> None:
        tab = self._tabs.currentIndex()
        if tab == 0:
            self._load_slow_moving()
        elif tab == 1:
            self._load_abc()
        elif tab == 2:
            self._load_pivot()

    def _load_slow_moving(self) -> None:
        with get_session() as session:
            report = slow_moving_report(session, self._selected_batch_id())
        self._slow_rows = [
            [
                row["sku"],
                row["name"],
                row["dept"],
                f"{row['on_hand']:g}",
                f"R {row['unit_cost']:,.2f}",
                f"R {row['stock_value']:,.2f}",
            ]
            for row in report
        ]
        self._slow_table.set_rows(self._slow_rows)
        self._configure_slow_table_columns()

    def _load_abc(self) -> None:
        with get_session() as session:
            report = abc_report(session, self._selected_batch_id())
            summary = abc_summary(session, self._selected_batch_id(), report=report)
        self._abc_rows = [
            [
                row["sku"],
                row["name"],
                row["dept"],
                f"{row['qty_sold_90']:g}",
                f"R {row['sales_value']:,.2f}",
                row["abc_class"],
                f"{row['cumulative_pct']:.1f}%",
            ]
            for row in report
        ]
        self._abc_table.set_rows(self._abc_rows)
        self._configure_abc_table_columns()
        if summary:
            self._abc_chart.set_chart_view(build_abc_chart(summary))
        else:
            self._abc_chart.set_chart_view(build_pie_chart({}, "No ABC data available."))

    def _load_pivot(self) -> None:
        with get_session() as session:
            headers, rows = build_pivot(
                session,
                self._pivot_row.currentText(),
                self._pivot_value.currentText(),
                self._selected_batch_id(),
            )
        self._pivot_headers = headers
        self._pivot_rows = rows
        if headers:
            self._pivot_table.set_headers(headers)
            self._pivot_table.set_rows(rows)
        else:
            self._pivot_table.clear_data()

    def _export_slow(self, fmt: str) -> None:
        headers = ["SKU", "Name", "Dept", "On Hand", "Unit Cost", "Stock Value"]
        with get_session() as session:
            title = f"Slow Moving Report — {report_period_label(session, self._selected_batch_id())}"
        if fmt == "excel":
            prompt_export_excel(self, title, headers, self._slow_rows, "slow_moving.xlsx")
        else:
            prompt_export_pdf(self, title, headers, self._slow_rows, "slow_moving.pdf")

    def _export_abc(self, fmt: str) -> None:
        headers = ["SKU", "Name", "Dept", "Qty 90d", "Sales Value", "ABC", "Cumulative %"]
        with get_session() as session:
            title = f"ABC Report — {report_period_label(session, self._selected_batch_id())}"
        if fmt == "excel":
            prompt_export_excel(self, title, headers, self._abc_rows, "abc_report.xlsx")
        else:
            prompt_export_pdf(self, title, headers, self._abc_rows, "abc_report.pdf")

    def _export_pivot(self, fmt: str) -> None:
        if not self._pivot_headers:
            self._load_pivot()
        if not self._pivot_headers:
            return
        with get_session() as session:
            title = f"Pivot — {report_period_label(session, self._selected_batch_id())}"
        prompt_export_excel(self, title, self._pivot_headers, self._pivot_rows, "pivot.xlsx")

    def refresh(self) -> None:
        with get_session() as session:
            has_initial = has_initial_baseline(session)
            enriched = has_enrichment(session)
            batches = list_period_batches(session) if enriched else []

        if not has_initial or not enriched:
            self._empty.show()
            self._content.hide()
            return

        self._empty.hide()
        self._content.show()

        current_batch = self._selected_batch_id()
        self._period_combo.blockSignals(True)
        self._period_combo.clear()
        self._batch_ids = []
        selected_index = 0
        for index, batch in enumerate(batches):
            self._period_combo.addItem(batch["label"])
            self._batch_ids.append(batch["id"])
            if current_batch == batch["id"]:
                selected_index = index
        if batches:
            self._period_combo.setCurrentIndex(selected_index)
        self._period_combo.blockSignals(False)

        if self._tabs.currentIndex() >= 0:
            self._reload_active_tab()
