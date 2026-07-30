"""Compare two stockholding exports side by side."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy import select

from stock_analysis.analytics.department_names import display_dept, list_item_departments, load_nickname_map
from stock_analysis.analytics.stockhold_compare import (
    UNKNOWN_DEPARTMENT,
    StockholdDiffLine,
    compare_stockholdings,
    filter_diff_lines,
    format_value_diff,
)
from stock_analysis.db.models import Item
from stock_analysis.db.session import get_session
from stock_analysis.importers.stockholding_parser import StockholdingParseResult, parse_stockholding_file
from stock_analysis.ui.widgets.data_table import DataTable


class ComparePage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._first_parsed: StockholdingParseResult | None = None
        self._second_parsed: StockholdingParseResult | None = None
        self._diff_lines: list[StockholdDiffLine] = []
        self._nickname_map: dict[str, str] = {}

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

        title = QLabel("Compare")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        self._first_label = QLabel("No file selected")
        self._first_label.setWordWrap(True)
        first_browse = QPushButton("Browse…")
        first_browse.clicked.connect(lambda: self._browse("first"))
        layout.addLayout(self._file_row("First stockhold", self._first_label, first_browse))

        self._second_label = QLabel("No file selected")
        self._second_label.setWordWrap(True)
        second_browse = QPushButton("Browse…")
        second_browse.clicked.connect(lambda: self._browse("second"))
        layout.addLayout(self._file_row("Second stockhold", self._second_label, second_browse))

        action_row = QHBoxLayout()
        self._compare_btn = QPushButton("Compare")
        self._compare_btn.setEnabled(False)
        self._compare_btn.clicked.connect(self._run_compare)
        action_row.addStretch()
        action_row.addWidget(self._compare_btn)
        layout.addLayout(action_row)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Difference:"))
        self._diff_filter = QComboBox()
        self._diff_filter.addItem("Show only differences", True)
        self._diff_filter.addItem("Show all", False)
        self._diff_filter.currentIndexChanged.connect(self._apply_filters)
        filter_row.addWidget(self._diff_filter)

        filter_row.addSpacing(16)
        filter_row.addWidget(QLabel("Dept:"))
        self._dept_filter = QComboBox()
        self._dept_filter.setMinimumWidth(160)
        self._dept_filter.currentIndexChanged.connect(self._apply_filters)
        filter_row.addWidget(self._dept_filter)
        filter_row.addStretch()
        layout.addLayout(filter_row)

        self._table = DataTable()
        self._table.set_headers(["Code", "Description", "On Hand", "Value"])
        self._table.enable_viewport_scrolling()
        layout.addWidget(self._table, stretch=1)

        self._populate_dept_combo()

    @staticmethod
    def _file_row(caption: str, path_label: QLabel, browse_btn: QPushButton) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(QLabel(caption))
        row.addWidget(path_label, stretch=1)
        row.addWidget(browse_btn)
        return row

    def _browse(self, which: str) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Stockholding CSV",
            "",
            "CSV Files (*.csv);;All Files (*)",
        )
        if not path:
            return

        parsed = parse_stockholding_file(Path(path))
        label = f"{Path(path).name} ({len(parsed.rows):,} rows parsed)"
        if which == "first":
            self._first_parsed = parsed
            self._first_label.setText(label)
        else:
            self._second_parsed = parsed
            self._second_label.setText(label)

        self._compare_btn.setEnabled(
            self._first_parsed is not None and self._second_parsed is not None
        )

    def _populate_dept_combo(self) -> None:
        with get_session() as session:
            departments = list_item_departments(session)
            self._nickname_map = load_nickname_map(session)

        current = self._dept_filter.currentData()
        self._dept_filter.blockSignals(True)
        self._dept_filter.clear()
        self._dept_filter.addItem("All departments", None)
        self._dept_filter.addItem("Unknown", UNKNOWN_DEPARTMENT)
        for dept in departments:
            self._dept_filter.addItem(display_dept(dept, self._nickname_map), dept)
        if current is not None:
            index = self._dept_filter.findData(current)
            if index >= 0:
                self._dept_filter.setCurrentIndex(index)
        self._dept_filter.blockSignals(False)

    def _run_compare(self) -> None:
        if self._first_parsed is None or self._second_parsed is None:
            return

        with get_session() as session:
            dept_by_sku = dict(session.execute(select(Item.sku, Item.department)).all())

        self._diff_lines = compare_stockholdings(
            self._first_parsed,
            self._second_parsed,
            dept_by_sku=dept_by_sku,
        )
        self._diff_filter.setCurrentIndex(0)
        self._apply_filters()

    def _apply_filters(self) -> None:
        if not self._diff_lines:
            self._table.clear_data()
            return

        differences_only = bool(self._diff_filter.currentData())
        dept_filter = self._dept_filter.currentData()
        visible = filter_diff_lines(
            self._diff_lines,
            differences_only=differences_only,
            dept_filter=dept_filter,
        )
        self._table.set_rows(
            [
                [
                    line.sku,
                    line.name,
                    f"{line.on_hand_diff:+g}",
                    format_value_diff(line.value_diff),
                ]
                for line in visible
            ]
        )

    def reset_to_base(self) -> None:
        self._first_parsed = None
        self._second_parsed = None
        self._diff_lines = []
        self._first_label.setText("No file selected")
        self._second_label.setText("No file selected")
        self._compare_btn.setEnabled(False)
        self._diff_filter.setCurrentIndex(0)
        self._dept_filter.setCurrentIndex(0)
        self._table.clear_data()

    def refresh(self) -> None:
        self._populate_dept_combo()
        if self._diff_lines:
            self._apply_filters()
