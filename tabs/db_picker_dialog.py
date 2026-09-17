"""Fluent modal that lets the user pick a discovered cases.db file
(or fall back to the native file picker for a custom path).

Public surface:

    pick_database_file(host) -> str | None

Returns the absolute path of the selected file or ``None`` on cancel.
"""
from __future__ import annotations

from .theme_palette import apply_fluent_modal_palette
import os
from datetime import datetime

from PySide6.QtCore import (
    Qt, QSize, QPropertyAnimation, QEasingCurve, Property,
)
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QToolButton, QFrame,
    QPushButton, QFileDialog, QButtonGroup, QRadioButton, QScrollArea,
)


def pick_database_file(host) -> str | None:
    """Open the Fluent DB picker. Falls back to a plain native picker if
    qfluentwidgets isn't available."""
    try:
        return _show_fluent_picker(host)
    except Exception:
        return _native_pick(host)


def _native_pick(host) -> str | None:
    path, _ = QFileDialog.getOpenFileName(
        host, "Select old cases.db", "", "SQLite Database (*.db);;All Files (*)"
    )
    return path or None


def _discover_db_files() -> list[dict]:
    """Return [{'path': str, 'size': int, 'mtime': float, 'label': str}, …]
    sorted by mtime desc. Best-effort — broken/non-DB files are filtered."""
    paths: set[str] = set()
    try:
        from db.database import _discover_cases_db_candidates, DB_PATH
        for p in _discover_cases_db_candidates(max_seconds=5):
            paths.add(os.path.abspath(p))
        if DB_PATH and os.path.exists(DB_PATH):
            paths.add(os.path.abspath(DB_PATH))
    except Exception:
        pass

    out = []
    for p in paths:
        try:
            st = os.stat(p)
            out.append({
                "path": p,
                "size": st.st_size,
                "mtime": st.st_mtime,
                "label": _hint_for_path(p),
            })
        except Exception:
            continue
    out.sort(key=lambda r: r["mtime"], reverse=True)
    return out


def _hint_for_path(p: str) -> str:
    low = p.replace("\\", "/").lower()
    if "onedrive" in low:
        return "OneDrive"
    if "appdata/roaming" in low:
        return "AppData (Roaming)"
    if "appdata/local" in low:
        return "AppData (Local)"
    if "programdata" in low:
        return "ProgramData"
    if "documents" in low:
        return "Documents"
    return "Local"


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def _fmt_mtime(ts: float) -> str:
    try:
        dt = datetime.fromtimestamp(ts)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "—"


def _show_fluent_picker(host) -> str | None:
    from qfluentwidgets import MessageBoxBase
    from .tabler_icons import TablerIcon

    candidates = _discover_db_files()

    class _Sheet(MessageBoxBase):
        def __init__(_s, h):
            super().__init__(h.window() if h is not None else None)
            _s.selected_path: str | None = None
            _s.browse_clicked: bool = False
            try:
                _s.setMaskColor(QColor(0, 0, 0, 170))
            except Exception:
                pass
            _s.widget.setObjectName("dbPickCard")
            apply_fluent_modal_palette(_s, "dbPickCard")
            _s.viewLayout.setContentsMargins(0, 8, 0, 8)
            _s.viewLayout.setSpacing(0)

            def _div():
                d = QFrame()
                d.setFixedHeight(1)
                d.setStyleSheet("background: #21262D; border: none;")
                return d

            # Header.
            hdr_wrap = QWidget()
            hl = QVBoxLayout(hdr_wrap)
            hl.setContentsMargins(22, 14, 22, 12)
            hl.setSpacing(6)
            hdr = QHBoxLayout(); hdr.setSpacing(12)
            ic = QToolButton()
            ic.setEnabled(False)
            ic.setIcon(TablerIcon("tabler_database.svg").icon(color=QColor("#58A6FF")))
            ic.setIconSize(QSize(20, 20))
            ic.setStyleSheet(
                "background: rgba(56,139,253,0.14); border: none;"
                " border-radius: 10px; padding: 6px;"
            )
            tc = QVBoxLayout(); tc.setSpacing(2)
            t = QLabel("Select database file")
            t.setStyleSheet(
                "color: #E6EDF3; font-size: 15px; font-weight: 700;"
                " background: transparent;"
            )
            n = len(candidates)
            s = QLabel(
                f"Found {n} database file(s) on this PC."
                if n else "No databases auto-detected. Browse for one."
            )
            s.setStyleSheet(
                "color: #8B949E; font-size: 11px; background: transparent;"
            )
            tc.addWidget(t); tc.addWidget(s)

            class _SpinX(QToolButton):
                def __init__(s, *a, **kw):
                    super().__init__(*a, **kw)
                    s._rot = 0.0
                    s._anim = QPropertyAnimation(s, b"rotation", s)
                    s._anim.setDuration(260)
                    s._anim.setEasingCurve(QEasingCurve.OutCubic)
                def get_rot(s): return s._rot
                def set_rot(s, v):
                    s._rot = float(v); s.update()
                rotation = Property(float, get_rot, set_rot)
                def paintEvent(s, e):
                    p = QPainter(s); p.setRenderHint(QPainter.Antialiasing)
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
            cb.setIcon(TablerIcon("tabler_x.svg").icon(color=QColor("#8B949E")))
            cb.setIconSize(QSize(22, 22))
            cb.setCursor(Qt.PointingHandCursor)
            cb.setFixedSize(34, 34)
            cb.setStyleSheet(
                "QToolButton { background: transparent; border: none;"
                "  border-radius: 17px; }"
                "QToolButton:hover { background: rgba(255,255,255,0.08); }"
            )
            cb.clicked.connect(_s.reject)
            hdr.addWidget(ic, 0, Qt.AlignTop)
            hdr.addLayout(tc, 1)
            hdr.addWidget(cb, 0, Qt.AlignTop)
            hl.addLayout(hdr)
            _s.viewLayout.addWidget(hdr_wrap)
            _s.viewLayout.addWidget(_div())

            # Body — scrollable cards list.
            body = QWidget()
            bl = QVBoxLayout(body)
            bl.setContentsMargins(22, 14, 22, 14)
            bl.setSpacing(8)

            _s._radio_group = QButtonGroup(_s)
            if candidates:
                scroll = QScrollArea()
                scroll.setWidgetResizable(True)
                scroll.setStyleSheet(
                    "QScrollArea { background: transparent; border: none; }"
                    "QScrollBar:vertical { background: transparent; width: 8px; }"
                    "QScrollBar::handle:vertical { background: #30363D;"
                    " border-radius: 4px; min-height: 24px; }"
                )
                list_w = QWidget()
                ll = QVBoxLayout(list_w)
                ll.setContentsMargins(0, 0, 0, 0)
                ll.setSpacing(6)
                for i, c in enumerate(candidates):
                    card = _DBCard(c, i == 0)
                    _s._radio_group.addButton(card.radio, i)
                    card.radio.setProperty("db_path", c["path"])
                    ll.addWidget(card)
                ll.addStretch()
                scroll.setWidget(list_w)
                scroll.setMinimumHeight(220)
                bl.addWidget(scroll)
            else:
                empty = QLabel(
                    "Click 'Browse computer…' to locate a cases.db file."
                )
                empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
                empty.setStyleSheet(
                    "color: #6E7681; font-size: 12px; padding: 30px;"
                    " border: 1px dashed #30363D; border-radius: 8px;"
                )
                bl.addWidget(empty)

            _s.viewLayout.addWidget(body, 1)
            _s.viewLayout.addWidget(_div())

            _s.widget.setMinimumWidth(620)
            _s.widget.setMinimumHeight(440)

            # Footer.
            _s.buttonLayout.removeWidget(_s.yesButton)
            _s.buttonLayout.removeWidget(_s.cancelButton)
            browse = QPushButton("  Browse computer…")
            browse.setFixedHeight(34)
            browse.setCursor(Qt.PointingHandCursor)
            try:
                browse.setIcon(TablerIcon("tabler_folder.svg").icon(color=QColor("#E6EDF3")))
                browse.setIconSize(QSize(14, 14))
            except Exception:
                pass
            browse.setStyleSheet(
                "QPushButton { background: transparent; border: 1px solid #30363D;"
                " color: #E6EDF3; border-radius: 6px; padding: 6px 14px;"
                " font-weight: 700; font-size: 11px; }"
                "QPushButton:hover { background: rgba(255,255,255,0.05); }"
            )
            def _do_browse():
                path = _native_pick(_s)
                if path:
                    _s.selected_path = path
                    _s.accept()
            browse.clicked.connect(_do_browse)
            _s.buttonLayout.addWidget(browse, 0, Qt.AlignmentFlag.AlignVCenter)
            _s.buttonLayout.addStretch(1)

            _s.cancelButton.setText("Cancel")
            _s.cancelButton.setFixedWidth(120)
            _s.cancelButton.setStyleSheet(
                "QPushButton { background: transparent; border: 1px solid #30363D;"
                "  color: #E6EDF3; border-radius: 6px; padding: 8px 22px;"
                "  font-weight: 700; font-size: 12px; }"
                "QPushButton:hover { background: rgba(255,255,255,0.05); }"
            )
            _s.yesButton.setText("   Use selected")
            _s.yesButton.setFixedWidth(150)
            _s.yesButton.setEnabled(bool(candidates))
            _s.yesButton.setStyleSheet(
                "QPushButton { background: #1e63e4; border: 1px solid #1e63e4;"
                "  color: white; border-radius: 6px; padding: 8px 22px;"
                "  font-weight: 700; font-size: 12px; }"
                "QPushButton:hover { background: #2a73f3; }"
                "QPushButton:disabled { background: #2a3a55; color: #8B949E; }"
            )
            try:
                _s.yesButton.setIcon(TablerIcon("tabler_check.svg").icon(color=QColor("#FFFFFF")))
                _s.yesButton.setIconSize(QSize(14, 14))
            except Exception:
                pass
            def _confirm_selected():
                btn = _s._radio_group.checkedButton()
                if btn is not None:
                    _s.selected_path = btn.property("db_path")
                    _s.accept()
            try:
                _s.yesButton.clicked.disconnect()
            except Exception:
                pass
            _s.yesButton.clicked.connect(_confirm_selected)
            _s.buttonLayout.addWidget(_s.cancelButton, 0, Qt.AlignmentFlag.AlignVCenter)
            _s.buttonLayout.addWidget(_s.yesButton, 0, Qt.AlignmentFlag.AlignVCenter)

    dlg = _Sheet(host)
    if dlg.exec():
        return dlg.selected_path
    return None


class _DBCard(QFrame):
    """Single radio-selectable card for a discovered DB file."""

    def __init__(self, info: dict, default_checked: bool):
        super().__init__()
        self.setObjectName("dbCard")
        self.setStyleSheet(
            "#dbCard { background: #161B22; border: 1px solid #21262D;"
            " border-radius: 8px; }"
            "QLabel { background: transparent; border: none; }"
            "QRadioButton { background: transparent; color: #C9D1D9; }"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(10)

        self.radio = QRadioButton()
        self.radio.setChecked(default_checked)
        lay.addWidget(self.radio, 0, Qt.AlignmentFlag.AlignVCenter)

        col = QVBoxLayout(); col.setSpacing(2)
        loc = QLabel(info.get("label", ""))
        loc.setStyleSheet(
            "color: #58A6FF; font-size: 10px; font-weight: 800;"
            " letter-spacing: 0.5px;"
        )
        col.addWidget(loc)
        path = QLabel(info.get("path", ""))
        path.setStyleSheet(
            "color: #E6EDF3; font-size: 11px;"
            " font-family: 'Consolas','Menlo',monospace;"
        )
        path.setWordWrap(True)
        col.addWidget(path)
        meta = QLabel(
            f"{_fmt_size(info.get('size', 0))} · {_fmt_mtime(info.get('mtime', 0))}"
        )
        meta.setStyleSheet("color: #8B949E; font-size: 10px;")
        col.addWidget(meta)
        lay.addLayout(col, 1)

        # Click anywhere on the card → select its radio.
        self.mousePressEvent = lambda _e: self.radio.setChecked(True)
