"""Sortable data table widget."""

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QPalette, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QSizePolicy, QTableView


class DataTable(QTableView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("dataTable")
        self._viewport_scroll = False
        self._apply_palette()
        self._model = QStandardItemModel(self)
        self.setModel(self._model)
        self.setSortingEnabled(True)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.horizontalHeader().setStretchLastSection(True)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.verticalHeader().setVisible(False)
        self._owns_model = True

    def minimumSizeHint(self):
        if self._viewport_scroll:
            header_height = self.horizontalHeader().height() or 32
            row_height = self.rowHeight(0) if self.model().rowCount() else 28
            return QSize(0, header_height + row_height + 4)
        return super().minimumSizeHint()

    def enable_viewport_scrolling(self) -> None:
        """Scroll inside the table instead of expanding parent layouts."""
        self._viewport_scroll = True
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setMinimumHeight(0)

    def _apply_palette(self) -> None:
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#f8fafc"))
        palette.setColor(QPalette.ColorRole.Text, QColor("#374151"))
        palette.setColor(QPalette.ColorRole.Highlight, QColor("#dbeafe"))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#1e40af"))
        self.setPalette(palette)

    def set_external_model(self, model) -> None:
        if self._owns_model:
            self._model.deleteLater()
        self._model = model
        self._owns_model = False
        self.setModel(model)

    def set_headers(self, headers: list[str]) -> None:
        self._model.setHorizontalHeaderLabels(headers)

    def set_rows(self, rows: list[list[str]]) -> None:
        sorting = self.isSortingEnabled()
        self.setSortingEnabled(False)
        self._model.setRowCount(0)
        for row_data in rows:
            items = []
            for value in row_data:
                item = QStandardItem(str(value))
                item.setEditable(False)
                if isinstance(value, (int, float)) or (
                    isinstance(value, str) and value.replace(".", "", 1).isdigit()
                ):
                    item.setData(
                        float(value) if value not in ("", "—") else 0,
                        Qt.ItemDataRole.UserRole,
                    )
                items.append(item)
            self._model.appendRow(items)
        self.setSortingEnabled(sorting)

    def clear_data(self) -> None:
        self._model.setRowCount(0)

    def resize_height_to_contents(self, *, max_rows: int | None = None) -> None:
        """Grow vertically to fit rows; parent scroll area handles overflow."""
        header_height = self.horizontalHeader().height()
        row_count = self.model().rowCount()
        visible_rows = row_count if max_rows is None else min(row_count, max_rows)
        if visible_rows == 0:
            height = header_height + 28
        else:
            rows_height = sum(self.rowHeight(row) for row in range(visible_rows))
            height = header_height + rows_height + 2
        self.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            if max_rows is None or row_count <= max_rows
            else Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(height)

    def row_key_at(self, row: int, column: int = 0) -> str | None:
        model = self.model()
        index = model.index(row, column)
        if not index.isValid():
            return None
        sku = model.data(index, Qt.ItemDataRole.UserRole)
        if sku:
            return str(sku)
        return model.data(index, Qt.ItemDataRole.DisplayRole)
