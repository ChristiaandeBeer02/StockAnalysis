"""Dialog for selecting the weekly movement closing day."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
)

from stock_analysis.analytics.movement_periods import WEEKDAY_NAMES
from stock_analysis.db.session import get_session, set_movement_closing_weekday

DEFAULT_CLOSING_WEEKDAY = 5  # Saturday


class MovementClosingDayDialog(QDialog):
    def __init__(self, parent=None, *, initial_weekday: int | None = None):
        super().__init__(parent)
        self.setWindowTitle("Weekly Movement Closing Day")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)

        intro = QLabel(
            "Movement reports cover one week from the day after your closing day "
            "through your closing day.\n\n"
            "For example, if you close on Saturday, each movement period runs "
            "Sunday through Saturday."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._weekday_combo = QComboBox()
        for name in WEEKDAY_NAMES:
            self._weekday_combo.addItem(name)
        default = initial_weekday if initial_weekday is not None else DEFAULT_CLOSING_WEEKDAY
        self._weekday_combo.setCurrentIndex(default)
        layout.addWidget(self._weekday_combo)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_weekday(self) -> int:
        return self._weekday_combo.currentIndex()


def run_closing_day_dialog(parent=None, *, initial_weekday: int | None = None) -> int | None:
    dialog = MovementClosingDayDialog(parent, initial_weekday=initial_weekday)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    weekday = dialog.selected_weekday()
    with get_session() as session:
        set_movement_closing_weekday(session, weekday)
    return weekday
