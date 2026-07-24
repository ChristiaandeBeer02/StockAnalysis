"""Stock upload and comparison panel."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from stock_analysis.baseline.manager import get_stock_take_history, get_stock_take_variance_lines
from stock_analysis.db.session import get_session, has_initial_baseline
from stock_analysis.ui.export_dialog import prompt_export_excel, prompt_export_pdf
from stock_analysis.ui.widgets.data_table import DataTable
from stock_analysis.ui.widgets.empty_state import EmptyState
from stock_analysis.ui.widgets.kpi_card import KpiCard
from stock_analysis.ui.wizards.stock_take_import_wizard import run_stock_take_wizard


class StockTakePage(QWidget):
    data_changed = Signal()

    def __init__(self, parent=None, *, embedded: bool = False):
        super().__init__(parent)
        self._embedded = embedded
        self._variance_rows: list[list] = []

        if embedded:
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            root = QVBoxLayout(self)
            root.setContentsMargins(0, 0, 0, 0)
            root.setSpacing(16)
            self._build_content(root)
            root.setStretchFactor(self._history_table, 1)
            for table in (self._variance_table, self._history_table):
                table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            return

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)

        container = QWidget()
        self._layout = QVBoxLayout(container)
        self._layout.setContentsMargins(24, 24, 24, 24)
        self._layout.setSpacing(16)
        scroll.setWidget(container)

        self._empty = EmptyState(
            "No baseline yet",
            "Complete Step 1 (initial baseline import) before uploading a stock take.",
        )
        self._layout.addWidget(self._empty)

        self._content = QWidget()
        content_layout = QVBoxLayout(self._content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(16)
        self._build_content(content_layout)
        self._content.hide()
        self._layout.addWidget(self._content)
        self._layout.addStretch()

    def _build_content(self, layout: QVBoxLayout) -> None:
        top = QHBoxLayout()
        title = QLabel("Stock Upload & Comparison")
        title.setObjectName("pageTitle")
        self._upload_btn = QPushButton("Upload Stock Take CSV…")
        self._upload_btn.clicked.connect(self._open_wizard)
        top.addWidget(title)
        top.addStretch()
        top.addWidget(self._upload_btn)
        layout.addLayout(top)

        kpi_grid = QGridLayout()
        self._kpi_items = KpiCard("Items Compared")
        self._kpi_variances = KpiCard("Variances")
        self._kpi_shrinkage = KpiCard("Shrinkage")
        self._kpi_overage = KpiCard("Overage")
        kpi_grid.addWidget(self._kpi_items, 0, 0)
        kpi_grid.addWidget(self._kpi_variances, 0, 1)
        kpi_grid.addWidget(self._kpi_shrinkage, 0, 2)
        kpi_grid.addWidget(self._kpi_overage, 0, 3)
        layout.addLayout(kpi_grid)

        variance_label = QLabel("Latest Session Variances")
        variance_label.setObjectName("pageTitle")
        variance_header = QHBoxLayout()
        variance_header.addWidget(variance_label)
        variance_header.addStretch()
        export_xlsx = QPushButton("Export Excel…")
        export_pdf = QPushButton("Export PDF…")
        export_xlsx.clicked.connect(lambda: self._export_variances("excel"))
        export_pdf.clicked.connect(lambda: self._export_variances("pdf"))
        variance_header.addWidget(export_xlsx)
        variance_header.addWidget(export_pdf)
        layout.addLayout(variance_header)
        self._variance_table = DataTable()
        self._variance_table.set_headers(
            ["SKU", "Name", "Baseline", "Counted", "Variance", "Value", "Type"]
        )
        layout.addWidget(self._variance_table)

        history_label = QLabel("Session History")
        history_label.setObjectName("pageTitle")
        layout.addWidget(history_label)
        self._history_table = DataTable()
        self._history_table.set_headers(
            ["Date", "File", "Variances", "Shrinkage", "Overage", "Applied"]
        )
        layout.addWidget(self._history_table)

    def _export_variances(self, fmt: str) -> None:
        headers = ["SKU", "Name", "Baseline", "Counted", "Variance", "Value", "Type"]
        title = "Stock Take Variances"
        if fmt == "excel":
            prompt_export_excel(self, title, headers, self._variance_rows, "stock_take_variances.xlsx")
        else:
            prompt_export_pdf(self, title, headers, self._variance_rows, "stock_take_variances.pdf")

    def _open_wizard(self) -> None:
        if run_stock_take_wizard(self):
            self.refresh()
            self.data_changed.emit()

    def _load_data(self) -> None:
        with get_session() as session:
            history = get_stock_take_history(session)
            self._history_table.set_rows(
                [
                    [
                        row["date"],
                        row["file"],
                        str(row["variances"]),
                        f"R {row['shrinkage']:,.2f}",
                        f"R {row['overage']:,.2f}",
                        row["applied_at"],
                    ]
                    for row in history
                ]
            )

            has_sessions = len(history) > 0

            if not has_sessions:
                self._kpi_items.set_value("—")
                self._kpi_variances.set_value("—")
                self._kpi_shrinkage.set_value("—")
                self._kpi_overage.set_value("—")
                self._variance_rows = []
                self._variance_table.clear_data()
            else:
                latest = history[0]
                self._kpi_items.set_value(f"{latest['total_items']:,}")
                self._kpi_variances.set_value(f"{latest['variances']:,}")
                self._kpi_shrinkage.set_value(f"R {latest['shrinkage']:,.2f}")
                self._kpi_overage.set_value(f"R {latest['overage']:,.2f}")

                lines = get_stock_take_variance_lines(session, latest["id"])
                self._variance_rows = [
                    [
                        line["sku"],
                        line["name"],
                        f"{line['baseline_qty']:g}",
                        f"{line['counted_qty']:g}",
                        f"{line['variance']:+g}",
                        f"R {line['variance_value']:,.2f}",
                        line["line_type"].replace("_", " "),
                    ]
                    for line in lines
                ]
                self._variance_table.set_rows(self._variance_rows)

        if self._embedded:
            self._fit_embedded_tables()
        else:
            self._fit_tables()

    def _fit_tables(self) -> None:
        for table in (self._variance_table, self._history_table):
            table.resize_height_to_contents()

    def _fit_embedded_tables(self) -> None:
        self._variance_table.resize_height_to_contents()
        if self._history_table.model().rowCount() > 0:
            self._history_table.resize_height_to_contents()
        else:
            self._history_table.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
            )
            self._history_table.setMinimumHeight(160)
            self._history_table.setMaximumHeight(16_777_215)
        self.updateGeometry()

    def refresh(self) -> None:
        with get_session() as session:
            ready = has_initial_baseline(session)

        if self._embedded:
            if not ready:
                self.hide()
                return
            self.show()
            self._load_data()
            return

        if not ready:
            self._empty.show()
            self._content.hide()
            return

        self._empty.hide()
        self._content.show()
        self._load_data()
