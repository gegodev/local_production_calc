"""Fluent dialog to configure the OT (overtime) daily hour limits.

Public surface:

    open_ot_limits_config(host) -> dict | None

Returns ``{"ot_max_weekday_hours": int, "ot_max_saturday_hours": int}``
when the user saves, or ``None`` when they cancel.
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


def open_ot_limits_config(host) -> dict | None:
    """Open the OT daily-limits modal anchored to ``host.window()``."""
    from qfluentwidgets import MessageBoxBase, ComboBox as FCombo
    from sync.app_config import load_config
    from .tabler_icons import TablerIcon

    class _OTSheet(MessageBoxBase):
        def __init__(_self, h):
            super().__init__(h.window())
            try:
                _self.setMaskColor(QColor(0, 0, 0, 170))
            except Exception:
                pass

            _self.widget.setObjectName("otLimitsCard")
            apply_fluent_modal_palette(_self, "otLimitsCard")

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
            icon_btn.setIcon(TablerIcon("tabler_clock.svg").icon(color=QColor("#F0883E")))
            icon_btn.setIconSize(QSize(20, 20))
            icon_btn.setStyleSheet(
                "background: rgba(240,136,62,0.14); border: none;"
                " border-radius: 8px; padding: 6px;"
            )
            title_col = QVBoxLayout()
            title_col.setSpacing(2)
            title_lbl = QLabel("OT daily limits")
            title_lbl.setStyleSheet(
                "color: #E6EDF3; font-size: 15px; font-weight: 700;"
                " background: transparent;"
            )
            sub_lbl = QLabel(
                "Maximum overtime hours allowed per day. "
                "Used to scale the OT gauge."
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

            # Body.
            body_w = QWidget()
            body_lay = QVBoxLayout(body_w)
            body_lay.setContentsMargins(22, 16, 22, 16)
            body_lay.setSpacing(8)

            cfg = load_config() or {}
            cur_wk = int(cfg.get("ot_max_weekday_hours", 3))
            cur_sat = int(cfg.get("ot_max_saturday_hours", 8))

            def _make_combo(initial, hours_range=range(1, 13)):
                c = FCombo()
                for h in hours_range:
                    c.addItem(f"{h} hour{'s' if h != 1 else ''}")
                idx = max(0, min(initial - 1, c.count() - 1))
                c.setCurrentIndex(idx)
                return c

            wk_lbl = QLabel("Monday – Friday")
            wk_lbl.setStyleSheet(
                "color: #C9D1D9; font-size: 13px; font-weight: 700;"
                " background: transparent;"
            )
            _self.wk_combo = _make_combo(cur_wk, range(1, 9))

            sat_lbl = QLabel("Saturday")
            sat_lbl.setStyleSheet(
                "color: #C9D1D9; font-size: 13px; font-weight: 700;"
                " background: transparent; padding-top: 4px;"
            )
            _self.sat_combo = _make_combo(cur_sat, range(1, 13))

            body_lay.addWidget(wk_lbl)
            body_lay.addWidget(_self.wk_combo)
            body_lay.addWidget(sat_lbl)
            body_lay.addWidget(_self.sat_combo)

            # Tips card.
            tips_card = QFrame()
            tips_card.setStyleSheet(
                "QFrame { background: rgba(240,136,62,0.08);"
                " border: 1px solid rgba(240,136,62,0.30);"
                " border-radius: 10px; }"
                "QLabel { background: transparent; border: none;"
                " color: #C9D1D9; font-size: 11px; }"
            )
            tips_lay = QHBoxLayout(tips_card)
            tips_lay.setContentsMargins(12, 10, 12, 10)
            tips_lay.setSpacing(8)
            bulb = QToolButton()
            bulb.setEnabled(False)
            bulb.setIcon(TablerIcon("tabler_alert_triangle.svg").icon(color=QColor("#F0883E")))
            bulb.setIconSize(QSize(16, 16))
            bulb.setStyleSheet("background: transparent; border: none;")
            tips_text_col = QVBoxLayout()
            tips_text_col.setSpacing(1)
            tips_title = QLabel("Tips")
            tips_title.setStyleSheet(
                "color: #F0883E; font-size: 11px; font-weight: 700;"
            )
            tips_body = QLabel(
                "Standard policy is 3 h weekday and 8 h Saturday. "
                "The OT gauge fills to 100% when you hit the max."
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

    dlg = _OTSheet(host)
    if dlg.exec():
        return {
            "ot_max_weekday_hours": dlg.wk_combo.currentIndex() + 1,
            "ot_max_saturday_hours": dlg.sat_combo.currentIndex() + 1,
        }
    return None
