"""Inventory table model backed by SQLite queries."""

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt

from stock_analysis.analytics.inventory_queries import inventory_headers, load_inventory_view_data
from stock_analysis.analytics.lookback import DEFAULT_LOOKBACK_WEEKS, get_lookback_weeks
from stock_analysis.db.session import get_session, has_enrichment
from stock_analysis.ui.sort_helpers import SORT_ROLE, cell_sort_value


class InventoryTableModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._lookback_weeks = DEFAULT_LOOKBACK_WEEKS
        self._headers = inventory_headers(self._lookback_weeks)
        self._rows: list[list[str]] = []
        self._search = ""
        self._status = "Active"
        self._dept: str | None = None
        self._has_enrichment = False
        self._nickname_map: dict[str, str] = {}
        self._last_summary: dict | None = None

    @property
    def last_summary(self) -> dict | None:
        return self._last_summary

    def set_nickname_map(self, nickname_map: dict[str, str]) -> None:
        self._nickname_map = nickname_map

    def set_lookback_weeks(self, lookback_weeks: int) -> None:
        self._lookback_weeks = lookback_weeks
        self._headers = inventory_headers(lookback_weeks)

    def rowCount(self, parent=QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._headers)

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row, col = index.row(), index.column()
        if row < 0 or row >= len(self._rows) or col < 0 or col >= len(self._headers):
            return None
        value = self._rows[row][col]
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            return value
        if role == Qt.ItemDataRole.UserRole and col == 0:
            return self._rows[row][0]
        if role in (Qt.ItemDataRole.UserRole + 1, Qt.ItemDataRole(SORT_ROLE)):
            sort_val = cell_sort_value(value)
            if isinstance(sort_val, float):
                return sort_val
            return sort_val
        return None

    def headerData(self, section: int, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(self._headers):
            return self._headers[section]
        return None

    def sort(self, column: int, order=Qt.SortOrder.AscendingOrder) -> None:
        self.layoutAboutToBeChanged.emit()
        reverse = order == Qt.SortOrder.DescendingOrder
        self._rows.sort(key=lambda r: cell_sort_value(r[column]), reverse=reverse)
        self.layoutChanged.emit()

    @property
    def total_count(self) -> int:
        return len(self._rows)

    def sku_at(self, row: int) -> str | None:
        if 0 <= row < len(self._rows):
            return self._rows[row][0]
        return None

    def set_filters(self, search: str, status: str) -> None:
        self.apply_filters(search, status, self._dept)

    def set_dept_filter(self, dept: str | None) -> None:
        self.apply_filters(self._search, self._status, dept)

    def apply_filters(self, search: str, status: str, dept: str | None, *, reload: bool = True) -> None:
        self._search = search.strip()
        self._status = status
        self._dept = dept
        if reload:
            self.reload()

    def reload(self) -> None:
        with get_session() as session:
            self._has_enrichment = has_enrichment(session)
            lookback_weeks = self._lookback_weeks or get_lookback_weeks(session)
            rows, summary = load_inventory_view_data(
                session,
                search=self._search,
                status=self._status,
                has_enrichment=self._has_enrichment,
                dept=self._dept,
                nickname_map=self._nickname_map,
                lookback_weeks=lookback_weeks,
            )
            self._last_summary = summary
        self.beginResetModel()
        self._headers = inventory_headers(lookback_weeks)
        self._rows = rows
        self.endResetModel()
