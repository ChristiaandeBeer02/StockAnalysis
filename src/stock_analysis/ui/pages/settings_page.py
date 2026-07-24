"""Application settings page."""

from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from stock_analysis.analytics.dashboard_config import get_dashboard_config, save_dashboard_config
from stock_analysis.config import APP_NAME, APP_VERSION, get_app_data_dir, get_database_path
from stock_analysis.db.session import get_session
from stock_analysis.ui.wizards.initial_baseline_wizard import InitialBaselineWizard


class SettingsPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
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
        self._show_stock_health = QCheckBox("Show stock health chart")
        dashboard_layout.addWidget(self._show_kpis)
        dashboard_layout.addWidget(self._show_charts)
        dashboard_layout.addWidget(self._show_alerts)
        dashboard_layout.addWidget(self._show_sales_tab)
        dashboard_layout.addWidget(self._show_slow_moving_tab)
        dashboard_layout.addWidget(self._show_stock_health)
        save_dashboard = QPushButton("Save Dashboard Layout")
        save_dashboard.clicked.connect(self._save_dashboard)
        dashboard_layout.addWidget(save_dashboard)
        layout.addWidget(dashboard_group)

        self._reimport_btn = QPushButton("Re-import Initial Baseline…")
        self._reimport_btn.clicked.connect(self._reimport_baseline)
        self._enrich_btn = QPushButton("Run Enrichment (Turn + Turnunder)…")
        self._enrich_btn.clicked.connect(self._run_enrichment)
        layout.addWidget(self._reimport_btn)
        layout.addWidget(self._enrich_btn)
        layout.addStretch()

        self._on_data_changed = None

    def set_data_changed_callback(self, callback) -> None:
        self._on_data_changed = callback

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
                    "show_stock_health": self._show_stock_health.isChecked(),
                },
            )
        if self._on_data_changed:
            self._on_data_changed()

    def _reimport_baseline(self) -> None:
        wizard = InitialBaselineWizard(self)
        if wizard.exec():
            if self._on_data_changed:
                self._on_data_changed()

    def _run_enrichment(self) -> None:
        from stock_analysis.ui.wizards.turn_import_wizard import run_enrichment_wizard

        if run_enrichment_wizard(self):
            if self._on_data_changed:
                self._on_data_changed()

    def refresh(self) -> None:
        self._data_dir.setText(str(get_app_data_dir()))
        self._db_path.setText(str(get_database_path()))
        with get_session() as session:
            config = get_dashboard_config(session)
        self._show_kpis.setChecked(config.get("show_kpis", True))
        self._show_charts.setChecked(config.get("show_charts", True))
        self._show_alerts.setChecked(config.get("show_alerts", True))
        self._show_sales_tab.setChecked(config.get("show_sales_tab", True))
        self._show_slow_moving_tab.setChecked(config.get("show_slow_moving_tab", True))
        self._show_stock_health.setChecked(config.get("show_stock_health", True))
