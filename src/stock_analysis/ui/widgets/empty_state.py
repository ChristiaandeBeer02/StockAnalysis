"""Empty state placeholder panel."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget


class EmptyState(QWidget):
    action_clicked = Signal()

    def __init__(
        self,
        title: str,
        message: str,
        action_label: str | None = None,
        parent=None,
    ):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(48, 48, 48, 48)
        layout.addStretch()

        title_label = QLabel(title)
        title_label.setObjectName("emptyTitle")
        title_label.setWordWrap(True)
        layout.addWidget(title_label)

        message_label = QLabel(message)
        message_label.setObjectName("emptyMessage")
        message_label.setWordWrap(True)
        layout.addWidget(message_label)

        self._action = None
        if action_label:
            self._action = QPushButton(action_label)
            self._action.clicked.connect(self.action_clicked.emit)
            layout.addWidget(self._action)

        layout.addStretch()
