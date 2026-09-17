import sys
import os
import re
from datetime import datetime, date
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QScrollArea, QCheckBox,
    QPushButton, QMessageBox, QLabel, QLineEdit, QDialog, QFrame,
    QVBoxLayout, QHBoxLayout, QDialogButtonBox,
)
from PySide6.QtCore import Qt, Signal, QThread, QTimer, QEvent, QSize, qInstallMessageHandler, QtMsgType
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut, QIcon


def _qt_message_filter(mode, context, message):
    """Drop Qt's harmless `QFont::setPointSize: Point size <= 0` chatter.

    Our stylesheets express font sizes in pixels (`font-size: 11px`), so the
    derived QFont has pointSize == -1. Some internal Qt paths still call
    `setPointSize(font.pointSize())` on those fonts and Qt emits a warning
    every time. The widgets render fine — it's pure noise. Pass everything
    else through to stderr so real warnings stay visible.
    """
    if message and "QFont::setPointSize: Point size <= 0" in message:
        return
    sys.stderr.write(message + "\n")


qInstallMessageHandler(_qt_message_filter)
from db.database import (
    init_db, migrate_legacy_db, discover_and_merge_background_dbs,
    backup_db_to_onedrive,
)
from sync.app_logger import log_event


def _install_crash_guard():
    """Keep the app alive on unhandled exceptions raised inside Qt slots.

    PySide6 terminates the whole process when a Python exception escapes a
    slot/callback. A transient "database is locked" on the OneDrive-hosted DB
    used to take the app down mid-action (the "freeze then close" bug). We log
    the traceback instead so the window survives and the user can retry.
    """
    import traceback

    def _hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        tb = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        try:
            log_event("main", f"Unhandled exception (survived):\n{tb}", level="ERROR")
        except Exception:
            pass
        sys.stderr.write(tb)

    sys.excepthook = _hook


_install_crash_guard()
from tabs.utils import load_units_eq_data
from tabs.breaks_dialog import (
    init_breaks_table, BreaksDialog, get_breaks,
    get_breaks_answered_today, set_break_attendance, _to_minutes,
    get_active_schedule,
)
import qtawesome as qta

try:
    from sync.daily_performance import (
        init_performance_table,
        get_daily_metrics,
        record_daily_snapshot,
        has_pending_justification,
        mark_success_shown,
        was_success_shown,
        save_justification,
        export_justification,
    )
    from tabs.performance_popups import SuccessPopup, JustificationPopup
    _PERF_OK = True
except Exception as _perf_err:
    print(f"[main] Performance module unavailable: {_perf_err}")
    _PERF_OK = False

_JUSTIFICATION_ENABLED = False


def _resource_path(relative: str) -> str:
    """Return absolute path to a bundled resource (works both frozen and in dev)."""
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative)

# ============== VERSION ==============
APP_VERSION = "1.1.5"
DB_SCHEMA_VERSION = 2
# =====================================

from tabs import font_scale
from tabs.tab_register import RegisterTab
from tabs.tab_production import ProductionTab
from tabs.tab_history import HistoryTab
from tabs.tab_standards import StandardsTab
from tabs.tab_dashboard import DashboardTab
from tabs.tab_theme_config import ThemeConfigTab, DEFAULT_LIGHT_COLORS
from PySide6.QtWidgets import QDialog, QVBoxLayout as _QVBox
from sync.app_config import load_config


class CenteredTabWidget(QTabWidget):
    """QTabWidget whose tab bar is always horizontally centered.

    Qt re-layouts the tab bar to x=0 on resize, tab switch, and other events.
    We install an event filter on the tab bar that catches every Move/Resize
    event Qt fires on it and immediately corrects the position.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        bar = self.tabBar()
        bar.setExpanding(False)
        bar.setUsesScrollButtons(True)
        bar.installEventFilter(self)
        self._centering = False  # re-entry guard

        # Stop the per-tab "size flicker" when switching tabs: by default
        # QTabWidget sizes its inner QStackedWidget to the current page's
        # sizeHint, so changing pages briefly relayouts everything. Pin a
        # single Expanding policy on the stacked area + ignore the per-page
        # size hint so the widget stays put across switches.
        from PySide6.QtWidgets import QStackedWidget, QSizePolicy
        stacked = self.findChild(QStackedWidget)
        if stacked is not None:
            stacked.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
            )
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        # Slide + fade transition on every tab switch. The page slides in
        # from the side opposite to the tab you came from (jumping forward
        # = enter from right, jumping back = enter from left). Duration is
        # long enough (320 ms) to read but short enough to stay snappy.
        self._fade_anim = None
        self._prev_tab_index = self.currentIndex()
        self.currentChanged.connect(self._animate_tab_change)

    # By default, QTabWidget.sizeHint asks the *current* page for its hint
    # which makes the parent layout request a different size when you switch
    # tabs (the visible "grow then settle" you noticed). Returning the union
    # of every page's hint keeps the geometry stable across switches.
    def sizeHint(self):
        from PySide6.QtCore import QSize
        hint = QSize(0, 0)
        for i in range(self.count()):
            page = self.widget(i)
            if page is None:
                continue
            ph = page.sizeHint()
            hint = QSize(max(hint.width(), ph.width()),
                         max(hint.height(), ph.height()))
        # Fall back to the default if no pages report a hint yet
        base = super().sizeHint()
        return QSize(max(hint.width(), base.width()),
                     max(hint.height(), base.height()))

    def minimumSizeHint(self):
        from PySide6.QtCore import QSize
        hint = QSize(0, 0)
        for i in range(self.count()):
            page = self.widget(i)
            if page is None:
                continue
            ph = page.minimumSizeHint()
            hint = QSize(max(hint.width(), ph.width()),
                         max(hint.height(), ph.height()))
        base = super().minimumSizeHint()
        return QSize(max(hint.width(), base.width()),
                     max(hint.height(), base.height()))

    # ── tab-switch fade animation ────────────────────────────────────────────

    def _animate_tab_change(self, index: int):
        """Slide-in + fade-in transition for the newly selected tab.

        Direction follows the navigation: when you click a tab to the right
        of the current one, the new page slides in from the right; when you
        go left, it slides in from the left. Combined with an opacity
        fade so the effect is unmistakable. ~320 ms with OutCubic easing.
        Reentrancy-safe: a new switch cancels any in-flight animation.
        """
        from PySide6.QtCore import (
            QPropertyAnimation, QEasingCurve, QParallelAnimationGroup, QPoint,
        )
        from PySide6.QtWidgets import QGraphicsOpacityEffect

        page = self.widget(index)
        if page is None:
            self._prev_tab_index = index
            return

        prev = getattr(self, "_prev_tab_index", index)
        # If somehow no movement, still play (e.g., forced re-selection).
        direction = 1 if index >= prev else -1
        self._prev_tab_index = index

        if self._fade_anim is not None:
            try:
                self._fade_anim.stop()
            except Exception:
                pass

        # ── Opacity (fade-in) ────────────────────────────────────────────
        effect = QGraphicsOpacityEffect(page)
        effect.setOpacity(0.0)
        page.setGraphicsEffect(effect)
        fade = QPropertyAnimation(effect, b"opacity")
        fade.setDuration(320)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)

        # ── Position (slide-in) ──────────────────────────────────────────
        # Slide distance scales with the tab area width so the motion is
        # always proportional to the visible content (~25 % of width).
        slide_distance = max(60, int(page.width() * 0.25))
        start_x = direction * slide_distance
        start_pos = QPoint(start_x, page.y())
        end_pos = QPoint(0, page.y())
        page.move(start_pos)
        slide = QPropertyAnimation(page, b"pos")
        slide.setDuration(320)
        slide.setStartValue(start_pos)
        slide.setEndValue(end_pos)
        slide.setEasingCurve(QEasingCurve.Type.OutCubic)

        group = QParallelAnimationGroup(self)
        group.addAnimation(fade)
        group.addAnimation(slide)

        def _cleanup():
            try:
                page.setGraphicsEffect(None)
                # Make sure the page rests exactly where the layout wants it
                page.move(end_pos)
            except Exception:
                pass
            if self._fade_anim is group:
                self._fade_anim = None

        group.finished.connect(_cleanup)
        self._fade_anim = group
        group.start()

    # ── event filter on the tab bar ───────────────────────────────────────────

    def eventFilter(self, obj, event):
        if obj is self.tabBar() and event.type() in (
            QEvent.Type.Move, QEvent.Type.Resize, QEvent.Type.Show,
        ):
            self._schedule_center()
        return False  # never consume the event

    def _schedule_center(self):
        if not self._centering:
            self._centering = True
            QTimer.singleShot(0, self._do_center)

    def _do_center(self):
        self._centering = False
        self._center_tabs()

    # ── centering logic ───────────────────────────────────────────────────────

    def _center_tabs(self):
        bar = self.tabBar()
        n = bar.count()
        if n == 0:
            return
        total = sum(bar.tabRect(i).width() for i in range(n))
        geo = bar.geometry()
        x = max(0, (self.width() - total) // 2)
        if geo.x() == x:
            return  # already centered — avoid triggering another Move event
        bar.setGeometry(x, geo.y(), self.width() - x, geo.height())

    # ── also re-center on widget resize / show / tab add ─────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._center_tabs()

    def showEvent(self, event):
        super().showEvent(event)
        self._schedule_center()

    def tabInserted(self, index):
        super().tabInserted(index)
        self._schedule_center()


# ── Global QSS theme bridge ──────────────────────────────────────────
# Monkeypatch QWidget.setStyleSheet so every inline stylesheet in the
# app passes through themed_qss() — that swaps Fluent-dark literals
# (#0D1117, #161B22, #21262D, text colours, etc.) for the active
# theme's palette values. In dark mode the swaps are no-ops; in light
# mode every widget with a dark literal stylesheet picks up the user's
# light palette without us touching each individual setStyleSheet call.
try:
    from PySide6.QtWidgets import QWidget as _QW_theme
    from tabs.theme_palette import themed_qss as _themed_qss_fn

    _ORIG_SETSTYLESHEET = _QW_theme.setStyleSheet

    def _themed_setstylesheet(self, qss):
        # Cache the raw template so we can re-translate on theme change.
        try:
            self._raw_qss_template = qss
        except Exception:
            pass
        try:
            translated = _themed_qss_fn(qss)
        except Exception:
            translated = qss
        return _ORIG_SETSTYLESHEET(self, translated)

    _QW_theme.setStyleSheet = _themed_setstylesheet

    def _reapply_all_themed_qss(root):
        """Walk a widget tree and re-translate every cached QSS template
        through ``themed_qss`` for the now-active theme."""
        try:
            for w in [root] + list(root.findChildren(_QW_theme)):
                tpl = getattr(w, "_raw_qss_template", None)
                if tpl:
                    try:
                        _ORIG_SETSTYLESHEET(w, _themed_qss_fn(tpl))
                    except Exception:
                        pass
        except Exception:
            pass
except Exception:
    _reapply_all_themed_qss = None


class MainWindow(QMainWindow):
    themeChanged = Signal(bool)
    fontSizeChanged = Signal(int)
    def __init__(self, dark_style="", light_style=""):
        super().__init__()
        self._dark_style = dark_style
        self._light_style = light_style
        self._light_style_base = light_style
        try:
            _cfg = load_config()
            _stored = int(_cfg.get("font_size", 12))
        except Exception:
            _stored = 12
        self._font_size = max(9, min(20, _stored))
        font_scale.set_current_px(self._font_size)
        self._sb_widgets = {}  # status bar refs for font-aware re-styling
        self._is_light = False
        self.setWindowTitle("Production Performance Calculator")
        _ico = _resource_path(os.path.join("data", "app_icon.ico"))
        self.setWindowIcon(QIcon(_ico))

        from tabs.fluent_nav import FluentNavigation
        self.tabs = FluentNavigation()

        self.register_tab = RegisterTab()
        self.production_tab = ProductionTab()
        self.history_tab = HistoryTab()
        self.standards_tab = StandardsTab()
        self.dashboard_tab = DashboardTab()

        self._load_and_apply_light_palette()

        def _safe_connect(signal, target, slot_name: str, name: str):
            try:
                slot = getattr(target, slot_name, None)
                if not callable(slot):
                    log_event("main", f"theme signal skipped (missing slot) for {name}", level="WARN")
                    return
                signal.connect(slot)
            except Exception as exc:
                log_event("main", f"theme signal connect failed for {name}: {exc}", level="WARN")

        # Connect a themeChanged signal to tabs so they update their local styles
        _safe_connect(self.themeChanged, self.register_tab,   "update_theme_labels",        "register.update_theme_labels")
        _safe_connect(self.themeChanged, self.history_tab,    "update_theme_labels",        "history.update_theme_labels")
        _safe_connect(self.themeChanged, self.production_tab, "update_theme_labels",        "production.update_theme_labels")
        _safe_connect(self.themeChanged, self.standards_tab,  "update_theme_labels",        "standards.update_theme_labels")
        _safe_connect(self.themeChanged, self.dashboard_tab,  "update_theme_labels",        "dashboard.update_theme_labels")
        _safe_connect(self.themeChanged, self.tabs,           "apply_palette",              "fluent_nav.apply_palette")
        self.themeChanged.connect(self._repaint_help_btn)
        self.themeChanged.connect(self._repaint_pro_cards)
        # Re-translate every cached inline QSS so dark literals adapt.
        try:
            if _reapply_all_themed_qss is not None:
                self.themeChanged.connect(
                    lambda _is_light: _reapply_all_themed_qss(self)
                )
        except Exception:
            pass

        # Font size — tabs refresh widgets that hold hard-coded point sizes
        _safe_connect(self.fontSizeChanged, self.dashboard_tab,  "update_font_sizes", "dashboard.update_font_sizes")
        _safe_connect(self.fontSizeChanged, self.production_tab, "update_font_sizes", "production.update_font_sizes")
        _safe_connect(self.fontSizeChanged, self.history_tab,    "update_font_sizes", "history.update_font_sizes")
        _safe_connect(self.fontSizeChanged, self.register_tab,   "update_font_sizes", "register.update_font_sizes")
        _safe_connect(self.fontSizeChanged, self.standards_tab,  "update_font_sizes", "standards.update_font_sizes")

        
        # Stagger heavy post-save refreshes via QTimer so a click on Import
        # right after Save Case isn't stuck behind them in the event queue.
        # Each refresh runs in its own event loop tick — user input has a
        # window to interleave between them.
        from PySide6.QtCore import QTimer as _QTimer

        def _deferred_refresh_after_save():
            _QTimer.singleShot(0,   self.production_tab.load_data)
            _QTimer.singleShot(80,  self.history_tab.load_all_cases)
            _QTimer.singleShot(180, self.dashboard_tab.refresh)
        self.register_tab.case_saved.connect(_deferred_refresh_after_save)

        # Downtime mutations (add/edit/delete/status) — refresh views that
        # aggregate downtime data so they don't show stale counts.
        #
        # These refreshes (full Dashboard rebuild + full History reload) are
        # heavy and used to run SYNCHRONOUSLY on the UI thread on every single
        # downtime add — freezing the app ~3 s even though those tabs weren't
        # visible. Instead we mark them "dirty" and only rebuild when the user
        # actually opens that tab (or, if it's already the current tab, on the
        # next event-loop tick so the downtime save stays instant).
        self._dirty_tabs: dict = {}   # {tab_widget: refresh_method_name}

        def _mark_tab_dirty(tab_widget, method_name):
            self._dirty_tabs[tab_widget] = method_name
            if self.tabs.currentWidget() is tab_widget:
                _QTimer.singleShot(
                    0, lambda w=tab_widget: _refresh_tab_if_dirty(w)
                )

        def _refresh_tab_if_dirty(tab_widget):
            method_name = self._dirty_tabs.pop(tab_widget, None)
            if not method_name:
                return
            try:
                getattr(tab_widget, method_name)()
            except Exception as exc:
                log_event("main", f"deferred tab refresh failed: {exc}",
                          level="WARN")

        def _on_downtime_mutated():
            _mark_tab_dirty(self.dashboard_tab, "refresh")
            _mark_tab_dirty(self.history_tab, "load_all_cases")

        self.register_tab.downtime_changed.connect(_on_downtime_mutated)
        # When a tab becomes visible, refresh it if it went stale while hidden.
        self.tabs.currentChanged.connect(
            lambda _i: _refresh_tab_if_dirty(self.tabs.currentWidget())
        )

        # Connect production tab edit/delete to register tab
        self.production_tab.case_updated.connect(self.on_production_case_updated)

        # Register tab in OT mode also refreshes OT views
        def _deferred_refresh_after_ot_save():
            _QTimer.singleShot(0,   self.register_tab._load_ot_day_cases)
            _QTimer.singleShot(80,  self.production_tab.load_data)
            _QTimer.singleShot(160, self.history_tab.load_all_cases)
            _QTimer.singleShot(240, self.dashboard_tab.refresh)
        self.register_tab.ot_saved.connect(_deferred_refresh_after_ot_save)

        # Connect standards tab to refresh app data when standards change
        self.standards_tab.standards_updated.connect(self.on_standards_updated)

        self._justification_blocking = False
        if _PERF_OK and _JUSTIFICATION_ENABLED:
            self.register_tab.case_saved.connect(self._check_performance_after_save)
            self._start_eod_timer()

        # Break reminder timer — asks "going on break?" ~10 min before each break
        self._break_reminder_shown = set()  # (fecha, break_id) already asked today
        self._start_break_reminder_timer()

        # Fallback qtawesome icons used only by the theme-toggle handler when it
        # needs to recolor icons in legacy paths. The sidebar nav uses native
        # FluentIcon (FIF) which auto-themes light/dark — see _fluent_tab_icons.
        self._tab_icons = [
            'fa5s.edit', 'fa5s.chart-bar',
            'fa5s.history', 'fa5s.cog', 'fa5s.tachometer-alt',
        ]
        from qfluentwidgets import FluentIcon as FIF
        from tabs.tabler_icons import TablerIcon as _TI
        self._fluent_tab_icons = [
            _TI("tabler_pencil_plus.svg"),         # Register (Case)
            _TI("tabler_brand_databricks.svg"),    # Production
            _TI("tabler_history.svg"),             # History
            _TI("tabler_file_time.svg"),           # Standards
            _TI("tabler_file_analytics.svg"),      # Dashboard
            _TI("tabler_flag.svg"),                # Review
        ]
        # Review tab — flagged cases for follow-up. Stored in a separate
        # `cases_review` table; the original cases remain in the normal
        # production flow.
        from tabs.tab_review import ReviewTab
        self.review_tab = ReviewTab()
        _safe_connect(self.fontSizeChanged, self.review_tab,
                      "update_font_sizes", "review.update_font_sizes")

        self.tabs.addTab(self.register_tab,   self._fluent_tab_icons[0], "Case")
        self.tabs.addTab(self.production_tab, self._fluent_tab_icons[1], "Production")
        self.tabs.addTab(self.history_tab,    self._fluent_tab_icons[2], "History")
        self.tabs.addTab(self.standards_tab,  self._fluent_tab_icons[3], "Standards")
        self.tabs.addTab(self.dashboard_tab,  self._fluent_tab_icons[4], "Dashboard")
        self.tabs.addTab(self.review_tab,     self._fluent_tab_icons[5], "Review")
        self.setCentralWidget(self.tabs)

        # Help icon docked at the top of the sidebar — click opens a modal
        # with version + hotkeys.
        try:
            from PySide6.QtWidgets import QToolButton as _QTB_h
            from PySide6.QtGui import QColor as _QC_h
            from PySide6.QtCore import QSize as _QSh, Qt as _Qth
            from tabs.tabler_icons import TablerIcon as _TI_h
            self._help_btn = _QTB_h()
            self._help_btn.setFixedSize(32, 32)
            self._help_btn.setCursor(_Qth.PointingHandCursor)
            self._help_btn.setIcon(_TI_h("tabler_help_circle.svg").icon(color=_QC_h("#8B949E")))
            self._help_btn.setIconSize(_QSh(20, 20))
            self._help_btn.setToolTip("Help · shortcuts · version")
            self._help_btn.clicked.connect(self._show_help_modal)
            self._repaint_help_btn(False)  # initial dark style
            self.tabs.add_top_action(self._help_btn)
        except Exception as _e:
            print(f"[main] help button skipped: {_e}")

        # ── Global clipboard-import shortcut (Ctrl+Shift+I) ─────────────────────
        # After copying a case page (Ctrl+A, Ctrl+C in the browser), press
        # Ctrl+Shift+I here to auto-fill Register.
        import_shortcut = QShortcut(QKeySequence("Ctrl+Shift+I"), self)
        import_shortcut.activated.connect(self._trigger_import_shortcut)

        # Font-size shortcuts: Ctrl+= / Ctrl++ (zoom in), Ctrl+- (zoom out),
        # Ctrl+0 (reset). Both `=` and `+` bound because `+` typically needs Shift.
        for seq in ("Ctrl+=", "Ctrl++", "Ctrl+Shift+="):
            sc = QShortcut(QKeySequence(seq), self)
            sc.activated.connect(lambda: self._change_font_size(1))
        sc_minus = QShortcut(QKeySequence("Ctrl+-"), self)
        sc_minus.activated.connect(lambda: self._change_font_size(-1))
        sc_zero = QShortcut(QKeySequence("Ctrl+0"), self)
        sc_zero.activated.connect(self._reset_font_size)

        # Ctrl+Shift+L → toggle dark/light theme (matches the sidebar button).
        sc_theme = QShortcut(QKeySequence("Ctrl+Shift+L"), self)
        sc_theme.activated.connect(self._toggle_theme_shortcut)

        # Get screen height for maximum window height
        screen = QGuiApplication.primaryScreen()
        screen_height = screen.availableGeometry().height()
        
        # Component-driven sizing — open at the layout's natural max width
        # (1128 px) clamped to the available screen, full screen height.
        # Width is locked: min == max so the user can only resize vertically.
        screen_width = screen.availableGeometry().width()
        max_width = min(1128, screen_width)
        # Track the base dims so font-size changes can scale the window
        # proportionally (cards stay readable instead of being squeezed).
        self._base_window_width = max_width
        self._base_window_height = screen_height
        self._base_font_px = 12  # the canonical default — Ctrl+0 returns here
        self.setMinimumSize(max_width, 550)
        self.setMaximumSize(screen_width, screen_height)
        self.resize(max_width, screen_height)
        # Bottom sidebar action icons — use FluentIcon family for visual
        # parity with the top nav items (same paint, same hover state).
        from qfluentwidgets import FluentIcon as _FIF
        from tabs.tabler_icons import TablerIcon
        try:
            self._sb_widgets["load_db"] = self.tabs.add_bottom_nav_action(
                TablerIcon("tabler_database.svg"),
                "Import cases from an old cases.db file",
                self.register_tab._on_load_db,
            )
        except Exception as exc:
            log_event("main", f"sidebar load_db setup failed: {exc}", level="WARN")

        try:
            self._sb_widgets["breaks"] = self.tabs.add_bottom_nav_action(
                TablerIcon("tabler_bell_school.svg"), "Configure break times",
                self._open_breaks_dialog,
            )
        except Exception as exc:
            log_event("main", f"sidebar breaks setup failed: {exc}", level="WARN")

        try:
            self._sb_widgets["palette"] = self.tabs.add_bottom_nav_action(
                TablerIcon("tabler_palette.svg"), "Customize Light Mode Colors",
                self._open_theme_config_dialog,
            )
        except Exception as exc:
            log_event("main", f"sidebar palette setup failed: {exc}", level="WARN")

        try:
            self._sb_widgets["ue_target"] = self.tabs.add_bottom_nav_action(
                TablerIcon("tabler_target.svg"), "Configure daily UE target by date",
                self._open_ue_target_dialog,
            )
        except Exception as exc:
            log_event("main", f"sidebar ue_target setup failed: {exc}", level="WARN")

        # Theme toggle checkbox in status bar
        try:
            # Theme toggle — animated sun/moon icon + "Theme: …" label.
            from PySide6.QtWidgets import (
                QPushButton as _QPB, QFrame as _QFr, QGraphicsOpacityEffect,
            )
            from PySide6.QtGui import QColor as _QColor
            from PySide6.QtCore import (
                QSize, QPropertyAnimation as _QPA, QEasingCurve as _QEC,
                QSequentialAnimationGroup as _QSAG,
            )
            from tabs.tabler_icons import TablerIcon as _TIT
            _sun = _TIT("tabler_sun.svg")
            _moon = _TIT("tabler_moon.svg")

            def _theme_icon(is_light: bool, fg_color=None):
                # Icon = the action you'd take next.
                #   Dark active → show sun (click to switch to light).
                #   Light active → show moon (click to switch back to dark).
                color = fg_color or (_QColor("#1F2328") if is_light else _QColor("#E6EDF3"))
                src = _moon if is_light else _sun
                return src.icon(color=color)

            theme_btn = _QPB()
            theme_btn.setObjectName("themeToggle")
            theme_btn.setCheckable(True)
            theme_btn.setFlat(True)
            theme_btn.setCursor(Qt.PointingHandCursor)
            theme_btn.setIcon(_theme_icon(False))
            theme_btn.setIconSize(QSize(18, 18))
            theme_btn.setToolTip("Toggle theme")

            theme_label = QLabel("Theme: Dark")
            theme_label.setObjectName("themeLabel")
            self._sb_widgets["theme_btn"] = theme_btn
            self._sb_widgets["theme_label"] = theme_label

            # Pill: "Theme: <name>  |  [animated sun/moon icon]"
            theme_wrap = _QFr()
            theme_wrap.setObjectName("themeWrap")
            theme_divider = _QFr()
            theme_divider.setObjectName("themeDivider")
            theme_divider.setFrameShape(_QFr.Shape.VLine)
            self._sb_widgets["theme_divider"] = theme_divider
            _tw_lay = QHBoxLayout(theme_wrap)
            # Right-padded so the theme pill sits ~60px to the left of the
            # next status-bar widget (the font group).
            _tw_lay.setContentsMargins(10, 0, 66, 0)
            _tw_lay.setSpacing(8)
            _tw_lay.addWidget(theme_label)
            _tw_lay.addWidget(theme_divider)
            _tw_lay.addWidget(theme_btn)
            self._sb_widgets["theme_wrap"] = theme_wrap
            self.statusBar().addPermanentWidget(theme_wrap)

            # Cool fade for the sun↔moon swap.
            _fx = QGraphicsOpacityEffect(theme_btn)
            _fx.setOpacity(1.0)
            theme_btn.setGraphicsEffect(_fx)
            _fade_out = _QPA(_fx, b"opacity", theme_btn)
            _fade_out.setDuration(140)
            _fade_out.setStartValue(1.0); _fade_out.setEndValue(0.0)
            _fade_out.setEasingCurve(_QEC.OutCubic)
            _fade_in = _QPA(_fx, b"opacity", theme_btn)
            _fade_in.setDuration(160)
            _fade_in.setStartValue(0.0); _fade_in.setEndValue(1.0)
            _fade_in.setEasingCurve(_QEC.OutCubic)
            _seq = _QSAG(theme_btn)
            _seq.addAnimation(_fade_out); _seq.addAnimation(_fade_in)

            def _light_palette_name() -> str:
                try:
                    cfg = load_config()
                    nm = cfg.get("light_palette_name")
                    return str(nm) if nm else "GitHub Light"
                except Exception:
                    return "GitHub Light"
            self._light_palette_name = _light_palette_name

            def on_theme_toggled(is_light: bool):
                self._is_light = is_light
                self._apply_style()
                if hasattr(self, "_refresh_font_icons"):
                    try:
                        self._refresh_font_icons(is_light)
                    except Exception:
                        pass

                def _swap():
                    theme_btn.setIcon(_theme_icon(is_light))
                    if is_light:
                        theme_label.setText(f"Theme: {_light_palette_name()}")
                    else:
                        theme_label.setText("Theme: Dark")
                _fade_out.finished.connect(_swap, type=Qt.SingleShotConnection)
                _seq.start()

                try:
                    from qfluentwidgets import setTheme, Theme
                    setTheme(Theme.LIGHT if is_light else Theme.DARK)
                except Exception:
                    pass
                try:
                    self.themeChanged.emit(is_light)
                except Exception as exc:
                    log_event("main", f"themeChanged emit failed: {exc}", level="WARN")

            theme_btn.toggled.connect(on_theme_toggled)

            # Font size buttons grouped in a single rounded pill (A-, divider, A+).
            from tabs.tabler_icons import TablerIcon as _TI
            _font_up_src = _TI("tabler_text_increase.svg")
            _font_dn_src = _TI("tabler_text_decrease.svg")

            btn_fup = _QPB()
            btn_fdn = _QPB()
            self._sb_widgets["fup"] = btn_fup
            self._sb_widgets["fdn"] = btn_fdn

            _font_reset_src = _TI("tabler_refresh.svg")
            btn_freset = _QPB()
            self._sb_widgets["freset"] = btn_freset

            def _refresh_font_icons(is_light: bool):
                c = _QColor("#1F2328") if is_light else _QColor("#E6EDF3")
                btn_fup.setIcon(_font_up_src.icon(color=c))
                btn_fdn.setIcon(_font_dn_src.icon(color=c))
                btn_freset.setIcon(_font_reset_src.icon(color=c))
            self._refresh_font_icons = _refresh_font_icons
            _refresh_font_icons(False)

            btn_fup.setToolTip("Increase font size (Ctrl+=)")
            btn_fdn.setToolTip("Decrease font size (Ctrl+-)")
            btn_freset.setToolTip("Reset font size (Ctrl+0)")
            btn_fup.clicked.connect(lambda: self._change_font_size(1))
            btn_fdn.clicked.connect(lambda: self._change_font_size(-1))
            btn_freset.clicked.connect(self._reset_font_size)

            font_group = _QFr()
            font_group.setObjectName("fontGroup")
            font_layout = QHBoxLayout(font_group)
            font_layout.setContentsMargins(0, 0, 0, 0)
            font_layout.setSpacing(0)
            font_divider = _QFr()
            font_divider.setFrameShape(_QFr.Shape.VLine)
            font_divider.setObjectName("fontDivider")
            font_divider2 = _QFr()
            font_divider2.setFrameShape(_QFr.Shape.VLine)
            font_divider2.setObjectName("fontDivider")
            font_layout.addWidget(btn_fdn)
            font_layout.addWidget(font_divider)
            font_layout.addWidget(btn_freset)
            font_layout.addWidget(font_divider2)
            font_layout.addWidget(btn_fup)
            self._sb_widgets["font_group"] = font_group
            self.statusBar().addPermanentWidget(font_group)
        except Exception as exc:
            log_event("main", f"statusbar theme controls setup failed: {exc}", level="WARN")

        # Apply initial style — scales QSS to persisted font_size and sizes
        # status-bar widgets correctly on first show.
        self._apply_style()

    def _open_breaks_dialog(self):
        """Open the breaks configuration dialog."""
        dlg = BreaksDialog(self)
        dlg.exec()

    def _open_ue_target_dialog(self):
        """Open the editor for the daily UE target by date."""
        try:
            from tabs.ue_target_dialog import UETargetDialog
        except ImportError as exc:
            QMessageBox.warning(self, "Unavailable", f"Could not load UE target editor:\n{exc}")
            return
        dlg = UETargetDialog(self)
        if dlg.exec():
            # Refresh the daily production labels so the new target shows up immediately
            try:
                self.register_tab.load_daily_production()
            except Exception as exc:
                log_event("main", f"refresh after UE target change failed: {exc}", level="WARN")

    def _open_theme_config_dialog(self):
        # Prefer the Fluent-styled wrapper; fall back to the inline panel
        # inside a plain QDialog if qfluentwidgets isn't available.
        from tabs.tab_theme_config import build_theme_dialog
        dlg = build_theme_dialog(self)
        if dlg is not None:
            dlg.theme_widget.theme_colors_changed.connect(self._on_light_palette_changed)
            dlg.exec()
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Light Theme Colors")
        dlg.setMinimumWidth(560)
        dlg.setMinimumHeight(520)
        layout = QVBoxLayout(dlg)
        cfg = ThemeConfigTab()
        cfg.theme_colors_changed.connect(self._on_light_palette_changed)
        layout.addWidget(cfg)
        dlg.exec()
    # ── Break reminder ─────────────────────────────────────────────────────
    def _start_break_reminder_timer(self):
        """Check every 60s if a break starts in ~10 minutes and ask the user."""
        self._break_timer = QTimer(self)
        self._break_timer.setInterval(60_000)  # every 60 seconds
        self._break_timer.timeout.connect(self._check_break_reminder)
        self._break_timer.start()

    def _check_break_reminder(self):
        """If a configured break starts in ≤10 minutes, show a popup asking
        whether the user is going on that break."""
        now = datetime.now()
        today = date.today()
        # Weekend: no breaks → skip the reminder loop entirely.
        if today.weekday() >= 5:
            return
        today_str = today.isoformat()
        now_mins = now.hour * 60 + now.minute

        from tabs.breaks_dialog import get_active_schedule_for_date
        breaks = get_breaks(get_active_schedule_for_date(today))
        answered = get_breaks_answered_today(today_str)

        for bid, name, b_start_str, _b_end_str in breaks:
            if bid in answered:
                continue  # already answered today
            key = (today_str, bid)
            if key in self._break_reminder_shown:
                continue  # already shown popup this session
            b_start = _to_minutes(b_start_str)
            # Show popup when we are 0–10 minutes before the break starts
            mins_until = b_start - now_mins
            if 0 <= mins_until <= 10:
                self._break_reminder_shown.add(key)
                self._show_break_popup(today_str, bid, name, b_start_str, _b_end_str)

    def _show_break_popup(self, fecha: str, break_id: int, name: str,
                          start: str, end: str):
        """Fluent reminder modal: 'Going on break?' for ``name`` (start–end)."""
        took = self._fluent_break_reminder(name, start, end)
        set_break_attendance(fecha, break_id, took)

    def _fluent_break_reminder(self, name: str, start: str, end: str) -> bool:
        """Modal Fluent prompt — Yes/No. Falls back to QMessageBox if Fluent
        isn't available."""
        try:
            from qfluentwidgets import MessageBoxBase
            from tabs.tabler_icons import TablerIcon
        except Exception:
            r = QMessageBox.question(
                self, "Break Reminder",
                f"Going on break ({name} {start}–{end})?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            return r == QMessageBox.StandardButton.Yes

        from PySide6.QtCore import (
            QPropertyAnimation, QEasingCurve, Property, QSize,
        )
        from PySide6.QtGui import QColor, QPainter
        from PySide6.QtWidgets import (
            QFrame as _QF, QToolButton as _QTB, QWidget as _QW,
        )

        host = self

        class _Sheet(MessageBoxBase):
            def __init__(_s, h):
                super().__init__(h.window() if h is not None else None)
                try:
                    _s.setMaskColor(QColor(0, 0, 0, 170))
                except Exception:
                    pass
                _s.widget.setObjectName("brkReminderCard")
                _s.widget.setStyleSheet(
                    "#brkReminderCard { background: #101824;"
                    " border: 1px solid #21262D; border-radius: 14px; }"
                )
                _s.buttonGroup.setStyleSheet(
                    "QFrame { background: #101824; border: none; }"
                )
                _s.viewLayout.setContentsMargins(22, 18, 22, 12)
                _s.viewLayout.setSpacing(10)

                # Header.
                hdr = QHBoxLayout(); hdr.setSpacing(12)
                ic = _QTB()
                ic.setEnabled(False)
                ic.setIcon(TablerIcon("tabler_coffee.svg").icon(color=QColor("#FFFFFF")))
                ic.setIconSize(QSize(22, 22))
                ic.setStyleSheet(
                    "background: transparent; border: none; padding: 0;"
                )
                tc = QVBoxLayout(); tc.setSpacing(2)
                t = QLabel("Break reminder")
                t.setStyleSheet(
                    "color: #E6EDF3; font-size: 15px; font-weight: 700;"
                    " background: transparent;"
                )
                s = QLabel("Are you going on this break?")
                s.setStyleSheet(
                    "color: #8B949E; font-size: 11px; background: transparent;"
                )
                tc.addWidget(t); tc.addWidget(s)
                hdr.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
                hdr.addLayout(tc, 1)
                _s.viewLayout.addLayout(hdr)

                # Big break name + time range row (borderless).
                pill = _QF()
                pill.setStyleSheet(
                    "QFrame { background: #161B22; border: none;"
                    " border-radius: 10px; }"
                    "QLabel { background: transparent; }"
                )
                pl = QHBoxLayout(pill)
                pl.setContentsMargins(14, 10, 14, 10)
                pl.setSpacing(10)
                n_lbl = QLabel(name)
                n_lbl.setStyleSheet(
                    "color: #E6EDF3; font-size: 14px; font-weight: 700;"
                )
                pl.addWidget(n_lbl, 1)
                t_lbl = QLabel(f"{start}  –  {end}")
                t_lbl.setStyleSheet(
                    "color: #F0883E; font-size: 14px; font-weight: 800;"
                    " font-family: 'Consolas','Menlo',monospace;"
                )
                pl.addWidget(t_lbl, 0, Qt.AlignmentFlag.AlignRight)
                _s.viewLayout.addWidget(pill)

                # Hint card.
                tip = _QF()
                tip.setStyleSheet(
                    "QFrame { background: rgba(56,139,253,0.08);"
                    " border: 1px solid rgba(56,139,253,0.30);"
                    " border-radius: 10px; }"
                    "QLabel { background: transparent; border: none;"
                    " color: #C9D1D9; font-size: 11px; }"
                )
                tl = QHBoxLayout(tip)
                tl.setContentsMargins(12, 10, 12, 10)
                tl.setSpacing(8)
                bulb = _QTB()
                bulb.setEnabled(False)
                bulb.setIcon(TablerIcon("tabler_bulb.svg").icon(color=QColor("#58A6FF")))
                bulb.setIconSize(QSize(16, 16))
                bulb.setStyleSheet("background: transparent; border: none;")
                tl.addWidget(bulb, 0, Qt.AlignmentFlag.AlignTop)
                tl.addWidget(QLabel(
                    "If YES, this break is subtracted from any case that "
                    "overlaps with it."
                ), 1)
                _s.viewLayout.addWidget(tip)

                _s.widget.setMinimumWidth(440)

                # Buttons.
                _s.buttonLayout.removeWidget(_s.yesButton)
                _s.buttonLayout.removeWidget(_s.cancelButton)
                _s.buttonLayout.addStretch(1)
                _s.cancelButton.setText("Not now")
                _s.cancelButton.setFixedWidth(120)
                _s.cancelButton.setStyleSheet(
                    "QPushButton { background: transparent; border: 1px solid #30363D;"
                    "  color: #E6EDF3; border-radius: 6px; padding: 8px 22px;"
                    "  font-weight: 700; font-size: 12px; }"
                    "QPushButton:hover { background: rgba(255,255,255,0.05); }"
                )
                _s.yesButton.setText("   Yes, on break")
                _s.yesButton.setFixedWidth(160)
                _s.yesButton.setStyleSheet(
                    "QPushButton { background: #F0883E; border: 1px solid #F0883E;"
                    "  color: white; border-radius: 6px; padding: 8px 22px;"
                    "  font-weight: 700; font-size: 12px; }"
                    "QPushButton:hover { background: #F49852; }"
                )
                try:
                    _s.yesButton.setIcon(
                        TablerIcon("tabler_check.svg").icon(color=QColor("#FFFFFF"))
                    )
                    _s.yesButton.setIconSize(QSize(14, 14))
                except Exception:
                    pass
                _s.buttonLayout.addWidget(_s.cancelButton, 0, Qt.AlignmentFlag.AlignVCenter)
                _s.buttonLayout.addWidget(_s.yesButton, 0, Qt.AlignmentFlag.AlignVCenter)

        return bool(_Sheet(host).exec())

    # ── Daily performance / justification logic ───────────────────────────────

    @staticmethod
    def _is_weekday() -> bool:
        return date.today().weekday() < 5  # 0=Mon, 4=Fri

    def _start_eod_timer(self):
        if not _PERF_OK or not _JUSTIFICATION_ENABLED:
            return
        self._eod_timer = QTimer(self)
        self._eod_timer.setInterval(30_000)
        self._eod_timer.timeout.connect(self._check_eod_trigger)
        self._eod_timer.start()

    def _check_eod_trigger(self):
        if not _JUSTIFICATION_ENABLED or not self._is_weekday():
            return
        now = datetime.now()
        if now.hour < 15 or (now.hour == 15 and now.minute < 15):
            return
        today_str = date.today().isoformat()
        from db.database import get_connection
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT justification_submitted FROM daily_performance WHERE fecha = ?",
                (today_str,),
            )
            row = cur.fetchone()
        finally:
            conn.close()
        if row and row[0]:
            return
        metrics = get_daily_metrics(today_str)
        record_daily_snapshot(today_str, metrics)
        if metrics["met_target"]:
            return
        if metrics["production_pct"] == 0 and metrics["equivalent_units"] == 0:
            return
        self._eod_timer.stop()
        self._show_justification_popup(today_str, metrics)

    def _check_performance_after_save(self):
        # Success popup is decoupled from the justification feature — fires
        # any time the user crosses the daily UE target for the first time
        # on a given date, regardless of whether justification is enabled.
        if not _PERF_OK or not self._is_weekday():
            return
        today_str = date.today().isoformat()
        metrics = get_daily_metrics(today_str)
        record_daily_snapshot(today_str, metrics)
        ue_target = metrics.get("ue_target") or 0.0
        ue_value = metrics.get("equivalent_units") or 0.0
        if (ue_target > 0 and ue_value >= ue_target
                and not was_success_shown(today_str)):
            mark_success_shown(today_str)
            dlg = SuccessPopup(metrics, today_str, parent=self)
            dlg.exec()

    def _check_pending_justification_on_start(self):
        if not _PERF_OK or not _JUSTIFICATION_ENABLED or not self._is_weekday():
            return
        pending_date = has_pending_justification()
        if not pending_date:
            return
        metrics = get_daily_metrics(pending_date)
        self._show_justification_popup(pending_date, metrics)

    def _show_justification_popup(self, fecha: str, metrics: dict):
        if not _JUSTIFICATION_ENABLED:
            return
        self._justification_blocking = True
        dlg = JustificationPopup(metrics, fecha, parent=self)
        dlg.exec()
        text = dlg.justification_text
        if text:
            save_justification(fecha, text)
            try:
                from sync.app_config import load_config
                cfg = load_config()
                designer = cfg.get("designer_name", "")
                export_justification(designer, fecha, metrics, text)
            except Exception as exc:
                print(f"[main] Justification export failed: {exc}")
        self._justification_blocking = False

    def closeEvent(self, event):
        if _JUSTIFICATION_ENABLED:
            if self._justification_blocking:
                event.ignore()
                return
            if _PERF_OK and self._is_weekday():
                now = datetime.now()
                if now.hour > 15 or (now.hour == 15 and now.minute >= 15):
                    today_str = date.today().isoformat()
                    metrics = get_daily_metrics(today_str)
                    record_daily_snapshot(today_str, metrics)
                    if not metrics["met_target"] and (metrics["production_pct"] > 0 or metrics["equivalent_units"] > 0):
                        from db.database import get_connection
                        conn = get_connection()
                        try:
                            cur = conn.cursor()
                            cur.execute(
                                "SELECT justification_submitted FROM daily_performance WHERE fecha = ?",
                                (today_str,),
                            )
                            row = cur.fetchone()
                        finally:
                            conn.close()
                        if not row or not row[0]:
                            event.ignore()
                            self._show_justification_popup(today_str, metrics)
                            return
        # Stop child timers explicitly so they can't fire after Qt cleanup.
        try:
            dm = getattr(self.register_tab, "downtime_manager", None)
            if dm is not None:
                if hasattr(dm, "_stop_refresh_timer"):
                    dm._stop_refresh_timer()
                if hasattr(dm, "_stop_retry_timer"):
                    dm._stop_retry_timer()
        except Exception:
            pass
        event.accept()

    def _check_first_use(self):
        """Show name confirmation dialog if the designer hasn't confirmed their name yet."""
        try:
            from sync.app_config import load_config, save_config, get_windows_display_name
            cfg = load_config()
            if cfg.get("name_confirmed"):
                return
            suggested = cfg.get("designer_name") or get_windows_display_name()

            dlg = QDialog(self)
            dlg.setWindowTitle("Setup — Your Name")
            dlg.setFixedWidth(380)
            dlg.setWindowFlags(dlg.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)
            layout = QVBoxLayout(dlg)
            layout.setContentsMargins(20, 18, 20, 18)
            layout.setSpacing(10)

            lbl = QLabel("Your name will appear on all production reports.\n"
                         "Confirm or change it:")
            lbl.setWordWrap(True)
            layout.addWidget(lbl)

            name_edit = QLineEdit(suggested)
            name_edit.setPlaceholderText("Your full name")
            layout.addWidget(name_edit)

            btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
            btns.accepted.connect(dlg.accept)
            layout.addWidget(btns)

            if dlg.exec() == QDialog.DialogCode.Accepted:
                name = name_edit.text().strip() or suggested
                cfg["designer_name"] = name
                cfg["name_confirmed"] = True
                save_config(cfg)
        except Exception as exc:
            log_event("main", f"first-use setup failed: {exc}", level="WARN")

    def on_standards_updated(self):
        """Reload standards and units_eq in Register and Production tabs when standards are modified."""
        load_units_eq_data(force=True)  # Invalidate shared cache so all tabs pick up new values
        self.register_tab.load_standards()
        self.register_tab.load_units_eq()
        self.register_tab.update_case_types()
        self.production_tab.load_units_eq()
        self.dashboard_tab._load_metadata()
        self.dashboard_tab.refresh()

    def on_production_case_updated(self):
        """Handle case update/delete from production tab"""
        # Check if production_tab has an editing_case_id (edit action)
        if hasattr(self.production_tab, 'editing_case_id') and self.production_tab.editing_case_id:
            # Route edit to Register depending on what was selected in ProductionTab
            editing_mode = getattr(self.production_tab, 'editing_mode', 'reg')
            db_id = self.production_tab.editing_case_id
            try:
                if editing_mode == 'ot':
                    self.register_tab.load_ot_case_for_edit(db_id)
                else:
                    self.register_tab.load_case_for_edit(db_id)
                self.tabs.setCurrentIndex(0)  # Always switch to Register tab
            finally:
                # clear editing marker on production tab
                self.production_tab.editing_case_id = None
        else:
            # Delete action — refresh whichever tab the deletion came from
            mode = getattr(self.production_tab, 'current_mode', 'reg')
            if mode == 'ot':
                self.register_tab._load_ot_day_cases()
                self.history_tab.load_all_cases()
                self.dashboard_tab.refresh()
            else:
                self.register_tab.load_daily_production()
                # Today's Cases table in Register also needs to refresh
                # after a case is deleted from Production.
                if hasattr(self.register_tab, "_load_regular_day_cases"):
                    self.register_tab._load_regular_day_cases()
                self.history_tab.load_all_cases()
                self.dashboard_tab.refresh()
        
        # Refresh history tab
        self.history_tab.load_all_cases()

    def _apply_style(self):
        """Re-apply the current stylesheet, scaling every `font-size: Npx`
        proportionally to the active font size (base = 12px).

        Result is cached by (theme, font_size, palette_id) so font/theme
        toggles don't re-run the regex over the whole QSS each time.
        """
        base = self._light_style if self._is_light else self._dark_style
        cache_key = (
            bool(self._is_light),
            int(self._font_size),
            # palette can mutate the light stylesheet; hash by identity.
            id(base),
        )
        cache = getattr(self, "_style_cache", None)
        if cache is None:
            cache = {}
            self._style_cache = cache
        styled = cache.get(cache_key)
        if styled is None:
            ratio = self._font_size / 12.0

            def _scale(match):
                v = int(match.group(1))
                return f"font-size: {max(6, round(v * ratio))}px"

            styled = re.sub(r"font-size:\s*(\d+)px", _scale, base)
            cache[cache_key] = styled
            # Bound the cache so palette swaps don't grow it forever.
            if len(cache) > 32:
                cache.pop(next(iter(cache)))

        QApplication.instance().setStyleSheet(styled)
        self._apply_status_bar_styles()

    def _apply_status_bar_styles(self):
        """Refresh inline stylesheets and sizes of status-bar widgets.
        These widgets carry their own stylesheets that bypass the app QSS,
        so we rebuild them whenever the global font size changes."""
        # StatusBar dimensions stay CONSTANT — the A+ / A- font controls only
        # affect content inside the tabs, not the bottom bar widgets.
        sb = 11
        h = 32
        w_side = 36
        w_af = 38
        ratio = 1.0

        # statusBar bg matches the sidebar (which inherits the app base), with
        # a slightly taller min-height so the pill controls breathe.
        sb_bar = self.statusBar()
        if sb_bar is not None:
            sb_bar.setMinimumHeight(h + 12)
            sb_bar.setStyleSheet(
                "QStatusBar {"
                f"  background-color: {'#F6F8FA' if self._is_light else '#0D1117'};"
                f"  border-top: 1px solid {'#D0D7DE' if self._is_light else '#21262D'};"
                f"  color: {'#1F2328' if self._is_light else '#8B949E'};"
                f"  font-size: {sb}px;"
                "}"
                "QStatusBar::item { border: none; }"
            )

        # load_db / breaks / palette / ue_target are sidebar nav items now;
        # they handle their own size + paint via _CompactNavButton — skip.

        # Palette-aware tokens.
        if self._is_light:
            bg, border, fg, divider = "#FFFFFF", "#D0D7DE", "#1F2328", "#D0D7DE"
            overlay = "0,0,0"
        else:
            bg, border, fg, divider = "#161B22", "#30363D", "#E6EDF3", "#30363D"
            overlay = "255,255,255"

        # A- / reset / A+ inside a single rounded pill with dividers between.
        af_icon = QSize(round(h * 0.62), round(h * 0.62))
        for key in ("fup", "fdn", "freset"):
            btn = self._sb_widgets.get(key)
            if btn is None:
                continue
            btn.setFixedSize(w_af, h)
            btn.setIconSize(af_icon)
            btn.setStyleSheet(
                "QPushButton { background: transparent; border: none; padding: 0; }"
                f"QPushButton:hover {{ background: rgba({overlay},0.08); }}"
                f"QPushButton:pressed {{ background: rgba({overlay},0.16); }}"
            )

        font_group = self._sb_widgets.get("font_group")
        if font_group is not None:
            font_group.setFixedHeight(h)
            font_group.setStyleSheet(
                "#fontGroup { background: transparent; border: none; }"
            )

        # Find and slim the font divider — VLine frame defaults paint thicker
        # than 1px because of the frame shadow. Switch to NoFrame + flat bg.
        for child in font_group.findChildren(QFrame) if font_group is not None else []:
            if child.objectName() == "fontDivider":
                child.setFrameShape(QFrame.Shape.NoFrame)
                child.setFixedWidth(1)
                child.setFixedHeight(round(h * 0.55))
                child.setStyleSheet(f"background: {divider}; border: none;")

        # Theme pill — no card. Plain label | icon.
        theme_wrap = self._sb_widgets.get("theme_wrap")
        if theme_wrap is not None:
            theme_wrap.setFixedHeight(h)
            theme_wrap.setStyleSheet("background: transparent; border: none;")

        theme_label = self._sb_widgets.get("theme_label")
        if theme_label is not None:
            theme_label.setStyleSheet(
                f"color: {fg}; font-size: {sb}px; font-weight: 600;"
                " background: transparent; border: none;"
            )
            if self._is_light and hasattr(self, "_light_palette_name"):
                theme_label.setText(f"Theme: {self._light_palette_name()}")
            elif not self._is_light:
                theme_label.setText("Theme: Dark")

        # Force the button itself to be borderless — overrides global QSS.
        theme_btn = self._sb_widgets.get("theme_btn")
        if theme_btn is not None:
            theme_btn.setFixedSize(h - 6, h - 6)
            theme_btn.setIconSize(QSize(round((h - 6) * 0.78), round((h - 6) * 0.78)))
            theme_btn.setStyleSheet(
                "QPushButton { background: transparent; border: none; padding: 0; }"
                f"QPushButton:hover {{ background: rgba({overlay},0.10);"
                f"  border-radius: 6px; }}"
            )

        theme_divider = self._sb_widgets.get("theme_divider")
        if theme_divider is not None:
            theme_divider.setFrameShape(theme_divider.Shape.NoFrame)
            theme_divider.setFixedWidth(1)
            theme_divider.setFixedHeight(round(h * 0.55))
            theme_divider.setStyleSheet(
                f"background: {divider}; border: none;"
            )

    def _apply_light_palette(self, colors: dict):
        mapping = {
            "#F6F8FA": colors.get("base_bg", DEFAULT_LIGHT_COLORS["base_bg"]),
            "#FFFFFF": colors.get("surface_bg", DEFAULT_LIGHT_COLORS["surface_bg"]),
            "#1F2328": colors.get("text_primary", DEFAULT_LIGHT_COLORS["text_primary"]),
            "#656D76": colors.get("text_muted", DEFAULT_LIGHT_COLORS["text_muted"]),
            "#D0D7DE": colors.get("border", DEFAULT_LIGHT_COLORS["border"]),
            "#0969DA": colors.get("accent", DEFAULT_LIGHT_COLORS["accent"]),
            "#DDF4FF": colors.get("selection_bg", DEFAULT_LIGHT_COLORS["selection_bg"]),
            "#EAEEF2": colors.get("button_bg", DEFAULT_LIGHT_COLORS["button_bg"]),
        }
        styled = self._light_style_base
        for old, new in mapping.items():
            if isinstance(new, str) and new.strip():
                styled = styled.replace(old, new.strip().upper())
        self._light_style = styled

    def _load_and_apply_light_palette(self):
        cfg = load_config()
        colors = cfg.get("light_theme_colors", {}) if isinstance(cfg, dict) else {}
        if not isinstance(colors, dict):
            colors = {}
        merged = dict(DEFAULT_LIGHT_COLORS)
        merged.update({k: v for k, v in colors.items() if k in DEFAULT_LIGHT_COLORS})
        self._apply_light_palette(merged)

    def _on_light_palette_changed(self, colors: dict):
        self._apply_light_palette(colors or {})
        # Refresh the status-bar "Theme: <name>" label if currently in light mode.
        if self._is_light:
            try:
                lbl = self._sb_widgets.get("theme_label")
                if lbl is not None and hasattr(self, "_light_palette_name"):
                    lbl.setText(f"Theme: {self._light_palette_name()}")
            except Exception:
                pass
            self._apply_style()
            try:
                self.themeChanged.emit(True)
            except Exception as exc:
                log_event("main", f"themeChanged emit after palette update failed: {exc}", level="WARN")

    def _change_font_size(self, delta: int):
        """Increment or decrement font size within [9, 20] range and re-apply style.
        Persists to config and notifies tabs so widgets with hard-coded QFont
        point sizes can refresh."""
        new_size = max(9, min(20, self._font_size + delta))
        if new_size == self._font_size:
            return
        self._font_size = new_size
        font_scale.set_current_px(new_size)
        # Push the new size onto the QApplication font so every widget
        # that doesn't override its font inline picks it up immediately.
        try:
            from PySide6.QtWidgets import QApplication as _QApp_fs
            from PySide6.QtGui import QFont as _QF_fs
            _app = _QApp_fs.instance()
            if _app is not None:
                f = _app.font()
                f.setPixelSize(new_size)
                _app.setFont(f)
        except Exception:
            pass
        self._apply_style()
        self._persist_font_size()
        # Only GROW the window when the user bumps the font size up.
        # On a decrease (or Ctrl+0 coming down from a larger size) we
        # leave the window where the user put it — the cards already fit.
        try:
            if delta > 0:
                base_w = getattr(self, "_base_window_width", self.width())
                base_px = getattr(self, "_base_font_px", 12)
                ratio = float(new_size) / float(base_px)
                screen_geo = QGuiApplication.primaryScreen().availableGeometry()
                target_w = min(int(base_w * ratio), screen_geo.width())
                # Never shrink horizontally — only grow.
                target_w = max(self.width(), target_w)
                # Vertical: keep whatever the user has now, don't touch it.
                self.setMaximumSize(screen_geo.width(), screen_geo.height())
                self.resize(target_w, self.height())
        except Exception as exc:
            log_event("main", f"window rescale on font change failed: {exc}", level="WARN")
        try:
            self.fontSizeChanged.emit(new_size)
        except Exception as exc:
            log_event("main", f"fontSizeChanged emit failed: {exc}", level="WARN")
        # Walk every widget that opted into the font hook
        # (apply_font_size) so individual point-sized labels can rescale.
        try:
            from PySide6.QtWidgets import QWidget as _QW_fs
            for w in self.findChildren(_QW_fs):
                fn = getattr(w, "apply_font_size", None)
                if callable(fn):
                    try:
                        fn(new_size)
                    except Exception:
                        pass
        except Exception:
            pass

    def _reset_font_size(self):
        """Reset font size to default (12px)."""
        self._change_font_size(12 - self._font_size)

    def _repaint_pro_cards(self, is_light: bool):
        """Walk the widget tree and call apply_palette() on any widget
        that opted into the theme hook (cards, palette-aware buttons,
        custom chips, etc.)."""
        from PySide6.QtWidgets import QWidget
        try:
            for w in self.findChildren(QWidget):
                fn = getattr(w, "apply_palette", None)
                if callable(fn):
                    try:
                        fn(is_light)
                    except Exception:
                        pass
        except Exception:
            pass

    def _repaint_help_btn(self, is_light: bool):
        """Restyle the sidebar help button for the active theme."""
        btn = getattr(self, "_help_btn", None)
        if btn is None:
            return
        try:
            from tabs.theme_palette import palette
            from tabs.tabler_icons import TablerIcon
            from PySide6.QtGui import QColor
            p = palette(is_light)
            btn.setIcon(
                TablerIcon("tabler_help_circle.svg").icon(color=QColor(p["muted"]))
            )
            hover = (
                "rgba(0,0,0,0.06)" if is_light else "rgba(255,255,255,0.06)"
            )
            btn.setStyleSheet(
                "QToolButton { background: transparent; border: none;"
                "  border-radius: 6px; }"
                f"QToolButton:hover {{ background: {hover}; }}"
            )
        except Exception:
            pass

    def _toggle_theme_shortcut(self):
        """Ctrl+Shift+L — flip dark↔light by clicking the sidebar toggle."""
        btn = None
        try:
            btn = self._sb_widgets.get("theme_btn")
        except Exception:
            btn = None
        if btn is not None:
            try:
                btn.toggle()
                return
            except Exception:
                pass
        # Fallback: flip the flag + reapply style.
        try:
            self._is_light = not getattr(self, "_is_light", False)
            self._apply_style()
        except Exception:
            pass

    def _persist_font_size(self):
        try:
            from sync.app_config import load_config, save_config
            cfg = load_config()
            cfg["font_size"] = int(self._font_size)
            save_config(cfg)
        except Exception as exc:
            log_event("main", f"font_size persist failed: {exc}", level="WARN")

    # ── Clipboard import shortcut ─────────────────────────────────────────────

    def _show_help_modal(self):
        """Help modal — version, hotkeys, dev info, feedback, about."""
        try:
            from qfluentwidgets import MessageBoxBase
            from tabs.tabler_icons import TablerIcon
            from PySide6.QtWidgets import (
                QToolButton as _QTBh, QFrame as _QFh,
            )
            from PySide6.QtGui import QColor as _QCh
            from PySide6.QtCore import QSize as _QSh2
        except Exception:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(
                self, f"Help · v{APP_VERSION}",
                "Ctrl+Shift+I — Clipboard import\n"
                "Ctrl+Shift+L — Toggle dark / light theme\n"
                "Ctrl+= / Ctrl++ — Increase font\n"
                "Ctrl+- — Decrease font\n"
                "Ctrl+0 — Reset font",
            )
            return

        host = self
        from datetime import datetime as _dt_now
        build_date = _dt_now.now().strftime("%Y.%m.%d")

        class _Sheet(MessageBoxBase):
            def __init__(_s, h):
                super().__init__(h.window() if h is not None else None)
                try:
                    _s.setMaskColor(_QCh(0, 0, 0, 170))
                except Exception:
                    pass
                _s.widget.setObjectName("helpCard")
                _s.widget.setStyleSheet(
                    "#helpCard { background: #101824;"
                    " border: 1px solid #21262D; border-radius: 14px; }"
                )
                _s.buttonGroup.setStyleSheet(
                    "QFrame { background: #101824; border: none; }"
                )
                _s.viewLayout.setContentsMargins(22, 18, 22, 12)
                _s.viewLayout.setSpacing(10)

                # ── Header (icon + title + version) ──
                hdr = QHBoxLayout(); hdr.setSpacing(12)
                ic = _QTBh(); ic.setEnabled(False)
                ic.setIcon(TablerIcon("tabler_help_circle.svg").icon(color=_QCh("#58A6FF")))
                ic.setIconSize(_QSh2(22, 22))
                ic.setStyleSheet(
                    "background: rgba(56,139,253,0.14); border: none;"
                    " border-radius: 10px; padding: 6px;"
                )
                tc = QVBoxLayout(); tc.setSpacing(2)
                t = QLabel("Production Performance Calculator")
                t.setStyleSheet(
                    "color: #E6EDF3; font-size: 15px; font-weight: 700;"
                    " background: transparent;"
                )
                s = QLabel(f"Version {APP_VERSION}")
                s.setStyleSheet(
                    "color: #8B949E; font-size: 11px; background: transparent;"
                )
                tc.addWidget(t); tc.addWidget(s)
                hdr.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
                hdr.addLayout(tc, 1)
                _s.viewLayout.addLayout(hdr)

                def _section_lbl(text, icon_svg, accent="#C9D1D9"):
                    row = QHBoxLayout(); row.setSpacing(6)
                    try:
                        ic_lbl = QLabel()
                        ic_lbl.setFixedSize(16, 16)
                        ic_lbl.setPixmap(
                            TablerIcon(icon_svg).icon(color=_QCh(accent)).pixmap(14, 14)
                        )
                        row.addWidget(ic_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
                    except Exception:
                        pass
                    l = QLabel(text)
                    l.setStyleSheet(
                        f"color: {accent}; font-size: 11px; font-weight: 700;"
                        " background: transparent;"
                    )
                    row.addWidget(l)
                    row.addStretch(1)
                    return row

                # ── Section: Keyboard shortcuts ──
                _s.viewLayout.addLayout(_section_lbl(
                    "Keyboard shortcuts", "tabler_keyboard.svg",
                ))
                rows = [
                    ("Ctrl + Shift + I",
                     "Clipboard import → auto-fills the Case form"),
                    ("Ctrl + Shift + L",
                     "Toggle dark / light theme"),
                    ("Ctrl + = / Ctrl + +", "Increase global font size"),
                    ("Ctrl + -", "Decrease global font size"),
                    ("Ctrl + 0", "Reset font size"),
                ]
                grid = _QFh()
                grid.setStyleSheet(
                    "QFrame { background: #161B22; border: 1px solid #21262D;"
                    "  border-radius: 8px; }"
                    "QLabel { background: transparent; border: none; }"
                )
                gl = QVBoxLayout(grid)
                gl.setContentsMargins(12, 8, 12, 8)
                gl.setSpacing(6)
                for key, desc in rows:
                    row = QHBoxLayout(); row.setSpacing(10)
                    k = QLabel(key)
                    k.setStyleSheet(
                        "color: #58A6FF; font-size: 11px; font-weight: 700;"
                        " font-family: 'Consolas','Menlo',monospace;"
                        " background: transparent;"
                    )
                    k.setMinimumWidth(160)
                    d = QLabel(desc)
                    d.setWordWrap(True)
                    d.setStyleSheet(
                        "color: #C9D1D9; font-size: 11px; background: transparent;"
                    )
                    row.addWidget(k, 0)
                    row.addWidget(d, 1)
                    gl.addLayout(row)
                _s.viewLayout.addWidget(grid)

                # ── Section: Developed by ──
                _s.viewLayout.addLayout(_section_lbl(
                    "Developed by", "tabler_user.svg",
                ))
                dev_card = _QFh()
                dev_card.setStyleSheet(
                    "QFrame { background: #161B22; border: 1px solid #21262D;"
                    "  border-radius: 8px; }"
                    "QLabel { background: transparent; border: none; }"
                )
                dv = QVBoxLayout(dev_card)
                dv.setContentsMargins(12, 8, 12, 8)
                dv.setSpacing(2)
                name = QLabel("Gerardo Gomez")
                name.setStyleSheet(
                    "color: #E6EDF3; font-size: 12px; font-weight: 700;"
                )
                dv.addWidget(name)
                _s.viewLayout.addWidget(dev_card)

                # ── Section: Feedback ──
                _s.viewLayout.addLayout(_section_lbl(
                    "Feedback", "tabler_message_circle.svg",
                ))
                fb_card = _QFh()
                fb_card.setStyleSheet(
                    "QFrame { background: #161B22; border: 1px solid #21262D;"
                    "  border-radius: 8px; }"
                    "QLabel { background: transparent; border: none; }"
                )
                fv = QHBoxLayout(fb_card)
                fv.setContentsMargins(12, 8, 12, 8)
                fv.setSpacing(8)
                fb_lbl = QLabel("Bugs, ideas, or improvements? Click ➜")
                fb_lbl.setStyleSheet(
                    "color: #C9D1D9; font-size: 11px;"
                )
                fv.addWidget(fb_lbl, 1)

                # Hardcoded Teams deep link to Gerardo's chat.
                _teams_email = "gerardo.gomez@envistaco.com"
                _teams_url = f"msteams:/l/chat/0/0?users={_teams_email}"

                tb = QPushButton("  Open Teams Chat")
                tb.setCursor(Qt.PointingHandCursor)
                tb.setFixedHeight(28)
                try:
                    tb.setIcon(
                        TablerIcon("tabler_brand_teams.svg").icon(color=_QCh("#FFFFFF"))
                    )
                    tb.setIconSize(_QSh2(14, 14))
                except Exception:
                    pass
                tb.setStyleSheet(
                    "QPushButton { background: #1e63e4; border: 1px solid #1e63e4;"
                    "  color: white; border-radius: 6px; padding: 4px 14px;"
                    "  font-weight: 700; font-size: 11px; }"
                    "QPushButton:hover { background: #2a73f3; }"
                )
                def _open_teams():
                    from PySide6.QtGui import QDesktopServices
                    from PySide6.QtCore import QUrl
                    QDesktopServices.openUrl(QUrl(_teams_url))
                tb.clicked.connect(_open_teams)
                fv.addWidget(tb)
                _s.viewLayout.addWidget(fb_card)

                # ── Section: About ──
                _s.viewLayout.addLayout(_section_lbl(
                    "About", "tabler_info_circle.svg",
                ))
                ab_card = _QFh()
                ab_card.setStyleSheet(
                    "QFrame { background: #161B22; border: 1px solid #21262D;"
                    "  border-radius: 8px; }"
                    "QLabel { background: transparent; border: none; }"
                )
                av = QVBoxLayout(ab_card)
                av.setContentsMargins(12, 8, 12, 8)
                av.setSpacing(2)
                bd = QLabel(f"Build {build_date}")
                bd.setStyleSheet("color: #C9D1D9; font-size: 11px;")
                tg = QLabel("Internal Production Tool")
                tg.setStyleSheet("color: #8B949E; font-size: 11px;")
                av.addWidget(bd)
                av.addWidget(tg)
                _s.viewLayout.addWidget(ab_card)

                _s.widget.setMinimumWidth(540)
                _s.cancelButton.hide()
                _s.yesButton.setText("Close")
                _s.yesButton.setFixedWidth(120)
                _s.yesButton.setStyleSheet(
                    "QPushButton { background: #1e63e4; border: 1px solid #1e63e4;"
                    "  color: white; border-radius: 6px; padding: 8px 22px;"
                    "  font-weight: 700; font-size: 12px; }"
                    "QPushButton:hover { background: #2a73f3; }"
                )

        _Sheet(host).exec()

    def _trigger_import_shortcut(self):
        """Ctrl+Shift+I — runs clipboard import on the currently visible tab."""
        current_widget = self.tabs.currentWidget()
        # Walk up the widget tree in case the tab is wrapped in a QScrollArea
        if hasattr(current_widget, 'widget'):
            current_widget = current_widget.widget()
        if hasattr(current_widget, '_on_import_case'):
            current_widget._on_import_case()
        else:
            self.statusBar().showMessage(
                "Clipboard import is only available in the Register tab.", 4000
            )

if __name__ == "__main__":
    # ── Self-installer: runs before Qt so no QApplication is needed yet ─────
    from self_installer import check_and_install, was_just_installed
    if check_and_install():
        sys.exit(0)

    init_db()
    # Seed the standards_history table with a baseline snapshot the
    # first time the app boots after this column was added. Cheap no-op
    # afterwards.
    try:
        from tabs.utils import _seed_standards_history_if_empty
        _seed_standards_history_if_empty()
    except Exception as _e:
        print(f"[boot] standards_history seed skipped: {_e}")
    init_breaks_table()
    if _PERF_OK:
        init_performance_table()
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(_resource_path(os.path.join("data", "app_icon.ico"))))

    # Initialize Fluent theme system (sidebar nav widget needs this).
    # Theme is set to DARK to match the dark stylesheet that follows; if the
    # user later toggles to light, the light-theme handler will call setTheme.
    try:
        from qfluentwidgets import setTheme, setThemeColor, Theme
        setTheme(Theme.DARK)
        setThemeColor("#388BFD")  # Match existing focus accent
    except Exception as _e:
        print(f"[main] Fluent theme init skipped: {_e}")

    DARK_STYLE = """
    /* ── Base ──────────────────────────────────────────────── */
    QWidget {
        background-color: #0D1117;
        color: #E6EDF3;
        font-family: "Segoe UI";
        font-size: 12px;
    }
    QLabel { background: transparent; color: #E6EDF3; }
    QFrame { background: transparent; }
    QScrollArea { border: none; background: transparent; }

    /* ── Inputs (Fluent-inspired) ───────────────────────────── */
    QLineEdit, QComboBox, QDateEdit, QTimeEdit,
    QTextEdit, QSpinBox, QDoubleSpinBox {
        background-color: #161B22;
        border: 1px solid #30363D;
        border-bottom: 1px solid #444C56;
        border-radius: 8px;
        padding: 7px 10px;
        color: #E6EDF3;
        selection-background-color: #1C2D4F;
    }
    QComboBox { padding-right: 26px; }
    QLineEdit:hover, QComboBox:hover, QDateEdit:hover, QTimeEdit:hover,
    QTextEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover {
        background-color: #1A1F26;
        border-color: #444C56;
    }
    QLineEdit:focus, QComboBox:focus, QDateEdit:focus,
    QTimeEdit:focus, QTextEdit:focus,
    QSpinBox:focus, QDoubleSpinBox:focus {
        border-color: #30363D;
        border-bottom: 2px solid #388BFD;
        background-color: #161B22;
    }
    QLineEdit:disabled, QComboBox:disabled { color: #6E7681; background-color: #0D1117; }
    QLineEdit::placeholder, QTextEdit::placeholder { color: #6E7681; }

    QTimeEdit::up-button, QTimeEdit::down-button,
    QDateEdit::up-button, QDateEdit::down-button { width: 0; border: none; }
    QTimeEdit, QDateEdit { padding-right: 6px; }

    QComboBox::drop-down, QDateEdit::drop-down {
        border: none;
        width: 20px;
        border-left: 1px solid #30363D;
    }
    QComboBox::down-arrow, QDateEdit::down-arrow {}
    QComboBox QAbstractItemView {
        background-color: #161B22;
        color: #E6EDF3;
        border: 1px solid #30363D;
        border-radius: 10px;
        padding: 6px;
        outline: none;
        selection-background-color: transparent;
    }
    QComboBox QAbstractItemView::item {
        background-color: transparent;
        color: #E6EDF3;
        padding: 3px 10px;
        border-radius: 5px;
        margin: 0 2px;
        min-height: 16px;
    }
    QComboBox QAbstractItemView::item:hover {
        background-color: rgba(255,255,255,0.06);
    }
    QComboBox QAbstractItemView::item:selected {
        background-color: rgba(56,139,253,0.18);
        color: #E6EDF3;
    }

    /* ── Calendar popup (QDateEdit) ─────────────────────────── */
    QCalendarWidget QWidget { alternate-background-color: #161B22; }
    QCalendarWidget QAbstractItemView {
        background-color: #161B22;
        color: #E6EDF3;
        selection-background-color: #1757D4;
        selection-color: #FFFFFF;
        outline: none;
        gridline-color: transparent;
    }
    /* Header row (Sun Mon Tue ...) */
    QCalendarWidget QAbstractItemView:enabled {
        font-size: 11px;
    }
    QCalendarWidget QToolButton {
        background-color: transparent;
        color: #E6EDF3;
        font-size: 12px;
        font-weight: 600;
        border: none;
        border-radius: 6px;
        padding: 4px 10px;
        margin: 2px;
    }
    QCalendarWidget QToolButton:hover {
        background-color: rgba(255,255,255,0.08);
    }
    QCalendarWidget QToolButton::menu-indicator { image: none; }
    QCalendarWidget QSpinBox {
        background-color: #161B22;
        color: #E6EDF3;
        border: 1px solid #30363D;
        border-radius: 6px;
    }
    /* Navigation bar (header strip) */
    QCalendarWidget QWidget#qt_calendar_navigationbar {
        background-color: #0D1117;
        border-bottom: 1px solid #21262D;
    }
    /* Disable the red weekend text — keep all days neutral. */
    QCalendarWidget QAbstractItemView:disabled { color: #4d5560; }

    /* ── Buttons (Fluent-inspired) ──────────────────────────── */
    QPushButton {
        background-color: #21262D;
        border: 1px solid #30363D;
        border-radius: 8px;
        padding: 7px 16px;
        color: #E6EDF3;
        font-weight: 600;
    }
    QPushButton:hover {
        background-color: #30363D;
        border-color: #58606A;
    }
    QPushButton:pressed { background-color: #161B22; border-color: #30363D; }
    QPushButton:disabled { color: #6E7681; border-color: #21262D; background-color: #161B22; }
    /* Primary action — opt-in via objectName="primary" */
    QPushButton#primary {
        background-color: #1757D4;
        border-color: #1757D4;
        color: #FFFFFF;
    }
    QPushButton#primary:hover { background-color: #388BFD; border-color: #388BFD; }
    QPushButton#primary:pressed { background-color: #1158C7; }

    /* ── Tab bar (underline style) ──────────────────────────── */
    QTabWidget::pane {
        border-top: 1px solid #21262D;
        background: #0D1117;
    }
    QTabBar { background: #0D1117; border-bottom: 1px solid #21262D; }
    QTabBar::tab {
        background: transparent;
        color: #8B949E;
        padding: 8px 14px;
        margin-right: 1px;
        border: none;
        border-bottom: 2px solid transparent;
        font-weight: 500;
        font-size: 12px;
        min-width: 52px;
    }
    QTabBar::tab:hover { color: #C9D1D9; border-bottom-color: #30363D; }
    QTabBar::tab:selected {
        color: #E6EDF3;
        font-weight: 700;
        border-bottom: 2px solid #388BFD;
    }

    /* ── Cards (QGroupBox) ──────────────────────────────────── */
    QGroupBox {
        border: 1px solid #21262D;
        border-radius: 10px;
        margin-top: 18px;
        padding: 14px 14px 14px 14px;
        background-color: #161B22;
    }
    QGroupBox::title {
        subcontrol-origin: margin;
        subcontrol-position: top left;
        left: 12px;
        padding: 2px 8px;
        color: #8B949E;
        font-size: 10px;
        font-weight: 700;
        background: #0D1117;
        border-radius: 4px;
    }

    /* ── Progress bars ──────────────────────────────────────── */
    QProgressBar {
        background-color: #21262D;
        border: none;
        border-radius: 6px;
        text-align: center;
        min-height: 24px;
        color: #E6EDF3;
        font-weight: 700;
        font-size: 11px;
    }
    QProgressBar::chunk { background-color: #388BFD; border-radius: 6px; }

    /* ── Tables ─────────────────────────────────────────────── */
    QTableWidget {
        background-color: #0D1117;
        gridline-color: #21262D;
        selection-background-color: #1C2D4F;
        selection-color: #E6EDF3;
        border: none;
        border-radius: 6px;
    }
    /* `border: none` here would force Qt's QSS painter to take over and
       ignore per-cell setBackground() — we use that to colour Eff% / Time
       cells. Padding alone leaves item brushes painting through. */
    QTableWidget::item { padding: 6px 8px; }
    QTableWidget::item:selected { background-color: #1C2D4F; }
    QHeaderView { background-color: #0D1117; }
    QHeaderView::section {
        background-color: #161B22;
        color: #8B949E;
        padding: 6px 8px;
        border: none;
        border-bottom: 1px solid #30363D;
        font-weight: 700;
        font-size: 10px;
    }

    /* ── Scrollbars ─────────────────────────────────────────── */
    QScrollBar:vertical { background: transparent; width: 6px; margin: 0; }
    QScrollBar::handle:vertical { background: #30363D; border-radius: 3px; min-height: 20px; }
    QScrollBar::handle:vertical:hover { background: #444C56; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
    QScrollBar:horizontal { background: transparent; height: 6px; margin: 0; }
    QScrollBar::handle:horizontal { background: #30363D; border-radius: 3px; min-width: 20px; }
    QScrollBar::handle:horizontal:hover { background: #444C56; }
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }

    /* ── Status bar ─────────────────────────────────────────── */
    QStatusBar {
        background-color: #161B22;
        border-top: 1px solid #21262D;
        color: #8B949E;
        font-size: 11px;
    }

    /* ── Misc ───────────────────────────────────────────────── */
    QToolTip {
        background-color: #161B22;
        color: #E6EDF3;
        border: 1px solid #30363D;
        border-radius: 6px;
        padding: 4px 8px;
        font-size: 11px;
    }
    QCheckBox { color: #8B949E; spacing: 5px; }
    QCheckBox::indicator {
        width: 14px; height: 14px;
        border: 1px solid #30363D; border-radius: 3px;
        background: #161B22;
    }
    QCheckBox::indicator:checked { background-color: #388BFD; border-color: #388BFD; }
    QDialog { background-color: #0D1117; }
    QMessageBox { background-color: #161B22; }
    """

    LIGHT_STYLE = """
    /* ── Base ──────────────────────────────────────────────── */
    QWidget {
        background-color: #F6F8FA;
        color: #1F2328;
        font-family: "Segoe UI";
        font-size: 12px;
    }
    QLabel { background: transparent; color: #1F2328; }
    QFrame { background: transparent; }
    QScrollArea { border: none; background: transparent; }

    /* ── Inputs (Fluent-inspired) ───────────────────────────── */
    QLineEdit, QComboBox, QDateEdit, QTimeEdit,
    QTextEdit, QSpinBox, QDoubleSpinBox {
        background-color: #FFFFFF;
        border: 1px solid #D0D7DE;
        border-bottom: 1px solid #B7BEC7;
        border-radius: 8px;
        padding: 7px 10px;
        color: #1F2328;
        selection-background-color: #DDF4FF;
    }
    QComboBox { padding-right: 26px; }
    QLineEdit:hover, QComboBox:hover, QDateEdit:hover, QTimeEdit:hover,
    QTextEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover {
        border-color: #B7BEC7;
        background-color: #FCFCFD;
    }
    QLineEdit:focus, QComboBox:focus, QDateEdit:focus,
    QTimeEdit:focus, QTextEdit:focus,
    QSpinBox:focus, QDoubleSpinBox:focus {
        border-color: #D0D7DE;
        border-bottom: 2px solid #0969DA;
    }

    QTimeEdit::up-button, QTimeEdit::down-button,
    QDateEdit::up-button, QDateEdit::down-button { width: 0; border: none; }
    QTimeEdit, QDateEdit { padding-right: 6px; }

    QComboBox::drop-down, QDateEdit::drop-down {
        border: none;
        width: 20px;
        border-left: 1px solid #D0D7DE;
    }
    QComboBox::down-arrow, QDateEdit::down-arrow {}
    QComboBox QAbstractItemView {
        background-color: #FFFFFF;
        color: #1F2328;
        border: 1px solid #D0D7DE;
        border-radius: 10px;
        padding: 6px;
        outline: none;
        selection-background-color: transparent;
    }
    QComboBox QAbstractItemView::item {
        background-color: transparent;
        color: #1F2328;
        padding: 3px 10px;
        border-radius: 5px;
        margin: 0 2px;
        min-height: 16px;
    }
    QComboBox QAbstractItemView::item:hover {
        background-color: #F3F4F6;
    }
    QComboBox QAbstractItemView::item:selected {
        background-color: #DDF4FF;
        color: #0969DA;
    }

    /* ── Buttons (Fluent-inspired) ──────────────────────────── */
    QPushButton {
        background-color: #FFFFFF;
        border: 1px solid #D0D7DE;
        border-radius: 8px;
        padding: 7px 16px;
        color: #1F2328;
        font-weight: 600;
    }
    QPushButton:hover { background-color: #F3F4F6; border-color: #B7BEC7; }
    QPushButton:pressed { background-color: #EAEEF2; }
    QPushButton:disabled { color: #8C959F; background-color: #F6F8FA; }
    QPushButton#primary {
        background-color: #0969DA;
        border-color: #0969DA;
        color: #FFFFFF;
    }
    QPushButton#primary:hover { background-color: #1B7BFE; border-color: #1B7BFE; }
    QPushButton#primary:pressed { background-color: #0857B0; }

    /* ── Tab bar ─────────────────────────────────────────────── */
    QTabWidget::pane { border-top: 1px solid #D8DEE4; background: #F6F8FA; }
    QTabBar { background: #F6F8FA; border-bottom: 1px solid #D8DEE4; }
    QTabBar::tab {
        background: transparent;
        color: #656D76;
        padding: 8px 14px;
        margin-right: 1px;
        border: none;
        border-bottom: 2px solid transparent;
        font-weight: 500;
        font-size: 12px;
        min-width: 52px;
    }
    QTabBar::tab:hover { color: #1F2328; border-bottom-color: #D0D7DE; }
    QTabBar::tab:selected {
        color: #0969DA;
        font-weight: 700;
        border-bottom: 2px solid #0969DA;
    }

    /* ── Cards ───────────────────────────────────────────────── */
    QGroupBox {
        border: 1px solid #D0D7DE;
        border-radius: 8px;
        margin-top: 18px;
        padding: 10px 12px 12px 12px;
        background-color: #FFFFFF;
    }
    QGroupBox::title {
        subcontrol-origin: margin;
        subcontrol-position: top left;
        left: 10px;
        padding: 2px 6px;
        color: #656D76;
        font-size: 10px;
        font-weight: 700;
        background: #F6F8FA;
    }

    /* ── Progress bars ───────────────────────────────────────── */
    QProgressBar {
        background-color: #EAEEF2;
        border: none;
        border-radius: 6px;
        text-align: center;
        min-height: 24px;
        color: #1F2328;
        font-weight: 700;
        font-size: 11px;
    }
    QProgressBar::chunk { background-color: #0969DA; border-radius: 6px; }

    /* ── Tables ──────────────────────────────────────────────── */
    QTableWidget {
        background-color: #FFFFFF;
        gridline-color: #EAEEF2;
        selection-background-color: #DDF4FF;
        selection-color: #0969DA;
        border: 1px solid #D0D7DE;
        border-radius: 6px;
    }
    QTableWidget::item { padding: 6px 8px; }
    QTableWidget::item:selected { background-color: #DDF4FF; color: #0969DA; }
    QHeaderView { background-color: #FFFFFF; }
    QHeaderView::section {
        background-color: #F6F8FA;
        color: #656D76;
        padding: 6px 8px;
        border: none;
        border-bottom: 1px solid #D0D7DE;
        font-weight: 700;
        font-size: 10px;
    }

    /* ── Scrollbars ──────────────────────────────────────────── */
    QScrollBar:vertical { background: transparent; width: 6px; margin: 0; }
    QScrollBar::handle:vertical { background: #D0D7DE; border-radius: 3px; min-height: 20px; }
    QScrollBar::handle:vertical:hover { background: #B3BAC3; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
    QScrollBar:horizontal { background: transparent; height: 6px; }
    QScrollBar::handle:horizontal { background: #D0D7DE; border-radius: 3px; min-width: 20px; }
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }

    /* ── Status bar ──────────────────────────────────────────── */
    QStatusBar {
        background-color: #FFFFFF;
        border-top: 1px solid #D0D7DE;
        color: #656D76;
        font-size: 11px;
    }

    /* ── Misc ────────────────────────────────────────────────── */
    QToolTip {
        background-color: #1F2328;
        color: #E6EDF3;
        border: 1px solid #30363D;
        border-radius: 6px;
        padding: 4px 8px;
        font-size: 11px;
    }
    QCheckBox { color: #656D76; spacing: 5px; }
    QCheckBox::indicator {
        width: 14px; height: 14px;
        border: 1px solid #D0D7DE; border-radius: 3px;
        background: #FFFFFF;
    }
    QCheckBox::indicator:checked { background-color: #0969DA; border-color: #0969DA; }
    QDialog { background-color: #FFFFFF; }
    QMessageBox { background-color: #FFFFFF; }
    """

    app.setStyleSheet(DARK_STYLE)
    
    window = MainWindow(dark_style=DARK_STYLE, light_style=LIGHT_STYLE)
    window.show()
    QTimer.singleShot(400, window._check_first_use)

    # One-time legacy DB migration (pre-local-DB path → local DB), moved off
    # the startup critical path. The main legacy candidate lives on OneDrive
    # (see db.database._get_legacy_db_candidates) — checking whether it has
    # data requires opening it, which forces OneDrive to hydrate the file if
    # it's a Files-On-Demand placeholder. That used to run BEFORE the window
    # even existed, so a cold OneDrive cache meant the app appeared to hang
    # for however long the download took. Now it runs on a background thread
    # after the window is already up and usable; if it finds something to
    # merge, the affected tabs refresh and the notice pops up once it's done.
    def _run_legacy_migration():
        import threading
        def _bg():
            try:
                msg = migrate_legacy_db()
            except Exception as exc:
                log_event("main", f"legacy migration error: {exc}", level="WARN")
                return
            if not msg:
                return

            def _apply():
                try:
                    window.register_tab.load_daily_production()
                    window.production_tab.load_data()
                    window.history_tab.load_all_cases()
                    window.dashboard_tab.refresh()
                except Exception as exc:
                    log_event("main", f"post-migration refresh failed: {exc}", level="WARN")
                QMessageBox.information(window, "Datos migrados a OneDrive", msg)
            QTimer.singleShot(0, _apply)
        threading.Thread(target=_bg, daemon=True).start()
    QTimer.singleShot(1200, _run_legacy_migration)

    # Startup safety backup (lightweight, background, non-blocking)
    def _run_startup_backup():
        import threading
        def _bg():
            try:
                from sync.safety_backup import run_startup_backups
                stats = run_startup_backups()
                log_event("main", f"startup backups done: {stats}")
            except Exception as exc:
                print(f"[main] Startup backup error: {exc}")
                log_event("main", f"startup backup error: {exc}", level="WARN")
        threading.Thread(target=_bg, daemon=True).start()
    QTimer.singleShot(1500, _run_startup_backup)

    # Durable OneDrive mirror of the LOCAL live DB. The live cases.db now lives
    # off OneDrive (no more UI freezes), so we push a copy to OneDrive on a
    # background thread — at startup, every 5 min, and on quit — so a dead PC
    # never loses data.
    def _mirror_db_to_onedrive():
        import threading
        def _bg():
            try:
                ok = backup_db_to_onedrive()
                if ok:
                    log_event("main", "DB mirrored to OneDrive")
            except Exception as exc:
                log_event("main", f"DB mirror error: {exc}", level="WARN")
        threading.Thread(target=_bg, daemon=True).start()

    _db_mirror_timer = QTimer()
    _db_mirror_timer.setInterval(5 * 60 * 1000)  # every 5 minutes
    _db_mirror_timer.timeout.connect(_mirror_db_to_onedrive)
    _db_mirror_timer.start()
    QTimer.singleShot(8000, _mirror_db_to_onedrive)  # first mirror shortly after boot
    # Final mirror on quit — run SYNCHRONOUSLY (a daemon thread might not finish
    # before the process exits). Copying a small DB is sub-second.
    def _mirror_on_quit():
        try:
            backup_db_to_onedrive()
        except Exception as exc:
            log_event("main", f"quit mirror error: {exc}", level="WARN")
    app.aboutToQuit.connect(_mirror_on_quit)

    # Background DB discovery/merge across common PC paths (time-bounded)
    def _run_background_db_discovery():
        import threading
        def _bg():
            try:
                cfg = load_config()
                if not bool(cfg.get("auto_discover_dbs", True)):
                    return
                summary = discover_and_merge_background_dbs(max_seconds=35)
                if summary:
                    log_event("main", summary)
                    print(f"[main] {summary}")
            except Exception as exc:
                print(f"[main] Background DB discovery error: {exc}")
                log_event("main", f"background DB discovery error: {exc}", level="WARN")
        threading.Thread(target=_bg, daemon=True).start()
    QTimer.singleShot(5000, _run_background_db_discovery)

    # Check for pending justifications from previous days
    if _PERF_OK and _JUSTIFICATION_ENABLED:
        QTimer.singleShot(1000, window._check_pending_justification_on_start)

    # Show a brief notice if the app was just self-installed
    if was_just_installed():
        window.statusBar().showMessage(
            "✓ Production Calc installed — shortcut created on your Desktop.", 8000
        )

    sys.exit(app.exec())

