"""Main application window."""

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeySequence, QMouseEvent, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QStatusBar,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from stock_analysis.analytics.cache import invalidate_summaries, load_summaries
from stock_analysis.config import APP_NAME
from stock_analysis.db.session import get_session, has_initial_baseline
from stock_analysis.ui.navigation import MAX_NAV_STACK, NavState
from stock_analysis.ui.pages.compare_page import ComparePage
from stock_analysis.ui.pages.home_page import HomePage
from stock_analysis.ui.pages.inventory_page import InventoryPage
from stock_analysis.ui.pages.reports_page import ReportsPage
from stock_analysis.ui.pages.settings_page import SettingsPage
from stock_analysis.ui.wizards.initial_baseline_wizard import InitialBaselineWizard
from stock_analysis.ui.wizards.movement_import_wizard import run_enrichment_wizard, run_period_import_wizard

NAV_ITEMS = [
    ("Home", "home"),
    ("Inventory", "inventory"),
    ("Reports", "reports"),
    ("Compare", "compare"),
    ("Settings", "settings"),
]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1280, 800)

        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(220)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(12, 16, 12, 16)

        app_label = QLabel(APP_NAME)
        app_label.setObjectName("appTitle")
        side_layout.addWidget(app_label)

        self._nav = QListWidget()
        self._nav.setObjectName("navList")
        for label, _key in NAV_ITEMS:
            item = QListWidgetItem(label)
            self._nav.addItem(item)
        self._nav.currentRowChanged.connect(self._on_nav_changed)
        self._nav.itemClicked.connect(self._on_nav_item_clicked)
        side_layout.addWidget(self._nav)
        side_layout.addStretch()
        root.addWidget(sidebar)

        self._stack = QStackedWidget()
        self._home = HomePage()
        self._inventory = InventoryPage()
        self._reports = ReportsPage()
        self._compare = ComparePage()
        self._settings = SettingsPage()

        self._pages = [
            self._home,
            self._inventory,
            self._reports,
            self._compare,
            self._settings,
        ]

        for page in self._pages:
            self._stack.addWidget(page)
        root.addWidget(self._stack, stretch=1)

        self._home.import_initial_requested.connect(self._run_initial_import)
        self._home.import_enrichment_requested.connect(self._run_enrichment_import)
        self._home.import_period_requested.connect(self._run_period_import)
        self._home.inventory_dept_requested.connect(self._navigate_to_inventory_items)
        self._home.item_detail_requested.connect(self.open_item_detail)
        self._home.data_changed.connect(self._on_data_changed)
        self._settings.set_data_changed_callback(self._on_data_changed)
        self._inventory.data_changed.connect(self._on_data_changed)
        self._inventory.item_detail_requested.connect(self.open_item_detail)
        self._inventory.stock_alert_requested.connect(self._navigate_to_home_stock_alerts)
        self._inventory.slow_moving_requested.connect(self._navigate_to_home_slow_moving)
        self._inventory.dead_stock_requested.connect(self._navigate_to_home_dead_stock)
        self._inventory._detail.back_requested.connect(self._go_back)

        self._status = QStatusBar()
        self.setStatusBar(self._status)

        self._dirty_pages: set[int] = set()
        self._nav_stack: list[NavState] = []
        self._restoring_nav = False
        self._programmatic_nav = False
        self._nav_ready = False
        self._last_sidebar_index = 0

        self._back_shortcut = QShortcut(QKeySequence("Alt+Left"), self)
        self._back_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self._back_shortcut.activated.connect(self._go_back)

        QApplication.instance().installEventFilter(self)

        self._nav.setCurrentRow(0)
        self._last_sidebar_index = self._nav.currentRow()
        self._nav_ready = True
        self.refresh_all()

    def _on_data_changed(self) -> None:
        self.refresh_all(invalidate_cache=True)

    def _on_nav_changed(self, index: int) -> None:
        if index < 0 or self._restoring_nav:
            return

        if (
            self._nav_ready
            and not self._programmatic_nav
            and index != self._last_sidebar_index
        ):
            self._push_nav_state(
                NavState(
                    sidebar_index=self._last_sidebar_index,
                    page_state=self._page_state_at(self._last_sidebar_index),
                )
            )

        self._last_sidebar_index = index
        self._stack.setCurrentIndex(index)
        if index in self._dirty_pages:
            page = self._pages[index]
            if hasattr(page, "refresh"):
                page.refresh()
            self._dirty_pages.discard(index)

        if self._nav_ready and not self._programmatic_nav and not self._restoring_nav:
            self._reset_page_to_base(index)

    def _on_nav_item_clicked(self, item: QListWidgetItem) -> None:
        if self._restoring_nav:
            return
        index = self._nav.row(item)
        if index == self._nav.currentRow():
            self._reset_page_to_base(index)

    def _reset_page_to_base(self, index: int) -> None:
        page = self._pages[index]
        if hasattr(page, "reset_to_base"):
            page.reset_to_base()

    def _page_state_at(self, index: int) -> object | None:
        page = self._pages[index]
        if hasattr(page, "capture_nav_state"):
            return page.capture_nav_state()
        return None

    def _capture_nav_state(self) -> NavState:
        index = self._stack.currentIndex()
        return NavState(sidebar_index=index, page_state=self._page_state_at(index))

    def _push_nav_state(self, state: NavState) -> None:
        self._nav_stack.append(state)
        if len(self._nav_stack) > MAX_NAV_STACK:
            self._nav_stack.pop(0)

    def _restore_nav_state(self, state: NavState) -> None:
        self._restoring_nav = True
        needs_refresh = state.sidebar_index in self._dirty_pages
        try:
            self._nav.blockSignals(True)
            self._nav.setCurrentRow(state.sidebar_index)
            self._nav.blockSignals(False)
            self._last_sidebar_index = state.sidebar_index
            self._stack.setCurrentIndex(state.sidebar_index)
            page = self._pages[state.sidebar_index]
            if hasattr(page, "restore_nav_state"):
                if page is self._inventory:
                    self._inventory.restore_nav_state(
                        state.page_state, needs_refresh=needs_refresh
                    )
                else:
                    page.restore_nav_state(state.page_state)
                    if needs_refresh and hasattr(page, "refresh"):
                        page.refresh()
            self._dirty_pages.discard(state.sidebar_index)
        finally:
            self._restoring_nav = False

    def _set_sidebar_index(self, index: int) -> None:
        self._programmatic_nav = True
        try:
            self._nav.setCurrentRow(index)
        finally:
            self._programmatic_nav = False

    def open_item_detail(self, sku: str) -> None:
        self._push_nav_state(self._capture_nav_state())
        self._set_sidebar_index(1)
        self._inventory.show_item_detail(sku)

    def _go_back(self) -> None:
        if not self._nav_stack:
            if self._inventory.is_showing_detail():
                self._inventory.show_list_view()
            return
        state = self._nav_stack.pop()
        self._restore_nav_state(state)

    def _is_navigation_target(self) -> bool:
        if not self.isVisible():
            return False
        active = QApplication.activeWindow()
        if active is not None and active is not self:
            return False
        return True

    def eventFilter(self, watched, event) -> bool:
        if not isinstance(event, QMouseEvent):
            return super().eventFilter(watched, event)

        if event.button() != Qt.MouseButton.BackButton:
            return super().eventFilter(watched, event)

        if event.type() == QEvent.Type.MouseButtonPress:
            if self._is_navigation_target():
                self._go_back()
            return True

        if event.type() == QEvent.Type.MouseButtonRelease:
            return True

        return super().eventFilter(watched, event)

    def _run_initial_import(self) -> None:
        wizard = InitialBaselineWizard(self)
        if wizard.exec():
            self.refresh_all(invalidate_cache=True)
            from stock_analysis.ui.wizards.post_baseline_setup import run_post_baseline_setup

            if run_post_baseline_setup(self, wizard.parsed):
                self.refresh_all(invalidate_cache=True)

    def _run_enrichment_import(self) -> None:
        if run_enrichment_wizard(self):
            self.refresh_all(invalidate_cache=True)

    def _run_period_import(self) -> None:
        if run_period_import_wizard(self):
            self.refresh_all(invalidate_cache=True)

    def _navigate_to_inventory_items(self, dept: str) -> None:
        self._push_nav_state(self._capture_nav_state())
        self._inventory.set_dept_filter(dept)
        self._set_sidebar_index(1)
        self._inventory.show_items_tab()

    def _navigate_to_home_stock_alerts(self, alert_type: str, dept: object) -> None:
        self._push_nav_state(self._capture_nav_state())
        self._set_sidebar_index(0)
        self._home.show_stock_alerts(
            alert_type,
            dept if isinstance(dept, str) else None,
        )

    def _navigate_to_home_slow_moving(self, dept: object) -> None:
        self._push_nav_state(self._capture_nav_state())
        self._set_sidebar_index(0)
        self._home.show_slow_moving(
            dept if isinstance(dept, str) else None,
        )

    def _navigate_to_home_dead_stock(self, dept: object) -> None:
        self._push_nav_state(self._capture_nav_state())
        self._set_sidebar_index(0)
        self._home.show_dead_stock(
            dept if isinstance(dept, str) else None,
        )

    def refresh_all(self, *, invalidate_cache: bool = True) -> None:
        if invalidate_cache:
            invalidate_summaries()
            self._dirty_pages = set(range(len(self._pages)))

        current = self._stack.currentIndex()
        page = self._pages[current]
        if hasattr(page, "refresh"):
            page.refresh()
        self._dirty_pages.discard(current)

        with get_session() as session:
            if has_initial_baseline(session):
                baseline = load_summaries(session).baseline
            else:
                baseline = None
        self._update_status(baseline)

    def _update_status(self, summary: dict | None = None) -> None:
        if summary is None:
            with get_session() as session:
                if not has_initial_baseline(session):
                    self._status.showMessage("No baseline — import initial stockholding to begin")
                    return
                summary = load_summaries(session).baseline

        if summary is None:
            self._status.showMessage("No baseline — import initial stockholding to begin")
            return

        self._status.showMessage(
            f"Baseline v{summary['baseline_version']}  |  "
            f"{summary['item_count']:,} items  |  "
            f"Stock value R {summary['total_value']:,.2f}"
        )
