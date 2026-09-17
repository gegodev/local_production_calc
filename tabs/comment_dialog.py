"""Fluent comment dialog used by Register (Regular + OT modes).

Extracted from tab_register.py so that 4000-line file stays focused on
the actual register flow. Public surface:

    open_comment_dialog(host, target_text_edit, *, read_only) -> bool

`host` is the QWidget owning the modal (typically RegisterTab); the
returned bool is True iff the user clicked Save and the editor text was
written back to `target_text_edit`.
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


_MAX_CHARS = 800


def open_comment_dialog(host, target_text_edit, *, read_only: bool) -> bool:
    """Open the Fluent comment sheet anchored to ``host.window()``.

    Reads/writes ``target_text_edit`` (a QTextEdit kept as the comment
    store). Returns True if the user saved (only meaningful when
    ``read_only`` is False).
    """
    from qfluentwidgets import MessageBoxBase, TextEdit as FTextEdit
    from .tabler_icons import TablerIcon

    initial = target_text_edit.toPlainText()

    class _CommentSheet(MessageBoxBase):
        def __init__(_self, h, init: str, ro: bool):
            super().__init__(h.window())
            try:
                _self.setMaskColor(QColor(0, 0, 0, 170))
            except Exception:
                pass

            _self.widget.setObjectName("commentCard")
            apply_fluent_modal_palette(_self, "commentCard")

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

            # Header (message icon + title + close X).
            header_row = QHBoxLayout()
            header_row.setSpacing(10)
            header_row.setContentsMargins(0, 0, 0, 0)
            icon_btn = QToolButton()
            icon_btn.setEnabled(False)
            icon_btn.setIcon(TablerIcon("tabler_message_circle.svg").icon(color=QColor("#388BFD")))
            icon_btn.setIconSize(QSize(20, 20))
            icon_btn.setStyleSheet(
                "background: rgba(56,139,253,0.12); border: none;"
                " border-radius: 8px; padding: 6px;"
            )
            title_col = QVBoxLayout()
            title_col.setSpacing(2)
            title_lbl = QLabel(
                "Case comment" if ro else
                ("Edit comment" if init.strip() else "Add comment")
            )
            title_lbl.setStyleSheet(
                "color: #E6EDF3; font-size: 15px; font-weight: 700;"
                " background: transparent;"
            )
            sub_lbl = QLabel(
                "Read the comment attached to this case." if ro else
                "Write a note for this case — visible to anyone reviewing it."
            )
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

            # Body: textarea + counter + tips.
            body_w = QWidget()
            body_lay = QVBoxLayout(body_w)
            body_lay.setContentsMargins(22, 16, 22, 16)
            body_lay.setSpacing(8)
            detail_lbl = QLabel(
                "Comment" if ro else
                "Comment <span style='color:#F85149;'>*</span>"
            )
            detail_lbl.setTextFormat(Qt.TextFormat.RichText)
            detail_lbl.setStyleSheet(
                "color: #C9D1D9; font-size: 13px; font-weight: 700;"
                " background: transparent;"
            )
            body_lay.addWidget(detail_lbl)
            helper = QLabel(
                "What you write here stays with the case for future review."
            )
            helper.setStyleSheet(
                "color: #8B949E; font-size: 12px; background: transparent;"
            )
            body_lay.addWidget(helper)

            _self.editor = FTextEdit()
            _self.editor.setPlainText(init)
            _self.editor.setReadOnly(ro)
            _self.editor.setMinimumHeight(140)
            if not ro:
                _self.editor.setPlaceholderText("Type your comment…")
            body_lay.addWidget(_self.editor)

            _self.counter = QLabel(f"{len(init)} / {_MAX_CHARS}")
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
                    _self.editor.setPlainText(
                        _self.editor.toPlainText()[:_MAX_CHARS]
                    )
                    _self.editor.blockSignals(False)
                    n = _MAX_CHARS
                color = "#F85149" if n >= _MAX_CHARS else "#6E7681"
                _self.counter.setText(f"{n} / {_MAX_CHARS}")
                _self.counter.setStyleSheet(
                    f"color: {color}; font-size: 10px; background: transparent;"
                )
            if not ro:
                _self.editor.textChanged.connect(_on_text)

            if not ro:
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
                    "Mention anything that helps the next reader: doctor "
                    "feedback, file issues, special handling, etc."
                )
                tips_body.setWordWrap(True)
                tips_text_col.addWidget(tips_title)
                tips_text_col.addWidget(tips_body)
                tips_lay.addWidget(bulb, 0, Qt.AlignTop)
                tips_lay.addLayout(tips_text_col, 1)
                body_lay.addWidget(tips_card)

            _self.viewLayout.addWidget(body_w)
            _self.viewLayout.addWidget(_div())
            _self.widget.setMinimumWidth(500)

            # Buttons.
            _self.buttonLayout.removeWidget(_self.yesButton)
            _self.buttonLayout.removeWidget(_self.cancelButton)
            _self.buttonLayout.addStretch(1)
            if ro:
                _self.hideYesButton()
                _self.cancelButton.setText("Close")
                _self.cancelButton.setFixedWidth(120)
                _self.cancelButton.setStyleSheet(
                    "QPushButton { background: transparent; border: 1px solid #30363D;"
                    "  color: #E6EDF3; border-radius: 10px; padding: 8px 22px;"
                    "  font-weight: 700; font-size: 12px; }"
                    "QPushButton:hover { background: rgba(255,255,255,0.05);"
                    "  border-color: #58606A; }"
                )
                _self.buttonLayout.addWidget(_self.cancelButton, 0, Qt.AlignVCenter)
            else:
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

    dlg = _CommentSheet(host, initial, read_only)
    if dlg.exec():
        if not read_only:
            target_text_edit.setPlainText(dlg.editor.toPlainText().strip())
            return True
    return False
