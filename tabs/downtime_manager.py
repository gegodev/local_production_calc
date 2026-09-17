import os
import uuid

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTimeEdit, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QComboBox, QMessageBox,
    QHeaderView, QDialog, QTextEdit, QDialogButtonBox, QSizePolicy,
    QApplication, QFrame,
)
from PySide6.QtCore import (
    QTime, QDate, QTimer, Qt, Signal, QPropertyAnimation, QEasingCurve,
)
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QGraphicsOpacityEffect
from db.database import get_connection
from .theme_table_utils import CLR_FG_LIGHT
from .theme_palette import apply_fluent_modal_palette
from sync.app_config import load_config
from sync.app_logger import log_event
from datetime import datetime

# Local mirror of the pending-status string.
STATUS_PENDING_LOCAL = "pending"
STATUS_APPROVED_LOCAL = "approved"
STATUS_REJECTED_LOCAL = "rejected"

# Canonical reason list — shared by the inline form and the edit modal.
DOWNTIME_REASONS: list[str] = [
    "Anatomy Reprocess", "Breastfeeding Leave", "CMS Down", "Computer Issues",
    "Consultation", "Corporate Event", "CSS' Feedback", "CSS Revision",
    "Customer Meeting", "Evacuation Drill", "Extend Check emails allowances",
    "Extend Master Control allowances", "Extended Consultation",
    "Extended Weekly Huddle", "Gemba & listening Events", "Hours of Paid Leave",
    "Low Rate Feedback", "Medical and/or legal appointment", "MFG Feedbacks",
    "Multitreatment", "No_Cases", "Non-planned meetings", "One on One",
    "Other Meetings", "Project", "Quality Feedback", "Re Training ODB",
    "Regulatory Audit", "Relocation (Hardware/Software)",
    "Relocation (Internet/Electricity Issues)", "Rev Guidelines",
    "Site (Internet/Electricity Issues)", "Software", "Spark Town Hall",
    "Survey", "Team Meeting", "Training", "Translation", "WorkDay Courses",
]


class DowntimeManager(QWidget):
    def __init__(self, parent=None, on_update_callback=None):
        super().__init__(parent)
        self.on_update_callback = on_update_callback
        self.delete_mode = False
        self.edit_mode = False
        self.current_date = datetime.now().strftime("%Y-%m-%d")
        self.init_ui()
        self.load_downtimes()
        self._start_refresh_timer()

    def _start_refresh_timer(self):
        """Refresh the downtime table every 15 s so status changes made on
        another machine (or by the background retry worker) become visible
        without requiring a manual reload."""
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(15_000)
        # Skip the DB hit when the table isn't actually on screen — saves a
        # query every 15 s for every Register tab in the app and keeps the
        # OneDrive-hosted DB out of the way of the active view.
        self._refresh_timer.timeout.connect(self._refresh_if_visible)
        self._refresh_timer.start()
        # Stop the timer if the widget is destroyed so it can't fire on a
        # deleted C++ object (which would crash the app on shutdown).
        self.destroyed.connect(self._stop_refresh_timer)

    def _refresh_if_visible(self):
        """Only reload when the widget is actually visible on screen."""
        try:
            if not self.isVisible():
                return
        except RuntimeError:
            # Widget already deleted — let the destroyed handler clean up.
            return
        self.load_downtimes()

    def _stop_refresh_timer(self, *_args):
        """Idempotent timer stop. Called on widget destruction and on close."""
        timer = getattr(self, "_refresh_timer", None)
        if timer is not None:
            try:
                timer.stop()
            except Exception:
                pass
            self._refresh_timer = None

    def closeEvent(self, event):
        self._stop_refresh_timer()
        super().closeEvent(event)

    def set_date(self, date_str: str):
        """Called by RegisterTab when the date picker changes."""
        self.current_date = date_str
        self.load_downtimes()

    def init_ui(self):
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(8)

        # Input section
        input_layout = QHBoxLayout()
        input_layout.setSpacing(6)

        # Trailing clock icon helper for the time edits.
        def _add_clock(time_edit):
            try:
                from .tabler_icons import TablerIcon as _TI
                from PySide6.QtGui import QAction, QColor as _QC
                from PySide6.QtWidgets import QLineEdit
                icn = _TI("tabler_clock.svg").icon(color=_QC("#8B949E"))
                le = time_edit.lineEdit() if hasattr(time_edit, "lineEdit") else None
                if le is not None:
                    act = QAction(icn, "", le)
                    le.addAction(act, QLineEdit.ActionPosition.TrailingPosition)
            except Exception:
                pass

        _time_input_css = (
            "QTimeEdit { border-radius: 5px; padding: 4px 8px; }"
        )

        input_layout.addWidget(QLabel("Start"))
        input_layout.addSpacing(4)
        self.downtime_start = QTimeEdit()
        self.downtime_start.setDisplayFormat("HH:mm")
        self.downtime_start.setTime(QTime.currentTime())
        self.downtime_start.setMinimumWidth(90)
        self.downtime_start.setMinimumHeight(30)
        self.downtime_start.setMaximumHeight(32)
        self.downtime_start.setStyleSheet(_time_input_css)
        _add_clock(self.downtime_start)
        input_layout.addWidget(self.downtime_start)

        input_layout.addSpacing(8)
        input_layout.addWidget(QLabel("End"))
        input_layout.addSpacing(4)
        self.downtime_end = QTimeEdit()
        self.downtime_end.setDisplayFormat("HH:mm")
        self.downtime_end.setTime(QTime.currentTime())
        self.downtime_end.setMinimumWidth(90)
        self.downtime_end.setMinimumHeight(30)
        self.downtime_end.setMaximumHeight(32)
        self.downtime_end.setStyleSheet(_time_input_css)
        _add_clock(self.downtime_end)
        # End cannot be earlier than Start (blocks typing + scroll below Start)
        self.downtime_end.setMinimumTime(self.downtime_start.time())
        self.downtime_start.timeChanged.connect(self._on_start_time_changed)
        self.downtime_end.timeChanged.connect(self._on_end_time_changed)
        input_layout.addWidget(self.downtime_end)

        input_layout.addSpacing(8)
        input_layout.addWidget(QLabel("Reason"))
        input_layout.addSpacing(4)
        self.downtime_reason = QComboBox()
        self.downtime_reason.addItems(DOWNTIME_REASONS)
        self.downtime_reason.setMinimumWidth(180)
        self.downtime_reason.setMinimumHeight(30)
        self.downtime_reason.setMaximumHeight(32)
        self.downtime_reason.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.downtime_reason.setStyleSheet(
            "QComboBox { border-radius: 5px; padding: 4px 26px 4px 8px; }"
            "QComboBox::drop-down { width: 22px;"
            "  border-left: 1px solid rgba(130,130,130,0.35); }"
        )
        input_layout.addWidget(self.downtime_reason)
        input_layout.setStretch(input_layout.count() - 1, 1)

        add_btn = QPushButton("Add")
        add_btn.setMaximumWidth(75)
        add_btn.setMinimumHeight(30)
        add_btn.setMaximumHeight(32)
        def _apply_add_btn(is_light: bool, _b=add_btn):
            try:
                from .theme_palette import palette
                p = palette(is_light)
            except Exception:
                p = {"accent": "#1757D4", "accent_2": "#1F6FEB"}
            _b.setStyleSheet(
                f"QPushButton {{ background-color: {p['accent']};"
                f"  border: 1px solid {p['accent']}; color: white;"
                f"  border-radius: 8px; font-weight: 700; font-size: 11px;"
                f"  padding: 6px 14px; }}"
                f"QPushButton:hover {{ background-color: {p['accent_2']};"
                f"  border-color: {p['accent_2']}; }}"
                f"QPushButton:pressed {{ background-color: {p['accent']}; }}"
            )
        add_btn.apply_palette = _apply_add_btn
        try:
            from qfluentwidgets.common.style_sheet import isDarkTheme
            _apply_add_btn(not isDarkTheme())
        except Exception:
            _apply_add_btn(False)
        add_btn.clicked.connect(self.add_downtime)
        input_layout.addWidget(add_btn)

        input_layout.addStretch()
        main_layout.addLayout(input_layout)

        # Table
        self.table = QTableWidget()
        self.table.setAlternatingRowColors(False)
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels([
            "Start", "End", "Mins", "Reason", "Status",
            "Sync", "Actions",
        ])
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.cellClicked.connect(self.on_cell_clicked)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(True)
        def _apply_dt_table(is_light: bool, _t=self.table):
            try:
                from .theme_palette import palette
                p = palette(is_light)
            except Exception:
                p = {"base": "#0D1117", "surface": "#161B22",
                     "border": "#21262D", "text": "#E6EDF3",
                     "muted": "#8B949E"}
            _t.setStyleSheet(f"""
                QTableWidget {{
                    background-color: {p['base']};
                    border: 1px solid {p['border']};
                    border-radius: 10px;
                    gridline-color: {p['border']};
                    outline: none;
                }}
                QTableWidget::item {{
                    padding: 6px 8px;
                    border: none;
                    color: {p['text']};
                }}
                QTableWidget::item:selected {{ background-color: transparent; }}
                QHeaderView {{ background: transparent; border: none; }}
                QHeaderView::section {{
                    background-color: {p['surface']};
                    color: {p['muted']};
                    padding: 8px 8px;
                    border: none;
                    border-bottom: 1px solid {p['border']};
                    font-weight: 700;
                    font-size: 10px;
                }}
                QHeaderView::section:first {{ border-top-left-radius: 10px; }}
                QHeaderView::section:last  {{ border-top-right-radius: 10px; }}
            """)
        self.table.apply_palette = _apply_dt_table
        try:
            from qfluentwidgets.common.style_sheet import isDarkTheme
            _apply_dt_table(not isDarkTheme())
        except Exception:
            _apply_dt_table(False)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 62)
        self.table.setColumnWidth(1, 62)
        self.table.setColumnWidth(2, 72)
        self.table.setColumnWidth(4, 140)
        self.table.setColumnWidth(5, 70)
        self.table.setColumnWidth(6, 220)
        self.table.setMinimumHeight(90)
        self.table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # Pick-mode hint label — collapsed by default; animated open/close
        # so entering Delete/Edit mode doesn't make the layout "jump".
        # Both min and max height are pinned to 0 so the empty label never
        # reserves residual space in the parent layout.
        self._pick_hint = QLabel("")
        self._pick_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._pick_hint.setStyleSheet(
            "QLabel { padding: 0 8px; border-radius: 4px; font-weight: 600; "
            "font-size: 11px; }"
        )
        self._pick_hint.setMinimumHeight(0)
        self._pick_hint.setMaximumHeight(0)
        self._pick_hint.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        # Opacity effect on the hint so it fades in/out alongside the slide.
        self._pick_hint_opacity = QGraphicsOpacityEffect(self._pick_hint)
        self._pick_hint_opacity.setOpacity(0.0)
        self._pick_hint.setGraphicsEffect(self._pick_hint_opacity)
        main_layout.addWidget(self._pick_hint)
        # Stacked container: real table OR empty-state placeholder.
        from PySide6.QtWidgets import QStackedWidget
        self._table_stack = QStackedWidget()
        self._table_stack.addWidget(self.table)          # index 0
        self._table_stack.addWidget(self._build_empty_state())  # index 1
        main_layout.addWidget(self._table_stack)

        # Animations driven by _refresh_pick_mode_ui.
        self._pick_hint_height_anim = QPropertyAnimation(
            self._pick_hint, b"maximumHeight", self
        )
        self._pick_hint_height_anim.setDuration(180)
        self._pick_hint_height_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._pick_hint_fade_anim = QPropertyAnimation(
            self._pick_hint_opacity, b"opacity", self
        )
        self._pick_hint_fade_anim.setDuration(180)
        self._pick_hint_fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        # Buttons layout - centered
        buttons_layout = QHBoxLayout()
        buttons_layout.setSpacing(10)
        buttons_layout.addStretch()

        try:
            from .tabler_icons import TablerIcon as _TI_btn
            from PySide6.QtCore import QSize as _QSizeBtn
            _trash_icon = _TI_btn("tabler_trash.svg").icon(color=QColor("#F85149"))
            _pencil_icon = _TI_btn("tabler_pencil.svg").icon(color=QColor("#388BFD"))
            _icn_size = _QSizeBtn(14, 14)
        except Exception:
            _trash_icon = _pencil_icon = None
            _icn_size = None

        self.delete_btn = QPushButton("Delete")
        self.delete_btn.setMaximumWidth(100)
        self.delete_btn.setMinimumHeight(30)
        if _trash_icon is not None:
            self.delete_btn.setIcon(_trash_icon)
            self.delete_btn.setIconSize(_icn_size)
        def _action_btn_css(is_light: bool, accent_hover: str) -> str:
            try:
                from .theme_palette import palette
                p = palette(is_light)
            except Exception:
                p = {"surface": "#161B22", "border_strong": "#30363D",
                     "text": "#E6EDF3"}
            return (
                f"QPushButton {{ background: {p['surface']};"
                f"  border: 1px solid {p['border_strong']};"
                f"  border-radius: 8px; color: {p['text']};"
                f"  font-weight: 700; font-size: 11px; padding: 5px 12px; }}"
                f"QPushButton:hover {{ background: rgba(248,81,73,0.10);"
                f"  border-color: {accent_hover}; color: {accent_hover}; }}"
                f"QPushButton:pressed {{ background: rgba(248,81,73,0.18); }}"
            )

        def _apply_del(is_light: bool, _b=self.delete_btn):
            _b.setStyleSheet(_action_btn_css(is_light, "#F85149"))
        self.delete_btn.apply_palette = _apply_del
        try:
            from qfluentwidgets.common.style_sheet import isDarkTheme
            _apply_del(not isDarkTheme())
        except Exception:
            _apply_del(False)
        self.delete_btn.clicked.connect(self.delete_downtime)
        buttons_layout.addWidget(self.delete_btn)

        self.edit_btn = QPushButton("Edit")
        self.edit_btn.setMaximumWidth(100)
        self.edit_btn.setMinimumHeight(30)
        if _pencil_icon is not None:
            self.edit_btn.setIcon(_pencil_icon)
            self.edit_btn.setIconSize(_icn_size)
        def _apply_edit(is_light: bool, _b=self.edit_btn):
            try:
                from .theme_palette import palette
                p = palette(is_light)
            except Exception:
                p = {"surface": "#161B22", "border_strong": "#30363D",
                     "text": "#E6EDF3", "accent_2": "#388BFD"}
            _b.setStyleSheet(
                f"QPushButton {{ background: {p['surface']};"
                f"  border: 1px solid {p['border_strong']};"
                f"  border-radius: 8px; color: {p['text']};"
                f"  font-weight: 700; font-size: 11px; padding: 5px 12px; }}"
                f"QPushButton:hover {{ background: rgba(56,139,253,0.10);"
                f"  border-color: {p['accent_2']}; color: {p['accent_2']}; }}"
                f"QPushButton:pressed {{ background: rgba(56,139,253,0.18); }}"
            )
        self.edit_btn.apply_palette = _apply_edit
        try:
            from qfluentwidgets.common.style_sheet import isDarkTheme
            _apply_edit(not isDarkTheme())
        except Exception:
            _apply_edit(False)
        self.edit_btn.clicked.connect(self.edit_downtime)
        buttons_layout.addWidget(self.edit_btn)

        buttons_layout.addStretch()
        main_layout.addLayout(buttons_layout)

        self.setLayout(main_layout)
        self._apply_table_layout_mode()

    def add_downtime(self):
        start = self.downtime_start.time().toString("HH:mm")
        end = self.downtime_end.time().toString("HH:mm")
        reason = self.downtime_reason.currentText()

        # Calculate duration
        start_mins = self.downtime_start.time().hour() * 60 + self.downtime_start.time().minute()
        end_mins = self.downtime_end.time().hour() * 60 + self.downtime_end.time().minute()
        duration = end_mins - start_mins
        if duration < 0:
            duration += 24 * 60

        err = self._validate_downtime(start_mins, end_mins, reason)
        if err:
            self._toast(err, level="warning")
            return

        detalle = self._get_downtime_detail(reason)
        if detalle is None:
            return  # Cancelado por el usuario

        # New flow: every DT starts in pending. The user pastes it to Teams
        # via the Copy button, the supervisor reacts, and the user marks it
        # Approved or Rejected manually inside the app. No auto-approval.
        client_uid = str(uuid.uuid4())
        # synced_to_teams is repurposed as "manually marked by user" (always 1
        # to keep the retry worker out of the Teams loop, which no longer exists).
        #
        # The DB lives on a OneDrive-synced folder, so the INSERT can hit a
        # transient "database is locked" while OneDrive holds the file. That
        # used to bubble out of this Qt slot unhandled and PySide6 would kill
        # the whole process (the "freeze then close" users reported). Catch it,
        # keep the app alive, and let the user retry — the entry is not lost.
        try:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO downtimes
                    (fecha, hora_inicio, hora_fin, razon, duracion, status, detalle,
                     client_uid, synced_to_excel, synced_to_teams)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 1)
            """, (
                self.current_date, start, end, reason, duration,
                STATUS_PENDING_LOCAL, detalle, client_uid,
            ))
            dt_id = cursor.lastrowid
            conn.commit()
        except Exception as exc:
            log_event("downtime_manager",
                      f"add_downtime INSERT failed: {exc}", level="ERROR")
            self._toast(
                "Couldn't save the downtime — the shared file was busy "
                "(OneDrive sync). Nothing was lost; try Add again in a moment.",
                level="error",
            )
            return
        self.load_downtimes()
        self.downtime_start.setTime(QTime.currentTime())
        self.downtime_end.setTime(QTime.currentTime())

        if self.on_update_callback:
            self.on_update_callback()

    def _get_downtime_detail(self, reason: str) -> str | None:
        """Fluent modal sheet for the downtime reason detail.
        Mirrors the ChatGPT mockup: title row with clock icon + X, required
        textarea with character counter, tips strip, Cancel / Save buttons.
        """
        needs_case_id = reason.strip().lower() == "multitreatment"
        try:
            from qfluentwidgets import (
                MessageBoxBase, BodyLabel, LineEdit as FLineEdit, TextEdit as FTextEdit,
            )
            from PySide6.QtCore import QSize as _QSizeDtl
            from PySide6.QtWidgets import QToolButton
            from .tabler_icons import TablerIcon as _TIdtl

            _MAX_CHARS = 500

            class _DetailSheet(MessageBoxBase):
                def __init__(_self, host, needs_case):
                    super().__init__(host.window())
                    # Heavier dim on the backdrop so the underlying UI reads
                    # as "behind a blur" without paying the cost of a real
                    # QGraphicsBlurEffect on a complex window.
                    try:
                        _self.setMaskColor(QColor(0, 0, 0, 170))
                    except Exception:
                        pass
                    # Card surface color override (#101824 instead of grey).
                    # Scoped to the dialog's own object so it doesn't leak to
                    # child QLabel / QFrame descendants (which would show as
                    # a grid of bordered boxes).
                    _self.widget.setObjectName("dtDetailCard")
                    _self.widget.setStyleSheet(
                        "#dtDetailCard { background: #101824;"
                        " border: 1px solid #21262D; border-radius: 14px; }"
                    )
                    # Style the buttonGroup directly so its dark accent strip
                    # from the default Fluent stylesheet is overridden.
                    _self.buttonGroup.setStyleSheet(
                        "QFrame { background: #101824; border: none; }"
                    )

                    # Reset viewLayout side margins so dividers can span the
                    # full card width — sections get their own inner padding.
                    _self.viewLayout.setContentsMargins(0, 8, 0, 8)
                    _self.viewLayout.setSpacing(0)

                    def _section_wrap(child_layout):
                        w = QWidget()
                        lw = QVBoxLayout(w)
                        lw.setContentsMargins(22, 12, 22, 12)
                        lw.setSpacing(6)
                        if isinstance(child_layout, QWidget):
                            lw.addWidget(child_layout)
                        else:
                            lw.addLayout(child_layout)
                        return w

                    def _full_divider():
                        d = QFrame()
                        d.setFixedHeight(1)
                        d.setStyleSheet("background: #21262D; border: none;")
                        return d

                    # ── Header (clock icon + title) ──
                    header_row = QHBoxLayout()
                    header_row.setSpacing(10)
                    header_row.setContentsMargins(0, 0, 0, 0)
                    icon_btn = QToolButton()
                    icon_btn.setEnabled(False)
                    icon_btn.setIcon(_TIdtl("tabler_clock.svg").icon(color=QColor("#388BFD")))
                    icon_btn.setIconSize(_QSizeDtl(20, 20))
                    icon_btn.setStyleSheet(
                        "background: rgba(56,139,253,0.12); border: none;"
                        " border-radius: 8px; padding: 6px;"
                    )
                    title_col = QVBoxLayout()
                    title_col.setSpacing(2)
                    title_lbl = QLabel("Add downtime detail")
                    title_lbl.setStyleSheet(
                        "color: #E6EDF3; font-size: 15px; font-weight: 700;"
                        " background: transparent;"
                    )
                    sub_lbl = QLabel("Add a reason so it's easy to review later.")
                    sub_lbl.setStyleSheet(
                        "color: #8B949E; font-size: 11px; background: transparent;"
                    )
                    title_col.addWidget(title_lbl)
                    title_col.addWidget(sub_lbl)
                    header_row.addWidget(icon_btn, 0, Qt.AlignTop)
                    header_row.addLayout(title_col, 1)

                    # Close ✕ button at top right — Tabler X icon with a
                    # 180° spin on hover (and back on leave).
                    from PySide6.QtCore import (
                        QPropertyAnimation as _QPA_x, QEasingCurve as _QEC_x,
                    )
                    from PySide6.QtWidgets import QGraphicsRotation

                    close_btn = QToolButton()
                    close_btn.setIcon(_TIdtl("tabler_x.svg").icon(color=QColor("#8B949E")))
                    close_btn.setIconSize(_QSizeDtl(22, 22))
                    close_btn.setCursor(Qt.PointingHandCursor)
                    close_btn.setFixedSize(34, 34)
                    close_btn.setStyleSheet(
                        "QToolButton { background: transparent; border: none;"
                        "  border-radius: 17px; }"
                        "QToolButton:hover { background: rgba(255,255,255,0.08); }"
                    )
                    close_btn.clicked.connect(_self.reject)

                    # Hover-driven 180° rotation animation on a custom
                    # _rotation property. enterEvent → spin to 180,
                    # leaveEvent → spin back to 0.
                    from PySide6.QtCore import Property as _QProp
                    from PySide6.QtGui import QTransform
                    class _SpinBtn(type(close_btn)):
                        def __init__(s, *a, **kw):
                            super().__init__(*a, **kw)
                            s._rot = 0.0
                            s._anim = _QPA_x(s, b"rotation", s)
                            s._anim.setDuration(260)
                            s._anim.setEasingCurve(_QEC_x.OutCubic)
                        def get_rot(s): return s._rot
                        def set_rot(s, v):
                            s._rot = float(v); s.update()
                        rotation = _QProp(float, get_rot, set_rot)
                        def paintEvent(s, e):
                            from PySide6.QtGui import QPainter
                            p = QPainter(s)
                            p.setRenderHint(QPainter.Antialiasing)
                            # bg circle (style-driven via QSS still applies)
                            p.save()
                            p.translate(s.width()/2, s.height()/2)
                            p.rotate(s._rot)
                            p.translate(-s.width()/2, -s.height()/2)
                            s.icon().paint(p, 6, 6,
                                           s.width()-12, s.height()-12)
                            p.restore()
                        def enterEvent(s, e):
                            s._anim.stop()
                            s._anim.setStartValue(s._rot)
                            s._anim.setEndValue(90.0)
                            s._anim.start()
                            super().enterEvent(e)
                        def leaveEvent(s, e):
                            s._anim.stop()
                            s._anim.setStartValue(s._rot)
                            s._anim.setEndValue(0.0)
                            s._anim.start()
                            super().leaveEvent(e)

                    close_btn = _SpinBtn()
                    close_btn.setIcon(_TIdtl("tabler_x.svg").icon(color=QColor("#8B949E")))
                    close_btn.setIconSize(_QSizeDtl(22, 22))
                    close_btn.setCursor(Qt.PointingHandCursor)
                    close_btn.setFixedSize(34, 34)
                    close_btn.setStyleSheet(
                        "QToolButton { background: transparent; border: none;"
                        "  border-radius: 17px; }"
                        "QToolButton:hover { background: rgba(255,255,255,0.08); }"
                    )
                    close_btn.clicked.connect(_self.reject)
                    header_row.addWidget(close_btn, 0, Qt.AlignTop)
                    _self.viewLayout.addWidget(_section_wrap(header_row))
                    _self.viewLayout.addWidget(_full_divider())

                    # ── Body: optional Case ID + Reason detail + textarea ──
                    body_w = QWidget()
                    body_lay = QVBoxLayout(body_w)
                    body_lay.setContentsMargins(22, 16, 22, 16)
                    body_lay.setSpacing(8)

                    _self.case_input = None
                    if needs_case:
                        case_lbl = QLabel(
                            "Case ID <span style='color:#F85149;'>*</span>"
                        )
                        case_lbl.setTextFormat(Qt.TextFormat.RichText)
                        case_lbl.setStyleSheet(
                            "color: #C9D1D9; font-size: 13px; font-weight: 700;"
                            " background: transparent;"
                        )
                        _self.case_input = FLineEdit()
                        _self.case_input.setPlaceholderText("e.g. 123456789")
                        body_lay.addWidget(case_lbl)
                        body_lay.addWidget(_self.case_input)

                    detail_lbl = QLabel(
                        "Reason detail <span style='color:#F85149;'>*</span>"
                    )
                    detail_lbl.setTextFormat(Qt.TextFormat.RichText)
                    detail_lbl.setStyleSheet(
                        "color: #C9D1D9; font-size: 13px; font-weight: 700;"
                        " background: transparent;"
                    )
                    body_lay.addWidget(detail_lbl)

                    helper = QLabel("Provide a short description of what happened.")
                    helper.setStyleSheet(
                        "color: #8B949E; font-size: 12px; background: transparent;"
                    )
                    body_lay.addWidget(helper)

                    _self.editor = FTextEdit()
                    _self.editor.setPlaceholderText("e.g. 'CMS down for maintenance' …")
                    _self.editor.setMinimumHeight(110)
                    body_lay.addWidget(_self.editor)

                    _self.counter = QLabel(f"0 / {_MAX_CHARS}")
                    _self.counter.setStyleSheet(
                        "color: #6E7681; font-size: 10px; background: transparent;"
                    )
                    counter_row = QHBoxLayout()
                    counter_row.addStretch()
                    counter_row.addWidget(_self.counter)
                    body_lay.addLayout(counter_row)

                    def _on_text():
                        n = len(_self.editor.toPlainText())
                        if n > _MAX_CHARS:
                            _self.editor.blockSignals(True)
                            _self.editor.setPlainText(_self.editor.toPlainText()[:_MAX_CHARS])
                            _self.editor.blockSignals(False)
                            n = _MAX_CHARS
                        color = "#F85149" if n >= _MAX_CHARS else "#6E7681"
                        _self.counter.setText(f"{n} / {_MAX_CHARS}")
                        _self.counter.setStyleSheet(
                            f"color: {color}; font-size: 10px; background: transparent;"
                        )
                    _self.editor.textChanged.connect(_on_text)

                    # ── Tips card ──
                    tips_card = QFrame()
                    tips_card.setStyleSheet(
                        "QFrame { background: rgba(56,139,253,0.08);"
                        " border: 1px solid rgba(56,139,253,0.30);"
                        " border-radius: 10px; }"
                        "QLabel { background: transparent; border: none; color: #C9D1D9;"
                        " font-size: 11px; }"
                    )
                    tips_lay = QHBoxLayout(tips_card)
                    tips_lay.setContentsMargins(12, 10, 12, 10)
                    tips_lay.setSpacing(8)
                    bulb = QToolButton()
                    bulb.setEnabled(False)
                    bulb.setIcon(_TIdtl("tabler_alert_triangle.svg").icon(color=QColor("#388BFD")))
                    bulb.setIconSize(_QSizeDtl(16, 16))
                    bulb.setStyleSheet("background: transparent; border: none;")
                    tips_text_col = QVBoxLayout()
                    tips_text_col.setSpacing(1)
                    tips_title = QLabel("Tips")
                    tips_title.setStyleSheet(
                        "color: #58A6FF; font-size: 11px; font-weight: 700;"
                    )
                    tips_body = QLabel(
                        "Be specific and include any relevant details that may help "
                        "later (context, impact, cause, etc.)."
                    )
                    tips_body.setWordWrap(True)
                    tips_text_col.addWidget(tips_title)
                    tips_text_col.addWidget(tips_body)
                    tips_lay.addWidget(bulb, 0, Qt.AlignTop)
                    tips_lay.addLayout(tips_text_col, 1)
                    body_lay.addWidget(tips_card)

                    _self.viewLayout.addWidget(body_w)
                    _self.viewLayout.addWidget(_full_divider())

                    _self.widget.setMinimumWidth(480)
                    _self.yesButton.setText("   Save")
                    _self.cancelButton.setText("Cancel")
                    try:
                        _self.yesButton.setIcon(
                            _TIdtl("tabler_device_floppy.svg").icon(color=QColor("#FFFFFF"))
                        )
                        _self.yesButton.setIconSize(_QSizeDtl(14, 14))
                    except Exception:
                        pass

                    # Swap order: Cancel on the left, Save (primary) on right.
                    _self.buttonLayout.removeWidget(_self.yesButton)
                    _self.buttonLayout.removeWidget(_self.cancelButton)
                    _self.cancelButton.setFixedWidth(120)
                    _self.yesButton.setFixedWidth(120)
                    _self.buttonLayout.addStretch(1)
                    _self.buttonLayout.addWidget(_self.cancelButton, 0, Qt.AlignVCenter)
                    _self.buttonLayout.addWidget(_self.yesButton, 0, Qt.AlignVCenter)

                    # Cancel pill — transparent outline.
                    _self.cancelButton.setStyleSheet(
                        "QPushButton { background: transparent; border: 1px solid #30363D;"
                        "  color: #E6EDF3; border-radius: 10px; padding: 8px 22px;"
                        "  font-weight: 700; font-size: 12px; max-width: 130px; }"
                        "QPushButton:hover { background: rgba(255,255,255,0.05);"
                        "  border-color: #58606A; }"
                    )
                    # Save pill — primary blue.
                    _self.yesButton.setStyleSheet(
                        "QPushButton { background: #1e63e4; border: 1px solid #1e63e4;"
                        "  color: white; border-radius: 10px; padding: 8px 22px;"
                        "  font-weight: 700; font-size: 12px; max-width: 130px; }"
                        "QPushButton:hover { background: #2a73f3; border-color: #2a73f3; }"
                        "QPushButton:pressed { background: #154fbb; }"
                    )

            dlg = _DetailSheet(self, needs_case_id)
            if not dlg.exec():
                return None
            detail = dlg.editor.toPlainText().strip()
            if needs_case_id:
                case_id = dlg.case_input.text().strip() if dlg.case_input else ""
                if not case_id:
                    self._toast("Multitreatment requires a Case ID.", level="warning")
                    return None
                return f"Case ID: {case_id} | {detail}" if detail else f"Case ID: {case_id}"
            return detail
        except Exception as _exc:
            log_event("downtime_manager",
                      f"Fluent detail sheet failed, falling back: {_exc}",
                      level="WARN")
            dialog = QDialog(self)
            dialog.setWindowTitle("Downtime detail")
            layout = QVBoxLayout(dialog)
            text_edit = QTextEdit()
            layout.addWidget(text_edit)
            buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            layout.addWidget(buttons)
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            return text_edit.toPlainText().strip() if dialog.exec() else None

    def _is_light_mode(self) -> bool:
        """Return last theme state pushed via update_theme_labels.

        Substring matching on `app.styleSheet()` breaks when the user
        customizes the light palette (the default `#F6F8FA` is replaced),
        so we cache the flag instead.
        """
        return bool(getattr(self, "_light_mode_active", False))

    def update_theme_labels(self, is_light: bool):
        """Hook called by parent when theme changes — caches the flag and
        reloads the table so item foregrounds get recomputed."""
        self._light_mode_active = bool(is_light)
        self.load_downtimes()

    _MIN_DURATION_MIN = 1

    def _validate_downtime(self, start_mins: int, end_mins: int,
                            reason: str, exclude_id: int | None = None) -> str | None:
        """Return an error message if the downtime is invalid, else None."""
        if not reason or not reason.strip():
            return "Reason is required."

        duration = end_mins - start_mins
        if duration < self._MIN_DURATION_MIN:
            return f"Duration must be at least {self._MIN_DURATION_MIN} minute(s)."

        # Overlap check against existing downtimes for the same date.
        # Two intervals [a,b) and [c,d) overlap iff a < d AND c < b.
        try:
            conn = get_connection()
            cur = conn.cursor()
            cur.execute(
                "SELECT id, hora_inicio, hora_fin FROM downtimes "
                "WHERE fecha = ? AND COALESCE(status,'') != 'rejected'",
                (self.current_date,),
            )
            rows = cur.fetchall()
            conn.close()
        except Exception as exc:
            return f"Could not validate overlaps: {exc}"

        for row_id, hi, hf in rows:
            if exclude_id is not None and row_id == exclude_id:
                continue
            try:
                hi_t = QTime.fromString(hi, "HH:mm")
                hf_t = QTime.fromString(hf, "HH:mm")
                s = hi_t.hour() * 60 + hi_t.minute()
                e = hf_t.hour() * 60 + hf_t.minute()
            except Exception:
                continue
            if start_mins < e and s < end_mins:
                return (f"Overlaps with existing downtime "
                        f"{hi}–{hf}. Adjust the time range.")
        return None

    def _on_start_time_changed(self, t: QTime):
        """Keep End >= Start. Bump End forward if Start moves past it."""
        self.downtime_end.setMinimumTime(t)
        if self.downtime_end.time() < t:
            self.downtime_end.setTime(t)

    def _on_end_time_changed(self, t: QTime):
        """Safety net — if End somehow drops below Start, snap it back."""
        start = self.downtime_start.time()
        if t < start:
            self.downtime_end.blockSignals(True)
            self.downtime_end.setTime(start)
            self.downtime_end.blockSignals(False)

    def _normalize_status(self, status: str) -> str:
        s = (status or "").strip().lower()
        if s in ("accept", "accepted", "approve"):
            return "approved"
        if s in ("reject",):
            return "rejected"
        if s in ("pending", "approved", "rejected"):
            return s
        # Default: every downtime starts pending until the designer marks it.
        return "pending"

    def _toast(self, message: str, level: str = "warning", duration_ms: int = 4500):
        """Fluent InfoBar instead of a modal QMessageBox warning."""
        try:
            from qfluentwidgets import InfoBar, InfoBarPosition, InfoBarIcon
            icon = {
                "success": InfoBarIcon.SUCCESS,
                "warning": InfoBarIcon.WARNING,
                "error":   InfoBarIcon.ERROR,
                "info":    InfoBarIcon.INFORMATION,
            }.get(level, InfoBarIcon.INFORMATION)
            InfoBar.new(
                icon=icon,
                title="",
                content=message,
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP_RIGHT,
                duration=duration_ms,
                parent=self.window(),
            )
        except Exception:
            # Fallback to modal if the Fluent lib is unavailable for any reason.
            QMessageBox.warning(self, "Notice", message)

    def _build_empty_state(self):
        """Centered placeholder shown when there are no downtimes."""
        from PySide6.QtWidgets import QToolButton
        from PySide6.QtCore import QSize as _QS_e
        from .tabler_icons import TablerIcon as _TI_e
        wrap = QWidget()
        v = QVBoxLayout(wrap)
        v.setContentsMargins(0, 12, 0, 12)
        v.setSpacing(6)
        v.addStretch(1)
        icon = QToolButton()
        icon.setEnabled(False)
        icon.setIcon(_TI_e("tabler_inbox.svg").icon(color=QColor("#444C56")))
        icon.setIconSize(_QS_e(56, 56))
        icon.setStyleSheet("border: none; background: transparent;")
        title = QLabel("No downtime records")
        title.setStyleSheet(
            "color: #C9D1D9; font-size: 14px; font-weight: 700;"
            " background: transparent;"
        )
        sub = QLabel("Add a downtime entry to get started.")
        sub.setStyleSheet(
            "color: #6E7681; font-size: 11px; background: transparent;"
        )
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        for w in (icon, title, sub):
            v.addWidget(w, 0, Qt.AlignmentFlag.AlignHCenter)
        v.addStretch(1)
        return wrap

    def load_downtimes(self):
        try:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, hora_inicio, hora_fin, duracion, razon, status, detalle,
                       COALESCE(synced_to_excel, 0),
                       COALESCE(last_sync_error, '')
                FROM downtimes
                WHERE fecha = ?
                ORDER BY hora_inicio DESC
            """, (self.current_date,))
            rows = cursor.fetchall()
            conn.close()
        except Exception as exc:
            print(f"[downtime_manager] load_downtimes failed: {exc}")
            return

        # Toggle between empty-state placeholder and the table itself.
        if hasattr(self, "_table_stack"):
            self._table_stack.setCurrentIndex(1 if not rows else 0)

        # Reset cell widgets from previous load to avoid stale labels stacking.
        # Cols 4 (Status), 5 (Sync), 6 (Actions) all use cell widgets.
        for _r in range(self.table.rowCount()):
            for _c in (4, 5, 6):
                self.table.removeCellWidget(_r, _c)
        self.table.setRowCount(len(rows))
        self.row_ids = []

        _STATUS_COLORS = {
            "pending":  QColor(0, 0, 0, 0),     # transparent background
            "rejected": QColor(0, 0, 0, 0),     # transparent background
            "approved": QColor(0, 0, 0, 0),     # transparent background
        }
        _STATUS_TEXT_COLORS = {
            "pending":  QColor("#F1C40F"),       # yellow text
            "rejected": QColor("#E74C3C"),       # red text
            "approved": QColor("#2ECC71"),       # green text
        }
        _STATUS_LABELS = {
            "pending":  "Pending",
            "approved": "Approved",
            "rejected": "Rejected",
        }

        compact = True  # always show the compact dropdown (Actions ▾)

        for idx, row in enumerate(rows):
            (row_id, start, end, duration, reason, status, detalle,
             sync_excel, last_err) = row
            status = self._normalize_status(status)

            values = [start, end, str(duration), reason,
                      _STATUS_LABELS.get(status, status),
                      "", ""]  # cols 5 (sync) and 6 (actions) rendered as widgets

            tooltip = detalle if detalle else reason

            is_light = self._is_light_mode()
            fg_default = QColor("#1F2328") if is_light else CLR_FG_LIGHT
            for col, val in enumerate(values):
                # Cols 4 (status), 5 (sync), 6 (actions) are widgets below;
                # keep underlying items empty so text doesn't bleed through.
                item_text = "" if col in (4, 5, 6) else str(val)
                item = QTableWidgetItem(item_text)
                item.setToolTip(tooltip)
                if col in (4, 5, 6):
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                else:
                    item.setForeground(fg_default)
                self.table.setItem(idx, col, item)

                # Status cell (col 4) rendered as label widget for solid bg
                if col == 4:
                    bg = _STATUS_COLORS.get(status, QColor(128, 128, 128))
                    fg = _STATUS_TEXT_COLORS.get(status, CLR_FG_LIGHT)
                    bg_css = "transparent" if bg.alpha() == 0 else bg.name()
                    fg_css = fg.name()
                    lbl = QLabel(_STATUS_LABELS.get(status, status))
                    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    lbl.setStyleSheet(
                        f"background-color: {bg_css}; color: {fg_css}; padding: 0px;"
                    )
                    self.table.setCellWidget(idx, 4, lbl)

            # ── Sync icon (col 5) ───────────────────────────────────────────
            # Reflects ONLY synced_to_excel now. Status decides ✓/⏳/✗ color.
            #   ✓ green  = excel synced + status approved
            #   ⏳ amber = excel synced + status pending (waiting on user mark)
            #   ✗ red    = excel synced + status rejected
            #   ⚠ red    = NOT synced to excel — retry worker will handle it
            if sync_excel == 0:
                sync_icon, sync_color = "⚠", "#E74C3C"
                sync_tip = "Pending sync to shared Excel"
                if last_err:
                    sync_tip += f"\nLast error: {last_err}"
            elif status == STATUS_PENDING_LOCAL:
                sync_icon, sync_color = "⏳", "#F1C40F"
                sync_tip = "Synced — paste to Teams, then mark Approved/Rejected"
            elif status == STATUS_REJECTED_LOCAL:
                sync_icon, sync_color = "✗", "#E74C3C"
                sync_tip = "Synced — rejected"
            else:
                sync_icon, sync_color = "✓", "#2ECC71"
                sync_tip = "Synced — approved"

            # Map status icons to Tabler SVGs where available so the column
            # blends with the rest of the app's iconography.
            from PySide6.QtWidgets import QPushButton as _QPB_chk
            from PySide6.QtCore import QSize as _QS_chk
            from .tabler_icons import TablerIcon as _TI_chk
            _svg_map = {
                "✓": ("tabler_check.svg",          "#2ECC71"),
                "⏳": ("tabler_alert_triangle.svg", "#F1C40F"),
                "✗": ("tabler_x.svg",              "#E74C3C"),
                "⚠": ("tabler_alert_triangle.svg", "#E74C3C"),
            }
            if sync_icon in _svg_map:
                svg_name, svg_color = _svg_map[sync_icon]
                sync_lbl = _QPB_chk()
                sync_lbl.setEnabled(False)
                sync_lbl.setIcon(_TI_chk(svg_name).icon(color=QColor(svg_color)))
                sync_lbl.setIconSize(_QS_chk(18, 18))
                sync_lbl.setStyleSheet(
                    "QPushButton { background: transparent; border: none; padding: 0; }"
                )
            else:
                sync_lbl = QLabel(sync_icon)
                sync_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                sync_lbl.setStyleSheet(
                    f"color: {sync_color}; font-size: 14px; font-weight: bold;"
                )
            sync_lbl.setToolTip(sync_tip)
            self.table.setCellWidget(idx, 5, sync_lbl)

            # ── Actions (col 6) ─────────────────────────────────────────────
            # In wide mode: 3 inline buttons [Copy] [✓] [✗].
            # In compact mode: a single dropdown menu with the same actions.
            actions_widget = self._build_actions_widget(row_id, status, compact)
            self.table.setCellWidget(idx, 6, actions_widget)

            self.row_ids.append(row_id)

        # Fixed columns are set once in init_ui; Reason stretches automatically
        self._apply_table_layout_mode()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_table_layout_mode()

    def _apply_table_layout_mode(self):
        """Adjust table columns for narrow layouts so rows remain visible."""
        compact = True
        self.table.setColumnWidth(0, 56 if compact else 62)   # Start
        self.table.setColumnWidth(1, 56 if compact else 62)   # End
        self.table.setColumnWidth(2, 64 if compact else 72)   # Mins
        self.table.setColumnWidth(4, 100 if compact else 140) # Status
        self.table.setColumnWidth(5, 50 if compact else 70)   # Sync
        self.table.setColumnWidth(6, 100 if compact else 220) # Actions

    # ── Actions cell (Copy + Approve + Reject) ─────────────────────────────
    def _build_actions_widget(self, dt_id: int, status: str, compact: bool):
        """Build the Actions cell. Inline buttons in wide layout, dropdown in
        compact layout (so labels never get clipped on narrow windows)."""
        from PySide6.QtWidgets import QWidget, QHBoxLayout, QMenu

        if compact:
            # Single dropdown button with all 3 actions
            btn = QPushButton("Actions ▾")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            try:
                from .theme_palette import palette
                from qfluentwidgets.common.style_sheet import isDarkTheme
                _p = palette(not isDarkTheme())
            except Exception:
                _p = {"raised": "#2D2F36", "surface": "#161B22",
                      "border": "#21262D", "border_strong": "#444",
                      "text": "#E6EDF3"}
            btn.setStyleSheet(
                f"QPushButton {{ background-color: {_p['raised']};"
                f"  color: {_p['text']};"
                f"  border: 1px solid {_p['border_strong']};"
                f"  border-radius: 3px; padding: 2px 6px; font-size: 11px; }}"
                f"QPushButton:hover {{ background-color: {_p['surface']}; }}"
                f"QPushButton::menu-indicator {{ image: none; width: 0; }}"
            )
            menu = QMenu(btn)
            menu.setCursor(Qt.CursorShape.PointingHandCursor)
            menu.setStyleSheet(
                f"QMenu {{ background-color: {_p['surface']};"
                f"  border: 1px solid {_p['border_strong']};"
                f"  border-radius: 6px; color: {_p['text']}; padding: 4px; }}"
                f"QMenu::item {{ padding: 6px 18px; border-radius: 4px;"
                f"  font-size: 11px; }}"
                f"QMenu::item:selected {{ background-color: rgba(56,139,253,0.18);"
                f"  color: {_p['text']}; }}"
                f"QMenu::separator {{ height: 1px; background: {_p['border']};"
                f"  margin: 4px 6px; }}"
            )
            menu.addAction("Copy for Teams",
                           lambda _id=dt_id: self._copy_dt_for_teams(_id))
            if status == STATUS_PENDING_LOCAL:
                menu.addSeparator()
                menu.addAction("Mark Approved",
                               lambda _id=dt_id: self._set_dt_status(_id, STATUS_APPROVED_LOCAL))
                menu.addAction("Mark Rejected",
                               lambda _id=dt_id: self._set_dt_status(_id, STATUS_REJECTED_LOCAL))
            else:
                menu.addSeparator()
                if status == STATUS_APPROVED_LOCAL:
                    menu.addAction("Power App →",
                                   lambda _id=dt_id: self._open_powerapp_export(_id))
                menu.addAction("Reset to Pending",
                               lambda _id=dt_id: self._set_dt_status(_id, STATUS_PENDING_LOCAL))
            btn.setMenu(menu)
            return btn

        # Wide layout — 3 inline buttons in a horizontal container
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(2, 1, 2, 1)
        layout.setSpacing(4)

        copy_btn = QPushButton("Copy")
        copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_btn.setToolTip("Copy DT details to clipboard, ready to paste in Teams")
        copy_btn.setStyleSheet(
            "QPushButton { background-color: #1757D4; color: white; border: none; "
            "border-radius: 3px; padding: 3px 8px; font-size: 11px; font-weight: 600; } "
            "QPushButton:hover { background-color: #388BFD; } "
            "QPushButton:pressed { background-color: #1A5FCF; }"
        )
        copy_btn.clicked.connect(lambda _checked=False, _id=dt_id: self._copy_dt_for_teams(_id))
        layout.addWidget(copy_btn)

        if status == STATUS_PENDING_LOCAL:
            ok_btn = QPushButton("✓")
            ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            ok_btn.setToolTip("Mark this DT as Approved")
            ok_btn.setMaximumWidth(34)
            ok_btn.setStyleSheet(
                "QPushButton { background-color: #2EA043; color: white; border: none; "
                "border-radius: 3px; padding: 3px 0; font-size: 12px; font-weight: 700; } "
                "QPushButton:hover { background-color: #3FB950; }"
            )
            ok_btn.clicked.connect(
                lambda _checked=False, _id=dt_id: self._set_dt_status(_id, STATUS_APPROVED_LOCAL))
            layout.addWidget(ok_btn)

            no_btn = QPushButton("✗")
            no_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            no_btn.setToolTip("Mark this DT as Rejected")
            no_btn.setMaximumWidth(34)
            no_btn.setStyleSheet(
                "QPushButton { background-color: #DA3633; color: white; border: none; "
                "border-radius: 3px; padding: 3px 0; font-size: 12px; font-weight: 700; } "
                "QPushButton:hover { background-color: #F85149; }"
            )
            no_btn.clicked.connect(
                lambda _checked=False, _id=dt_id: self._set_dt_status(_id, STATUS_REJECTED_LOCAL))
            layout.addWidget(no_btn)
        else:
            # Approved DTs get a quick "→ Power App" re-trigger; rejected
            # DTs only need the undo affordance.
            if status == STATUS_APPROVED_LOCAL:
                pa_btn = QPushButton("⚡")
                pa_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                pa_btn.setToolTip("Push this DT to the Power App")
                pa_btn.setMaximumWidth(34)
                pa_btn.setStyleSheet(
                    "QPushButton { background-color: #742774; color: #FFD700; border: none; "
                    "border-radius: 3px; padding: 3px 0; font-size: 14px; font-weight: 700; } "
                    "QPushButton:hover { background-color: #8E3A8E; }"
                )
                pa_btn.clicked.connect(
                    lambda _checked=False, _id=dt_id: self._open_powerapp_export(_id))
                layout.addWidget(pa_btn)

            # Reset-to-pending undo
            undo_btn = QPushButton("↺")
            undo_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            undo_btn.setToolTip("Reset to Pending")
            undo_btn.setMaximumWidth(34)
            undo_btn.setStyleSheet(
                "QPushButton { background-color: #444C56; color: #C9D1D9; border: none; "
                "border-radius: 3px; padding: 3px 0; font-size: 12px; } "
                "QPushButton:hover { background-color: #545D69; }"
            )
            undo_btn.clicked.connect(
                lambda _checked=False, _id=dt_id: self._set_dt_status(_id, STATUS_PENDING_LOCAL))
            layout.addWidget(undo_btn)

        layout.addStretch()
        return container

    def _copy_dt_for_teams(self, dt_id: int):
        """Build a Teams-ready text block for this DT and put it on the clipboard."""
        if not dt_id:
            return
        try:
            conn = get_connection()
            cur = conn.cursor()
            cur.execute(
                "SELECT fecha, hora_inicio, hora_fin, duracion, razon, detalle, "
                "       COALESCE(downtime_case_id, '') "
                "FROM downtimes WHERE id = ?",
                (int(dt_id),),
            )
            r = cur.fetchone()
            conn.close()
        except Exception as exc:
            QMessageBox.warning(self, "Copy failed", f"Could not load DT #{dt_id}: {exc}")
            return
        if not r:
            QMessageBox.warning(self, "Copy failed", f"DT #{dt_id} not found.")
            return

        fecha, h_ini, h_fin, dur, razon, detalle, pid = r

        lines = [
            f"Razon: {razon or ''}",
            f"Hora inicio - Hora final: {h_ini or ''} - {h_fin or ''}",
            f"Total: {int(dur or 0)} min",
            f"Descripcion: {detalle or ''}",
        ]
        if pid:
            lines.append(f"PID: {pid}")
        text = "\n".join(lines)

        try:
            QApplication.clipboard().setText(text)
        except Exception as exc:
            QMessageBox.warning(self, "Copy failed", f"Clipboard error: {exc}")
            return

        self._flash_status("Copied! Paste it in the Teams chat.")

    def _set_dt_status(self, dt_id: int, new_status: str):
        """User marks a DT as approved/rejected/pending after Teams reaction."""
        if not dt_id or new_status not in (
                STATUS_PENDING_LOCAL, STATUS_APPROVED_LOCAL, STATUS_REJECTED_LOCAL):
            return

        try:
            conn = get_connection()
            cur = conn.cursor()
            # When the user changes the decision, mark the row as needing
            # re-export so the shared Excel reflects the new status.
            cur.execute(
                "UPDATE downtimes "
                "SET status = ?, synced_to_excel = 0, "
                "    responded_by = COALESCE(NULLIF(?, ''), responded_by), "
                "    responded_at = ? "
                "WHERE id = ?",
                (
                    new_status,
                    self._current_designer_name(),
                    datetime.now().isoformat(timespec="seconds")
                        if new_status != STATUS_PENDING_LOCAL else "",
                    int(dt_id),
                ),
            )
            conn.commit()
            conn.close()
        except Exception as exc:
            QMessageBox.warning(self, "Status update failed",
                                f"Could not update DT #{dt_id}: {exc}")
            return

        self.load_downtimes()
        if self.on_update_callback:
            self.on_update_callback()

    def _current_designer_name(self) -> str:
        try:
            cfg = load_config()
            return cfg.get("designer_name", "") or ""
        except Exception:
            return ""

    def _open_powerapp_export(self, dt_id: int):
        """Show the per-field clipboard helper for an approved DT (Plan B)."""
        try:
            conn = get_connection()
            cur = conn.cursor()
            cur.execute(
                "SELECT fecha, hora_inicio, hora_fin, razon, detalle "
                "FROM downtimes WHERE id = ?",
                (dt_id,),
            )
            row = cur.fetchone()
            conn.close()
        except Exception as exc:
            log_event("downtime_manager",
                      f"powerapp export query failed for DT #{dt_id}: {exc}",
                      level="WARN")
            return
        if not row:
            return
        fecha, start, end, reason, detalle = row

        try:
            cfg = load_config() or {}
        except Exception:
            cfg = {}
        designer = cfg.get("designer_name", "") or ""
        supervisor = cfg.get("default_supervisor", "") or ""
        url = cfg.get("powerapps_downtime_url", "") or None

        try:
            from .downtime_export_dialog import (
                DowntimeExportDialog, build_fields_from_dt,
            )
        except ImportError as exc:
            log_event("downtime_manager",
                      f"powerapp export dialog unavailable: {exc}", level="WARN")
            return
        fields = build_fields_from_dt(
            designer=designer,
            fecha=str(fecha or ""),
            reason=str(reason or ""),
            start=str(start or ""),
            end=str(end or ""),
            detalle=str(detalle or ""),
            supervisor=supervisor,
        )
        dlg = DowntimeExportDialog(fields, powerapp_url=url, parent=self)
        dlg.exec()

    def _confirm_delete_downtime_modal(self) -> bool:
        """Fluent confirmation modal for deleting a downtime row."""
        try:
            from qfluentwidgets import MessageBoxBase
            from .tabler_icons import TablerIcon
            from PySide6.QtWidgets import QToolButton as _QTBd
            from PySide6.QtGui import QColor as _QCd
            from PySide6.QtCore import QSize as _QSd
        except Exception:
            r = QMessageBox.question(
                self, "Delete downtime",
                "Are you sure you want to delete this downtime?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            return r == QMessageBox.StandardButton.Yes

        host = self
        ok = {"v": False}

        class _Sheet(MessageBoxBase):
            def __init__(_s, h):
                super().__init__(h.window() if h is not None else None)
                try:
                    _s.setMaskColor(_QCd(0, 0, 0, 170))
                except Exception:
                    pass
                _s.widget.setObjectName("dtDelCard")
                apply_fluent_modal_palette(_s, "dtDelCard")
                _s.viewLayout.setContentsMargins(22, 18, 22, 12)
                _s.viewLayout.setSpacing(12)

                hdr = QHBoxLayout(); hdr.setSpacing(12)
                ic = _QTBd(); ic.setEnabled(False)
                ic.setIcon(TablerIcon("tabler_alert_triangle.svg").icon(color=_QCd("#F85149")))
                ic.setIconSize(_QSd(22, 22))
                ic.setStyleSheet(
                    "background: rgba(248,81,73,0.14); border: none;"
                    " border-radius: 10px; padding: 6px;"
                )
                tc = QVBoxLayout(); tc.setSpacing(2)
                t = QLabel("Delete downtime")
                t.setStyleSheet(
                    "color: #E6EDF3; font-size: 15px; font-weight: 700;"
                    " background: transparent;"
                )
                s = QLabel(
                    "This removes the downtime entry from the database. "
                    "Cannot be undone."
                )
                s.setWordWrap(True)
                s.setStyleSheet(
                    "color: #8B949E; font-size: 11px; background: transparent;"
                )
                tc.addWidget(t); tc.addWidget(s)
                hdr.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
                hdr.addLayout(tc, 1)
                _s.viewLayout.addLayout(hdr)
                _s.widget.setMinimumWidth(440)

                _s.cancelButton.setText("Cancel")
                _s.cancelButton.setFixedWidth(120)
                _s.cancelButton.setStyleSheet(
                    "QPushButton { background: transparent;"
                    "  border: 1px solid #30363D; color: #E6EDF3;"
                    "  border-radius: 6px; padding: 8px 22px;"
                    "  font-weight: 700; font-size: 12px; }"
                    "QPushButton:hover { background: rgba(255,255,255,0.05); }"
                )
                _s.yesButton.setText("   Delete")
                _s.yesButton.setFixedWidth(140)
                _s.yesButton.setStyleSheet(
                    "QPushButton { background: #F85149; border: 1px solid #F85149;"
                    "  color: white; border-radius: 6px; padding: 8px 22px;"
                    "  font-weight: 700; font-size: 12px; }"
                    "QPushButton:hover { background: #FF6B61; }"
                )
                try:
                    _s.yesButton.setIcon(
                        TablerIcon("tabler_trash.svg").icon(color=_QCd("#FFFFFF"))
                    )
                    _s.yesButton.setIconSize(_QSd(14, 14))
                except Exception:
                    pass

                def _on_yes():
                    ok["v"] = True
                    _s.accept()
                try:
                    _s.yesButton.clicked.disconnect()
                except Exception:
                    pass
                _s.yesButton.clicked.connect(_on_yes)

        _Sheet(host).exec()
        return ok["v"]

    def _flash_status(self, msg: str, ms: int = 2000):
        """Show a Fluent InfoBar toast at the top of the tab. Falls back
        to status bar / window-title flicker if Fluent is unavailable."""
        try:
            from qfluentwidgets import InfoBar, InfoBarPosition, InfoBarIcon
            InfoBar.new(
                icon=InfoBarIcon.SUCCESS,
                title="",
                content=msg,
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=ms,
                parent=self,
            )
            return
        except Exception:
            pass
        try:
            wnd = self.window()
            sb = getattr(wnd, "statusBar", None)
            if callable(sb):
                wnd.statusBar().showMessage(msg, ms)
                return
        except Exception:
            pass
        try:
            wnd = self.window()
            old_title = wnd.windowTitle()
            wnd.setWindowTitle(f"{msg}   —   {old_title}")
            QTimer.singleShot(ms, lambda: wnd.setWindowTitle(old_title))
        except Exception:
            pass

    def on_cell_clicked(self, row, column):
        """Route the click through the active pick mode (delete or edit).
        Actions cell (col 6) and Sync (col 5) host buttons of their own —
        don't hijack those clicks for pick-mode actions."""
        if column in (5, 6):
            return
        if self.delete_mode:
            self.delete_downtime_at_row(row)
            self.delete_mode = False
            self._refresh_pick_mode_ui()
        elif self.edit_mode:
            self.edit_mode = False
            self._refresh_pick_mode_ui()
            self._open_edit_modal_for_row(row)


    def edit_downtime(self):
        """Always enter pick-edit mode — the user must see the hint and click
        the row they want to edit. Second click on Edit cancels the mode."""
        if self.edit_mode:
            self.edit_mode = False
            self._refresh_pick_mode_ui()
            return

        self.edit_mode = True
        self.delete_mode = False
        # Clear any pre-existing table selection so the prior highlight does
        # not look like a hint that a row is already "armed" for editing.
        self.table.clearSelection()
        self.table.setCurrentCell(-1, -1)
        self._refresh_pick_mode_ui()

    def _open_edit_modal_for_row(self, row: int):
        """Open the edit dialog for a specific table row index."""
        if row < 0 or row >= len(self.row_ids):
            return
        row_id = self.row_ids[row]

        # Load full row from DB (table only shows a subset of columns)
        try:
            conn = get_connection()
            cur = conn.cursor()
            cur.execute(
                "SELECT hora_inicio, hora_fin, razon, detalle, "
                "       COALESCE(downtime_case_id, '') "
                "FROM downtimes WHERE id = ?",
                (row_id,),
            )
            db_row = cur.fetchone()
            conn.close()
        except Exception as exc:
            QMessageBox.warning(self, "Edit failed", f"Could not load DT #{row_id}: {exc}")
            return
        if not db_row:
            return
        start, end, reason, detalle, case_id = db_row

        from .downtime_edit_dialog import DowntimeEditDialog
        dlg = DowntimeEditDialog(
            start=str(start or "00:00"),
            end=str(end or "00:00"),
            reason=str(reason or ""),
            detalle=str(detalle or ""),
            case_id=str(case_id or ""),
            reasons=DOWNTIME_REASONS,
            parent=self,
        )
        if not dlg.exec():
            return

        vals = dlg.result_values()
        duration = vals["end_mins"] - vals["start_mins"]
        if duration < 0:
            duration += 24 * 60

        err = self._validate_downtime(
            vals["start_mins"], vals["end_mins"], vals["reason"],
            exclude_id=row_id,
        )
        if err:
            self._toast(err, level="warning")
            return

        # Extra rule: Multitreatment requires a Case ID
        if vals["reason"].strip().lower() == "multitreatment" and not vals["case_id"]:
            self._toast(
                "A Case ID is required for Multitreatment downtimes.",
                level="warning",
            )
            return

        # Persist — editing resets approval state so the supervisor re-reviews.
        try:
            conn = get_connection()
            cur = conn.cursor()
            cur.execute("""
                UPDATE downtimes
                SET hora_inicio = ?, hora_fin = ?, razon = ?, duracion = ?,
                    detalle = ?, downtime_case_id = ?,
                    status = 'pending', synced_to_excel = 0,
                    responded_by = '', responded_at = ''
                WHERE id = ?
            """, (
                vals["start"], vals["end"], vals["reason"], duration,
                vals["detail"], vals["case_id"], row_id,
            ))
            conn.commit()
            conn.close()
        except Exception as exc:
            QMessageBox.warning(self, "Edit failed", f"Could not update DT #{row_id}: {exc}")
            return

        self.load_downtimes()
        if self.on_update_callback:
            self.on_update_callback()

    def update_button_colors(self):
        """Update button styles based on the active pick mode."""
        if self.delete_mode:
            # Delete pick active — red glow on Delete, neutral Edit
            self.delete_btn.setText("Cancel")
            self.delete_btn.setStyleSheet(
                "QPushButton { background-color: #B71C1C; border-radius: 6px; "
                "color: white; font-weight: 700; } "
                "QPushButton:hover { background-color: #C62828; }"
            )
            self.edit_btn.setText("Edit")
            self.edit_btn.setStyleSheet("")
        elif self.edit_mode:
            # Edit pick active — blue glow on Edit, neutral Delete
            self.edit_btn.setText("Cancel")
            self.edit_btn.setStyleSheet(
                "QPushButton { background-color: #1757D4; border: none; "
                "border-radius: 6px; color: white; font-weight: 700; } "
                "QPushButton:hover { background-color: #388BFD; }"
            )
            self.delete_btn.setText("Delete")
            self.delete_btn.setStyleSheet("")
        else:
            self.edit_btn.setText("Edit")
            self.delete_btn.setText("Delete")
            self.delete_btn.setStyleSheet("")
            self.edit_btn.setStyleSheet("")

    def delete_downtime_at_row(self, row):
        """Delete downtime at specific row with confirmation"""
        if row >= len(self.row_ids):
            return

        # Exit delete mode first (clears hint label + table outline too)
        self.delete_mode = False
        self._refresh_pick_mode_ui()
        
        if not self._confirm_delete_downtime_modal():
            return
        
        row_id = self.row_ids[row]
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM downtimes WHERE id = ?", (row_id,))
        conn.commit()
        conn.close()

        self.load_downtimes()

        if self.on_update_callback:
            self.on_update_callback()

    def delete_downtime(self):
        """Toggle delete-pick mode. While active, the table is highlighted
        and clicking a row triggers the delete-with-confirmation flow."""
        self.delete_mode = not self.delete_mode
        if self.delete_mode:
            self.edit_mode = False
        self._refresh_pick_mode_ui()

    _PICK_HINT_HEIGHT = 36  # collapsed → expanded target (room for padding + border)

    def _refresh_pick_mode_ui(self):
        """Sync visual state (table border, cursor, hint label, button
        styling) with the current pick-mode flags. Slide+fade animated."""
        self.update_button_colors()

        if self.delete_mode:
            self._set_pick_hint(
                "🗑  Click the downtime you want to DELETE  —  click Cancel to abort",
                bg="#3F1F1F", fg="#FFB4B4", border="#DA3633",
            )
            self._animate_pick_hint(open_=True)
            self.table.setCursor(Qt.CursorShape.PointingHandCursor)
            self._apply_pick_table_style("#DA3633")
        elif self.edit_mode:
            self._set_pick_hint(
                "✎  Click the downtime you want to EDIT  —  click Cancel to abort",
                bg="#1F2A40", fg="#B4D4FF", border="#388BFD",
            )
            self._animate_pick_hint(open_=True)
            self.table.setCursor(Qt.CursorShape.PointingHandCursor)
            self._apply_pick_table_style("#388BFD")
        else:
            self._animate_pick_hint(open_=False)
            self.table.unsetCursor()
            self._apply_pick_table_style(None)

    def _set_pick_hint(self, text: str, *, bg: str, fg: str, border: str):
        self._pick_hint.setText(text)
        self._pick_hint.setStyleSheet(
            "QLabel { padding: 4px 8px; border-radius: 4px; font-weight: 600; "
            f"font-size: 11px; background: {bg}; color: {fg}; "
            f"border: 1px solid {border}; }}"
        )

    def _animate_pick_hint(self, *, open_: bool):
        """Slide + fade the hint label in/out smoothly."""
        # Height animation — drives the layout reflow
        self._pick_hint_height_anim.stop()
        self._pick_hint_height_anim.setStartValue(self._pick_hint.maximumHeight())
        self._pick_hint_height_anim.setEndValue(
            self._PICK_HINT_HEIGHT if open_ else 0
        )
        self._pick_hint_height_anim.start()

        # Opacity — softens the appearance/disappearance
        self._pick_hint_fade_anim.stop()
        self._pick_hint_fade_anim.setStartValue(self._pick_hint_opacity.opacity())
        self._pick_hint_fade_anim.setEndValue(1.0 if open_ else 0.0)
        self._pick_hint_fade_anim.start()

    def _apply_pick_table_style(self, accent: str | None):
        """When a pick mode is active, highlight rows on hover with the
        accent colour. No widget border — the outline used to overlap the
        action buttons because the table's expanding geometry pushed past
        them. The animated hint label above plus the row-hover tint is a
        cleaner visual cue."""
        if accent:
            # Build a translucent fill from the accent's hex
            r = int(accent[1:3], 16)
            g = int(accent[3:5], 16)
            b = int(accent[5:7], 16)
            self.table.setStyleSheet(
                "QTableWidget::item:hover { "
                f"background: rgba({r}, {g}, {b}, 60); }}"
            )
        else:
            self.table.setStyleSheet("")
