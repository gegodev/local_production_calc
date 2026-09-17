# -*- coding: utf-8 -*-
"""Modal dialog to edit an existing downtime row.

Uses the app's Fluent MessageBoxBase look (dark card, tinted header,
matched buttons). Falls back to a plain QDialog if qfluentwidgets is
unavailable so the editor still works.
"""
from __future__ import annotations

from .theme_palette import apply_fluent_modal_palette
from PySide6.QtCore import Qt, QTime
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTimeEdit, QComboBox, QLineEdit, QPlainTextEdit, QToolButton,
    QDialogButtonBox,
)


def _fluent_or_plain(parent, **kwargs):
    """Return (dialog_object, is_fluent). Fluent if qfluentwidgets imports."""
    try:
        from qfluentwidgets import MessageBoxBase
        from .tabler_icons import TablerIcon
        from PySide6.QtCore import QSize
    except Exception:
        return _PlainDialog(parent=parent, **kwargs), False
    return _FluentSheet(parent=parent, **kwargs), True


class DowntimeEditDialog:
    """Factory-like wrapper. Construct + .exec() like a dialog."""

    def __init__(self, *, start: str, end: str, reason: str,
                 detalle: str, case_id: str,
                 reasons: list[str], parent=None):
        self._impl, _ = _fluent_or_plain(
            parent,
            start=start, end=end, reason=reason, detalle=detalle,
            case_id=case_id, reasons=reasons,
        )

    def exec(self) -> int:
        return self._impl.exec()

    def result_values(self) -> dict:
        return self._impl.result_values()


def _build_form_widgets(host, *, start, end, reason, detalle, case_id, reasons):
    """Build the editable fields + return them as a dict for later read-back."""
    try:
        from .widgets import _icon_url as _icu
        chev = _icu("tabler_chevron_down.svg")
    except Exception:
        chev = ""

    input_css = (
        "QLineEdit, QPlainTextEdit, QComboBox, QTimeEdit {"
        "  background: #161B22; border: 1px solid #30363D;"
        "  border-radius: 6px; padding: 4px 22px 4px 8px; color: #E6EDF3;"
        "  font-size: 12px; min-height: 28px; }"
        "QTimeEdit::up-button, QTimeEdit::down-button {"
        "  width: 0; border: none; }"
        "QComboBox::drop-down { subcontrol-origin: padding;"
        "  subcontrol-position: right center; width: 22px; border: none; }"
        f"QComboBox::down-arrow {{ image: url({chev});"
        "  width: 12px; height: 12px; }"
        "QPlainTextEdit { min-height: 80px; }"
    )

    start_edit = QTimeEdit()
    start_edit.setDisplayFormat("HH:mm")
    start_edit.setTime(QTime.fromString(start, "HH:mm"))

    end_edit = QTimeEdit()
    end_edit.setDisplayFormat("HH:mm")
    end_edit.setTime(QTime.fromString(end, "HH:mm"))
    end_edit.setMinimumTime(start_edit.time())

    def _on_start(t: QTime):
        end_edit.setMinimumTime(t)
        if end_edit.time() < t:
            end_edit.setTime(t)

    def _on_end(t: QTime):
        s = start_edit.time()
        if t < s:
            end_edit.blockSignals(True)
            end_edit.setTime(s)
            end_edit.blockSignals(False)

    start_edit.timeChanged.connect(_on_start)
    end_edit.timeChanged.connect(_on_end)

    reason_combo = QComboBox()
    reason_combo.addItems(reasons)
    idx = reason_combo.findText(reason)
    if idx >= 0:
        reason_combo.setCurrentIndex(idx)

    case_id_edit = QLineEdit(case_id or "")
    case_id_edit.setPlaceholderText("Required for Multitreatment")

    def _on_reason(r: str):
        needs = (r or "").strip().lower() == "multitreatment"
        case_id_edit.setEnabled(needs)
        if not needs:
            case_id_edit.clear()

    reason_combo.currentTextChanged.connect(_on_reason)
    _on_reason(reason_combo.currentText())

    detail_edit = QPlainTextEdit(detalle or "")
    detail_edit.setPlaceholderText("Additional context (optional)")
    detail_edit.setMinimumHeight(80)

    for w in (start_edit, end_edit, reason_combo, case_id_edit, detail_edit):
        w.setStyleSheet(input_css)

    return {
        "start_edit": start_edit,
        "end_edit": end_edit,
        "reason_combo": reason_combo,
        "case_id_edit": case_id_edit,
        "detail_edit": detail_edit,
    }


def _read_values(fields) -> dict:
    s = fields["start_edit"].time()
    e = fields["end_edit"].time()
    return {
        "start": s.toString("HH:mm"),
        "end": e.toString("HH:mm"),
        "start_mins": s.hour() * 60 + s.minute(),
        "end_mins": e.hour() * 60 + e.minute(),
        "reason": fields["reason_combo"].currentText(),
        "case_id": fields["case_id_edit"].text().strip(),
        "detail": fields["detail_edit"].toPlainText().strip(),
    }


def _add_form_row(layout: QVBoxLayout, label_text: str, widget):
    """Stacked row: small label + input below."""
    lbl = QLabel(label_text)
    lbl.setStyleSheet(
        "color: #C9D1D9; font-size: 11px; font-weight: 700;"
        " background: transparent;"
    )
    layout.addWidget(lbl)
    layout.addWidget(widget)


try:
    from qfluentwidgets import MessageBoxBase

    class _FluentSheet(MessageBoxBase):
        """Fluent-styled editor matching the app's other modals."""

        def __init__(self, *, parent, start, end, reason, detalle,
                     case_id, reasons):
            super().__init__(parent.window() if parent is not None else None)
            try:
                self.setMaskColor(QColor(0, 0, 0, 170))
            except Exception:
                pass
            self.widget.setObjectName("dtEditCard")
            apply_fluent_modal_palette(self, "dtEditCard")
            self.viewLayout.setContentsMargins(22, 18, 22, 12)
            self.viewLayout.setSpacing(10)

            try:
                from .tabler_icons import TablerIcon
                from PySide6.QtCore import QSize
                hdr = QHBoxLayout(); hdr.setSpacing(12)
                ic = QToolButton(); ic.setEnabled(False)
                ic.setIcon(TablerIcon("tabler_clock.svg").icon(color=QColor("#F0883E")))
                ic.setIconSize(QSize(22, 22))
                ic.setStyleSheet(
                    "background: rgba(240,136,62,0.14); border: none;"
                    " border-radius: 10px; padding: 6px;"
                )
                tc = QVBoxLayout(); tc.setSpacing(2)
                t = QLabel("Edit downtime")
                t.setStyleSheet(
                    "color: #E6EDF3; font-size: 15px; font-weight: 700;"
                    " background: transparent;"
                )
                s = QLabel("Update the start/end, reason, or notes for this entry.")
                s.setStyleSheet(
                    "color: #8B949E; font-size: 11px; background: transparent;"
                )
                tc.addWidget(t); tc.addWidget(s)
                hdr.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
                hdr.addLayout(tc, 1)
                self.viewLayout.addLayout(hdr)
            except Exception:
                pass

            self._fields = _build_form_widgets(
                self, start=start, end=end, reason=reason,
                detalle=detalle, case_id=case_id, reasons=reasons,
            )

            row_times = QHBoxLayout(); row_times.setSpacing(10)
            col_start = QVBoxLayout(); col_start.setSpacing(2)
            _add_form_row(col_start, "Start", self._fields["start_edit"])
            col_end = QVBoxLayout(); col_end.setSpacing(2)
            _add_form_row(col_end, "End", self._fields["end_edit"])
            row_times.addLayout(col_start, 1)
            row_times.addLayout(col_end, 1)
            self.viewLayout.addLayout(row_times)

            _add_form_row(self.viewLayout, "Reason", self._fields["reason_combo"])
            _add_form_row(self.viewLayout, "Case ID", self._fields["case_id_edit"])
            _add_form_row(self.viewLayout, "Detail", self._fields["detail_edit"])

            self.widget.setMinimumWidth(460)

            self.cancelButton.setText("Cancel")
            self.cancelButton.setFixedWidth(120)
            self.cancelButton.setStyleSheet(
                "QPushButton { background: transparent;"
                "  border: 1px solid #30363D; color: #E6EDF3;"
                "  border-radius: 6px; padding: 8px 22px;"
                "  font-weight: 700; font-size: 12px; }"
                "QPushButton:hover { background: rgba(255,255,255,0.05); }"
            )
            self.yesButton.setText("   Save")
            self.yesButton.setFixedWidth(140)
            self.yesButton.setStyleSheet(
                "QPushButton { background: #1e63e4; border: 1px solid #1e63e4;"
                "  color: white; border-radius: 6px; padding: 8px 22px;"
                "  font-weight: 700; font-size: 12px; }"
                "QPushButton:hover { background: #2a73f3; }"
            )
            try:
                from .tabler_icons import TablerIcon
                from PySide6.QtCore import QSize
                self.yesButton.setIcon(
                    TablerIcon("tabler_device_floppy.svg").icon(color=QColor("#FFFFFF"))
                )
                self.yesButton.setIconSize(QSize(14, 14))
            except Exception:
                pass

        def result_values(self) -> dict:
            return _read_values(self._fields)
except Exception:
    _FluentSheet = None  # noqa


class _PlainDialog(QDialog):
    """Vanilla QDialog fallback when qfluentwidgets is missing."""

    def __init__(self, *, parent, start, end, reason, detalle,
                 case_id, reasons):
        super().__init__(parent)
        self.setWindowTitle("Edit Downtime")
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        self._fields = _build_form_widgets(
            self, start=start, end=end, reason=reason, detalle=detalle,
            case_id=case_id, reasons=reasons,
        )
        for key in ("start_edit", "end_edit", "reason_combo",
                    "case_id_edit", "detail_edit"):
            layout.addWidget(QLabel(key.replace("_edit", "").replace("_combo", "").title()))
            layout.addWidget(self._fields[key])

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def result_values(self) -> dict:
        return _read_values(self._fields)
