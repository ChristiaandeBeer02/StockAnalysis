"""Horizontal department slicer chips."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget


class SlicerBar(QFrame):
    filter_changed = Signal(object)  # str | None

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("slicerBar")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(6)

        label = QLabel("Department:")
        label.setObjectName("tileSubtitle")
        layout.addWidget(label)

        self._chip_layout = QHBoxLayout()
        self._chip_layout.setSpacing(6)
        layout.addLayout(self._chip_layout)
        layout.addStretch()

        self._chips: dict[str, QPushButton] = {}
        self._active: str | None = None

        self._all_btn = QPushButton("All")
        self._all_btn.setObjectName("slicerChip")
        self._all_btn.setCheckable(True)
        self._all_btn.setChecked(True)
        self._all_btn.clicked.connect(self._on_all)
        self._chip_layout.addWidget(self._all_btn)

    def set_departments(self, departments: list[str]) -> None:
        for btn in self._chips.values():
            btn.deleteLater()
        self._chips.clear()
        self._active = None
        self._all_btn.setChecked(True)

        for dept in sorted(departments):
            btn = QPushButton(dept)
            btn.setObjectName("slicerChip")
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked, d=dept: self._on_dept(d))
            self._chip_layout.addWidget(btn)
            self._chips[dept] = btn

    def set_active_dept(self, dept: str | None) -> None:
        self._active = dept
        self._all_btn.setChecked(dept is None)
        for name, btn in self._chips.items():
            btn.setChecked(name == dept)

    def _on_all(self) -> None:
        self.set_active_dept(None)
        self.filter_changed.emit(None)

    def _on_dept(self, dept: str) -> None:
        if self._active == dept:
            self._on_all()
            return
        self.set_active_dept(dept)
        self.filter_changed.emit(dept)
