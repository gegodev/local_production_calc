from .theme_palette import apply_fluent_modal_palette
import time

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QDateEdit, QFileDialog, QGroupBox,
    QFormLayout, QTextEdit, QMessageBox, QDialog, QDialogButtonBox,
)
from PySide6.QtCore import QDate, Qt, QThread, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QSizePolicy

_TRANSIENT_KEYWORDS = (
    "permissionerror", "locked", "cannot access", "winerror",
    "timed out", "timeout", "network", "connection reset",
    "remote end closed", "odmpath", "onedrive",
)


def _is_transient_error(msg: str) -> bool:
    m = msg.lower()
    return any(k in m for k in _TRANSIENT_KEYWORDS)

try:
    from sync.app_config import load_config, save_config
    from sync.sharepoint_sync import (
        export_to_sharepoint,
        export_all_missing_to_sharepoint,
        audit_dashboard_vs_summaries,
        rebuild_historical_dashboards,
    )
    _SYNC_AVAILABLE = True
except Exception as _e:
    _SYNC_AVAILABLE = False
    _SYNC_ERROR = str(_e)


class _ExportThread(QThread):
    """Run export_to_sharepoint in background with up to 3 retries on transient errors."""
    done = Signal(bool, str)
    status = Signal(str)

    def __init__(self, target_date: str):
        super().__init__()
        self._date = target_date

    def run(self):
        last_ok, last_msg = False, ""
        for attempt in range(3):
            try:
                ok, msg = export_to_sharepoint(self._date)
                if ok or not _is_transient_error(msg) or attempt == 2:
                    self.done.emit(ok, msg)
                    return
                last_ok, last_msg = ok, msg
            except Exception as e:
                last_ok, last_msg = False, str(e)
                if not _is_transient_error(last_msg) or attempt == 2:
                    self.done.emit(last_ok, last_msg)
                    return
            delay = 5 * (attempt + 1)
            self.status.emit(f"Transient error — retrying in {delay}s… (attempt {attempt + 2}/3)")
            time.sleep(delay)
        self.done.emit(last_ok, last_msg)


class _BulkHistoryThread(QThread):
    """Run export_all_missing_to_sharepoint in background with progress updates."""
    progress = Signal(int, int, str)
    done = Signal(bool, str)

    def run(self):
        def _cb(idx, total, message):
            self.progress.emit(idx, total, message)
        try:
            ok, msg = export_all_missing_to_sharepoint(progress_cb=_cb)
            self.done.emit(ok, msg)
        except Exception as e:
            self.done.emit(False, str(e))


class _AuditThread(QThread):
    """Run audit_dashboard_vs_summaries in background."""
    done = Signal(bool, str, dict)

    def run(self):
        try:
            issues, report = audit_dashboard_vs_summaries()
            self.done.emit(len(issues) == 0, report, issues)
        except Exception as e:
            self.done.emit(False, str(e), {})


class _HistoricalRebuildThread(QThread):
    """Run rebuild_historical_dashboards in background."""
    progress = Signal(int, int, str)
    done = Signal(bool, str)

    def __init__(self, dates=None):
        super().__init__()
        self._dates = dates

    def run(self):
        def _cb(idx, total, message):
            self.progress.emit(idx, total, message)
        try:
            ok, msg = rebuild_historical_dashboards(
                dates=self._dates, progress_cb=_cb,
            )
            self.done.emit(ok, msg)
        except Exception as e:
            self.done.emit(False, str(e))


class SyncTab(QWidget):
    def __init__(self):
        super().__init__()
        self._thread = None
        self._busy = False
        self._init_ui()
        self._load_config()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(18)

        # Title + subtitle removed — the modal header (in main.py) shows
        # "SharePoint Sync" already, no need to duplicate it inside the body.

        # ── Settings card ───────────────────────────────────────────────────
        from PySide6.QtWidgets import QFrame as _QFr, QToolButton as _QTB
        from PySide6.QtCore import QSize as _QSz
        from PySide6.QtGui import QColor as _QCol
        try:
            from .tabler_icons import TablerIcon as _TI
        except Exception:
            _TI = None

        settings_box = _QFr()
        settings_box.setObjectName("syncSettingsCard")
        settings_box.setStyleSheet(
            "#syncSettingsCard { background: #0D1117; border: 1px solid #21262D;"
            " border-radius: 10px; }"
            "QLabel { background: transparent; }"
        )
        sb_lay = QVBoxLayout(settings_box)
        sb_lay.setContentsMargins(16, 14, 16, 14)
        sb_lay.setSpacing(12)

        # Header row: numbered badge + title + Save button (right).
        s_hdr = QHBoxLayout(); s_hdr.setSpacing(10)
        badge = QLabel("1")
        badge.setFixedSize(22, 22)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet(
            "background: #1e63e4; color: white; border-radius: 11px;"
            " font-weight: 700; font-size: 11px;"
        )
        s_title = QLabel("Settings")
        s_title.setStyleSheet(
            "color: #E6EDF3; font-size: 13px; font-weight: 700;"
        )
        s_hdr.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)
        s_hdr.addWidget(s_title, 0, Qt.AlignmentFlag.AlignVCenter)
        s_hdr.addStretch()

        save_btn = QPushButton("  Save Settings")
        save_btn.setFixedHeight(30)
        save_btn.setCursor(Qt.PointingHandCursor)
        if _TI is not None:
            save_btn.setIcon(_TI("tabler_device_floppy.svg").icon(color=_QCol("#E6EDF3")))
            save_btn.setIconSize(_QSz(14, 14))
        save_btn.setStyleSheet(
            "QPushButton { background: transparent; border: 1px solid #30363D;"
            "  color: #E6EDF3; border-radius: 6px; padding: 4px 14px;"
            "  font-weight: 700; font-size: 11px; }"
            "QPushButton:hover { background: rgba(255,255,255,0.05);"
            "  border-color: #58606A; }"
        )
        save_btn.clicked.connect(self._save_settings)
        s_hdr.addWidget(save_btn)
        sb_lay.addLayout(s_hdr)

        # Helper for a field row with leading icon.
        def _field_row(icon_svg, label_text, field_widget, extra=None):
            row = QHBoxLayout(); row.setSpacing(10)
            ic = _QTB()
            ic.setEnabled(False)
            ic.setFixedSize(28, 28)
            ic.setIconSize(_QSz(16, 16))
            ic.setStyleSheet(
                "background: #161B22; border: 1px solid #21262D;"
                " border-radius: 6px;"
            )
            if _TI is not None and icon_svg:
                try:
                    ic.setIcon(_TI(icon_svg).icon(color=_QCol("#E6EDF3")))
                except Exception:
                    pass
            row.addWidget(ic, 0, Qt.AlignmentFlag.AlignVCenter)
            lbl = QLabel(label_text)
            lbl.setFixedWidth(110)
            lbl.setStyleSheet(
                "color: #C9D1D9; font-size: 11px; font-weight: 600;"
            )
            row.addWidget(lbl, 0, Qt.AlignmentFlag.AlignVCenter)
            row.addWidget(field_widget, 1)
            if extra is not None:
                row.addWidget(extra, 0)
            return row

        self.designer_name = QLineEdit()
        self.designer_name.setPlaceholderText("Your name as it will appear on reports")
        self.designer_name.setMinimumHeight(32)
        sb_lay.addLayout(_field_row("tabler_user.svg", "Designer name", self.designer_name))

        self.export_folder = QLineEdit()
        self.export_folder.setPlaceholderText("Path to Teams/SharePoint synced folder…")
        self.export_folder.setReadOnly(True)
        self.export_folder.setMinimumHeight(32)
        browse_btn = QPushButton("Browse…")
        browse_btn.setFixedHeight(32)
        browse_btn.setFixedWidth(86)
        browse_btn.setCursor(Qt.PointingHandCursor)
        browse_btn.setStyleSheet(
            "QPushButton { background: transparent; border: 1px solid #30363D;"
            "  color: #E6EDF3; border-radius: 6px; padding: 4px 14px;"
            "  font-weight: 600; font-size: 11px; }"
            "QPushButton:hover { background: rgba(255,255,255,0.05); }"
        )
        browse_btn.clicked.connect(self._browse_folder)
        sb_lay.addLayout(_field_row("tabler_folder.svg", "Export folder",
                                     self.export_folder, extra=browse_btn))

        self.folder_hint = QLabel("")
        self.folder_hint.setStyleSheet("color: #3FB950; font-size: 10px;"
                                        " padding-left: 148px;")
        sb_lay.addWidget(self.folder_hint)

        root.addWidget(settings_box)

        # ── Export Report card ─────────────────────────────────────────────
        export_box = _QFr()
        export_box.setObjectName("syncExportCard")
        export_box.setStyleSheet(
            "#syncExportCard { background: #0D1117; border: 1px solid #21262D;"
            " border-radius: 10px; }"
            "QLabel { background: transparent; }"
        )
        exp_layout = QVBoxLayout(export_box)
        exp_layout.setContentsMargins(16, 14, 16, 14)
        exp_layout.setSpacing(10)

        # Header row.
        e_hdr = QHBoxLayout(); e_hdr.setSpacing(10)
        e_badge = QLabel("2")
        e_badge.setFixedSize(22, 22)
        e_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        e_badge.setStyleSheet(
            "background: #1e63e4; color: white; border-radius: 11px;"
            " font-weight: 700; font-size: 11px;"
        )
        e_title = QLabel("Export Report")
        e_title.setStyleSheet(
            "color: #E6EDF3; font-size: 13px; font-weight: 700;"
        )
        e_hdr.addWidget(e_badge, 0, Qt.AlignmentFlag.AlignVCenter)
        e_hdr.addWidget(e_title, 0, Qt.AlignmentFlag.AlignVCenter)
        e_hdr.addStretch()
        exp_layout.addLayout(e_hdr)

        # Date row — icon + label + standard DateEditWithShortcut + Today.
        from .widgets import DateEditWithShortcut as _DateEditShort
        date_row = QHBoxLayout(); date_row.setSpacing(10)

        d_ic = _QTB()
        d_ic.setEnabled(False)
        d_ic.setFixedSize(28, 28)
        d_ic.setIconSize(_QSz(16, 16))
        d_ic.setStyleSheet(
            "background: #161B22; border: 1px solid #21262D;"
            " border-radius: 6px;"
        )
        if _TI is not None:
            try:
                d_ic.setIcon(_TI("tabler_calendar.svg").icon(color=_QCol("#E6EDF3")))
            except Exception:
                pass
        d_lbl = QLabel("Export date")
        d_lbl.setFixedWidth(110)
        d_lbl.setStyleSheet(
            "color: #C9D1D9; font-size: 11px; font-weight: 600;"
        )

        self.export_date = _DateEditShort()
        self.export_date.setDate(QDate.currentDate())
        self.export_date.setMinimumHeight(32)
        # Tighter fixed width so Today sits next to it, not far away.
        self.export_date.setFixedWidth(160)

        self.today_btn = QPushButton("  Today")
        self.today_btn.setFixedHeight(32)
        self.today_btn.setCursor(Qt.PointingHandCursor)
        if _TI is not None:
            self.today_btn.setIcon(_TI("tabler_calendar.svg").icon(color=_QCol("#E6EDF3")))
            self.today_btn.setIconSize(_QSz(14, 14))
        self.today_btn.setStyleSheet(
            "QPushButton { background: transparent; border: 1px solid #30363D;"
            "  color: #E6EDF3; border-radius: 6px; padding: 4px 12px;"
            "  font-weight: 600; font-size: 11px; }"
            "QPushButton:hover { background: rgba(255,255,255,0.05); }"
        )
        self.today_btn.clicked.connect(lambda: self.export_date.setDate(QDate.currentDate()))

        date_row.addWidget(d_ic, 0, Qt.AlignmentFlag.AlignVCenter)
        date_row.addWidget(d_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
        date_row.addWidget(self.export_date, 0, Qt.AlignmentFlag.AlignVCenter)
        date_row.addWidget(self.today_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        date_row.addStretch()
        exp_layout.addLayout(date_row)

        # Helper for the stacked action buttons (title + subtitle inside btn).
        def _action_btn(title, subtitle, icon_svg, *, primary=False,
                        height=46, on_click=None):
            btn = QPushButton()
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(height)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding,
                              QSizePolicy.Policy.Fixed)
            if primary:
                btn.setStyleSheet(
                    "QPushButton { background: #1e63e4; border: 1px solid #1e63e4;"
                    "  color: white; border-radius: 8px; text-align: center;"
                    "  padding: 6px 12px; }"
                    "QPushButton:hover { background: #2a73f3; }"
                    "QPushButton:disabled { background: #2a3a55; color: #8B949E; }"
                )
                fg = "#FFFFFF"; sub_fg = "#CFE0FF"
            else:
                btn.setStyleSheet(
                    "QPushButton { background: #161B22;"
                    "  border: 1px solid #21262D; color: #E6EDF3;"
                    "  border-radius: 8px; text-align: center;"
                    "  padding: 6px 12px; }"
                    "QPushButton:hover { background: #1C232C; }"
                    "QPushButton:disabled { background: #1a1f25; color: #555; }"
                )
                fg = "#E6EDF3"; sub_fg = "#8B949E"
            # Vertical layout: top row centers icon+title together,
            # subtitle row below centers independently — keeps the cloud
            # icon visually pinned next to the title text.
            outer_v = QVBoxLayout(btn)
            outer_v.setContentsMargins(12, 6, 12, 6)
            outer_v.setSpacing(2)

            title_row = QHBoxLayout()
            title_row.setSpacing(8)
            title_row.addStretch()
            if _TI is not None and icon_svg:
                ic = QLabel()
                pix = _TI(icon_svg).icon(color=_QCol(fg)).pixmap(18, 18)
                ic.setPixmap(pix)
                ic.setFixedSize(18, 18)
                title_row.addWidget(ic, 0, Qt.AlignmentFlag.AlignVCenter)
            t = QLabel(title)
            t.setStyleSheet(f"color: {fg}; font-size: 12px; font-weight: 700;"
                            " background: transparent;")
            title_row.addWidget(t, 0, Qt.AlignmentFlag.AlignVCenter)
            title_row.addStretch()
            outer_v.addLayout(title_row)

            if subtitle:
                s = QLabel(subtitle)
                s.setStyleSheet(f"color: {sub_fg}; font-size: 10px;"
                                " background: transparent;")
                s.setAlignment(Qt.AlignmentFlag.AlignCenter)
                outer_v.addWidget(s)
            if on_click is not None:
                btn.clicked.connect(on_click)
            return btn

        self.export_btn = _action_btn(
            "Export to SharePoint",
            "Export the daily report to the configured SharePoint folder",
            "tabler_cloud_upload.svg",
            primary=True,
            height=50,
            on_click=self._do_export,
        )
        exp_layout.addWidget(self.export_btn)

        self.bulk_btn = _action_btn(
            "Upload Missing History",
            "Upload any missing historical reports",
            "tabler_upload.svg",
            height=46,
            on_click=self._do_bulk_history,
        )
        self.bulk_btn.setToolTip(
            "Uploads daily files for every day in your local database that is missing\n"
            "or corrupt in the shared folder. Valid files are never overwritten."
        )
        exp_layout.addWidget(self.bulk_btn)

        # Bottom row: Audit + Rebuild side-by-side.
        admin_row = QHBoxLayout()
        admin_row.setSpacing(10)
        self.audit_btn = _action_btn(
            "Audit Dashboard History",
            "View audit dashboard export history",
            "tabler_history.svg",
            height=44,
            on_click=self._do_audit,
        )
        self.audit_btn.setToolTip(
            "Compare each dated dashboard snapshot against the per-designer\n"
            "summary files. Reports missing designers, stale values."
        )
        self.rebuild_btn = _action_btn(
            "Rebuild Historical Dashboards",
            "Rebuild historical dashboards",
            "tabler_refresh.svg",
            height=44,
            on_click=self._do_rebuild_history,
        )
        self.rebuild_btn.setToolTip(
            "Regenerate every _Dashboard_YYYY-MM-DD.xlsx snapshot from the per-\n"
            "designer summary files. Live _Dashboard.xlsx is not touched."
        )
        admin_row.addWidget(self.audit_btn, 1)
        admin_row.addWidget(self.rebuild_btn, 1)
        exp_layout.addLayout(admin_row)

        root.addWidget(export_box)

        # ── Status card ────────────────────────────────────────────────────
        log_box = _QFr()
        log_box.setObjectName("syncStatusCard")
        log_box.setStyleSheet(
            "#syncStatusCard { background: #0D1117; border: 1px solid #21262D;"
            " border-radius: 10px; }"
            "QLabel { background: transparent; }"
        )
        log_layout = QVBoxLayout(log_box)
        log_layout.setContentsMargins(16, 14, 16, 14)
        log_layout.setSpacing(10)

        # Header row.
        l_hdr = QHBoxLayout(); l_hdr.setSpacing(10)
        l_badge = QLabel("3")
        l_badge.setFixedSize(22, 22)
        l_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        l_badge.setStyleSheet(
            "background: #1e63e4; color: white; border-radius: 11px;"
            " font-weight: 700; font-size: 11px;"
        )
        l_title = QLabel("Status")
        l_title.setStyleSheet(
            "color: #E6EDF3; font-size: 13px; font-weight: 700;"
        )
        l_hdr.addWidget(l_badge, 0, Qt.AlignmentFlag.AlignVCenter)
        l_hdr.addWidget(l_title, 0, Qt.AlignmentFlag.AlignVCenter)
        l_hdr.addStretch()
        log_layout.addLayout(l_hdr)

        # Summary banner — updates per sync state.
        self._status_banner = _QFr()
        self._status_banner.setObjectName("syncStatusBanner")
        st_lay = QHBoxLayout(self._status_banner)
        st_lay.setContentsMargins(14, 10, 14, 10)
        st_lay.setSpacing(12)
        self._status_icon = QLabel()
        self._status_icon.setFixedSize(28, 28)
        self._status_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        st_lay.addWidget(self._status_icon, 0, Qt.AlignmentFlag.AlignVCenter)
        text_col = QVBoxLayout(); text_col.setSpacing(2)
        self._status_title = QLabel("Idle")
        self._status_title.setStyleSheet(
            "color: #C9D1D9; font-size: 12px; font-weight: 700;"
        )
        self._status_sub = QLabel("No sync run yet")
        self._status_sub.setStyleSheet(
            "color: #8B949E; font-size: 11px;"
        )
        text_col.addWidget(self._status_title)
        text_col.addWidget(self._status_sub)
        st_lay.addLayout(text_col, 1)

        self._status_detail_btn = QPushButton("  View details  ›")
        self._status_detail_btn.setFixedHeight(30)
        self._status_detail_btn.setCursor(Qt.PointingHandCursor)
        self._status_detail_btn.setStyleSheet(
            "QPushButton { background: transparent; border: 1px solid #30363D;"
            "  color: #E6EDF3; border-radius: 6px; padding: 4px 14px;"
            "  font-weight: 600; font-size: 11px; }"
            "QPushButton:hover { background: rgba(255,255,255,0.05); }"
        )
        self._status_detail_btn.clicked.connect(self._open_status_details)
        st_lay.addWidget(self._status_detail_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        log_layout.addWidget(self._status_banner)
        self._set_status_state("idle")

        # Hidden detailed log — richer rendering: bordered card, gutter
        # timestamps, color-coded severity dot, monospace message, hover row.
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setFixedHeight(200)
        # The default document margins push the first line awkwardly; tighten
        # them so the log feels like a terminal pane.
        self.log.document().setDocumentMargin(0)
        self.log.setStyleSheet(
            "QTextEdit { background: #0B0F14; border: 1px solid #21262D;"
            "  border-radius: 8px; color: #C9D1D9;"
            "  font-family: 'Consolas','Cascadia Code','Menlo',monospace;"
            "  font-size: 11px; padding: 0; }"
            "QScrollBar:vertical { background: transparent; width: 8px;"
            "  margin: 0; }"
            "QScrollBar::handle:vertical { background: #30363D;"
            "  border-radius: 4px; min-height: 24px; }"
            "QScrollBar::handle:vertical:hover { background: #58606A; }"
        )
        self.log.setVisible(False)

        # Empty-state placeholder shown above the QTextEdit while no log
        # entries have arrived yet. Keeps the box from looking dead.
        self._log_empty = QLabel("No activity yet — run an action above to see logs here.")
        self._log_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._log_empty.setStyleSheet(
            "color: #6E7681; font-size: 11px; background: #0B0F14;"
            " border: 1px dashed #21262D; border-radius: 8px;"
            " padding: 26px; font-style: italic;"
        )
        self._log_empty.setVisible(False)

        log_layout.addWidget(self._log_empty)
        log_layout.addWidget(self.log)

        root.addWidget(log_box)

        # Footer status — centered text replaces the "Exporting…" overload
        # on the Export button so the button stays clean during a run.
        self._footer_status = QLabel("")
        self._footer_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._footer_status.setStyleSheet(
            "color: #8B949E; font-size: 11px; background: transparent;"
            " padding: 4px;"
        )
        root.addWidget(self._footer_status)

        if not _SYNC_AVAILABLE:
            self.export_btn.setEnabled(False)
            self.bulk_btn.setEnabled(False)
            self.audit_btn.setEnabled(False)
            self.rebuild_btn.setEnabled(False)
            self._log(f"⚠ Sync module unavailable: {_SYNC_ERROR}", error=True)

        root.addStretch()

    # ── Busy state ────────────────────────────────────────────────────────────

    def is_busy(self) -> bool:
        return self._busy

    def _set_busy(self, busy: bool):
        self._busy = busy
        enabled = not busy and _SYNC_AVAILABLE
        self.export_btn.setEnabled(enabled)
        self.bulk_btn.setEnabled(enabled)
        self.audit_btn.setEnabled(enabled)
        self.rebuild_btn.setEnabled(enabled)

    # ── Config ────────────────────────────────────────────────────────────────

    def _load_config(self):
        if not _SYNC_AVAILABLE:
            return
        try:
            cfg = load_config()
            self.designer_name.setText(cfg.get("designer_name", ""))
            folder = cfg.get("export_folder", "")
            self.export_folder.setText(folder)
            if folder:
                self.folder_hint.setText(f"Folder found: {folder}" if __import__('os').path.isdir(folder)
                                         else "Folder not found — please re-select")
                self.folder_hint.setStyleSheet(
                    "color: #66bb6a; font-size: 10px;" if __import__('os').path.isdir(folder)
                    else "color: #ef9a9a; font-size: 10px;"
                )
        except Exception as e:
            self._log(f"Could not load config: {e}", error=True)

    def _set_status_state(self, state: str, *, ts: str = "", message: str = ""):
        """Update the status banner — accepts 'idle' / 'running' / 'ok' / 'error'."""
        try:
            from .tabler_icons import TablerIcon as _TI
        except Exception:
            _TI = None
        from PySide6.QtGui import QColor as _QCol
        cfg = {
            "idle":    {"fg": "#8B949E", "bg": "rgba(139,148,158,0.10)",
                         "br": "rgba(139,148,158,0.40)",
                         "ic": "tabler_clock.svg",
                         "t": "Idle", "s": message or "No sync run yet"},
            "running": {"fg": "#58A6FF", "bg": "rgba(88,166,255,0.10)",
                         "br": "rgba(88,166,255,0.45)",
                         "ic": "tabler_refresh.svg",
                         "t": "Sync in progress",
                         "s": message or "Uploading to SharePoint…"},
            "ok":      {"fg": "#3FB950", "bg": "rgba(63,185,80,0.10)",
                         "br": "rgba(63,185,80,0.45)",
                         "ic": "tabler_circle_check.svg",
                         "t": "All changes synced successfully",
                         "s": message or (f"Sync completed at {ts}" if ts else "")},
            "error":   {"fg": "#F85149", "bg": "rgba(248,81,73,0.10)",
                         "br": "rgba(248,81,73,0.45)",
                         "ic": "tabler_alert_triangle.svg",
                         "t": "Sync failed",
                         "s": message or "Click View details for the error log."},
        }.get(state, None)
        if cfg is None:
            return
        self._status_banner.setStyleSheet(
            f"#syncStatusBanner {{ background: {cfg['bg']};"
            f" border: 1px solid {cfg['br']}; border-radius: 8px; }}"
        )
        self._status_title.setText(cfg["t"])
        self._status_title.setStyleSheet(
            f"color: {cfg['fg']}; font-size: 12px; font-weight: 700;"
        )
        self._status_sub.setText(cfg["s"])
        if _TI is not None:
            try:
                pix = _TI(cfg["ic"]).icon(color=_QCol(cfg["fg"])).pixmap(20, 20)
                self._status_icon.setPixmap(pix)
            except Exception:
                pass

    def _fluent_confirm(self, *, title: str, subtitle: str, body: str,
                          icon_svg: str = "tabler_alert_triangle.svg",
                          primary_text: str = "Continue",
                          primary_color: str = "#1e63e4",
                          primary_hover: str = "#2a73f3") -> bool:
        """Fluent confirmation modal matching the rest of the app's dialogs."""
        try:
            from qfluentwidgets import MessageBoxBase
            from .tabler_icons import TablerIcon as _TI
        except Exception:
            # Fallback: classic message box.
            resp = QMessageBox.question(
                self, title, body.replace("<br>", "\n").replace("<b>", "")
                                  .replace("</b>", ""),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            return resp == QMessageBox.StandardButton.Yes

        from PySide6.QtCore import (
            QPropertyAnimation as _QPA, QEasingCurve as _QEC,
            Property as _QProp, QSize as _QSz,
        )
        from PySide6.QtGui import QColor as _QCol, QPainter as _QPn
        from PySide6.QtWidgets import QToolButton as _QTB, QFrame as _QFr

        host = self

        class _ConfirmSheet(MessageBoxBase):
            def __init__(_s, h):
                super().__init__(h.window() if h is not None else None)
                try:
                    _s.setMaskColor(_QCol(0, 0, 0, 170))
                except Exception:
                    pass
                _s.widget.setObjectName("confirmFluent")
                apply_fluent_modal_palette(_s, "confirmFluent")
                _s.viewLayout.setContentsMargins(22, 18, 22, 12)
                _s.viewLayout.setSpacing(10)

                hdr = QHBoxLayout(); hdr.setSpacing(12)
                ic = _QTB()
                ic.setEnabled(False)
                ic.setIcon(_TI(icon_svg).icon(color=_QCol(primary_color)))
                ic.setIconSize(_QSz(22, 22))
                ic.setStyleSheet(
                    f"background: rgba({_QCol(primary_color).red()},"
                    f"{_QCol(primary_color).green()},"
                    f"{_QCol(primary_color).blue()},0.14);"
                    " border: none; border-radius: 10px; padding: 6px;"
                )
                tc = QVBoxLayout(); tc.setSpacing(2)
                t = QLabel(title)
                t.setStyleSheet(
                    "color: #E6EDF3; font-size: 15px; font-weight: 700;"
                    " background: transparent;"
                )
                s = QLabel(subtitle)
                s.setWordWrap(True)
                s.setStyleSheet(
                    "color: #8B949E; font-size: 11px; background: transparent;"
                )
                tc.addWidget(t); tc.addWidget(s)
                hdr.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
                hdr.addLayout(tc, 1)
                _s.viewLayout.addLayout(hdr)

                body_lbl = QLabel(body)
                body_lbl.setTextFormat(Qt.TextFormat.RichText)
                body_lbl.setWordWrap(True)
                body_lbl.setStyleSheet(
                    "color: #C9D1D9; font-size: 12px; background: transparent;"
                )
                _s.viewLayout.addWidget(body_lbl)

                _s.widget.setMinimumWidth(440)

                _s.buttonLayout.removeWidget(_s.yesButton)
                _s.buttonLayout.removeWidget(_s.cancelButton)
                _s.buttonLayout.addStretch(1)
                _s.cancelButton.setText("Cancel")
                _s.cancelButton.setFixedWidth(120)
                _s.cancelButton.setStyleSheet(
                    "QPushButton { background: transparent; border: 1px solid #30363D;"
                    "  color: #E6EDF3; border-radius: 6px; padding: 8px 22px;"
                    "  font-weight: 700; font-size: 12px; }"
                    "QPushButton:hover { background: rgba(255,255,255,0.05);"
                    "  border-color: #58606A; }"
                )
                _s.yesButton.setText(f"   {primary_text}")
                _s.yesButton.setFixedWidth(140)
                _s.yesButton.setStyleSheet(
                    f"QPushButton {{ background: {primary_color};"
                    f"  border: 1px solid {primary_color}; color: white;"
                    "  border-radius: 6px; padding: 8px 18px;"
                    "  font-weight: 700; font-size: 12px; }}"
                    f"QPushButton:hover {{ background: {primary_hover};"
                    f"  border-color: {primary_hover}; }}"
                )
                _s.buttonLayout.addWidget(_s.cancelButton, 0, Qt.AlignmentFlag.AlignVCenter)
                _s.buttonLayout.addWidget(_s.yesButton, 0, Qt.AlignmentFlag.AlignVCenter)

        return bool(_ConfirmSheet(host).exec())

    def _set_footer_status(self, text: str, *, state: str = "idle"):
        """Center status text shown under all cards while exports run."""
        if not hasattr(self, "_footer_status"):
            return
        color = {
            "running": "#58A6FF",
            "ok":      "#3FB950",
            "error":   "#F85149",
            "idle":    "#8B949E",
        }.get(state, "#8B949E")
        self._footer_status.setText(text or "")
        self._footer_status.setStyleSheet(
            f"color: {color}; font-size: 11px; background: transparent;"
            " padding: 4px; font-weight: 600;"
        )

    def _open_status_details(self):
        """Toggle the detailed log under the status banner."""
        show = not (self.log.isVisible() or
                    (hasattr(self, "_log_empty") and self._log_empty.isVisible()))
        is_empty = self.log.toPlainText().strip() == ""
        if show:
            if is_empty and hasattr(self, "_log_empty"):
                self._log_empty.setVisible(True)
                self.log.setVisible(False)
            else:
                self.log.setVisible(True)
                if hasattr(self, "_log_empty"):
                    self._log_empty.setVisible(False)
        else:
            self.log.setVisible(False)
            if hasattr(self, "_log_empty"):
                self._log_empty.setVisible(False)
        self._status_detail_btn.setText(
            "  Hide details  ›" if (self.log.isVisible() or
                (hasattr(self, "_log_empty") and self._log_empty.isVisible()))
            else "  View details  ›"
        )

    def _save_settings(self):
        if not _SYNC_AVAILABLE:
            return
        try:
            cfg = load_config()
            cfg["designer_name"] = self.designer_name.text().strip()
            cfg["export_folder"] = self.export_folder.text().strip()
            save_config(cfg)
            self._log("Settings saved.")
        except Exception as e:
            self._log(f"Error saving settings: {e}", error=True)

    def _browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Teams/SharePoint export folder")
        if folder:
            import os
            self.export_folder.setText(folder)
            self.folder_hint.setText(f"✓ {folder}")
            self.folder_hint.setStyleSheet("color: #66bb6a; font-size: 10px;")

    # ── Export ────────────────────────────────────────────────────────────────

    def _do_export(self):
        if not _SYNC_AVAILABLE or self._busy:
            return
        self._save_settings()
        folder = self.export_folder.text().strip()
        if not folder:
            QMessageBox.warning(self, "No folder set",
                                "Please set the export folder in Settings first.")
            return
        import os
        if not os.path.isdir(folder):
            QMessageBox.warning(self, "Folder not found",
                                f"The export folder does not exist:\n{folder}")
            return
        target_date = self.export_date.date().toString("yyyy-MM-dd")
        if not self._fluent_confirm(
            title="Export to SharePoint",
            subtitle="Push the daily report to the team workbook.",
            body=(
                f"Export the daily report for <b>{target_date}</b> to the "
                "configured SharePoint folder.<br><br>"
                "Existing rows for this date are overwritten with the values "
                "from your local database."
            ),
            icon_svg="tabler_cloud_upload.svg",
            primary_text="Export",
        ):
            return
        self._log(f"Exporting {target_date}…")
        self._set_busy(True)
        self._set_footer_status(f"Exporting {target_date}…", state="running")
        self._thread = _ExportThread(target_date)
        self._thread.done.connect(self._on_export_done)
        self._thread.status.connect(self._log)
        self._thread.start()

    def _on_export_done(self, ok: bool, msg: str):
        self._set_busy(False)
        self._set_footer_status("Export to SharePoint",
                                  state="ok" if ok else "error")
        if ok:
            self._log(f"✓ {msg}")
        else:
            self._log(f"✗ {msg}", error=True)
            QMessageBox.critical(self, "Export Error", msg)

    # ── Bulk history upload ───────────────────────────────────────────────────

    def _do_bulk_history(self):
        if not _SYNC_AVAILABLE or self._busy:
            return
        self._save_settings()
        folder = self.export_folder.text().strip()
        if not folder:
            QMessageBox.warning(self, "No folder set",
                                "Please set the export folder in Settings first.")
            return
        import os
        if not os.path.isdir(folder):
            QMessageBox.warning(self, "Folder not found",
                                f"The export folder does not exist:\n{folder}")
            return
        if not self._fluent_confirm(
            title="Upload Missing History",
            subtitle="Scan local DB and push missing daily files.",
            body=(
                "This will scan every day in your local database and upload "
                "a daily file for any day that is missing or corrupt in the "
                "shared folder.<br><br>"
                "<b>Existing valid files will NOT be overwritten.</b><br><br>"
                "The Dashboard will be rebuilt once at the end."
            ),
            icon_svg="tabler_upload.svg",
            primary_text="Continue",
        ):
            return
        self._log("Starting bulk history upload…")
        self._set_busy(True)
        self._set_footer_status("Uploading missing history…", state="running")
        self._bulk_thread = _BulkHistoryThread()
        self._bulk_thread.progress.connect(self._on_bulk_progress)
        self._bulk_thread.done.connect(self._on_bulk_done)
        self._bulk_thread.start()

    def _on_bulk_progress(self, idx: int, total: int, message: str):
        self._log(f"[{idx}/{total}] {message}")

    def _on_bulk_done(self, ok: bool, msg: str):
        self._set_busy(False)
        self._set_footer_status("Upload Missing History",
                                  state="ok" if ok else "error")
        if ok:
            self._log("✓ Bulk history upload complete.")
        else:
            self._log("✗ Bulk history upload finished with errors.", error=True)
        self._log(msg)
        self._show_scrollable_report("Upload Missing History", msg)

    # ── Audit / rebuild historical snapshots ─────────────────────────────────

    def _do_audit(self):
        if not _SYNC_AVAILABLE or self._busy:
            return
        if not self._fluent_confirm(
            title="Audit Dashboard History",
            subtitle="Compare snapshots against per-designer summaries.",
            body=(
                "This will scan every dated <b>_Dashboard_YYYY-MM-DD.xlsx</b> "
                "snapshot in the Dashboards folder and compare it against the "
                "per-designer summary files.<br><br>"
                "<b>No files are modified</b> — the audit only reports missing "
                "designers, stale values, or mismatches."
            ),
            icon_svg="tabler_history.svg",
            primary_text="Run audit",
        ):
            return
        self._log("Running audit…")
        self._set_busy(True)
        self._set_footer_status("Auditing dashboard history…", state="running")
        self._audit_thread = _AuditThread()
        self._audit_thread.done.connect(self._on_audit_done)
        self._audit_thread.start()

    def _on_audit_done(self, clean: bool, report: str, issues: dict):
        self._set_busy(False)
        self._set_footer_status("Audit Dashboard History",
                                  state="ok" if clean else "error")
        self._log(report)
        self._last_audit_issues = issues
        self._show_scrollable_report("Audit", report)

    def _show_scrollable_report(self, title: str, text: str):
        """Fluent-styled scrollable report card matching the other modals."""
        try:
            from qfluentwidgets import MessageBoxBase
            from .tabler_icons import TablerIcon as _TI
        except Exception:
            # Fallback to the legacy QDialog if Fluent isn't available.
            dlg = QDialog(self)
            dlg.setWindowTitle(title)
            dlg.resize(720, 560)
            layout = QVBoxLayout(dlg)
            layout.setContentsMargins(12, 12, 12, 12)
            layout.setSpacing(8)
            view = QTextEdit()
            view.setReadOnly(True)
            view.setPlainText(text)
            layout.addWidget(view, 1)
            btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
            btns.rejected.connect(dlg.reject)
            btns.accepted.connect(dlg.accept)
            layout.addWidget(btns)
            dlg.exec()
            return

        from PySide6.QtCore import (
            QPropertyAnimation as _QPA, QEasingCurve as _QEC,
            Property as _QProp, QSize as _QSz,
        )
        from PySide6.QtGui import QColor as _QCol, QPainter as _QPn
        from PySide6.QtWidgets import QToolButton as _QTB, QFrame as _QFr

        # Pick an icon + accent that match the report type.
        title_low = title.lower()
        if "audit" in title_low:
            icon_svg, accent = "tabler_history.svg", "#58A6FF"
        elif "rebuild" in title_low:
            icon_svg, accent = "tabler_refresh.svg", "#D29922"
        elif "upload" in title_low:
            icon_svg, accent = "tabler_upload.svg", "#3FB950"
        else:
            icon_svg, accent = "tabler_file_analytics.svg", "#58A6FF"

        host = self

        class _ReportSheet(MessageBoxBase):
            def __init__(_s, h):
                super().__init__(h.window() if h is not None else None)
                try:
                    _s.setMaskColor(_QCol(0, 0, 0, 170))
                except Exception:
                    pass
                _s.widget.setObjectName("reportCard")
                apply_fluent_modal_palette(_s, "reportCard")
                _s.viewLayout.setContentsMargins(0, 8, 0, 8)
                _s.viewLayout.setSpacing(0)

                def _div():
                    d = _QFr()
                    d.setFixedHeight(1)
                    d.setStyleSheet("background: #21262D; border: none;")
                    return d

                # Header: icon + title + close X.
                hdr_wrap = QWidget()
                hl = QVBoxLayout(hdr_wrap)
                hl.setContentsMargins(22, 12, 22, 12)
                hl.setSpacing(6)
                hdr = QHBoxLayout(); hdr.setSpacing(10)
                ic = _QTB()
                ic.setEnabled(False)
                ic.setIcon(_TI(icon_svg).icon(color=_QCol(accent)))
                ic.setIconSize(_QSz(20, 20))
                ic.setStyleSheet(
                    f"background: rgba({_QCol(accent).red()},"
                    f"{_QCol(accent).green()},{_QCol(accent).blue()},0.14);"
                    " border: none; border-radius: 8px; padding: 6px;"
                )
                tc = QVBoxLayout(); tc.setSpacing(2)
                t_lbl = QLabel(title)
                t_lbl.setStyleSheet(
                    "color: #E6EDF3; font-size: 15px; font-weight: 700;"
                    " background: transparent;"
                )
                # Quick line count for context.
                line_count = len((text or "").splitlines())
                s_lbl = QLabel(f"{line_count} line(s) in the report.")
                s_lbl.setStyleSheet(
                    "color: #8B949E; font-size: 11px; background: transparent;"
                )
                tc.addWidget(t_lbl); tc.addWidget(s_lbl)

                class _SpinX(_QTB):
                    def __init__(s, *a, **kw):
                        super().__init__(*a, **kw)
                        s._rot = 0.0
                        s._anim = _QPA(s, b"rotation", s)
                        s._anim.setDuration(260)
                        s._anim.setEasingCurve(_QEC.OutCubic)
                    def get_rot(s): return s._rot
                    def set_rot(s, v):
                        s._rot = float(v); s.update()
                    rotation = _QProp(float, get_rot, set_rot)
                    def paintEvent(s, e):
                        p = _QPn(s); p.setRenderHint(_QPn.Antialiasing)
                        p.save()
                        p.translate(s.width()/2, s.height()/2)
                        p.rotate(s._rot)
                        p.translate(-s.width()/2, -s.height()/2)
                        s.icon().paint(p, 6, 6, s.width()-12, s.height()-12)
                        p.restore()
                    def enterEvent(s, e):
                        s._anim.stop(); s._anim.setStartValue(s._rot)
                        s._anim.setEndValue(90.0); s._anim.start()
                        super().enterEvent(e)
                    def leaveEvent(s, e):
                        s._anim.stop(); s._anim.setStartValue(s._rot)
                        s._anim.setEndValue(0.0); s._anim.start()
                        super().leaveEvent(e)

                cb = _SpinX()
                cb.setIcon(_TI("tabler_x.svg").icon(color=_QCol("#8B949E")))
                cb.setIconSize(_QSz(22, 22))
                cb.setCursor(Qt.PointingHandCursor)
                cb.setFixedSize(34, 34)
                cb.setStyleSheet(
                    "QToolButton { background: transparent; border: none;"
                    "  border-radius: 17px; }"
                    "QToolButton:hover { background: rgba(255,255,255,0.08); }"
                )
                cb.clicked.connect(_s.reject)
                hdr.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
                hdr.addLayout(tc, 1)
                hdr.addWidget(cb, 0, Qt.AlignmentFlag.AlignTop)
                hl.addLayout(hdr)
                _s.viewLayout.addWidget(hdr_wrap)
                _s.viewLayout.addWidget(_div())

                # Body — scrollable monospace text in a tinted card.
                body = QWidget()
                bl = QVBoxLayout(body)
                bl.setContentsMargins(22, 14, 22, 14)
                view = QTextEdit()
                view.setReadOnly(True)
                view.setPlainText(text)
                view.setStyleSheet(
                    "QTextEdit { background: #0B0F14;"
                    " border: 1px solid #21262D; border-radius: 8px;"
                    " color: #C9D1D9; font-family: Consolas, monospace;"
                    " font-size: 11px; padding: 10px; }"
                )
                bl.addWidget(view, 1)
                _s.viewLayout.addWidget(body, 1)
                _s.viewLayout.addWidget(_div())

                _s.widget.setMinimumWidth(780)
                _s.widget.setMinimumHeight(560)

                # Footer: Copy + Close.
                copy_btn = QPushButton("  Copy")
                copy_btn.setCursor(Qt.PointingHandCursor)
                copy_btn.setFixedHeight(30)
                copy_btn.setFixedWidth(110)
                try:
                    copy_btn.setIcon(_TI("tabler_file.svg").icon(color=_QCol("#E6EDF3")))
                    copy_btn.setIconSize(_QSz(13, 13))
                except Exception:
                    pass
                copy_btn.setStyleSheet(
                    "QPushButton { background: transparent; border: 1px solid #30363D;"
                    "  color: #E6EDF3; border-radius: 6px; padding: 4px 14px;"
                    "  font-weight: 600; font-size: 11px; }"
                    "QPushButton:hover { background: rgba(255,255,255,0.05); }"
                )
                from PySide6.QtWidgets import QApplication as _QApp
                copy_btn.clicked.connect(
                    lambda: _QApp.clipboard().setText(text or "")
                )

                _s.buttonLayout.removeWidget(_s.yesButton)
                _s.buttonLayout.removeWidget(_s.cancelButton)
                _s.yesButton.hide()
                _s.buttonLayout.addWidget(copy_btn, 0, Qt.AlignmentFlag.AlignVCenter)
                _s.buttonLayout.addStretch(1)
                _s.cancelButton.setText("Close")
                _s.cancelButton.setFixedWidth(120)
                _s.cancelButton.setStyleSheet(
                    "QPushButton { background: transparent; border: 1px solid #30363D;"
                    "  color: #E6EDF3; border-radius: 6px; padding: 8px 22px;"
                    "  font-weight: 700; font-size: 12px; }"
                    "QPushButton:hover { background: rgba(255,255,255,0.05);"
                    "  border-color: #58606A; }"
                )
                _s.buttonLayout.addWidget(_s.cancelButton, 0, Qt.AlignmentFlag.AlignVCenter)

        _ReportSheet(host).exec()

    def _do_rebuild_history(self):
        if not _SYNC_AVAILABLE:
            return
        self._save_settings()

        folder = self.export_folder.text().strip()
        import os
        if not folder or not os.path.isdir(folder):
            QMessageBox.warning(self, "Folder not set",
                                "Export folder must be set in Settings first.")
            return

        if not self._fluent_confirm(
            title="Rebuild Historical Dashboards",
            subtitle="Regenerate every dated dashboard snapshot.",
            body=(
                "This will regenerate every <b>_Dashboard_YYYY-MM-DD.xlsx</b> "
                "snapshot in the Dashboards folder from the per-designer "
                "summary files.<br><br>"
                "Live <b>_Dashboard.xlsx</b> is NOT touched. Existing "
                "snapshots are overwritten.<br><br>"
                "Recommended: have each designer run "
                "<i>Upload Missing History</i> first so their summaries are "
                "complete."
            ),
            icon_svg="tabler_refresh.svg",
            primary_text="Rebuild",
            primary_color="#D29922",
            primary_hover="#E8AC2D",
        ):
            return

        self._log("Starting historical dashboard rebuild…")
        self._set_busy(True)
        self._set_footer_status("Rebuilding historical dashboards…", state="running")

        self._hist_thread = _HistoricalRebuildThread(dates=None)
        self._hist_thread.progress.connect(self._on_hist_progress)
        self._hist_thread.done.connect(self._on_hist_done)
        self._hist_thread.start()

    def _on_hist_progress(self, idx: int, total: int, message: str):
        self._log(f"[{idx}/{total}] {message}")

    def _on_hist_done(self, ok: bool, msg: str):
        self._set_busy(False)
        self._set_footer_status("Rebuild Historical Dashboards",
                                  state="ok" if ok else "error")
        if ok:
            self._log("✓ Historical dashboards rebuilt.")
        else:
            self._log("✗ Historical rebuild finished with errors.", error=True)
        self._log(msg)
        self._show_scrollable_report("Rebuild Historical Dashboards", msg)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _log(self, msg: str, error: bool = False):
        """Append a polished log entry to the details pane.

        Each row renders as a small bordered card with a colored severity
        chip on the left, a monospace timestamp gutter, and the message
        body. Severity is inferred from the message text when error=False
        so existing call sites don't need to be touched."""
        from datetime import datetime
        import html as _html
        ts = datetime.now().strftime("%H:%M:%S")

        low = msg.lower()
        if error:
            level, fg, bg, border = "ERROR", "#F85149", "rgba(248,81,73,0.06)", "#5b2522"
            tag = "ERR"
        elif msg.startswith("✓") or any(k in low for k in (
                "success", "completed", "done", "ok ", "uploaded", "rebuilt")):
            level, fg, bg, border = "OK", "#3FB950", "rgba(63,185,80,0.05)", "#1f4f2a"
            tag = " OK"
        elif any(k in low for k in (
                "starting", "uploading", "exporting", "rebuilding",
                "building", "running", "processing", "scanning", "syncing")):
            level, fg, bg, border = "INFO", "#58A6FF", "rgba(88,166,255,0.05)", "#1F4F8F"
            tag = "RUN"
        elif msg.startswith("[") and "]" in msg[:8]:
            # Progress lines like "[12/45] doing thing"
            level, fg, bg, border = "PROG", "#A371F7", "rgba(163,113,247,0.05)", "#3d2d6e"
            tag = "···"
        elif any(k in low for k in ("warning", "warn", "skipping", "skip ",
                                       "deprecated")):
            level, fg, bg, border = "WARN", "#D29922", "rgba(210,153,34,0.06)", "#5b4824"
            tag = "WRN"
        else:
            level, fg, bg, border = "INFO", "#8B949E", "rgba(139,148,158,0.04)", "#2D333B"
            tag = "···"

        # Strip leading symbol so the chip doesn't double up with "✓ Done".
        body = msg
        for prefix in ("✓ ", "✗ "):
            if body.startswith(prefix):
                body = body[len(prefix):]
                break

        safe_msg = _html.escape(body).replace("\n", "<br>")
        chip_html = (
            f"<span style='display:inline-block; min-width:32px;"
            f" color:{fg}; background:{bg}; border:1px solid {border};"
            f" border-radius:4px; padding:1px 6px; font-size:10px;"
            f" font-weight:700; text-align:center;'>{tag}</span>"
        )
        ts_html = (
            f"<span style='color:#6E7681; font-family:Consolas,monospace;"
            f" font-size:11px;'>{ts}</span>"
        )
        body_html = (
            f"<span style='color:#C9D1D9; font-family:Consolas,monospace;"
            f" font-size:11px;'> {safe_msg}</span>"
        )
        line_html = (
            f"<div style='padding:4px 10px; border-left:3px solid {fg};'>"
            f"{chip_html} &nbsp; {ts_html} {body_html}"
            f"</div>"
        )
        self.log.append(line_html)

        # Reveal the box and hide the empty-state placeholder once the first
        # entry lands.
        if hasattr(self, "_log_empty") and self._log_empty.isVisible():
            self._log_empty.setVisible(False)
        # Mirror the latest event onto the status banner so the user sees
        # the outcome without expanding the detail log.
        if hasattr(self, "_status_banner"):
            low = msg.lower()
            if error:
                self._set_status_state("error", ts=ts, message=msg[:140])
            elif any(k in low for k in ("starting", "uploading", "processing",
                                          "running", "building", "rebuilding",
                                          "syncing")):
                self._set_status_state("running", ts=ts)
            elif any(k in low for k in ("done", "completed", "success",
                                          "exported", "uploaded", "rebuilt")):
                self._set_status_state("ok", ts=ts)
