"""Department nickname assignment page."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from stock_analysis.analytics.department_names import (
    list_imported_departments,
    load_nickname_map,
    save_nicknames,
)
from stock_analysis.db.session import get_session
from stock_analysis.ui.widgets.empty_state import EmptyState


class _DepartmentRow(QWidget):
    def __init__(self, code: str, nickname: str, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 6)
        layout.setSpacing(16)

        code_label = QLabel(code)
        code_label.setMinimumWidth(140)
        code_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        code_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)

        self.nickname_edit = QLineEdit(nickname)
        self.nickname_edit.setMinimumHeight(40)
        self.nickname_edit.setPlaceholderText("Enter nickname…")
        self.nickname_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout.addWidget(code_label)
        layout.addWidget(self.nickname_edit, 1)


class DepartmentNamingPage(QWidget):
    back_requested = Signal()
    nicknames_saved = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        header = QHBoxLayout()
        back_btn = QPushButton("← Settings")
        back_btn.clicked.connect(self.back_requested.emit)
        header.addWidget(back_btn)
        header.addStretch()
        layout.addLayout(header)

        title = QLabel("Department Naming")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        description = QLabel(
            "Assign friendly names to imported department codes. "
            "Nicknames are shown throughout the app instead of the original codes."
        )
        description.setWordWrap(True)
        layout.addWidget(description)

        self._empty = EmptyState(
            "No departments found",
            "Import enrichment data to see departments here.",
        )
        layout.addWidget(self._empty)

        self._list_panel = QWidget()
        list_panel_layout = QVBoxLayout(self._list_panel)
        list_panel_layout.setContentsMargins(0, 0, 0, 0)
        list_panel_layout.setSpacing(0)

        column_header = QHBoxLayout()
        column_header.setContentsMargins(0, 0, 0, 8)
        column_header.setSpacing(16)
        code_header = QLabel("Imported Code")
        code_header.setMinimumWidth(140)
        code_header.setStyleSheet("font-weight: 600; color: #475569;")
        nickname_header = QLabel("Nickname")
        nickname_header.setStyleSheet("font-weight: 600; color: #475569;")
        column_header.addWidget(code_header)
        column_header.addWidget(nickname_header, 1)
        list_panel_layout.addLayout(column_header)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet("color: #e2e8f0;")
        list_panel_layout.addWidget(divider)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._rows_container = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_container)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(0)
        self._rows_layout.addStretch()
        scroll.setWidget(self._rows_container)
        list_panel_layout.addWidget(scroll, 1)

        layout.addWidget(self._list_panel, 1)

        actions = QHBoxLayout()
        actions.addStretch()
        self._save_btn = QPushButton("Save")
        self._save_btn.clicked.connect(self._save)
        actions.addWidget(self._save_btn)
        layout.addLayout(actions)

        self._departments: list[str] = []
        self._row_widgets: dict[str, _DepartmentRow] = {}

    def _clear_rows(self) -> None:
        while self._rows_layout.count() > 1:
            item = self._rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._row_widgets.clear()

    def refresh(self) -> None:
        with get_session() as session:
            self._departments = list_imported_departments(session)
            nickname_map = load_nickname_map(session)

        has_departments = bool(self._departments)
        self._empty.setVisible(not has_departments)
        self._list_panel.setVisible(has_departments)
        self._save_btn.setEnabled(has_departments)

        self._clear_rows()
        stretch_index = self._rows_layout.count() - 1
        for code in self._departments:
            row = _DepartmentRow(code, nickname_map.get(code, ""))
            self._row_widgets[code] = row
            self._rows_layout.insertWidget(stretch_index, row)

    def _save(self) -> None:
        mapping = {code: row.nickname_edit.text() for code, row in self._row_widgets.items()}
        with get_session() as session:
            save_nicknames(session, mapping)
        self.nicknames_saved.emit()
