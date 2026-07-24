"""Report header band with title, subtitle, and optional controls."""

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget


class ReportHeader(QFrame):
    def __init__(
        self,
        title: str,
        subtitle: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("reportHeader")
        self.setFrameShape(QFrame.Shape.StyledPanel)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)

        left = QVBoxLayout()
        left.setSpacing(2)
        self._title = QLabel(title)
        self._title.setObjectName("pageTitle")
        self._subtitle = QLabel(subtitle)
        self._subtitle.setObjectName("tileSubtitle")
        self._subtitle.setVisible(bool(subtitle))
        left.addWidget(self._title)
        left.addWidget(self._subtitle)
        layout.addLayout(left, stretch=1)

        self._controls = QHBoxLayout()
        self._controls.setSpacing(8)
        layout.addLayout(self._controls)

    def set_subtitle(self, text: str) -> None:
        self._subtitle.setText(text)
        self._subtitle.setVisible(bool(text))

    def add_control(self, widget: QWidget) -> None:
        self._controls.addWidget(widget)

    def add_stretch(self) -> None:
        self._controls.addStretch()
