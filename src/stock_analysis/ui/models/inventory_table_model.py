"""Inventory table model backed by SQLite queries."""

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt

from stock_analysis.analytics.inventory_queries import fetch_inventory_rows, inventory_headers
from stock_analysis.analytics.lookback import DEFAULT_LOOKBACK, get_lookback_days
from stock_analysis.db.session import get_session, has_enrichment


class InventoryTableModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._lookback_days = DEFAULT_LOOKBACK
        self._headers = inventory_headers(self._lookback_days)
        self._rows: list[list[str]] = []
        self._search = ""
        self._status = "Active"
        self._dept: str | None = None
        self._batch_id: int | None = None
        self._has_enrichment = False
        self._nickname_map: dict[str, str] = {}

    def set_nickname_map(self, nickname_map: dict[str, str]) -> None:
        self._nickname_map = nickname_map

    def set_batch_id(self, batch_id: int | None) -> None:
        self._batch_id = batch_id

    def set_lookback_days(self, lookback_days: int) -> None:
        self._lookback_days = lookback_days
        self._headers = inventory_headers(lookback_days)

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
        if role == Qt.ItemDataRole.UserRole + 1:
            if isinstance(value, (int, float)):
                return float(value)
            if isinstance(value, str) and value.replace(".", "", 1).isdigit() and value not in ("", "—"):
                return float(value)
            return 0.0
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
        self._rows.sort(key=lambda r: r[column].lower() if isinstance(r[column], str) else r[column], reverse=reverse)
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
            lookback_days = self._lookback_days or get_lookback_days(session)
            rows = fetch_inventory_rows(
                session,
                search=self._search,
                status=self._status,
                has_enrichment=self._has_enrichment,
                dept=self._dept,
                nickname_map=self._nickname_map,
                batch_id=self._batch_id,
                lookback_days=lookback_days,
            )
        self.beginResetModel()
        self._headers = inventory_headers(lookback_days)
        self._rows = rows
        self.endResetModel()
