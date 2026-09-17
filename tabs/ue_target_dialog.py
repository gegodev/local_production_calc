# -*- coding: utf-8 -*-
"""Dialog to manage the UE daily target by effective date.

Each row defines a period: starting on `start_date`, the daily UE target is
`value`. The newest start_date that is <= a given date wins. Users can add
rows for any frequency (weekly, monthly, quarterly, ad hoc) — the schema
doesn't care, it just resolves by date.

Styled to match the Add comment / Downtime detail modals.
"""
from __future__ import annotations

from .theme_palette import apply_fluent_modal_palette
from PySide6.QtCore import (
    Qt, QDate, QSize, QPropertyAnimation, QEasingCurve, Property,
)
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QDateEdit, QDoubleSpinBox,
    QFrame, QWidget, QToolButton, QSizePolicy,
)

from sync.daily_performance import (
    UE_TARGET, list_ue_target_periods, set_ue_target_periods,
)


def _build_ue_target_dialog(parent=None):
    """Return a MessageBoxBase-styled UE Target dialog or None on failure.

    Kept as a factory so the caller can fall back to the legacy dialog
    if qfluentwidgets is unavailable.
    """
    from qfluentwidgets import MessageBoxBase
    from tabs.tabler_icons import TablerIcon

    class UETargetDialog(MessageBoxBase):
        def __init__(self, host):
            super().__init__(host.window() if host is not None else None)
            try:
                self.setMaskColor(QColor(0, 0, 0, 170))
            except Exception:
                pass

            self.widget.setObjectName("ueTargetCard")
            apply_fluent_modal_palette(self, "ueTargetCard")

            self.viewLayout.setContentsMargins(0, 8, 0, 8)
            self.viewLayout.setSpacing(0)

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

            # ── Header (target icon + title + close) ──
            header_row = QHBoxLayout()
            header_row.setSpacing(10)
            icon_btn = QToolButton()
            icon_btn.setEnabled(False)
            icon_btn.setIcon(TablerIcon("tabler_target.svg").icon(color=QColor("#388BFD")))
            icon_btn.setIconSize(QSize(20, 20))
            icon_btn.setStyleSheet(
                "background: rgba(56,139,253,0.12); border: none;"
                " border-radius: 8px; padding: 6px;"
            )
            title_col = QVBoxLayout()
            title_col.setSpacing(2)
            title_lbl = QLabel("Daily UE Target")
            title_lbl.setStyleSheet(
                "color: #E6EDF3; font-size: 15px; font-weight: 700;"
                " background: transparent;"
            )
            sub_lbl = QLabel(
                "Set the daily UE target by effective date. The most recent "
                "start date on or before any given day wins."
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
                    p = QPainter(s)
                    p.setRenderHint(QPainter.Antialiasing)
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
            close_btn.clicked.connect(self.reject)

            header_row.addWidget(icon_btn, 0, Qt.AlignTop)
            header_row.addLayout(title_col, 1)
            header_row.addWidget(close_btn, 0, Qt.AlignTop)
            self.viewLayout.addWidget(_wrap(header_row))
            self.viewLayout.addWidget(_div())

            # ── Body ──
            body_w = QWidget()
            body_lay = QVBoxLayout(body_w)
            body_lay.setContentsMargins(22, 16, 22, 16)
            body_lay.setSpacing(10)

            # Editor row — label-on-top + input below, with a help icon
            # next to each label that opens a mini explainer popup on click.
            def _label_with_info(text: str, tip_title: str, tip_body: str):
                row = QHBoxLayout()
                row.setContentsMargins(0, 0, 0, 0)
                row.setSpacing(4)
                lbl = QLabel(text)
                lbl.setStyleSheet(
                    "color: #C9D1D9; font-size: 11px; font-weight: 700;"
                    " background: transparent;"
                )
                info = QToolButton()
                info.setIcon(TablerIcon("tabler_help.svg").icon(color=QColor("#6E7681")))
                info.setIconSize(QSize(14, 14))
                info.setCursor(Qt.PointingHandCursor)
                info.setFixedSize(20, 20)
                info.setStyleSheet(
                    "QToolButton { background: transparent; border: none;"
                    "  border-radius: 10px; padding: 0; }"
                    "QToolButton:hover { background: rgba(255,255,255,0.06); }"
                )
                info.clicked.connect(
                    lambda _=False, b=info, t=tip_title, bd=tip_body:
                        self._show_help_popup(b, t, bd)
                )
                row.addWidget(lbl)
                row.addWidget(info)
                row.addStretch()
                return row

            editor = QHBoxLayout()
            editor.setSpacing(10)

            # Effective from column.
            eff_col = QVBoxLayout()
            eff_col.setSpacing(4)
            eff_col.addLayout(_label_with_info(
                "Effective from",
                "What is 'Effective from'?",
                "It's the start date for this UE target row. The most recent "
                "start date on or before any given day decides which target "
                "applies that day."
            ))
            eff_lbl = QLabel("Effective from")  # kept as reference (hidden)
            eff_lbl.hide()

            class _DateWithWeek(QDateEdit):
                """QDateEdit that prepends the ISO week number to the value."""
                def textFromDateTime(self, dt):
                    s = dt.toString("yyyy-MM-dd")
                    wk = dt.date().weekNumber()[0]
                    return f"{s}    W{wk:02d}"
                def dateTimeFromText(self, text: str):
                    # Drop everything after the date portion ("YYYY-MM-DD").
                    base = (text or "").split()[0] if text else ""
                    from PySide6.QtCore import QDateTime, QDate as _QD
                    qd = _QD.fromString(base, "yyyy-MM-dd")
                    return QDateTime(qd if qd.isValid() else _QD.currentDate())

            self.date_edit = _DateWithWeek()
            self.date_edit.setCalendarPopup(True)
            # Use the custom textFromDateTime via an empty format.
            self.date_edit.setDate(QDate.currentDate())
            self.date_edit.setFixedHeight(36)
            try:
                from tabs.widgets import _icon_url as _icu_d
                _chev = _icu_d("tabler_chevron_down.svg")
            except Exception:
                _chev = ""
            self.date_edit.setStyleSheet(
                "QDateEdit { background: #161B22; border: 1px solid #30363D;"
                "  border-radius: 6px; padding: 4px 26px 4px 8px; color: #E6EDF3; }"
                "QDateEdit::drop-down { subcontrol-origin: padding;"
                "  subcontrol-position: right center; width: 22px; border: none; }"
                f"QDateEdit::down-arrow {{ image: url({_chev});"
                "  width: 12px; height: 12px; }"
            )
            # Leading calendar icon inside the date input.
            try:
                from PySide6.QtGui import QAction
                from PySide6.QtWidgets import QLineEdit
                _cal_icon = TablerIcon("tabler_calendar.svg").icon(color=QColor("#8B949E"))
                _le = self.date_edit.lineEdit() if hasattr(self.date_edit, "lineEdit") else None
                if _le is not None:
                    _act = QAction(_cal_icon, "", _le)
                    _le.addAction(_act, QLineEdit.ActionPosition.LeadingPosition)
            except Exception:
                pass
            # Strip the red weekend formatting + match the app calendar look.
            try:
                from PySide6.QtGui import QTextCharFormat, QBrush
                cal = self.date_edit.calendarWidget()
                if cal is not None:
                    neutral = QTextCharFormat()
                    neutral.setForeground(QBrush(QColor("#E6EDF3")))
                    cal.setWeekdayTextFormat(Qt.DayOfWeek.Saturday, neutral)
                    cal.setWeekdayTextFormat(Qt.DayOfWeek.Sunday, neutral)
                    cal.setVerticalHeaderFormat(cal.VerticalHeaderFormat.NoVerticalHeader)
                    cal.setHorizontalHeaderFormat(cal.HorizontalHeaderFormat.SingleLetterDayNames)
                    cal.setGridVisible(False)
                    cal.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
            except Exception:
                pass

            eff_col.addWidget(self.date_edit)

            # Target UE column.
            val_col = QVBoxLayout()
            val_col.setSpacing(4)
            val_col.addLayout(_label_with_info(
                "Target UE",
                "What is 'Target UE'?",
                "Daily Equivalent Units goal. The dashboards and progress "
                "bars compare your actual UE against this number."
            ))
            val_lbl = QLabel("Target UE")
            val_lbl.hide()
            self.value_edit = QDoubleSpinBox()
            self.value_edit.setRange(0.1, 999.0)
            self.value_edit.setDecimals(2)
            self.value_edit.setSingleStep(0.5)
            self.value_edit.setValue(UE_TARGET)
            self.value_edit.setFixedHeight(36)
            try:
                from tabs.widgets import _icon_url as _icu
                _up = _icu("tabler_chevron_up.svg")
                _dn = _icu("tabler_chevron_down.svg")
            except Exception:
                _up = _dn = ""
            self.value_edit.setStyleSheet(
                "QDoubleSpinBox { background: #161B22; border: 1px solid #30363D;"
                "  border-radius: 6px; padding: 4px 24px 4px 8px; color: #E6EDF3; }"
                "QDoubleSpinBox::up-button { subcontrol-origin: border;"
                "  subcontrol-position: top right; width: 18px; border: none;"
                "  background: transparent; }"
                "QDoubleSpinBox::down-button { subcontrol-origin: border;"
                "  subcontrol-position: bottom right; width: 18px; border: none;"
                "  background: transparent; }"
                f"QDoubleSpinBox::up-arrow {{ image: url({_up});"
                "  width: 10px; height: 10px; }"
                f"QDoubleSpinBox::down-arrow {{ image: url({_dn});"
                "  width: 10px; height: 10px; }"
                "QDoubleSpinBox::up-button:hover,"
                "QDoubleSpinBox::down-button:hover { background: rgba(255,255,255,0.05); }"
            )

            val_col.addWidget(self.value_edit)

            add_btn = QPushButton("  Add / Update")
            add_btn.setCursor(Qt.PointingHandCursor)
            add_btn.setFixedHeight(36)
            try:
                add_btn.setIcon(TablerIcon("tabler_plus.svg").icon(color=QColor("#FFFFFF")))
                add_btn.setIconSize(QSize(14, 14))
            except Exception:
                pass
            add_btn.setStyleSheet(
                "QPushButton { background: #1e63e4; border: 1px solid #1e63e4;"
                "  color: white; border-radius: 8px; padding: 4px 14px;"
                "  font-weight: 700; font-size: 11px; }"
                "QPushButton:hover { background: #2a73f3; }"
            )
            add_btn.clicked.connect(self._on_add)

            # Place columns + add-button on a single row, with the button
            # vertically centered to the inputs (not the labels).
            btn_col = QVBoxLayout()
            btn_col.setSpacing(4)
            btn_col.addSpacing(18)  # match label height above
            btn_col.addWidget(add_btn)

            editor.addLayout(eff_col, 2)
            editor.addLayout(val_col, 1)
            editor.addLayout(btn_col, 0)
            body_lay.addLayout(editor)

            # Periods table — 3 cols: date, value, per-row actions.
            self.table = QTableWidget(0, 3)
            self.table.setHorizontalHeaderLabels(["EFFECTIVE FROM", "UE TARGET", ""])
            self.table.verticalHeader().setVisible(False)
            self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
            self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            self.table.setMinimumHeight(180)
            self.table.setShowGrid(False)
            self.table.verticalHeader().setDefaultSectionSize(44)
            self.table.setMouseTracking(False)
            self.table.setStyleSheet("""
                QTableWidget {
                    border: 1px solid #21262D;
                    border-radius: 10px;
                    gridline-color: transparent;
                    outline: none;
                    selection-background-color: transparent;
                }
                QTableWidget::item { padding: 10px 8px; }
                QTableWidget::item:hover { background-color: transparent; }
                QTableWidget::item:selected { background-color: transparent; color: #E6EDF3; }
                QHeaderView { background: transparent; border: none; }
                QHeaderView::section {
                    background-color: #161B22;
                    color: #8B949E;
                    padding: 10px 8px;
                    border: none;
                    border-bottom: 1px solid #21262D;
                    font-weight: 700;
                    font-size: 10px;
                }
                QHeaderView::section:first { border-top-left-radius: 10px; }
                QHeaderView::section:last  { border-top-right-radius: 10px; }
            """)
            self.table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
            h = self.table.horizontalHeader()
            h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            h.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            h.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(2, 120)
            body_lay.addWidget(self.table)

            # "How it works" tip card.
            tip_card = QFrame()
            tip_card.setStyleSheet(
                "QFrame { background: rgba(56,139,253,0.08);"
                " border: 1px solid rgba(56,139,253,0.30);"
                " border-radius: 10px; }"
                "QLabel { background: transparent; border: none; color: #C9D1D9;"
                " font-size: 11px; }"
            )
            tip_lay = QHBoxLayout(tip_card)
            tip_lay.setContentsMargins(12, 10, 12, 10)
            tip_lay.setSpacing(8)
            tip_icon = QToolButton()
            tip_icon.setEnabled(False)
            tip_icon.setIcon(TablerIcon("tabler_info_circle.svg").icon(color=QColor("#388BFD")))
            tip_icon.setIconSize(QSize(16, 16))
            tip_icon.setStyleSheet("background: transparent; border: none;")
            tip_col = QVBoxLayout()
            tip_col.setSpacing(1)
            tip_title = QLabel("How it works")
            tip_title.setStyleSheet(
                "color: #58A6FF; font-size: 11px; font-weight: 700;"
            )
            tip_body = QLabel(
                "The most recent start date on or before any given day wins."
            )
            tip_body.setWordWrap(True)
            tip_col.addWidget(tip_title)
            tip_col.addWidget(tip_body)
            tip_lay.addWidget(tip_icon, 0, Qt.AlignTop)
            tip_lay.addLayout(tip_col, 1)
            body_lay.addWidget(tip_card)

            self.viewLayout.addWidget(body_w)
            self.viewLayout.addWidget(_div())

            self.widget.setMinimumWidth(560)
            self.widget.setMinimumHeight(520)

            # Button row — Cancel + Save on the right (delete is per-row now).
            self.buttonLayout.removeWidget(self.yesButton)
            self.buttonLayout.removeWidget(self.cancelButton)
            self.buttonLayout.addStretch(1)

            self.yesButton.setText("   Save")
            self.cancelButton.setText("Cancel")
            self.cancelButton.setFixedWidth(120)
            self.yesButton.setFixedWidth(120)
            self.cancelButton.setStyleSheet(
                "QPushButton { background: transparent; border: 1px solid #30363D;"
                "  color: #E6EDF3; border-radius: 10px; padding: 8px 22px;"
                "  font-weight: 700; font-size: 12px; }"
                "QPushButton:hover { background: rgba(255,255,255,0.05);"
                "  border-color: #58606A; }"
            )
            self.yesButton.setStyleSheet(
                "QPushButton { background: #1e63e4; border: 1px solid #1e63e4;"
                "  color: white; border-radius: 10px; padding: 8px 22px;"
                "  font-weight: 700; font-size: 12px; }"
                "QPushButton:hover { background: #2a73f3; border-color: #2a73f3; }"
                "QPushButton:pressed { background: #154fbb; }"
            )
            try:
                self.yesButton.setIcon(
                    TablerIcon("tabler_device_floppy.svg").icon(color=QColor("#FFFFFF"))
                )
                self.yesButton.setIconSize(QSize(14, 14))
            except Exception:
                pass
            self.buttonLayout.addWidget(self.cancelButton, 0, Qt.AlignVCenter)
            self.buttonLayout.addWidget(self.yesButton, 0, Qt.AlignVCenter)

            self.yesButton.clicked.disconnect()
            self.yesButton.clicked.connect(self._on_save)

            self._load()

        # ── data plumbing ──
        def _load(self):
            self.table.setRowCount(0)
            for start_date, value in list_ue_target_periods():
                self._append_row(start_date, value)

        def _append_row(self, start_date: str, value: float):
            row = self.table.rowCount()
            self.table.insertRow(row)
            # Compute ISO week number for clearer identification.
            try:
                from datetime import date as _date
                wk = _date.fromisoformat(start_date).isocalendar()[1]
                display = f"{start_date}    ────    Week {wk:02d}"
            except Exception:
                display = start_date
            d = QTableWidgetItem(display)
            d.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            # Stash the raw date for the save/sort path (item.text() now has decorations).
            d.setData(Qt.ItemDataRole.UserRole, start_date)
            v = QTableWidgetItem(f"{value:.2f}")
            v.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 0, d)
            self.table.setItem(row, 1, v)
            self.table.setCellWidget(row, 2, self._build_row_actions(row))

        def _build_row_actions(self, row_idx):
            from PySide6.QtWidgets import (
                QWidget as _W, QHBoxLayout as _H, QToolButton as _TB,
            )
            wrap = _W()
            wrap.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            lay = _H(wrap)
            lay.setContentsMargins(0, 0, 8, 0)
            lay.setSpacing(4)
            lay.addStretch()

            edit_btn = _TB()
            edit_btn.setAutoRaise(True)
            edit_btn.setCursor(Qt.PointingHandCursor)
            edit_btn.setFixedSize(30, 26)
            edit_btn.setIcon(TablerIcon("tabler_pencil.svg").icon(color=QColor("#388BFD")))
            edit_btn.setIconSize(QSize(16, 16))
            edit_btn.setToolTip("Edit row")
            edit_btn.setStyleSheet(
                "QToolButton { background: transparent; border: 0;"
                " border-radius: 6px; padding: 0; margin: 0; }"
                "QToolButton:hover { background: rgba(56,139,253,0.14); }"
            )
            edit_btn.clicked.connect(lambda _=False, b=edit_btn: self._edit_row_from_btn(b))

            del_btn = _TB()
            del_btn.setAutoRaise(True)
            del_btn.setCursor(Qt.PointingHandCursor)
            del_btn.setFixedSize(30, 26)
            del_btn.setIcon(TablerIcon("tabler_trash.svg").icon(color=QColor("#F85149")))
            del_btn.setIconSize(QSize(16, 16))
            del_btn.setToolTip("Delete row")
            del_btn.setStyleSheet(
                "QToolButton { background: transparent; border: 0;"
                " border-radius: 6px; padding: 0; margin: 0; }"
                "QToolButton:hover { background: rgba(248,81,73,0.14); }"
            )
            del_btn.clicked.connect(lambda _=False, b=del_btn: self._delete_row_from_btn(b))

            from PySide6.QtWidgets import QFrame as _QF_div
            sep = _QF_div()
            sep.setFixedSize(1, 18)
            sep.setStyleSheet("background: #30363D; border: none;")

            lay.addWidget(edit_btn, 0, Qt.AlignmentFlag.AlignVCenter)
            lay.addWidget(sep, 0, Qt.AlignmentFlag.AlignVCenter)
            lay.addWidget(del_btn, 0, Qt.AlignmentFlag.AlignVCenter)
            return wrap

        def _row_for_widget(self, widget):
            """Find which table row hosts the given action button."""
            for r in range(self.table.rowCount()):
                cw = self.table.cellWidget(r, 2)
                if cw is not None and (widget is cw or widget.parent() is cw):
                    return r
            return -1

        def _edit_row_from_btn(self, btn):
            r = self._row_for_widget(btn)
            if r < 0:
                return
            raw_date = self._raw_date_at(r)
            v_item = self.table.item(r, 1)
            if not raw_date or v_item is None:
                return
            self.date_edit.setDate(QDate.fromString(raw_date, "yyyy-MM-dd"))
            try:
                self.value_edit.setValue(float(v_item.text()))
            except ValueError:
                pass
            self._flash_editor_highlight()

        def _delete_row_from_btn(self, btn):
            r = self._row_for_widget(btn)
            if r < 0:
                return
            date_str = self._raw_date_at(r)
            v_item = self.table.item(r, 1)
            val_str = v_item.text() if v_item else ""
            if self._confirm_delete(date_str, val_str):
                self.table.removeRow(r)

        def _flash_editor_highlight(self):
            """Temporarily put a blue glowing border on the editor inputs."""
            from PySide6.QtCore import QTimer
            de_base = self.date_edit.styleSheet()
            ve_base = self.value_edit.styleSheet()
            glow = "border: 1px solid #388BFD; "
            self.date_edit.setStyleSheet(de_base + " QDateEdit {" + glow + "}")
            self.value_edit.setStyleSheet(ve_base + " QDoubleSpinBox {" + glow + "}")
            def _revert():
                self.date_edit.setStyleSheet(de_base)
                self.value_edit.setStyleSheet(ve_base)
            QTimer.singleShot(1400, _revert)

        def _confirm_delete(self, date_str: str, val_str: str) -> bool:
            """Fluent-styled confirmation modal before deleting a target row."""
            try:
                from qfluentwidgets import MessageBoxBase
            except Exception:
                from PySide6.QtWidgets import QMessageBox
                return QMessageBox.question(
                    self, "Delete row",
                    f"Delete UE target for {date_str}?",
                ) == QMessageBox.Yes

            class _ConfirmSheet(MessageBoxBase):
                def __init__(_s, host):
                    super().__init__(host.window() if host is not None else None)
                    try:
                        _s.setMaskColor(QColor(0, 0, 0, 170))
                    except Exception:
                        pass
                    _s.widget.setObjectName("confirmCard")
                    apply_fluent_modal_palette(_s, "confirmCard")
                    _s.viewLayout.setContentsMargins(22, 18, 22, 12)
                    _s.viewLayout.setSpacing(8)

                    header = QHBoxLayout()
                    header.setSpacing(12)
                    icon = QToolButton()
                    icon.setEnabled(False)
                    icon.setIcon(TablerIcon("tabler_trash.svg").icon(color=QColor("#F85149")))
                    icon.setIconSize(QSize(22, 22))
                    icon.setStyleSheet(
                        "background: rgba(248,81,73,0.12); border: none;"
                        " border-radius: 10px; padding: 6px;"
                    )
                    col = QVBoxLayout()
                    col.setSpacing(2)
                    t = QLabel("Delete target?")
                    t.setStyleSheet(
                        "color: #E6EDF3; font-size: 15px; font-weight: 700;"
                        " background: transparent;"
                    )
                    b = QLabel(
                        f"Remove the UE target <b>{val_str}</b> effective "
                        f"from <b>{date_str}</b>? This action can be undone "
                        "by re-adding the row before saving."
                    )
                    b.setTextFormat(Qt.TextFormat.RichText)
                    b.setWordWrap(True)
                    b.setStyleSheet(
                        "color: #8B949E; font-size: 11px; background: transparent;"
                    )
                    col.addWidget(t)
                    col.addWidget(b)
                    header.addWidget(icon, 0, Qt.AlignTop)
                    header.addLayout(col, 1)
                    _s.viewLayout.addLayout(header)

                    _s.widget.setMinimumWidth(420)

                    _s.buttonLayout.removeWidget(_s.yesButton)
                    _s.buttonLayout.removeWidget(_s.cancelButton)
                    _s.buttonLayout.addStretch(1)
                    _s.yesButton.setText("Delete")
                    _s.cancelButton.setText("Cancel")
                    _s.cancelButton.setFixedWidth(120)
                    _s.yesButton.setFixedWidth(120)
                    _s.cancelButton.setStyleSheet(
                        "QPushButton { background: transparent; border: 1px solid #30363D;"
                        "  color: #E6EDF3; border-radius: 10px; padding: 8px 22px;"
                        "  font-weight: 700; font-size: 12px; }"
                        "QPushButton:hover { background: rgba(255,255,255,0.05);"
                        "  border-color: #58606A; }"
                    )
                    _s.yesButton.setStyleSheet(
                        "QPushButton { background: #F85149; border: 1px solid #F85149;"
                        "  color: white; border-radius: 10px; padding: 8px 22px;"
                        "  font-weight: 700; font-size: 12px; }"
                        "QPushButton:hover { background: #FF6961; border-color: #FF6961; }"
                        "QPushButton:pressed { background: #C73D36; }"
                    )
                    _s.buttonLayout.addWidget(_s.cancelButton, 0, Qt.AlignVCenter)
                    _s.buttonLayout.addWidget(_s.yesButton, 0, Qt.AlignVCenter)

            dlg = _ConfirmSheet(self)
            return bool(dlg.exec())

        def _raw_date_at(self, r: int) -> str:
            """Return the raw ISO date for row r (item.text() now carries
            the 'YYYY-MM-DD — Week NN' decoration, so we stash the raw
            value in UserRole at build time)."""
            it = self.table.item(r, 0)
            if it is None:
                return ""
            raw = it.data(Qt.ItemDataRole.UserRole)
            return str(raw) if raw else (it.text().split()[0] if it.text() else "")

        def _periods_from_table(self):
            out = []
            for r in range(self.table.rowCount()):
                d = self._raw_date_at(r)
                val = float(self.table.item(r, 1).text())
                out.append((d, val))
            return out

        def _on_add(self):
            new_date = self.date_edit.date().toString("yyyy-MM-dd")
            new_value = self.value_edit.value()
            for r in range(self.table.rowCount()):
                if self._raw_date_at(r) == new_date:
                    self.table.item(r, 1).setText(f"{new_value:.2f}")
                    return
            self._append_row(new_date, new_value)
            self.table.sortItems(0, Qt.SortOrder.AscendingOrder)

        def _on_delete(self):
            rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
            for r in rows:
                self.table.removeRow(r)

        def _on_save(self):
            try:
                set_ue_target_periods(self._periods_from_table())
            except Exception as exc:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.critical(self, "Save failed", str(exc))
                return
            self.accept()

        def _show_help_popup(self, anchor_btn, title: str, body: str):
            """Floating mini-card explaining a field, anchored under the (?)"""
            popup = QFrame(self, Qt.Popup)
            popup.setStyleSheet(
                "QFrame { background: #161B22; border: 1px solid #30363D;"
                " border-radius: 10px; }"
                "QLabel#ttl { color: #58A6FF; font-size: 11px; font-weight: 700;"
                " background: transparent; }"
                "QLabel#bd { color: #C9D1D9; font-size: 11px; background: transparent; }"
            )
            lay = QVBoxLayout(popup)
            lay.setContentsMargins(14, 12, 14, 12)
            lay.setSpacing(4)
            t = QLabel(title); t.setObjectName("ttl")
            b = QLabel(body); b.setObjectName("bd")
            b.setWordWrap(True)
            lay.addWidget(t)
            lay.addWidget(b)
            popup.setMaximumWidth(280)
            popup.adjustSize()
            # Position below the (?) button, anchored to its bottom-left.
            anchor_pos = anchor_btn.mapToGlobal(anchor_btn.rect().bottomLeft())
            popup.move(anchor_pos.x() - 6, anchor_pos.y() + 4)
            popup.show()

    return UETargetDialog(parent)


# Legacy class kept as a fallback when the Fluent build can't be created.
class UETargetDialog:
    """Backwards-compat factory: returns a Fluent dialog when possible."""

    def __new__(cls, parent=None):
        try:
            return _build_ue_target_dialog(parent)
        except Exception:
            # Fall back to the simple QDialog implementation below.
            return _LegacyUETargetDialog(parent)


from PySide6.QtWidgets import QDialog


class _LegacyUETargetDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Daily UE Target")
        self.setMinimumSize(460, 380)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("(Fallback dialog — see logs)"))
        btn = QPushButton("Close")
        btn.clicked.connect(self.accept)
        layout.addWidget(btn)
