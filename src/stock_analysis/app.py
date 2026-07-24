"""QApplication setup and styling."""

import sys

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from stock_analysis.config import APP_NAME
from stock_analysis.db.session import init_db
from stock_analysis.ui.main_window import MainWindow

STYLESHEET = """
QMainWindow {
    background-color: #f5f6f8;
}
QWidget {
    background-color: #f5f6f8;
    color: #1f2937;
    font-size: 13px;
}
#sidebar {
    background-color: #243b64;
    border-right: 1px solid #35527a;
}
#sidebar QLabel {
    color: #f1f5f9;
    background: transparent;
}
#appTitle {
    color: #ffffff;
    font-size: 16px;
    font-weight: 600;
    padding: 8px 4px 16px 4px;
}
#navList {
    background: transparent;
    border: none;
    color: #e8edf5;
    outline: none;
}
#navList::item {
    padding: 10px 12px;
    border-radius: 6px;
    color: #e8edf5;
}
#navList::item:selected {
    background-color: #4a6fa5;
    color: #ffffff;
}
#navList::item:hover {
    background-color: #35527a;
    color: #ffffff;
}
#kpiCard {
    background-color: #ffffff;
    border: 1px solid #e0e3eb;
    border-radius: 8px;
}
#kpiCard[selected="true"] {
    border: 2px solid #118DFF;
}
#kpiCard[accent="danger"] {
    border-left: 4px solid #d13438;
}
#kpiCard[accent="warning"] {
    border-left: 4px solid #E66C37;
}
#kpiCard[accent="amber"] {
    border-left: 4px solid #D9B300;
}
#kpiCard[accent="success"] {
    border-left: 4px solid #107c10;
}
#kpiDeltaUp {
    color: #107c10;
    font-size: 11px;
}
#kpiDeltaDown {
    color: #d13438;
    font-size: 11px;
}
#kpiDelta {
    color: #6b7280;
    font-size: 11px;
}
#dashboardCanvas {
    background-color: #eaeaea;
}
#dashboardTile {
    background-color: #ffffff;
    border: 1px solid #d9d9d9;
    border-radius: 4px;
}
#tileTitle {
    font-size: 13px;
    font-weight: 600;
    color: #252423;
}
#tileSubtitle {
    font-size: 11px;
    color: #605e5c;
}
#slicerBar {
    background: transparent;
}
QPushButton#slicerChip {
    background: #ffffff;
    border: 1px solid #8a8886;
    border-radius: 2px;
    padding: 4px 12px;
    color: #252423;
}
QPushButton#slicerChip:checked {
    background: #118DFF;
    color: #ffffff;
    border-color: #118DFF;
}
#reportHeader {
    background-color: #ffffff;
    border: 1px solid #d9d9d9;
    border-radius: 4px;
}
#kpiTitle {
    color: #6b7280;
    font-size: 12px;
}
#kpiValue {
    font-size: 22px;
    font-weight: 600;
    color: #111827;
}
#banner {
    background-color: #fff8e6;
    border: 1px solid #f0d78c;
    border-radius: 8px;
    padding: 8px;
}
#banner QLabel {
    color: #3d3422;
}
#emptyTitle {
    font-size: 20px;
    font-weight: 600;
    color: #1f2937;
}
#emptyMessage {
    color: #6b7280;
    margin-bottom: 12px;
}
#pageTitle {
    font-size: 18px;
    font-weight: 600;
    color: #1f2937;
}
#placeholder {
    color: #6b7280;
    padding: 24px;
    background: #ffffff;
    border: 1px dashed #d1d5db;
    border-radius: 8px;
}
QPushButton {
    background-color: #2563eb;
    color: #ffffff;
    border: none;
    padding: 8px 16px;
    border-radius: 6px;
}
QPushButton:hover {
    background-color: #1d4ed8;
    color: #ffffff;
}
QPushButton:disabled {
    background-color: #9ca3af;
    color: #f3f4f6;
}
QComboBox {
    padding: 6px 8px;
    border: 1px solid #d1d5db;
    border-radius: 6px;
    background: white;
    color: #374151;
    min-width: 120px;
}
QComboBox QAbstractItemView {
    background: white;
    color: #374151;
    selection-background-color: #dbeafe;
    selection-color: #1e40af;
}
QLineEdit {
    padding: 8px;
    border: 1px solid #d1d5db;
    border-radius: 6px;
    background: white;
    color: #1f2937;
}
QTableView, QTableView#dataTable {
    background-color: #ffffff;
    alternate-background-color: #f8fafc;
    color: #374151;
    border: 1px solid #e0e3eb;
    border-radius: 8px;
    gridline-color: #e5e7eb;
    selection-background-color: #dbeafe;
    selection-color: #1e40af;
    outline: none;
}
QTableView::item {
    padding: 6px 8px;
    color: #374151;
    background-color: #ffffff;
}
QTableView::item:alternate {
    background-color: #f8fafc;
    color: #374151;
}
QTableView::item:selected {
    background-color: #dbeafe;
    color: #1e40af;
}
QTableView::item:selected:active {
    background-color: #bfdbfe;
    color: #1e3a8a;
}
QTableView::item:hover {
    background-color: #eff6ff;
    color: #374151;
}
QHeaderView::section {
    background-color: #f1f5f9;
    color: #475569;
    padding: 8px;
    border: none;
    border-bottom: 1px solid #e2e8f0;
    font-weight: 600;
}
QHeaderView::section:hover {
    background-color: #e2e8f0;
    color: #334155;
}
QStatusBar {
    background-color: #eef1f6;
    color: #4b5563;
}
"""


def create_app(argv: list[str] | None = None) -> QApplication:
    app = QApplication(argv or sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    font = QFont("Segoe UI", 10)
    app.setFont(font)
    app.setStyleSheet(STYLESHEET)
    return app


def run() -> int:
    init_db()
    app = create_app()
    window = MainWindow()
    window.show()
    return app.exec()
