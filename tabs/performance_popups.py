# -*- coding: utf-8 -*-
"""
End-of-day performance popup dialogs.

Two dialogs:
  1. SuccessPopup    — congratulations + daily summary (can be closed freely)
  2. JustificationPopup — encouragement + daily summary + required text input
                          (cannot be closed until justification is submitted)
"""
from .theme_palette import apply_fluent_modal_palette

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit,
    QPushButton, QMessageBox, QFrame,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from . import font_scale


def _summary_text(metrics: dict, fecha: str) -> str:
    """Build a human-readable daily summary from metrics dict."""
    lines = []
    lines.append(f"Date: {fecha}")
    lines.append("")
    lines.append(f"Production:  {metrics['production_pct']:.2f}%")
    if metrics["downtime_pct"] > 0:
        lines.append(f"   Cases:        {metrics['cases_pct']:.2f}%")
        lines.append(f"   Downtime:  {metrics['downtime_pct']:.2f}%")
    target = metrics.get("ue_target")
    target_suffix = f"  (target: {target:.2f})" if target else ""
    lines.append(f"Equivalent Units:  {metrics['equivalent_units']:.2f}{target_suffix}")
    if metrics["downtime_ue"] > 0:
        lines.append(f"   Cases:        {metrics['cases_ue']:.2f}")
        lines.append(f"   Downtime:  {metrics['downtime_ue']:.2f}")
    lines.append(f"Total Cases:  {metrics['total_cases']}")
    if metrics["total_downtime_min"] > 0:
        lines.append(f"Total Downtime:  {metrics['total_downtime_min']:.0f} min")
    return "\n".join(lines)


def _build_success_modal(metrics: dict, fecha: str, parent=None):
    """Build the Fluent-styled success modal (MessageBoxBase). Returns
    the dialog instance, ready to .exec()."""
    from qfluentwidgets import MessageBoxBase
    from PySide6.QtCore import (
        QSize, QPropertyAnimation, QEasingCurve, Property,
    )
    from PySide6.QtGui import QColor, QPainter
    from PySide6.QtWidgets import (
        QWidget as _QW, QToolButton as _QTB, QFrame as _QF,
    )
    from .tabler_icons import TablerIcon

    class _Sheet(MessageBoxBase):
        def __init__(_s, h):
            super().__init__(h.window() if h is not None else None)
            try:
                _s.setMaskColor(QColor(0, 0, 0, 170))
            except Exception:
                pass
            _s.widget.setObjectName("successCard")
            apply_fluent_modal_palette(_s, "successCard")
            _s.viewLayout.setContentsMargins(0, 8, 0, 8)
            _s.viewLayout.setSpacing(0)

            def _div():
                d = _QF()
                d.setFixedHeight(1)
                d.setStyleSheet("background: #21262D; border: none;")
                return d

            # Header: trophy/check icon + title + close.
            hdr_wrap = _QW()
            hl = QVBoxLayout(hdr_wrap)
            hl.setContentsMargins(22, 14, 22, 12)
            hl.setSpacing(6)
            hdr = QHBoxLayout(); hdr.setSpacing(12)
            ic = _QTB()
            ic.setEnabled(False)
            ic.setIcon(TablerIcon("tabler_circle_check.svg").icon(color=QColor("#3FB950")))
            ic.setIconSize(QSize(24, 24))
            ic.setStyleSheet(
                "background: rgba(63,185,80,0.14); border: none;"
                " border-radius: 10px; padding: 7px;"
            )
            tc = QVBoxLayout(); tc.setSpacing(2)
            t = QLabel("Daily target reached")
            t.setStyleSheet(
                "color: #3FB950; font-size: 16px; font-weight: 800;"
                " background: transparent;"
            )
            sub = QLabel("Great work — you hit today's goal. Keep it up!")
            sub.setStyleSheet(
                "color: #C9D1D9; font-size: 12px; background: transparent;"
            )
            sub.setWordWrap(True)
            tc.addWidget(t); tc.addWidget(sub)

            class _SpinX(_QTB):
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

            # Body — KPI tiles + stat rows.
            body = _QW()
            bl = QVBoxLayout(body)
            bl.setContentsMargins(22, 16, 22, 16)
            bl.setSpacing(10)

            date_lbl = QLabel(f"Summary · {fecha}")
            date_lbl.setStyleSheet(
                "color: #8B949E; font-size: 11px; font-weight: 700;"
                " letter-spacing: 0.5px; background: transparent;"
            )
            bl.addWidget(date_lbl)

            def _kpi_tile(label, value, accent):
                w = _QF()
                w.setObjectName("kpiTile")
                w.setStyleSheet(
                    "#kpiTile { background: #161B22; border: none;"
                    " border-radius: 8px; }"
                    "#kpiTile QLabel { background: transparent; border: none; }"
                )
                v = QVBoxLayout(w); v.setContentsMargins(10, 8, 10, 8); v.setSpacing(2)
                vl = QLabel(value)
                vl.setStyleSheet(
                    f"color: {accent}; font-size: 16px; font-weight: 800;"
                    " background: transparent; border: none;"
                )
                ll = QLabel(label)
                ll.setStyleSheet(
                    "color: #8B949E; font-size: 10px; font-weight: 600;"
                    " background: transparent; border: none;"
                )
                v.addWidget(vl); v.addWidget(ll)
                return w

            tiles = QHBoxLayout(); tiles.setSpacing(8)
            ue_t = metrics.get("ue_target") or 0.0
            tiles.addWidget(_kpi_tile(
                "Production",
                f"{metrics['production_pct']:.1f}%",
                "#58A6FF",
            ), 1)
            tiles.addWidget(_kpi_tile(
                "Equivalent Units",
                f"{metrics['equivalent_units']:.2f}",
                "#A371F7",
            ), 1)
            tiles.addWidget(_kpi_tile(
                "UE Target",
                f"{ue_t:.2f}" if ue_t else "—",
                "#F0883E",
            ), 1)
            tiles.addWidget(_kpi_tile(
                "Cases",
                str(metrics.get("total_cases", 0)),
                "#3FB950",
            ), 1)
            bl.addLayout(tiles)

            # Extra detail line — downtime if any.
            if metrics.get("total_downtime_min", 0):
                dt_lbl = QLabel(
                    f"Includes {int(metrics['total_downtime_min'])} min downtime "
                    f"({metrics['downtime_pct']:.1f}% of base)."
                )
                dt_lbl.setStyleSheet(
                    "color: #8B949E; font-size: 11px; background: transparent;"
                )
                bl.addWidget(dt_lbl)

            # Tips card — green tinted congratulatory note.
            tips_card = _QF()
            tips_card.setStyleSheet(
                "QFrame { background: rgba(63,185,80,0.08);"
                " border: 1px solid rgba(63,185,80,0.30);"
                " border-radius: 10px; }"
                "QLabel { background: transparent; border: none; color: #C9D1D9;"
                " font-size: 11px; }"
            )
            tl = QHBoxLayout(tips_card)
            tl.setContentsMargins(12, 10, 12, 10); tl.setSpacing(8)
            bulb = _QTB()
            bulb.setEnabled(False)
            bulb.setIcon(TablerIcon("tabler_bulb.svg").icon(color=QColor("#3FB950")))
            bulb.setIconSize(QSize(16, 16))
            bulb.setStyleSheet("background: transparent; border: none;")
            tc2 = QVBoxLayout(); tc2.setSpacing(1)
            tt = QLabel("Nice work")
            tt.setStyleSheet("color: #3FB950; font-size: 11px; font-weight: 700;")
            tb = QLabel(
                "You met today's daily target. Stay consistent — small daily "
                "wins compound into great weeks."
            )
            tb.setWordWrap(True)
            tc2.addWidget(tt); tc2.addWidget(tb)
            tl.addWidget(bulb, 0, Qt.AlignTop)
            tl.addLayout(tc2, 1)
            bl.addWidget(tips_card)

            _s.viewLayout.addWidget(body)
            _s.viewLayout.addWidget(_div())
            _s.widget.setMinimumWidth(560)

            # Footer button.
            _s.buttonLayout.removeWidget(_s.yesButton)
            _s.buttonLayout.removeWidget(_s.cancelButton)
            _s.yesButton.hide()
            _s.buttonLayout.addStretch(1)
            _s.cancelButton.setText("Close")
            _s.cancelButton.setFixedWidth(120)
            _s.cancelButton.setStyleSheet(
                "QPushButton { background: #3FB950; border: 1px solid #3FB950;"
                "  color: white; border-radius: 6px; padding: 8px 22px;"
                "  font-weight: 700; font-size: 12px; }"
                "QPushButton:hover { background: #4FC95F; }"
            )
            _s.buttonLayout.addWidget(_s.cancelButton, 0, Qt.AlignVCenter)

    return _Sheet(parent)


class SuccessPopup:
    """Compat wrapper — keep the legacy ``SuccessPopup(metrics, fecha, parent).exec()``
    call shape that callers use. Falls back to the original QDialog if
    qfluentwidgets isn't available."""

    def __new__(cls, metrics: dict, fecha: str, parent=None):
        try:
            return _build_success_modal(metrics, fecha, parent)
        except Exception:
            return _LegacySuccessPopup(metrics, fecha, parent)


class _LegacySuccessPopup(QDialog):
    def __init__(self, metrics: dict, fecha: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Daily Target Reached!")
        self.setMinimumWidth(420)
        self.setWindowFlags(
            self.windowFlags()
            & ~Qt.WindowType.WindowContextHelpButtonHint
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        header = QLabel("🎉  Congratulations!")
        header.setFont(QFont("Segoe UI", font_scale.scale_pt(18), QFont.Weight.Bold))
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(header)
        msg = QLabel("You've reached your daily production target.")
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(msg)
        layout.addWidget(QLabel(_summary_text(metrics, fecha)))
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignCenter)


class JustificationPopup(QDialog):
    """Shown when the designer does NOT meet the target.
    Cannot be closed until a justification is typed and submitted."""

    def __init__(self, metrics: dict, fecha: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("End of Day — Justification Required")
        self.setMinimumWidth(480)
        self.setMinimumHeight(440)
        self.justification_text = ""
        # Block closing without submitting
        self.setWindowFlags(
            self.windowFlags()
            & ~Qt.WindowType.WindowContextHelpButtonHint
            & ~Qt.WindowType.WindowCloseButtonHint
        )
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        # Encouragement header
        header = QLabel("💪  Great effort today!")
        header.setFont(QFont("Segoe UI", font_scale.scale_pt(16), QFont.Weight.Bold))
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(header)

        msg = QLabel(
            "You didn't quite reach today's target — but that's okay!\n"
            "Stay consistent and you'll get there. Don't lose the rhythm."
        )
        msg.setFont(QFont("Segoe UI", font_scale.scale_pt(11)))
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        msg.setWordWrap(True)
        layout.addWidget(msg)

        # Summary frame
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        frame.setStyleSheet("""
            QFrame {
                background-color: #B71C1C;
                border-radius: 8px;
                padding: 12px;
            }
            QLabel {
                color: #FFCDD2;
                font-family: Consolas, monospace;
                font-size: 12px;
            }
        """)
        frame_layout = QVBoxLayout(frame)
        summary_lbl = QLabel(_summary_text(metrics, fecha))
        summary_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        frame_layout.addWidget(summary_lbl)
        layout.addWidget(frame)

        # Justification input
        input_label = QLabel("Please explain why the target was not met today:")
        input_label.setFont(QFont("Segoe UI", font_scale.scale_pt(10), QFont.Weight.Bold))
        layout.addWidget(input_label)

        self._text_edit = QTextEdit()
        self._text_edit.setPlaceholderText(
            "Write your justification here... (minimum 10 characters)"
        )
        self._text_edit.setMinimumHeight(80)
        layout.addWidget(self._text_edit)

        # Submit button
        self._submit_btn = QPushButton("Submit Justification")
        self._submit_btn.setFixedWidth(200)
        self._submit_btn.setStyleSheet("""
            QPushButton {
                background-color: #E65100;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 10px 20px;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover { background-color: #BF360C; }
        """)
        self._submit_btn.clicked.connect(self._on_submit)
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_layout.addWidget(self._submit_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

    def _on_submit(self):
        text = self._text_edit.toPlainText().strip()
        if len(text) < 10:
            QMessageBox.warning(
                self, "Too short",
                "Please write at least 10 characters explaining the situation."
            )
            return
        self.justification_text = text
        self.accept()

    def reject(self):
        """Override reject to prevent closing with Escape or X button."""
        # Do nothing — force the user to submit
        pass

    def closeEvent(self, event):
        """Prevent closing the dialog via Alt+F4 or window manager."""
        event.ignore()
