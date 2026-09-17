"""Fluent shift-hours configuration modal.

Extracted from tab_register.py. Public surface:

    open_shift_config(host) -> dict | None

Returns the new ``{"shift_start_hour": int, "shift_end_hour": int}`` dict
when the user saves, or ``None`` when they cancel. The caller is in
charge of persisting via ``sync.app_config.save_config`` and updating
any visible UI label.
"""
from __future__ import annotations

from .theme_palette import apply_fluent_modal_palette
from PySide6.QtCore import (
    Qt, QSize, QPropertyAnimation, QEasingCurve, Property,
)
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QToolButton, QFrame,
)


def open_shift_config(host) -> dict | None:
    """Open the shift-hours modal anchored to ``host.window()``.

    Returns the new shift hour dict (start/end) when the user clicks
    Save, otherwise ``None``.
    """
    from qfluentwidgets import MessageBoxBase, ComboBox as FCombo
    from sync.app_config import load_config
    from .tabler_icons import TablerIcon

    class _ShiftSheet(MessageBoxBase):
        def __init__(_self, h):
            super().__init__(h.window())
            try:
                _self.setMaskColor(QColor(0, 0, 0, 170))
            except Exception:
                pass

            _self.widget.setObjectName("shiftCard")
            apply_fluent_modal_palette(_self, "shiftCard")

            _self.viewLayout.setContentsMargins(0, 8, 0, 8)
            _self.viewLayout.setSpacing(0)

            def _wrap(child):
                w = QWidget()
                lw = QVBoxLayout(w)
                lw.setContentsMargins(22, 12, 22, 12)
                lw.setSpacing(6)
                if isinstance(child, QWidget):
                    lw.addWidget(child)
                else:
                    lw.addLayout(child)
                return w

            def _div():
                d = QFrame()
                d.setFixedHeight(1)
                d.setStyleSheet("background: #21262D; border: none;")
                return d

            # Header.
            header_row = QHBoxLayout()
            header_row.setSpacing(10)
            icon_btn = QToolButton()
            icon_btn.setEnabled(False)
            icon_btn.setIcon(TablerIcon("tabler_settings.svg").icon(color=QColor("#388BFD")))
            icon_btn.setIconSize(QSize(20, 20))
            icon_btn.setStyleSheet(
                "background: rgba(56,139,253,0.12); border: none;"
                " border-radius: 8px; padding: 6px;"
            )
            title_col = QVBoxLayout()
            title_col.setSpacing(2)
            title_lbl = QLabel("Shift hours")
            title_lbl.setStyleSheet(
                "color: #E6EDF3; font-size: 15px; font-weight: 700;"
                " background: transparent;"
            )
            sub_lbl = QLabel(
                "Set the working window used for the daily production "
                "calculation."
            )
            sub_lbl.setWordWrap(True)
            sub_lbl.setStyleSheet(
                "color: #8B949E; font-size: 11px; background: transparent;"
            )
            title_col.addWidget(title_lbl)
            title_col.addWidget(sub_lbl)

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

            close_btn = _SpinX()
            close_btn.setIcon(TablerIcon("tabler_x.svg").icon(color=QColor("#8B949E")))
            close_btn.setIconSize(QSize(22, 22))
            close_btn.setCursor(Qt.PointingHandCursor)
            close_btn.setFixedSize(34, 34)
            close_btn.setStyleSheet(
                "QToolButton { background: transparent; border: none;"
                "  border-radius: 17px; }"
                "QToolButton:hover { background: rgba(255,255,255,0.08); }"
            )
            close_btn.clicked.connect(_self.reject)

            header_row.addWidget(icon_btn, 0, Qt.AlignTop)
            header_row.addLayout(title_col, 1)
            header_row.addWidget(close_btn, 0, Qt.AlignTop)
            _self.viewLayout.addWidget(_wrap(header_row))
            _self.viewLayout.addWidget(_div())

            # Body: two combos.
            body_w = QWidget()
            body_lay = QVBoxLayout(body_w)
            body_lay.setContentsMargins(22, 16, 22, 16)
            body_lay.setSpacing(8)

            cfg = load_config() or {}
            cur_start = int(cfg.get("shift_start_hour", 6))
            cur_end = int(cfg.get("shift_end_hour", 15))

            def _make_combo(initial):
                c = FCombo()
                for h in range(0, 24):
                    suf = "AM" if h < 12 else "PM"
                    disp = h if h == 12 else h % 12
                    if disp == 0:
                        disp = 12
                    c.addItem(f"{disp}:00 {suf}")
                c.setCurrentIndex(initial)
                return c

            start_lbl = QLabel("Shift starts at")
            start_lbl.setStyleSheet(
                "color: #C9D1D9; font-size: 13px; font-weight: 700;"
                " background: transparent;"
            )
            _self.start_combo = _make_combo(cur_start)

            end_lbl = QLabel("Shift ends at")
            end_lbl.setStyleSheet(
                "color: #C9D1D9; font-size: 13px; font-weight: 700;"
                " background: transparent; padding-top: 4px;"
            )
            _self.end_combo = _make_combo(cur_end)

            body_lay.addWidget(start_lbl)
            body_lay.addWidget(_self.start_combo)
            body_lay.addWidget(end_lbl)
            body_lay.addWidget(_self.end_combo)

            # Tips card.
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
            bulb.setIcon(TablerIcon("tabler_alert_triangle.svg").icon(color=QColor("#388BFD")))
            bulb.setIconSize(QSize(16, 16))
            bulb.setStyleSheet("background: transparent; border: none;")
            tips_text_col = QVBoxLayout()
            tips_text_col.setSpacing(1)
            tips_title = QLabel("Tips")
            tips_title.setStyleSheet(
                "color: #58A6FF; font-size: 11px; font-weight: 700;"
            )
            tips_body = QLabel(
                "Useful when teams work in different time zones "
                "(e.g. Spain vs. Mexico)."
            )
            tips_body.setWordWrap(True)
            tips_text_col.addWidget(tips_title)
            tips_text_col.addWidget(tips_body)
            tips_lay.addWidget(bulb, 0, Qt.AlignTop)
            tips_lay.addLayout(tips_text_col, 1)
            body_lay.addWidget(tips_card)

            _self.viewLayout.addWidget(body_w)
            _self.viewLayout.addWidget(_div())
            _self.widget.setMinimumWidth(480)

            # Buttons.
            _self.buttonLayout.removeWidget(_self.yesButton)
            _self.buttonLayout.removeWidget(_self.cancelButton)
            _self.buttonLayout.addStretch(1)
            _self.yesButton.setText("   Save")
            _self.cancelButton.setText("Cancel")
            _self.cancelButton.setFixedWidth(120)
            _self.yesButton.setFixedWidth(120)
            _self.cancelButton.setStyleSheet(
                "QPushButton { background: transparent; border: 1px solid #30363D;"
                "  color: #E6EDF3; border-radius: 10px; padding: 8px 22px;"
                "  font-weight: 700; font-size: 12px; }"
                "QPushButton:hover { background: rgba(255,255,255,0.05);"
                "  border-color: #58606A; }"
            )
            _self.yesButton.setStyleSheet(
                "QPushButton { background: #1e63e4; border: 1px solid #1e63e4;"
                "  color: white; border-radius: 10px; padding: 8px 22px;"
                "  font-weight: 700; font-size: 12px; }"
                "QPushButton:hover { background: #2a73f3; border-color: #2a73f3; }"
                "QPushButton:pressed { background: #154fbb; }"
            )
            try:
                _self.yesButton.setIcon(
                    TablerIcon("tabler_device_floppy.svg").icon(color=QColor("#FFFFFF"))
                )
                _self.yesButton.setIconSize(QSize(14, 14))
            except Exception:
                pass
            _self.buttonLayout.addWidget(_self.cancelButton, 0, Qt.AlignVCenter)
            _self.buttonLayout.addWidget(_self.yesButton, 0, Qt.AlignVCenter)

    dlg = _ShiftSheet(host)
    if dlg.exec():
        return {
            "shift_start_hour": dlg.start_combo.currentIndex(),
            "shift_end_hour": dlg.end_combo.currentIndex(),
        }
    return None
