# -*- coding: utf-8 -*-
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QTimeEdit, QTableWidget, QTableWidgetItem, QHeaderView,
    QMessageBox, QLineEdit, QComboBox, QFrame, QCheckBox, QWidget,
)
from PySide6.QtCore import Qt, QTime
from PySide6.QtGui import QFont
from . import font_scale
from .theme_palette import apply_fluent_modal_palette
from db.database import get_connection

_DEFAULT = "default"


# ── DB helpers ────────────────────────────────────────────────────────────────

def init_breaks_table():
    """Create/migrate breaks tables."""
    conn = get_connection()
    cur = conn.cursor()

    # Core breaks table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS breaks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            hora_inicio TEXT,
            hora_fin TEXT,
            schedule_group TEXT DEFAULT 'default'
        )
    """)
    # Add schedule_group column if upgrading from older schema
    cur.execute("PRAGMA table_info(breaks)")
    cols = {r[1] for r in cur.fetchall()}
    if "schedule_group" not in cols:
        cur.execute("ALTER TABLE breaks ADD COLUMN schedule_group TEXT DEFAULT 'default'")

    # Named schedule groups (non-default). `weekdays` is a comma-separated
    # list of weekday indices (0=Mon … 4=Fri) the schedule applies to.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS break_schedule_groups (
            name TEXT PRIMARY KEY,
            is_active INTEGER DEFAULT 0,
            weekdays TEXT DEFAULT ''
        )
    """)
    cur.execute("PRAGMA table_info(break_schedule_groups)")
    _bg_cols = {r[1] for r in cur.fetchall()}
    if "weekdays" not in _bg_cols:
        cur.execute(
            "ALTER TABLE break_schedule_groups ADD COLUMN weekdays TEXT DEFAULT ''"
        )

    # Break attendance (unchanged)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS break_attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT,
            break_id INTEGER,
            took_break INTEGER DEFAULT 1,
            UNIQUE(fecha, break_id)
        )
    """)
    conn.commit()
    conn.close()


def get_breaks(schedule_group: str = _DEFAULT):
    """Return breaks for a given schedule as (id, name, start, end)."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, name, hora_inicio, hora_fin FROM breaks "
        "WHERE schedule_group = ? ORDER BY hora_inicio",
        (schedule_group,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_schedule_groups():
    """Return list of (name, is_active) for all non-default schedules."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT name, is_active FROM break_schedule_groups ORDER BY name")
    rows = cur.fetchall()
    conn.close()
    return rows


def add_schedule_group(name: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO break_schedule_groups (name, is_active) VALUES (?, 0)",
        (name,),
    )
    conn.commit()
    conn.close()


def get_schedule_weekdays(name: str) -> list[int]:
    """Return the list of weekday indices (0=Mon..4=Fri) this schedule
    applies to. Empty list = none assigned."""
    if name == _DEFAULT:
        # Default covers every weekday NOT claimed by another schedule.
        claimed = set()
        for other, _act in get_schedule_groups():
            claimed.update(get_schedule_weekdays(other))
        return [d for d in range(5) if d not in claimed]
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT weekdays FROM break_schedule_groups WHERE name = ?", (name,)
    )
    row = cur.fetchone()
    conn.close()
    if not row or not row[0]:
        return []
    out = []
    for chunk in str(row[0]).split(","):
        chunk = chunk.strip()
        if chunk.isdigit():
            d = int(chunk)
            if 0 <= d <= 4:
                out.append(d)
    return out


def set_schedule_weekdays(name: str, weekdays: list[int]):
    """Persist the weekday assignment for a conditional schedule."""
    if name == _DEFAULT:
        return  # Default is implicit — claims whatever isn't taken
    serialised = ",".join(str(d) for d in sorted(set(weekdays)) if 0 <= d <= 4)
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE break_schedule_groups SET weekdays = ? WHERE name = ?",
        (serialised, name),
    )
    # If at least one weekday is claimed, mark this schedule active.
    cur.execute(
        "UPDATE break_schedule_groups SET is_active = ? WHERE name = ?",
        (1 if serialised else 0, name),
    )
    conn.commit()
    conn.close()


def get_active_schedule_for_date(fecha) -> str:
    """Return which schedule applies on the given date.

    `fecha` may be a date / datetime / 'YYYY-MM-DD' string.
    """
    from datetime import date as _date, datetime as _dt
    if isinstance(fecha, str):
        try:
            d = _date.fromisoformat(fecha)
        except Exception:
            return _DEFAULT
    elif isinstance(fecha, _dt):
        d = fecha.date()
    elif isinstance(fecha, _date):
        d = fecha
    else:
        return _DEFAULT
    wd = d.weekday()  # 0=Mon..6=Sun
    if wd > 4:
        return _DEFAULT
    for name, _is_active in get_schedule_groups():
        if wd in get_schedule_weekdays(name):
            return name
    return _DEFAULT


def set_schedule_active(name: str, active: bool):
    conn = get_connection()
    cur = conn.cursor()
    if active:
        # Keep only one conditional schedule active at a time.
        cur.execute("UPDATE break_schedule_groups SET is_active = 0")
        cur.execute(
            "UPDATE break_schedule_groups SET is_active = 1 WHERE name = ?",
            (name,),
        )
    else:
        cur.execute(
            "UPDATE break_schedule_groups SET is_active = 0 WHERE name = ?",
            (name,),
        )
    conn.commit()
    conn.close()


def delete_schedule_group(name: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM breaks WHERE schedule_group = ?", (name,))
    cur.execute("DELETE FROM break_schedule_groups WHERE name = ?", (name,))
    conn.commit()
    conn.close()


def get_active_schedule() -> str:
    """Return the name of the first active non-default schedule, or 'default'."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT name FROM break_schedule_groups WHERE is_active = 1 LIMIT 1"
    )
    row = cur.fetchone()
    conn.close()
    return row[0] if row else _DEFAULT


# ── Attendance helpers (unchanged) ────────────────────────────────────────────

def get_break_attendance(fecha: str, break_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT took_break FROM break_attendance WHERE fecha = ? AND break_id = ?",
        (fecha, break_id),
    )
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None


def set_break_attendance(fecha: str, break_id: int, took_break: bool):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO break_attendance (fecha, break_id, took_break) VALUES (?, ?, ?)",
        (fecha, break_id, 1 if took_break else 0),
    )
    conn.commit()
    conn.close()


def get_breaks_taken_today(fecha: str) -> set:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT break_id FROM break_attendance WHERE fecha = ? AND took_break = 1",
        (fecha,),
    )
    ids = {r[0] for r in cur.fetchall()}
    conn.close()
    return ids


def get_breaks_answered_today(fecha: str) -> set:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT break_id FROM break_attendance WHERE fecha = ?", (fecha,))
    ids = {r[0] for r in cur.fetchall()}
    conn.close()
    return ids


def calculate_break_overlap(start_str: str, end_str: str, fecha: str = None) -> float:
    """Overlap minutes between a case and the active break schedule for
    ``fecha``. Weekend (Sat/Sun) is treated as no-breaks: returns 0.0
    immediately.

    The schedule is picked per-weekday via
    :func:`get_active_schedule_for_date` so conditional schedules (e.g.
    Site Day on Tuesdays) replace Default only on the days they own.
    """
    from datetime import date as _date

    if fecha:
        try:
            d = _date.fromisoformat(fecha)
            if d.weekday() >= 5:  # Sat/Sun
                return 0.0
            active = get_active_schedule_for_date(d)
        except Exception:
            active = get_active_schedule()
    else:
        active = get_active_schedule()
    breaks = get_breaks(active)
    if not breaks:
        return 0.0

    case_start = _to_minutes(start_str)
    case_end = _to_minutes(end_str)
    if case_end <= case_start:
        return 0.0

    if fecha:
        taken_ids = get_breaks_taken_today(fecha)
        answered_ids = get_breaks_answered_today(fecha)
    else:
        taken_ids = set()
        answered_ids = set()

    total = 0.0
    for bid, _, b_start_str, b_end_str in breaks:
        if fecha and bid in answered_ids and bid not in taken_ids:
            continue
        b_start = _to_minutes(b_start_str)
        b_end = _to_minutes(b_end_str)
        overlap_start = max(case_start, b_start)
        overlap_end = min(case_end, b_end)
        if overlap_end > overlap_start:
            total += overlap_end - overlap_start

    return total


def _to_minutes(time_str: str) -> float:
    parts = time_str.split(":")
    return int(parts[0]) * 60 + int(parts[1])


# ── Dialog ────────────────────────────────────────────────────────────────────

try:
    from qfluentwidgets import MessageBoxBase as _BreaksBase
    _BREAKS_USE_FLUENT = True
except Exception:
    _BreaksBase = QDialog
    _BREAKS_USE_FLUENT = False


class BreaksDialog(_BreaksBase):
    def __init__(self, parent=None):
        if _BREAKS_USE_FLUENT and parent is not None:
            super().__init__(parent.window() if hasattr(parent, "window") else parent)
        else:
            super().__init__(parent)
        if not _BREAKS_USE_FLUENT:
            self.setWindowTitle("Break Times Configuration")
            self.setMinimumWidth(620)
            self.setMinimumHeight(540)
        self._current_group = _DEFAULT

        if _BREAKS_USE_FLUENT:
            self._build_ui_fluent()
        else:
            self.setStyleSheet(
                "QDialog { background: #0D1117; }"
                "QLabel { color: #E6EDF3; background: transparent; }"
            )
            self._build_ui()
        self._refresh_selector()

    def _build_ui_fluent(self):
        """Build the dialog body inside the MessageBoxBase shell."""
        from PySide6.QtGui import QColor as _QC
        try:
            self.setMaskColor(_QC(0, 0, 0, 170))
        except Exception:
            pass
        self.widget.setObjectName("breaksCard")
        apply_fluent_modal_palette(self, "breaksCard")
        self.viewLayout.setContentsMargins(20, 18, 20, 14)
        self.viewLayout.setSpacing(12)
        # Run the existing builder but parented to the Fluent body.
        self._build_ui_into(self.viewLayout)
        # Footer: keep Delete Selected on the left of the button row, Close on
        # the right (reuse the MessageBoxBase yes/cancel slots).
        self._wire_fluent_footer()

    def _wire_fluent_footer(self):
        # Footer: contextual tip on the left, Close button on the right.
        # Delete Selected was removed — each row has its own Action buttons.
        self.buttonLayout.removeWidget(self.yesButton)
        self.buttonLayout.removeWidget(self.cancelButton)
        self.yesButton.hide()

        tip = QLabel(
            "Edit/delete a break with the row buttons. "
            "Times are auto-subtracted from each case duration."
        )
        tip.setWordWrap(True)
        tip.setStyleSheet(
            "color: #8B949E; font-size: 11px; background: transparent;"
            " padding-right: 10px;"
        )
        self.buttonLayout.addWidget(tip, 1, Qt.AlignmentFlag.AlignVCenter)

        self.cancelButton.setText("Close")
        self.cancelButton.setFixedWidth(120)
        self.cancelButton.setStyleSheet(
            "QPushButton { background: transparent; border: 1px solid #30363D;"
            " color: #E6EDF3; border-radius: 6px; padding: 8px 22px;"
            " font-weight: 700; font-size: 12px; }"
            "QPushButton:hover { background: rgba(255,255,255,0.05); }"
        )
        try:
            self.cancelButton.clicked.disconnect()
        except Exception:
            pass
        self.cancelButton.clicked.connect(self.accept)
        self.buttonLayout.addWidget(self.cancelButton, 0, Qt.AlignmentFlag.AlignVCenter)

        self.widget.setMinimumWidth(620)
        self.widget.setMinimumHeight(540)

    # ── UI construction ──────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 14)
        root.setSpacing(12)
        self._build_ui_into(root, include_footer=True)

    def _build_ui_into(self, root, *, include_footer: bool = False):
        """Populate the dialog body into the given layout.

        ``include_footer`` controls whether the bottom button row is added
        (legacy QDialog path needs it; Fluent path uses the buttonGroup of
        MessageBoxBase instead)."""
        try:
            from .tabler_icons import TablerIcon as _TI
            from PySide6.QtGui import QColor as _QC
            from PySide6.QtCore import QSize as _QS
        except Exception:
            _TI = None

        # ── Header row (icon + title + subtitle) ──
        hdr = QHBoxLayout(); hdr.setSpacing(12)
        if _TI is not None:
            from PySide6.QtWidgets import QToolButton as _QTB
            ic = _QTB()
            ic.setEnabled(False)
            ic.setIcon(_TI("tabler_clock.svg").icon(color=_QC("#388BFD")))
            ic.setIconSize(_QS(22, 22))
            ic.setStyleSheet(
                "background: rgba(56,139,253,0.14); border: none;"
                " border-radius: 10px; padding: 7px;"
            )
            hdr.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
        tcol = QVBoxLayout(); tcol.setSpacing(2)
        title = QLabel("Break Times Configuration")
        title.setStyleSheet(
            "color: #E6EDF3; font-size: 16px; font-weight: 800;"
        )
        sub = QLabel(
            "Break time is subtracted from case duration automatically."
        )
        sub.setStyleSheet("color: #8B949E; font-size: 11px;")
        tcol.addWidget(title); tcol.addWidget(sub)
        hdr.addLayout(tcol, 1)
        root.addLayout(hdr)

        # ── Section card helper ──
        # Palette snapshot used throughout the modal interior.
        try:
            from qfluentwidgets.common.style_sheet import isDarkTheme
            from .theme_palette import palette as _p_fn
            _mp = _p_fn(not isDarkTheme())
        except Exception:
            _mp = {"base": "#0D1117", "surface": "#161B22",
                   "border": "#21262D", "border_strong": "#30363D",
                   "text": "#E6EDF3", "accent": "#1e63e4"}

        def _section_card(badge_text: str, title_text: str):
            card = QFrame()
            card.setObjectName("brkSection")
            card.setStyleSheet(
                f"#brkSection {{ background: {_mp['base']};"
                f" border: 1px solid {_mp['border']};"
                f" border-radius: 10px; }}"
                f"QLabel {{ background: transparent; }}"
            )
            v = QVBoxLayout(card)
            v.setContentsMargins(14, 12, 14, 12)
            v.setSpacing(10)
            hdr2 = QHBoxLayout(); hdr2.setSpacing(8)
            b = QLabel(badge_text)
            b.setFixedSize(22, 22)
            b.setAlignment(Qt.AlignmentFlag.AlignCenter)
            b.setStyleSheet(
                f"background: {_mp['accent']}; color: white;"
                f" border-radius: 11px; font-weight: 700; font-size: 11px;"
            )
            t = QLabel(title_text)
            t.setStyleSheet(
                f"color: {_mp['text']}; font-size: 13px; font-weight: 700;"
            )
            hdr2.addWidget(b, 0, Qt.AlignmentFlag.AlignVCenter)
            hdr2.addWidget(t, 0, Qt.AlignmentFlag.AlignVCenter)
            hdr2.addStretch()
            v.addLayout(hdr2)
            return card, v

        try:
            from .widgets import _icon_url as _icu
            _chev_url = _icu("tabler_chevron_down.svg")
        except Exception:
            _chev_url = ""
        _input_css = (
            f"QLineEdit, QTimeEdit, QComboBox {{ background: {_mp['surface']};"
            f"  border: 1px solid {_mp['border_strong']}; border-radius: 6px;"
            f"  padding: 4px 8px; color: {_mp['text']}; font-size: 11px;"
            f"  min-height: 26px; }}"
            f"QComboBox::drop-down {{ subcontrol-origin: padding;"
            f"  subcontrol-position: right center; width: 22px;"
            f"  border: none; }}"
            f"QComboBox::down-arrow {{ image: url({_chev_url});"
            f"  width: 12px; height: 12px; }}"
        )

        # ── Section 1: Select Schedule ──
        sec1, sec1_lay = _section_card("1", "Active Schedule")
        sec1_lay.addWidget(QLabel(
            "Pick which break set applies today. Click a pill to switch."
        ))
        sec1_lay.itemAt(sec1_lay.count() - 1).widget().setStyleSheet(
            "color: #8B949E; font-size: 11px; background: transparent;"
        )

        # Hidden combo kept for back-compat with rest of the dialog logic
        # (handlers read self._sel.currentIndex / itemData). The pill bar
        # below drives this combo.
        self._sel = QComboBox()
        self._sel.hide()
        self._sel.currentIndexChanged.connect(self._on_schedule_changed)

        # ── Schedule pill bar ─────────────────────────────────────────────
        self._pill_row_widget = QWidget()
        self._pill_row = QHBoxLayout(self._pill_row_widget)
        self._pill_row.setContentsMargins(0, 4, 0, 0)
        self._pill_row.setSpacing(8)
        sec1_lay.addWidget(self._pill_row_widget)

        # "+ New schedule" / "Delete current" removed from UI. Kept hidden
        # widgets so legacy handlers that reference them don't crash.
        self._btn_new_sched = QPushButton(); self._btn_new_sched.hide()
        self._btn_del_sched = QPushButton(); self._btn_del_sched.hide()

        # Hidden compat widgets the legacy handler logic still references.
        self._active_row = QFrame(); self._active_row.hide()
        self._active_chk = QCheckBox(); self._active_chk.hide()
        self._active_chk.toggled.connect(self._on_active_toggled)

        root.addWidget(sec1)

        # ── Section 2: Add Break Time ──
        sec2, sec2_lay = _section_card("2", "Add Break Time")
        form = QHBoxLayout(); form.setSpacing(8)
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Name (e.g. Lunch)")
        self.name_input.setMinimumWidth(140)
        self.name_input.setStyleSheet(_input_css)
        if _TI is not None:
            from PySide6.QtGui import QAction as _QA_n
            try:
                _cof_act = _QA_n(
                    _TI("tabler_coffee.svg").icon(color=_QC("#8B949E")),
                    "", self.name_input,
                )
                self.name_input.addAction(
                    _cof_act, QLineEdit.ActionPosition.LeadingPosition
                )
            except Exception:
                pass
        form.addWidget(self.name_input, 1)
        def _attach_clock(time_edit):
            if _TI is None:
                return
            try:
                from PySide6.QtGui import QAction as _QA_t
                le = time_edit.lineEdit()
                if le is None:
                    return
                act = _QA_t(
                    _TI("tabler_clock.svg").icon(color=_QC("#8B949E")),
                    "", le,
                )
                le.addAction(act, QLineEdit.ActionPosition.TrailingPosition)
            except Exception:
                pass

        lbl_from = QLabel("From"); lbl_from.setStyleSheet("color:#8B949E;font-size:11px;font-weight:600;")
        form.addWidget(lbl_from)
        self.start_input = QTimeEdit()
        self.start_input.setDisplayFormat("HH:mm")
        self.start_input.setTime(QTime(12, 0))
        self.start_input.setStyleSheet(_input_css)
        self.start_input.setFixedWidth(100)
        _attach_clock(self.start_input)
        form.addWidget(self.start_input)
        lbl_to = QLabel("To"); lbl_to.setStyleSheet("color:#8B949E;font-size:11px;font-weight:600;")
        form.addWidget(lbl_to)
        self.end_input = QTimeEdit()
        self.end_input.setDisplayFormat("HH:mm")
        self.end_input.setTime(QTime(12, 30))
        self.end_input.setStyleSheet(_input_css)
        self.end_input.setFixedWidth(100)
        _attach_clock(self.end_input)
        form.addWidget(self.end_input)

        # Keep end ≥ start + 1 min: as the user moves start up past end,
        # auto-bump end so the form never lands in an "End <= Start" state.
        def _ensure_end_after_start(_):
            s = self.start_input.time()
            e = self.end_input.time()
            if e <= s:
                bumped = s.addSecs(60)
                self.end_input.blockSignals(True)
                self.end_input.setTime(bumped)
                self.end_input.blockSignals(False)
        self.start_input.timeChanged.connect(_ensure_end_after_start)
        self._btn_add = QPushButton("  Add")
        self._btn_add.setCursor(Qt.PointingHandCursor)
        self._add_icon_plus = (
            _TI("tabler_plus.svg").icon(color=_QC("#FFFFFF"))
            if _TI is not None else None
        )
        self._add_icon_save = (
            _TI("tabler_device_floppy.svg").icon(color=_QC("#FFFFFF"))
            if _TI is not None else None
        )
        if self._add_icon_plus is not None:
            self._btn_add.setIcon(self._add_icon_plus)
            self._btn_add.setIconSize(_QS(14, 14))
        self._btn_add.setStyleSheet(
            "QPushButton { background: #1e63e4; border: 1px solid #1e63e4;"
            " color: white; border-radius: 6px; padding: 6px 14px;"
            " font-weight: 700; font-size: 11px; }"
            "QPushButton:hover { background: #2a73f3; }"
        )
        self._btn_add.clicked.connect(self._add_break)
        form.addWidget(self._btn_add)

        # Track edit state — set by _edit_break_row, cleared by _add_break /
        # _load_breaks.
        self._editing_bid = None
        self._editing_original = None  # (name, start, end)
        sec2_lay.addLayout(form)
        root.addWidget(sec2)

        # ── Section 3: Configured Break Times ──
        sec3, sec3_lay = _section_card("3", "Configured Break Times")
        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(
            ["SCHEDULE", "NAME", "START", "END", "DURATION", "ACTIONS"]
        )
        _th = self.table.horizontalHeader()
        for c in range(6):
            _th.setSectionResizeMode(c, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(False)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.setStyleSheet(
            "QTableWidget { background: #161B22; border: 1px solid #21262D;"
            " border-radius: 8px; gridline-color: transparent;"
            " color: #E6EDF3; outline: none; }"
            "QTableWidget::item { padding: 8px 6px; border: none; }"
            "QTableWidget::item:selected { background-color: rgba(56,139,253,0.18); }"
            "QHeaderView { background: transparent; border: none; }"
            "QHeaderView::section { background-color: #161B22; color: #8B949E;"
            " padding: 8px 6px; border: none; border-bottom: 1px solid #21262D;"
            " font-weight: 700; font-size: 10px; letter-spacing: 0.5px; }"
        )
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        sec3_lay.addWidget(self.table)
        root.addWidget(sec3, 1)

        if include_footer:
            # Bottom buttons (legacy QDialog path). Fluent path uses the
            # MessageBoxBase buttonGroup wired in _wire_fluent_footer().
            btn_row = QHBoxLayout(); btn_row.setSpacing(8)
            btn_del = QPushButton("  Delete Selected")
            btn_del.setCursor(Qt.PointingHandCursor)
            if _TI is not None:
                btn_del.setIcon(_TI("tabler_trash.svg").icon(color=_QC("#F85149")))
                btn_del.setIconSize(_QS(14, 14))
            btn_del.setStyleSheet(
                "QPushButton { background: transparent; border: 1px solid #F85149;"
                " color: #F85149; border-radius: 6px; padding: 8px 16px;"
                " font-weight: 700; font-size: 12px; }"
                "QPushButton:hover { background: rgba(248,81,73,0.10); }"
            )
            btn_del.clicked.connect(self._delete_selected)
            btn_row.addWidget(btn_del)
            btn_row.addStretch()
            btn_close = QPushButton("Close")
            btn_close.setCursor(Qt.PointingHandCursor)
            btn_close.setFixedWidth(120)
            btn_close.setStyleSheet(
                "QPushButton { background: transparent; border: 1px solid #30363D;"
                " color: #E6EDF3; border-radius: 6px; padding: 8px 22px;"
                " font-weight: 700; font-size: 12px; }"
                "QPushButton:hover { background: rgba(255,255,255,0.05); }"
            )
            btn_close.clicked.connect(self.accept)
            btn_row.addWidget(btn_close)
            root.addLayout(btn_row)

    # ── Data helpers ─────────────────────────────────────────────────────────

    def _refresh_selector(self):
        """Rebuild the (hidden) combo + the visible pill bar."""
        self._sel.blockSignals(True)
        prev = self._current_group
        self._sel.clear()
        self._sel.addItem("Default", _DEFAULT)
        groups = list(get_schedule_groups())
        for name, _is_active in groups:
            self._sel.addItem(name, name)

        # Figure out which schedule is currently active (Default unless one
        # of the conditional ones is toggled on).
        active = _DEFAULT
        for name, is_active in groups:
            if is_active:
                active = name
                break

        # Restore previous *editing* selection if still present, else jump
        # to the active one.
        target = prev if any(
            self._sel.itemData(i) == prev for i in range(self._sel.count())
        ) else active
        for i in range(self._sel.count()):
            if self._sel.itemData(i) == target:
                self._sel.setCurrentIndex(i)
                break
        self._sel.blockSignals(False)

        # Build pill bar.
        self._rebuild_pill_bar(active)
        self._on_schedule_changed()

    def _rebuild_pill_bar(self, active_name: str):
        # Clear existing pills.
        while self._pill_row.count():
            it = self._pill_row.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()

        def _make_pill(label: str, schedule_key: str, is_active: bool):
            b = QPushButton(label)
            b.setCursor(Qt.PointingHandCursor)
            b.setCheckable(True)
            b.setChecked(is_active)
            b.setMinimumHeight(32)
            if is_active:
                css = (
                    "QPushButton { background: #1e63e4; border: 1px solid #1e63e4;"
                    " color: white; border-radius: 16px; padding: 4px 18px;"
                    " font-size: 11px; font-weight: 700; }"
                )
            else:
                css = (
                    "QPushButton { background: transparent; border: 1px solid #30363D;"
                    " color: #C9D1D9; border-radius: 16px; padding: 4px 18px;"
                    " font-size: 11px; font-weight: 600; }"
                    "QPushButton:hover { background: rgba(255,255,255,0.05);"
                    " border-color: #58606A; }"
                )
            b.setStyleSheet(css)
            b.clicked.connect(
                lambda _=False, k=schedule_key: self._activate_pill(k)
            )
            return b

        # Default pill always present — show which weekdays it currently
        # owns (the ones not claimed by another schedule).
        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri"]
        def _fmt(days):
            if not days:
                return ""
            if days == list(range(5)):
                return "Mon–Fri"
            return ", ".join(day_names[d] for d in days)

        def _pill_label(prefix: str, key: str) -> str:
            days = sorted(get_schedule_weekdays(key))
            tag = _fmt(days)
            return f"{prefix}  ·  {tag}" if tag else prefix

        def _make_quick_add(key: str):
            """Small + button that starts an Add Break Time entry targeting
            the given schedule (Default or a named schedule)."""
            from PySide6.QtWidgets import QToolButton as _QTBp
            try:
                from .tabler_icons import TablerIcon as _TIp
                from PySide6.QtGui import QColor as _QCp
                from PySide6.QtCore import QSize as _QSp
                _TIp_available = True
            except Exception:
                _TIp_available = False

            b = _QTBp()
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedSize(28, 28)
            b.setToolTip(f"Add a break to {key}")
            if _TIp_available:
                b.setIcon(_TIp("tabler_plus.svg").icon(color=_QCp("#58A6FF")))
                b.setIconSize(_QSp(14, 14))
            else:
                b.setText("+")
            b.setStyleSheet(
                "QToolButton { background: transparent; border: 1px solid #388BFD;"
                " border-radius: 14px; color: #58A6FF; }"
                "QToolButton:hover { background: rgba(56,139,253,0.10); }"
            )
            b.clicked.connect(lambda _=False, k=key: self._quick_add_for(k))
            return b

        # Quick "+" on the LEFT of Regular day.
        self._pill_row.addWidget(_make_quick_add(_DEFAULT))
        default_days = get_schedule_weekdays(_DEFAULT)
        self._pill_row.addWidget(
            _make_pill(
                _pill_label("Regular day", _DEFAULT),
                _DEFAULT,
                bool(default_days),
            )
        )
        groups = list(get_schedule_groups())
        for name, _is_active in groups:
            self._pill_row.addWidget(
                _make_pill(
                    _pill_label(name, name),
                    name,
                    bool(get_schedule_weekdays(name)),
                )
            )
            # Quick "+" on the RIGHT of every conditional pill.
            self._pill_row.addWidget(_make_quick_add(name))
        self._pill_row.addStretch(1)

    def _quick_add_for(self, schedule_key: str):
        """Set the current editing schedule to ``schedule_key`` and put the
        focus into the Add Break Time form. Used by the small + buttons
        beside each pill."""
        self._maybe_exit_edit_on_switch()
        self._current_group = schedule_key
        # Update the hidden combo so the rest of the dialog stays in sync.
        for i in range(self._sel.count()):
            if self._sel.itemData(i) == schedule_key:
                self._sel.blockSignals(True)
                self._sel.setCurrentIndex(i)
                self._sel.blockSignals(False)
                break
        self.name_input.clear()
        self.name_input.setFocus()

    def _maybe_exit_edit_on_switch(self):
        """Schedule switch abandons any in-progress edit."""
        if getattr(self, "_editing_bid", None) is not None:
            self._exit_edit_mode()

    def _activate_pill(self, key: str):
        """Click on a pill:
          - Regular day → claim all weekdays for Default (clear other schedules' weekday assignments).
          - Non-default schedule → open a weekday picker, then assign.

        Editing target switches to the clicked schedule either way."""
        self._maybe_exit_edit_on_switch()
        if key == _DEFAULT:
            # Strip weekday claims from every conditional schedule so
            # Default once again covers Mon-Fri.
            for name, _is_active in get_schedule_groups():
                set_schedule_weekdays(name, [])
                set_schedule_active(name, False)
        else:
            picked = self._pick_weekdays_for(key)
            if picked is None:
                return  # user cancelled
            set_schedule_weekdays(key, picked)

        for i in range(self._sel.count()):
            if self._sel.itemData(i) == key:
                self._sel.setCurrentIndex(i)
                break
        self._refresh_selector()

    def _pick_weekdays_for(self, key: str):
        """Fluent modal with Mon-Fri chips. Returns the new weekday list
        or ``None`` if the user cancelled."""
        try:
            from qfluentwidgets import MessageBoxBase
            from .tabler_icons import TablerIcon
        except Exception:
            return self._pick_weekdays_fallback(key)

        from PySide6.QtGui import QColor as _QC
        from PySide6.QtCore import QSize as _QS
        from PySide6.QtWidgets import QToolButton as _QTB

        current = set(get_schedule_weekdays(key))

        # Walk up to the real top-level window (not the BreaksDialog modal
        # itself) so the inner modal's mask doesn't detach as a stray
        # top-level "python" window.
        _top_parent = self.window()
        try:
            from PySide6.QtWidgets import QApplication
            for w in QApplication.topLevelWidgets():
                if w.objectName() and w.objectName() != "":
                    if hasattr(w, "centralWidget") and w.centralWidget():
                        _top_parent = w
                        break
        except Exception:
            pass

        class _PickSheet(MessageBoxBase):
            def __init__(_s, h):
                super().__init__(h)
                try:
                    _s.setMaskColor(_QC(0, 0, 0, 170))
                except Exception:
                    pass
                _s.widget.setObjectName("pickDaysCard")
                apply_fluent_modal_palette(_s, "pickDaysCard")
                _s.viewLayout.setContentsMargins(22, 18, 22, 12)
                _s.viewLayout.setSpacing(10)

                # Header.
                hdr = QHBoxLayout(); hdr.setSpacing(10)
                ic = _QTB()
                ic.setEnabled(False)
                ic.setIcon(TablerIcon("tabler_calendar.svg").icon(color=_QC("#388BFD")))
                ic.setIconSize(_QS(20, 20))
                ic.setStyleSheet(
                    "background: rgba(56,139,253,0.14); border: none;"
                    " border-radius: 8px; padding: 6px;"
                )
                tc = QVBoxLayout(); tc.setSpacing(2)
                t = QLabel(f"Which days does '{key}' apply to?")
                t.setStyleSheet(
                    "color: #E6EDF3; font-size: 15px; font-weight: 700;"
                    " background: transparent;"
                )
                s = QLabel(
                    "Picked days follow this schedule. The rest fall back "
                    "to the Regular day breaks."
                )
                s.setWordWrap(True)
                s.setStyleSheet(
                    "color: #8B949E; font-size: 11px; background: transparent;"
                )
                tc.addWidget(t); tc.addWidget(s)
                hdr.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
                hdr.addLayout(tc, 1)
                _s.viewLayout.addLayout(hdr)

                # Chips row.
                chips_row = QHBoxLayout(); chips_row.setSpacing(6)
                names = ["Mon", "Tue", "Wed", "Thu", "Fri"]
                _s.chip_btns = []
                for i, n in enumerate(names):
                    b = QPushButton(n)
                    b.setCheckable(True)
                    b.setChecked(i in current)
                    b.setCursor(Qt.PointingHandCursor)
                    b.setFixedSize(58, 34)
                    def _refresh_css(btn=b):
                        if btn.isChecked():
                            btn.setStyleSheet(
                                "QPushButton { background: #1e63e4;"
                                " border: 1px solid #1e63e4; color: white;"
                                " border-radius: 8px; font-weight: 700;"
                                " font-size: 11px; }"
                            )
                        else:
                            btn.setStyleSheet(
                                "QPushButton { background: transparent;"
                                " border: 1px solid #30363D; color: #C9D1D9;"
                                " border-radius: 8px; font-weight: 600;"
                                " font-size: 11px; }"
                                "QPushButton:hover { background: rgba(255,255,255,0.05);"
                                " border-color: #58606A; }"
                            )
                    _refresh_css(b)
                    b.toggled.connect(lambda _c, btn=b: _refresh_css(btn))
                    _s.chip_btns.append(b)
                    chips_row.addWidget(b)
                chips_row.addStretch()
                _s.viewLayout.addLayout(chips_row)

                _s.widget.setMinimumWidth(440)

                # Footer.
                _s.buttonLayout.removeWidget(_s.yesButton)
                _s.buttonLayout.removeWidget(_s.cancelButton)
                _s.buttonLayout.addStretch(1)
                _s.cancelButton.setText("Cancel")
                _s.cancelButton.setFixedWidth(120)
                _s.cancelButton.setStyleSheet(
                    "QPushButton { background: transparent; border: 1px solid #30363D;"
                    "  color: #E6EDF3; border-radius: 6px; padding: 8px 22px;"
                    "  font-weight: 700; font-size: 12px; }"
                    "QPushButton:hover { background: rgba(255,255,255,0.05); }"
                )
                _s.yesButton.setText("   Apply")
                _s.yesButton.setFixedWidth(120)
                _s.yesButton.setStyleSheet(
                    "QPushButton { background: #1e63e4; border: 1px solid #1e63e4;"
                    "  color: white; border-radius: 6px; padding: 8px 22px;"
                    "  font-weight: 700; font-size: 12px; }"
                    "QPushButton:hover { background: #2a73f3; }"
                )
                _s.buttonLayout.addWidget(_s.cancelButton, 0, Qt.AlignmentFlag.AlignVCenter)
                _s.buttonLayout.addWidget(_s.yesButton, 0, Qt.AlignmentFlag.AlignVCenter)

        dlg = _PickSheet(_top_parent)
        if dlg.exec():
            return [i for i, b in enumerate(dlg.chip_btns) if b.isChecked()]
        return None

    def _pick_weekdays_fallback(self, key: str):
        """Plain-QDialog fallback if qfluentwidgets is unavailable."""
        from PySide6.QtWidgets import QDialog as _QD
        current = set(get_schedule_weekdays(key))
        dlg = _QD(self.window())
        dlg.setWindowTitle("Pick days")
        v = QVBoxLayout(dlg)
        v.addWidget(QLabel(f"Which days does '{key}' apply to?"))
        chips_row = QHBoxLayout()
        names = ["Mon", "Tue", "Wed", "Thu", "Fri"]
        chip_btns = []
        for i, n in enumerate(names):
            b = QPushButton(n); b.setCheckable(True); b.setChecked(i in current)
            chip_btns.append(b); chips_row.addWidget(b)
        v.addLayout(chips_row)
        from PySide6.QtWidgets import QDialogButtonBox
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Apply
            | QDialogButtonBox.StandardButton.Cancel
        )
        bb.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        v.addWidget(bb)
        if dlg.exec():
            return [i for i, b in enumerate(chip_btns) if b.isChecked()]
        return None

    def _on_schedule_changed(self):
        idx = self._sel.currentIndex()
        if idx < 0:
            return
        group = self._sel.itemData(idx)
        self._current_group = group or _DEFAULT

        is_default = self._current_group == _DEFAULT
        self._active_row.setVisible(not is_default)
        self._btn_del_sched.setEnabled(not is_default)

        if not is_default:
            groups = {n: a for n, a in get_schedule_groups()}
            active = bool(groups.get(self._current_group, 0))
            self._active_chk.blockSignals(True)
            self._active_chk.setChecked(active)
            self._active_chk.blockSignals(False)

        self._load_breaks()

    def _on_active_toggled(self, checked: bool):
        if self._current_group == _DEFAULT:
            return
        set_schedule_active(self._current_group, checked)
        self._refresh_selector()

    def _load_breaks(self):
        from PySide6.QtGui import QColor as _QC_lb
        from PySide6.QtCore import QSize as _QS_lb
        from PySide6.QtWidgets import (
            QWidget as _W_lb, QHBoxLayout as _H_lb, QToolButton as _TB_lb,
        )
        try:
            from .tabler_icons import TablerIcon as _TI_lb
        except Exception:
            _TI_lb = None

        # Pull breaks from EVERY schedule so the table acts as a unified
        # view across Default + any conditional schedule (Site Day, etc).
        all_rows = []  # list of (sched_label, bid, name, start, end)
        for bid, n, s, e in get_breaks(_DEFAULT):
            all_rows.append(("Regular day", bid, n, s, e))
        for sched_name, _is_active in get_schedule_groups():
            for bid, n, s, e in get_breaks(sched_name):
                all_rows.append((sched_name, bid, n, s, e))
        # Sort: by schedule first, then by start time.
        all_rows.sort(key=lambda r: (r[0].lower(), _to_minutes(r[3] or "00:00")))

        self.table.setRowCount(len(all_rows))
        self.table.verticalHeader().setDefaultSectionSize(42)
        self._break_ids = []
        for i, (sched_label, bid, name, start, end) in enumerate(all_rows):
            self._break_ids.append(bid)

            # SCHEDULE col: colored chip showing which schedule owns this row.
            is_default = (sched_label == "Regular day")
            chip_fg = "#58A6FF" if is_default else "#F0883E"
            chip_bg = (
                "rgba(56,139,253,0.10)" if is_default
                else "rgba(240,136,62,0.10)"
            )
            chip_border = chip_fg
            # Empty item so the QTableWidgetItem text doesn't bleed through
            # the cellWidget chip rendered on top.
            sched_cell = QTableWidgetItem("")
            sched_cell.setData(Qt.ItemDataRole.UserRole, sched_label)
            # Make the schedule name stand out — font 10px bold via a label
            # cell widget instead so we can give it a chip background.
            chip_w = _W_lb()
            chip_lay = _H_lb(chip_w)
            chip_lay.setContentsMargins(0, 0, 0, 0)
            chip_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
            chip_lbl = QLabel(sched_label)
            chip_lbl.setStyleSheet(
                f"QLabel {{ color: {chip_fg}; background: {chip_bg};"
                f" border: 1px solid {chip_border}; border-radius: 10px;"
                "  padding: 3px 12px; font-size: 10px; font-weight: 700; }}"
            )
            chip_lay.addWidget(chip_lbl)
            chip_w.setStyleSheet("background: transparent;")
            self.table.setItem(i, 0, sched_cell)  # keep item for selection
            self.table.setCellWidget(i, 0, chip_w)

            # NAME col.
            name_it = QTableWidgetItem(name or "")
            name_it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            name_it.setData(Qt.ItemDataRole.UserRole, name or "")
            self.table.setItem(i, 1, name_it)

            for col, txt in (
                (2, start), (3, end),
                (4, f"{_to_minutes(end) - _to_minutes(start):.0f} min"),
            ):
                it = QTableWidgetItem(txt)
                it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(i, col, it)

            # ACTIONS cell — transparent wrapper with vertically centred
            # edit + delete buttons. The wrapper widget must declare a
            # transparent background, otherwise Qt paints the QWidget default
            # (system bg) which reads as a black box on the dark table.
            act_w = _W_lb()
            act_w.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            act_w.setStyleSheet("background: transparent;")
            al = _H_lb(act_w)
            al.setContentsMargins(0, 0, 0, 0)
            al.setSpacing(6)
            al.addStretch(1)

            def _make_btn(svg, color, tooltip, on_click):
                b = _TB_lb()
                b.setCursor(Qt.PointingHandCursor)
                b.setFixedSize(26, 24)
                b.setToolTip(tooltip)
                if _TI_lb is not None:
                    b.setIcon(_TI_lb(svg).icon(color=_QC_lb(color)))
                    b.setIconSize(_QS_lb(14, 14))
                rgb = _QC_lb(color)
                b.setStyleSheet(
                    f"QToolButton {{ background: transparent;"
                    f" border: 1px solid {color}; border-radius: 5px; padding: 0; }}"
                    f"QToolButton:hover {{ background: rgba({rgb.red()},{rgb.green()},{rgb.blue()},0.16); }}"
                )
                b.clicked.connect(on_click)
                return b

            edit_btn = _make_btn(
                "tabler_pencil.svg", "#388BFD", "Edit",
                lambda _=False, bid_=bid, n=name, s=start, e=end:
                    self._edit_break_row(bid_, n, s, e),
            )
            del_btn = _make_btn(
                "tabler_trash.svg", "#F85149", "Delete",
                lambda _=False, bid_=bid: self._delete_break_by_id(bid_),
            )
            al.addWidget(edit_btn, 0, Qt.AlignmentFlag.AlignVCenter)
            al.addWidget(del_btn, 0, Qt.AlignmentFlag.AlignVCenter)
            al.addStretch(1)
            self.table.setCellWidget(i, 5, act_w)

    def _edit_break_row(self, bid, name, start, end):
        """Click Edit on a row → load values into the Add form, remember
        we're editing, change the "+ Add" button to a "Save" button.

        The original row stays in the table until the user clicks Save AND
        actually changes something. Closing the dialog or clicking another
        row's Edit silently abandons the in-progress edit."""
        self._editing_bid = bid
        self._editing_original = (name or "", start or "", end or "")
        self.name_input.setText(name or "")
        try:
            sh, sm = (start or "00:00").split(":")
            self.start_input.setTime(QTime(int(sh), int(sm)))
        except Exception:
            pass
        try:
            eh, em = (end or "00:00").split(":")
            self.end_input.setTime(QTime(int(eh), int(em)))
        except Exception:
            pass
        # Swap the primary button to "Save" mode.
        self._btn_add.setText("  Save")
        if self._add_icon_save is not None:
            self._btn_add.setIcon(self._add_icon_save)

    def _exit_edit_mode(self):
        self._editing_bid = None
        self._editing_original = None
        self.name_input.clear()
        self._btn_add.setText("  Add")
        if self._add_icon_plus is not None:
            self._btn_add.setIcon(self._add_icon_plus)

    def _toast_warning(self, message: str):
        """Non-modal Fluent toast (InfoBar) instead of a blocking dialog."""
        try:
            from qfluentwidgets import InfoBar, InfoBarPosition, InfoBarIcon
            InfoBar.new(
                icon=InfoBarIcon.WARNING,
                title="",
                content=message,
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=2500,
                parent=self,
            )
        except Exception:
            # Fallback to status-bar style label or no-op.
            QMessageBox.warning(self, "Invalid", message)

    def _silently_delete_break(self, bid):
        """DELETE without confirmation — internal use only (edit flow)."""
        try:
            conn = get_connection()
            cur = conn.cursor()
            cur.execute("DELETE FROM breaks WHERE id = ?", (bid,))
            conn.commit()
            conn.close()
        except Exception:
            return
        self._load_breaks()

    def _delete_break_by_id(self, bid):
        # Confirmation — break delete is irreversible from the UI.
        try:
            conn = get_connection()
            cur = conn.cursor()
            cur.execute("SELECT name FROM breaks WHERE id = ?", (bid,))
            row = cur.fetchone()
            conn.close()
        except Exception:
            row = None
        name = (row[0] if row and row[0] else "this break")
        if self._confirm_delete_break(name):
            self._silently_delete_break(bid)

    def _confirm_delete_break(self, name: str) -> bool:
        """Fluent confirmation modal — falls back to QMessageBox if needed."""
        try:
            from qfluentwidgets import MessageBoxBase
            from .tabler_icons import TablerIcon
        except Exception:
            resp = QMessageBox.question(
                self, "Delete break",
                f"Delete '{name}'?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            return resp == QMessageBox.StandardButton.Yes

        from PySide6.QtGui import QColor as _QC
        from PySide6.QtCore import QSize as _QS
        from PySide6.QtWidgets import QToolButton as _QTB

        class _ConfirmSheet(MessageBoxBase):
            def __init__(_s, h):
                super().__init__(h.window())
                try:
                    _s.setMaskColor(_QC(0, 0, 0, 170))
                except Exception:
                    pass
                _s.widget.setObjectName("delBrkCard")
                apply_fluent_modal_palette(_s, "delBrkCard")
                _s.viewLayout.setContentsMargins(22, 18, 22, 12)
                _s.viewLayout.setSpacing(10)

                hdr = QHBoxLayout(); hdr.setSpacing(12)
                ic = _QTB()
                ic.setEnabled(False)
                ic.setIcon(TablerIcon("tabler_trash.svg").icon(color=_QC("#F85149")))
                ic.setIconSize(_QS(22, 22))
                ic.setStyleSheet(
                    "background: rgba(248,81,73,0.14); border: none;"
                    " border-radius: 10px; padding: 6px;"
                )
                tc = QVBoxLayout(); tc.setSpacing(2)
                t = QLabel("Delete break?")
                t.setStyleSheet(
                    "color: #E6EDF3; font-size: 15px; font-weight: 700;"
                    " background: transparent;"
                )
                s = QLabel(
                    f"Remove <b>{name}</b> from this schedule. "
                    "This can't be undone from the UI."
                )
                s.setTextFormat(Qt.TextFormat.RichText)
                s.setWordWrap(True)
                s.setStyleSheet(
                    "color: #8B949E; font-size: 11px; background: transparent;"
                )
                tc.addWidget(t); tc.addWidget(s)
                hdr.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
                hdr.addLayout(tc, 1)
                _s.viewLayout.addLayout(hdr)
                _s.widget.setMinimumWidth(420)

                _s.buttonLayout.removeWidget(_s.yesButton)
                _s.buttonLayout.removeWidget(_s.cancelButton)
                _s.buttonLayout.addStretch(1)
                _s.cancelButton.setText("Cancel")
                _s.cancelButton.setFixedWidth(120)
                _s.cancelButton.setStyleSheet(
                    "QPushButton { background: transparent; border: 1px solid #30363D;"
                    "  color: #E6EDF3; border-radius: 6px; padding: 8px 22px;"
                    "  font-weight: 700; font-size: 12px; }"
                    "QPushButton:hover { background: rgba(255,255,255,0.05); }"
                )
                _s.yesButton.setText("   Delete")
                _s.yesButton.setFixedWidth(120)
                _s.yesButton.setStyleSheet(
                    "QPushButton { background: #F85149; border: 1px solid #F85149;"
                    "  color: white; border-radius: 6px; padding: 8px 22px;"
                    "  font-weight: 700; font-size: 12px; }"
                    "QPushButton:hover { background: #FF6961; }"
                )
                _s.buttonLayout.addWidget(_s.cancelButton, 0, Qt.AlignmentFlag.AlignVCenter)
                _s.buttonLayout.addWidget(_s.yesButton, 0, Qt.AlignmentFlag.AlignVCenter)

        return bool(_ConfirmSheet(self).exec())

    # ── Actions ──────────────────────────────────────────────────────────────

    def _add_schedule(self):
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(
            self, "New Schedule", "Schedule name (e.g. Site Day):"
        )
        name = name.strip()
        if not ok or not name or name.lower() == _DEFAULT:
            return
        add_schedule_group(name)
        self._current_group = name
        self._refresh_selector()

    def _delete_schedule(self):
        if self._current_group == _DEFAULT:
            return
        reply = QMessageBox.question(
            self, "Delete Schedule",
            f"Delete schedule '{self._current_group}' and all its breaks?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        delete_schedule_group(self._current_group)
        self._current_group = _DEFAULT
        self._refresh_selector()

    def _add_break(self):
        name = self.name_input.text().strip() or "Break"
        start = self.start_input.time().toString("HH:mm")
        end = self.end_input.time().toString("HH:mm")

        if _to_minutes(end) <= _to_minutes(start):
            self._toast_warning("End time must be after start time.")
            return

        # If editing, UPDATE the existing row instead of inserting a new one.
        if self._editing_bid is not None:
            if self._editing_original == (name, start, end):
                # No change — abandon edit mode silently.
                self._exit_edit_mode()
                return
            try:
                conn = get_connection()
                cur = conn.cursor()
                cur.execute(
                    "UPDATE breaks SET name = ?, hora_inicio = ?, hora_fin = ?"
                    " WHERE id = ?",
                    (name, start, end, self._editing_bid),
                )
                conn.commit()
                conn.close()
            except Exception:
                pass
            self._exit_edit_mode()
            self._load_breaks()
            return

        # Normal add — insert new break.
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO breaks (name, hora_inicio, hora_fin, schedule_group) VALUES (?, ?, ?, ?)",
            (name, start, end, self._current_group),
        )
        conn.commit()
        conn.close()
        self.name_input.clear()
        self._load_breaks()

    def _delete_selected(self):
        rows = {idx.row() for idx in self.table.selectedIndexes()}
        if not rows:
            return
        conn = get_connection()
        cur = conn.cursor()
        for r in sorted(rows, reverse=True):
            if r < len(self._break_ids):
                cur.execute("DELETE FROM breaks WHERE id = ?", (self._break_ids[r],))
        conn.commit()
        conn.close()
        self._load_breaks()
