"""Reusable KPI summary card."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout


class KpiCard(QFrame):
    clicked = Signal()

    def __init__(self, title: str, value: str = "—", parent=None, *, filter_key: str | None = None):
        super().__init__(parent)
        self.setObjectName("kpiCard")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._filter_key = filter_key

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(4)

        self._title = QLabel(title)
        self._title.setObjectName("kpiTitle")
        self._value = QLabel(value)
        self._value.setObjectName("kpiValue")
        self._delta = QLabel("")
        self._delta.setObjectName("kpiDelta")
        self._delta.hide()

        layout.addWidget(self._title)
        layout.addWidget(self._value)
        layout.addWidget(self._delta)

    @property
    def filter_key(self) -> str | None:
        return self._filter_key

    def set_value(self, value: str) -> None:
        self._value.setText(value)

    def set_title(self, title: str) -> None:
        self._title.setText(title)

    def set_delta(self, text: str, direction: str = "neutral") -> None:
        if not text or text == "—":
            self._delta.hide()
            return
        self._delta.setText(text)
        self._delta.setProperty("direction", direction)
        self._delta.setObjectName(
            "kpiDeltaUp" if direction == "up" else "kpiDeltaDown" if direction == "down" else "kpiDelta"
        )
        self._delta.style().unpolish(self._delta)
        self._delta.style().polish(self._delta)
        self._delta.show()

    def set_accent(self, accent: str | None) -> None:
        self.setProperty("accent", accent or "")
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)
