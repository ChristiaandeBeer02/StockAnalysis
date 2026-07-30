"""Application settings page."""

from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from stock_analysis.analytics.cache import invalidate_summaries
from stock_analysis.analytics.queries import (
    get_stock_buffer_pct_range,
    set_stock_buffer_pct_range,
)
from stock_analysis.analytics.dashboard_config import get_dashboard_config, save_dashboard_config
from stock_analysis.analytics.department_names import flush_item_departments
from stock_analysis.config import APP_NAME, APP_VERSION, get_app_data_dir, get_database_path
from stock_analysis.analytics.movement_periods import format_baseline_as_of_label, weekday_name
from stock_analysis.db.session import (
    get_baseline_anchor_date,
    get_movement_closing_weekday,
    get_session,
)
from stock_analysis.ui.pages.department_naming_page import DepartmentNamingPage
from stock_analysis.ui.wizards.initial_baseline_wizard import InitialBaselineWizard

_SETTINGS_INDEX = 0
_NAMING_INDEX = 1


class SettingsPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self._stack = QStackedWidget()
        outer.addWidget(self._stack)

        self._main = QWidget()
        layout = QVBoxLayout(self._main)
        layout.setContentsMargins(24, 24, 24, 24)

        form = QFormLayout()
        self._app_name = QLabel(APP_NAME)
        self._version = QLabel(APP_VERSION)
        self._data_dir = QLabel(str(get_app_data_dir()))
        self._db_path = QLabel(str(get_database_path()))
        form.addRow("Application:", self._app_name)
        form.addRow("Version:", self._version)
        form.addRow("Data folder:", self._data_dir)
        form.addRow("Database:", self._db_path)
        layout.addLayout(form)

        dashboard_group = QGroupBox("Home Dashboard")
        dashboard_layout = QVBoxLayout(dashboard_group)
        self._show_kpis = QCheckBox("Show KPI cards")
        self._show_charts = QCheckBox("Show charts")
        self._show_alerts = QCheckBox("Show stock alerts")
        self._show_sales_tab = QCheckBox("Show sales tab")
        self._show_slow_moving_tab = QCheckBox("Show slow moving tab")
        self._show_dead_stock_tab = QCheckBox("Show dead stock tab")
        self._show_stock_health = QCheckBox("Show stock health chart")
        dashboard_layout.addWidget(self._show_kpis)
        dashboard_layout.addWidget(self._show_charts)
        dashboard_layout.addWidget(self._show_alerts)
        dashboard_layout.addWidget(self._show_sales_tab)
        dashboard_layout.addWidget(self._show_slow_moving_tab)
        dashboard_layout.addWidget(self._show_dead_stock_tab)
        dashboard_layout.addWidget(self._show_stock_health)
        save_dashboard = QPushButton("Save Dashboard Layout")
        save_dashboard.clicked.connect(self._save_dashboard)
        dashboard_layout.addWidget(save_dashboard)
        layout.addWidget(dashboard_group)

        holding_group = QGroupBox("Stock holding buffer")
        holding_layout = QVBoxLayout(holding_group)
        holding_form = QFormLayout()
        self._buffer_min_pct = QDoubleSpinBox()
        self._buffer_min_pct.setRange(0, 500)
        self._buffer_min_pct.setDecimals(1)
        self._buffer_min_pct.setSuffix("%")
        self._buffer_max_pct = QDoubleSpinBox()
        self._buffer_max_pct.setRange(0, 500)
        self._buffer_max_pct.setDecimals(1)
        self._buffer_max_pct.setSuffix("%")
        holding_form.addRow("Minimum buffer:", self._buffer_min_pct)
        holding_form.addRow("Maximum buffer:", self._buffer_max_pct)
        holding_layout.addLayout(holding_form)
        holding_layout.addWidget(
            QLabel(
                "Healthy stock is between target × (1 + min%) and target × (1 + max%). "
                "Target = average weekly sales × hold weeks."
            )
        )
        save_holding = QPushButton("Save Stock Holding Settings")
        save_holding.clicked.connect(self._save_stock_holding)
        holding_layout.addWidget(save_holding)
        layout.addWidget(holding_group)

        self._dept_naming_btn = QPushButton("Department Naming…")
        self._dept_naming_btn.clicked.connect(self._open_department_naming)
        layout.addWidget(self._dept_naming_btn)

        self._import_dept_btn = QPushButton("Import Departments…")
        self._import_dept_btn.clicked.connect(self._import_departments)
        layout.addWidget(self._import_dept_btn)

        self._flush_dept_btn = QPushButton("Flush Departments…")
        self._flush_dept_btn.clicked.connect(self._flush_departments)
        layout.addWidget(self._flush_dept_btn)

        movement_group = QGroupBox("Movement Calendar")
        movement_form = QFormLayout(movement_group)
        self._baseline_as_of_label = QLabel("—")
        self._closing_day_label = QLabel("—")
        self._change_closing_btn = QPushButton("Change…")
        self._change_closing_btn.clicked.connect(self._change_closing_day)
        closing_row = QHBoxLayout()
        closing_row.addWidget(self._closing_day_label, stretch=1)
        closing_row.addWidget(self._change_closing_btn)
        movement_form.addRow("Baseline as of:", self._baseline_as_of_label)
        movement_form.addRow("Closing day:", closing_row)
        layout.addWidget(movement_group)

        self._reimport_btn = QPushButton("Re-import Initial Baseline…")
        self._reimport_btn.clicked.connect(self._reimport_baseline)
        self._enrich_btn = QPushButton("Import Movement Period (Step 2)…")
        self._enrich_btn.clicked.connect(self._run_enrichment)
        self._backdate_btn = QPushButton("Backdate Import…")
        self._backdate_btn.clicked.connect(self._run_backdate_import)
        layout.addWidget(self._reimport_btn)
        layout.addWidget(self._enrich_btn)
        layout.addWidget(self._backdate_btn)
        layout.addStretch()

        self._stack.addWidget(self._main)

        self._naming_page = DepartmentNamingPage()
        self._naming_page.back_requested.connect(self._show_main)
        self._naming_page.nicknames_saved.connect(self._on_nicknames_saved)
        self._stack.addWidget(self._naming_page)

        self._on_data_changed = None

    def set_data_changed_callback(self, callback) -> None:
        self._on_data_changed = callback

    def _show_main(self) -> None:
        self._stack.setCurrentIndex(_SETTINGS_INDEX)

    def reset_to_base(self) -> None:
        self._show_main()

    def _open_department_naming(self) -> None:
        self._naming_page.refresh()
        self._stack.setCurrentIndex(_NAMING_INDEX)

    def _import_departments(self) -> None:
        from stock_analysis.ui.wizards.stocklist_department_import_wizard import (
            run_stocklist_department_import_wizard,
        )

        if run_stocklist_department_import_wizard(self):
            if self._on_data_changed:
                self._on_data_changed()

    def _flush_departments(self) -> None:
        reply = QMessageBox.warning(
            self,
            "Flush Departments",
            "Clear department assignments from all inventory items and movement history?\n\n"
            "Use Import Departments afterwards to load them again from a Stocklist export.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        with get_session() as session:
            cleared = flush_item_departments(session)
        invalidate_summaries()
        QMessageBox.information(
            self,
            "Departments Flushed",
            f"Cleared departments from {cleared:,} item(s).",
        )
        if self._on_data_changed:
            self._on_data_changed()

    def _on_nicknames_saved(self) -> None:
        if self._on_data_changed:
            self._on_data_changed()

    def _save_dashboard(self) -> None:
        with get_session() as session:
            save_dashboard_config(
                session,
                {
                    "show_kpis": self._show_kpis.isChecked(),
                    "show_charts": self._show_charts.isChecked(),
                    "show_alerts": self._show_alerts.isChecked(),
                    "show_sales_tab": self._show_sales_tab.isChecked(),
                    "show_slow_moving_tab": self._show_slow_moving_tab.isChecked(),
                    "show_dead_stock_tab": self._show_dead_stock_tab.isChecked(),
                    "show_stock_health": self._show_stock_health.isChecked(),
                },
            )
        if self._on_data_changed:
            self._on_data_changed()

    def _save_stock_holding(self) -> None:
        min_pct = self._buffer_min_pct.value()
        max_pct = self._buffer_max_pct.value()
        if max_pct < min_pct:
            max_pct = min_pct
            self._buffer_max_pct.setValue(max_pct)
        with get_session() as session:
            set_stock_buffer_pct_range(session, min_pct, max_pct)
        invalidate_summaries()
        if self._on_data_changed:
            self._on_data_changed()

    def _reimport_baseline(self) -> None:
        wizard = InitialBaselineWizard(self)
        if wizard.exec():
            if self._on_data_changed:
                self._on_data_changed()
            from stock_analysis.ui.wizards.post_baseline_setup import run_post_baseline_setup

            if run_post_baseline_setup(self, wizard.parsed) and self._on_data_changed:
                self._on_data_changed()

    def _change_closing_day(self) -> None:
        from stock_analysis.ui.wizards.movement_closing_dialog import run_closing_day_dialog

        with get_session() as session:
            current = get_movement_closing_weekday(session)
        if run_closing_day_dialog(self, initial_weekday=current) is not None:
            self.refresh()

    def _run_enrichment(self) -> None:
        from stock_analysis.ui.wizards.movement_import_wizard import run_enrichment_wizard

        if run_enrichment_wizard(self):
            if self._on_data_changed:
                self._on_data_changed()

    def _run_backdate_import(self) -> None:
        from stock_analysis.ui.wizards.movement_import_wizard import run_backdate_import_wizard

        if run_backdate_import_wizard(self):
            if self._on_data_changed:
                self._on_data_changed()

    def refresh(self) -> None:
        if self._stack.currentIndex() == _NAMING_INDEX:
            self._naming_page.refresh()
        self._data_dir.setText(str(get_app_data_dir()))
        self._db_path.setText(str(get_database_path()))
        with get_session() as session:
            config = get_dashboard_config(session)
            closing = get_movement_closing_weekday(session)
            anchor = get_baseline_anchor_date(session)
            min_pct, max_pct = get_stock_buffer_pct_range(session)
        self._buffer_min_pct.setValue(min_pct)
        self._buffer_max_pct.setValue(max_pct)
        self._baseline_as_of_label.setText(format_baseline_as_of_label(anchor, closing))
        self._closing_day_label.setText(
            weekday_name(closing) if closing is not None else "Not set"
        )
        self._show_kpis.setChecked(config.get("show_kpis", True))
        self._show_charts.setChecked(config.get("show_charts", True))
        self._show_alerts.setChecked(config.get("show_alerts", True))
        self._show_sales_tab.setChecked(config.get("show_sales_tab", True))
        self._show_slow_moving_tab.setChecked(config.get("show_slow_moving_tab", True))
        self._show_dead_stock_tab.setChecked(config.get("show_dead_stock_tab", True))
        self._show_stock_health.setChecked(config.get("show_stock_health", True))
