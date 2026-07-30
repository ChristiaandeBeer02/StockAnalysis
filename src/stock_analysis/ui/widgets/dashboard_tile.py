"""Power BI-style visual tile container."""

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget


class DashboardTile(QFrame):
    def __init__(
        self,
        title: str,
        subtitle: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("dashboardTile")
        self.setFrameShape(QFrame.Shape.StyledPanel)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 12)
        root.setSpacing(8)

        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self._title = QLabel(title)
        self._title.setObjectName("tileTitle")
        self._subtitle = QLabel(subtitle)
        self._subtitle.setObjectName("tileSubtitle")
        self._subtitle.setVisible(bool(subtitle))
        titles.addWidget(self._title)
        titles.addWidget(self._subtitle)
        header.addLayout(titles, stretch=1)

        self._actions = QHBoxLayout()
        self._actions.setSpacing(6)
        header.addLayout(self._actions)
        root.addLayout(header)

        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(0)
        root.addWidget(self._content, stretch=1)

    def set_subtitle(self, text: str) -> None:
        self._subtitle.setText(text)
        self._subtitle.setVisible(bool(text))

    def set_title(self, text: str) -> None:
        self._title.setText(text)

    def add_action(self, widget: QWidget) -> None:
        self._actions.addWidget(widget)

    def set_content(self, widget: QWidget) -> None:
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
        widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._content_layout.addWidget(widget, stretch=1)
