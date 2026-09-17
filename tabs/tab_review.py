"""Casos Para Revisar — queue of cases flagged for follow-up (doctor
review, software issues, etc).

Read/write helpers + ReviewTab widget. Mirrors the Production tab's
dark theme.
"""
from __future__ import annotations

from .theme_palette import apply_fluent_modal_palette

from datetime import datetime
from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtGui import QColor, QBrush, QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QComboBox,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView, QFrame,
    QToolButton, QMessageBox,
)

from db.database import get_connection


# ── DB helpers ────────────────────────────────────────────────────────

def add_case_to_review(*, case_id, doctor="", region="", tipo_caso="",
                       fecha="", comment="", reason="",
                       category="Other") -> int:
    """Insert a case into the review queue. Returns the new row id."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO cases_review (case_id, doctor, region, tipo_caso, fecha,"
        " comment, reason, status, created_at, category)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
        (case_id, doctor, region, tipo_caso, fecha,
         comment, reason, datetime.now().isoformat(timespec="seconds"),
         category),
    )
    new_id = cur.lastrowid
    conn.commit()
    conn.close()
    return new_id


def list_review_cases(status: str = ""):
    """Fetch review rows ordered by created_at desc. status='' returns all."""
    conn = get_connection()
    cur = conn.cursor()
    if status:
        cur.execute(
            "SELECT id, case_id, doctor, region, tipo_caso, fecha, comment,"
            " reason, status, created_at, resolved_at,"
            " COALESCE(category, 'Other')"
            " FROM cases_review WHERE status = ?"
            " ORDER BY created_at DESC", (status,),
        )
    else:
        cur.execute(
            "SELECT id, case_id, doctor, region, tipo_caso, fecha, comment,"
            " reason, status, created_at, resolved_at,"
            " COALESCE(category, 'Other')"
            " FROM cases_review ORDER BY created_at DESC",
        )
    rows = cur.fetchall()
    conn.close()
    return rows


def mark_review_resolved(review_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE cases_review SET status = 'resolved', resolved_at = ?"
        " WHERE id = ?",
        (datetime.now().isoformat(timespec="seconds"), review_id),
    )
    conn.commit()
    conn.close()


def reopen_review(review_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE cases_review SET status = 'pending', resolved_at = ''"
        " WHERE id = ?", (review_id,),
    )
    conn.commit()
    conn.close()


def delete_review(review_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM cases_review WHERE id = ?", (review_id,))
    conn.commit()
    conn.close()


# ── UI ────────────────────────────────────────────────────────────────

class ReviewTab(QWidget):
    """List of cases flagged for review."""

    review_count_changed = Signal(int)

    def __init__(self):
        super().__init__()
        self._init_ui()
        self.reload()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 10)
        root.setSpacing(12)

        title = QLabel("Cases For Review")
        title.setStyleSheet(
            "color: #E6EDF3; font-size: 18px; font-weight: 800;"
            " letter-spacing: 0.3px;"
        )
        title.setAlignment(Qt.AlignmentFlag.AlignLeft)
        root.addWidget(title)
        subtitle = QLabel("Cases flagged for software issues, doctor inquiries, "
                          "or anything else worth a second look.")
        subtitle.setStyleSheet(
            "color: #8B949E; font-size: 11px; background: transparent;"
        )
        subtitle.setAlignment(Qt.AlignmentFlag.AlignLeft)
        root.addWidget(subtitle)

        # Filter / actions toolbar.
        try:
            from .widgets import _icon_url as _icu_rv
            _chev_rv = _icu_rv("tabler_chevron_down.svg")
        except Exception:
            _chev_rv = ""
        bar = QFrame()
        bar.setObjectName("revBar")
        bar.setStyleSheet(
            "#revBar { background: #0D1117; border: 1px solid #21262D;"
            " border-radius: 10px; }"
            "QLabel { background: transparent; color: #C9D1D9;"
            " font-size: 12px; font-weight: 700; }"
            "QComboBox, QLineEdit { background: #161B22; border: 1px solid #30363D;"
            " border-radius: 6px; padding: 4px 22px 4px 8px; color: #E6EDF3;"
            " font-size: 11px; min-height: 26px; }"
            "QComboBox::drop-down { subcontrol-origin: padding;"
            " subcontrol-position: right center; width: 22px; border: none; }"
            f"QComboBox::down-arrow {{ image: url({_chev_rv});"
            " width: 12px; height: 12px; }"
        )
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(14, 12, 14, 12)
        bl.setSpacing(12)

        bl.addWidget(QLabel("Status"))
        self.status_combo = QComboBox()
        self.status_combo.addItems(["All", "Pending", "Resolved"])
        self.status_combo.setFixedHeight(28)
        self.status_combo.setMinimumWidth(120)
        self.status_combo.currentTextChanged.connect(self.reload)
        bl.addWidget(self.status_combo)

        bl.addSpacing(20)
        bl.addWidget(QLabel("Search"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Case ID / Doctor / Reason…")
        self.search.setFixedHeight(28)
        self.search.setMinimumWidth(260)
        self.search.textChanged.connect(self._apply_filter)
        bl.addWidget(self.search)

        bl.addStretch(1)

        # Category filter chips.
        self._category_filter = "All"

        def _make_chip(text, value, accent):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedHeight(28)
            b.setStyleSheet(
                "QPushButton { background: #161B22; border: 1px solid #30363D;"
                "  color: #C9D1D9; border-radius: 6px; padding: 0 12px;"
                "  font-size: 11px; font-weight: 600; }"
                "QPushButton:hover { border-color: #58606A; }"
                f"QPushButton:checked {{ border-color: {accent};"
                f"  color: {accent}; background: rgba(0,0,0,0); }}"
            )
            return b

        self._chip_all = _make_chip("All", "All", "#58A6FF")
        self._chip_sw = _make_chip("Software Issues", "Software Issue", "#F85149")
        self._chip_dr = _make_chip("Doctor Inquiries", "Doctor Inquiry", "#58A6FF")
        self._chip_ot = _make_chip("Other", "Other", "#D29922")
        self._chip_all.setChecked(True)
        from PySide6.QtWidgets import QButtonGroup as _QBG_r
        cat_grp = _QBG_r(self)
        cat_grp.setExclusive(True)
        for c, v in (
            (self._chip_all, "All"),
            (self._chip_sw, "Software Issue"),
            (self._chip_dr, "Doctor Inquiry"),
            (self._chip_ot, "Other"),
        ):
            cat_grp.addButton(c)
            c.clicked.connect(lambda _=False, _v=v: self._on_category_chip(_v))
            bl.addWidget(c)

        root.addWidget(bar)

        # Table.
        self.table = QTableWidget()
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels([
            "CASE ID", "DOCTOR", "REGION", "TYPE", "DATE", "REASON",
            "STATUS", "ACTIONS",
        ])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(42)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        # Double-click a row → open a modal showing the full reason/comment.
        self.table.cellDoubleClicked.connect(self._on_row_double_clicked)
        self.table.setShowGrid(True)
        header = self.table.horizontalHeader()
        for c in range(self.table.columnCount()):
            header.setSectionResizeMode(c, QHeaderView.ResizeMode.Stretch)
        header.setStretchLastSection(False)
        self.table.setStyleSheet(
            "QTableWidget { background: #0D1117; border: 1px solid #21262D;"
            "  border-radius: 10px; gridline-color: #21262D; outline: none;"
            "  color: #E6EDF3; }"
            "QTableWidget::item { padding: 6px;"
            "  border-right: 1px solid #21262D;"
            "  border-bottom: 1px solid #21262D; }"
            "QHeaderView::section { background: #161B22; color: #8B949E;"
            "  padding: 10px 6px; border: none;"
            "  border-right: 1px solid #21262D;"
            "  border-bottom: 1px solid #21262D;"
            "  font-weight: 700; font-size: 10px; }"
        )
        root.addWidget(self.table, 1)

        # Empty state — card with a big flag icon + headline + subtitle.
        self.empty_state = QFrame()
        self.empty_state.setObjectName("emptyState")
        self.empty_state.setStyleSheet(
            "#emptyState { background: #0D1117; border: 1px solid #21262D;"
            "  border-radius: 10px; }"
            "QLabel { background: transparent; border: none; }"
        )
        es = QVBoxLayout(self.empty_state)
        es.setContentsMargins(20, 32, 20, 32)
        es.setSpacing(8)
        es.setAlignment(Qt.AlignmentFlag.AlignCenter)
        try:
            from .tabler_icons import TablerIcon as _TI_es
            ic_lbl = QLabel()
            ic_lbl.setFixedSize(48, 48)
            ic_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ic_lbl.setPixmap(
                _TI_es("tabler_flag.svg").icon(color=QColor("#30363D")).pixmap(40, 40)
            )
            es.addWidget(ic_lbl, 0, Qt.AlignmentFlag.AlignCenter)
        except Exception:
            pass
        self.empty_lbl = QLabel("No cases pending for review")
        self.empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_lbl.setStyleSheet(
            "color: #C9D1D9; font-size: 14px; font-weight: 700;"
        )
        es.addWidget(self.empty_lbl)
        empty_sub = QLabel(
            "Flag a case from the Case Information panel and it'll show up here."
        )
        empty_sub.setWordWrap(True)
        empty_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_sub.setStyleSheet("color: #6E7681; font-size: 11px;")
        es.addWidget(empty_sub)
        self.empty_state.hide()
        root.addWidget(self.empty_state, 1)

    def showEvent(self, event):
        """Auto-refresh whenever the tab becomes visible so cases flagged
        from elsewhere in the app show up without manual reload."""
        super().showEvent(event)
        try:
            self.reload()
        except Exception:
            pass

    def reload(self):
        sel = self.status_combo.currentText()
        if sel == "Pending":
            rows = list_review_cases("pending")
        elif sel == "Resolved":
            rows = list_review_cases("resolved")
        else:
            rows = list_review_cases("")
        self._all_rows = rows
        self._apply_filter()
        # Emit pending count for sidebar badges if anyone listens.
        try:
            pend = len([r for r in list_review_cases("pending")])
            self.review_count_changed.emit(pend)
        except Exception:
            pass

    def _on_category_chip(self, value: str):
        self._category_filter = value
        self._apply_filter()

    def _apply_filter(self):
        needle = self.search.text().strip().lower()
        rows = self._all_rows
        cat = getattr(self, "_category_filter", "All")
        if cat and cat != "All":
            # category at column 11 (added in SELECT)
            rows = [r for r in rows if (r[11] if len(r) > 11 else "Other") == cat]
        if needle:
            rows = [
                r for r in rows
                if needle in (r[1] or "").lower()
                or needle in (r[2] or "").lower()
                or needle in (r[7] or "").lower()
                or needle in (r[6] or "").lower()
            ]
        self._populate(rows)

    def _populate(self, rows):
        self.table.setRowCount(len(rows))
        self.empty_state.setVisible(not rows)
        self.table.setVisible(bool(rows))
        # Keep a row → tuple map so double-click can recover the full
        # reason/comment for the popup.
        self._row_data = rows

        for i, r in enumerate(rows):
            (rid, case_id, doctor, region, tipo, fecha, comment, reason,
             status, created, resolved, category) = r[:12] if len(r) >= 12 else (*r, "Other")

            def _item(text, fg=None, bold=False, tt=None):
                it = QTableWidgetItem(str(text or "-"))
                it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if fg:
                    it.setForeground(QBrush(QColor(fg)))
                if bold:
                    f = QFont(); f.setBold(True); it.setFont(f)
                if tt:
                    it.setToolTip(tt)
                return it

            self.table.setItem(i, 0, _item(case_id, bold=True))
            self.table.setItem(i, 1, _item(doctor))
            self.table.setItem(i, 2, _item(region))
            self.table.setItem(i, 3, _item(tipo))
            self.table.setItem(i, 4, _item(fecha))
            reason_text = reason or comment or ""
            short = (reason_text if len(reason_text) <= 40
                     else reason_text[:38] + "…")
            self.table.setItem(i, 5, _item(short, tt=reason_text))

            status_color = "#3FB950" if status == "resolved" else "#D29922"
            self.table.setItem(i, 6, _item(
                "Resolved" if status == "resolved" else "Pending",
                fg=status_color, bold=True,
            ))

            # Actions: mark-resolved / reopen + delete.
            self.table.setCellWidget(i, 7, self._actions(rid, status))

    def _actions(self, rid: int, status: str) -> QWidget:
        wrap = QWidget()
        wrap.setStyleSheet("background: transparent;")
        h = QHBoxLayout(wrap)
        h.setContentsMargins(4, 0, 4, 0)
        h.setSpacing(4)
        h.addStretch(1)

        try:
            from .tabler_icons import TablerIcon as _TI
        except Exception:
            _TI = None

        def _btn(icon_svg, color, tooltip, callback):
            b = QToolButton()
            b.setFixedSize(26, 26)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(tooltip)
            if _TI is not None and icon_svg:
                b.setIcon(_TI(icon_svg).icon(color=QColor(color)))
                b.setIconSize(QSize(14, 14))
            b.setStyleSheet(
                "QToolButton { background: transparent;"
                f" border: 1px solid {color}; border-radius: 6px; }}"
                "QToolButton:hover { background: rgba(255,255,255,0.06); }"
            )
            b.clicked.connect(callback)
            return b

        if status == "resolved":
            h.addWidget(_btn(
                "tabler_arrow_back_up.svg", "#58A6FF", "Reopen",
                lambda _=False, x=rid: self._on_reopen(x),
            ))
        else:
            h.addWidget(_btn(
                "tabler_check.svg", "#3FB950", "Mark resolved",
                lambda _=False, x=rid: self._on_resolve(x),
            ))
        h.addWidget(_btn(
            "tabler_trash.svg", "#F85149", "Delete",
            lambda _=False, x=rid: self._on_delete(x),
        ))
        h.addStretch(1)
        return wrap

    def _on_resolve(self, rid: int):
        mark_review_resolved(rid)
        self.reload()

    def _on_reopen(self, rid: int):
        reopen_review(rid)
        self.reload()

    def _on_row_double_clicked(self, row: int, _col: int):
        """Open a Fluent modal showing the full reason / comment for the
        case in that row."""
        rows = getattr(self, "_row_data", None) or []
        if row < 0 or row >= len(rows):
            return
        r = rows[row]
        case_id = r[1] if len(r) > 1 else ""
        comment = r[6] if len(r) > 6 else ""
        reason = r[7] if len(r) > 7 else ""
        category = r[11] if len(r) > 11 else "Other"
        body = (reason or "").strip() or (comment or "").strip()
        if not body:
            body = "(no reason recorded)"
        self._show_review_detail(str(case_id), category, body)

    def _show_review_detail(self, case_id: str, category: str, body: str):
        """Fluent modal with case ID + category badge + reason text."""
        try:
            from qfluentwidgets import MessageBoxBase
            from .tabler_icons import TablerIcon
            from PySide6.QtWidgets import (
                QToolButton as _QTBh, QTextEdit as _QTE, QFrame as _QF,
            )
            from PySide6.QtGui import QColor as _QCh
            from PySide6.QtCore import QSize as _QSh
        except Exception:
            QMessageBox.information(self, f"Case {case_id}", body)
            return

        host = self
        cat_color = {
            "Software Issue": "#F85149",
            "Doctor Inquiry": "#58A6FF",
            "Other": "#D29922",
        }.get(category, "#D29922")

        class _Sheet(MessageBoxBase):
            def __init__(_s, h):
                super().__init__(h.window() if h is not None else None)
                try:
                    _s.setMaskColor(_QCh(0, 0, 0, 170))
                except Exception:
                    pass
                _s.widget.setObjectName("revDetailCard")
                apply_fluent_modal_palette(_s, "revDetailCard")
                _s.viewLayout.setContentsMargins(22, 18, 22, 12)
                _s.viewLayout.setSpacing(12)

                hdr = QHBoxLayout(); hdr.setSpacing(12)
                ic = _QTBh(); ic.setEnabled(False)
                ic.setIcon(TablerIcon("tabler_flag.svg").icon(color=_QCh(cat_color)))
                ic.setIconSize(_QSh(22, 22))
                rgb = _QCh(cat_color)
                ic.setStyleSheet(
                    f"background: rgba({rgb.red()},{rgb.green()},{rgb.blue()},0.14);"
                    " border: none; border-radius: 10px; padding: 6px;"
                )
                tc = QVBoxLayout(); tc.setSpacing(2)
                t = QLabel(f"Case {case_id}")
                t.setStyleSheet(
                    "color: #E6EDF3; font-size: 15px; font-weight: 700;"
                    " background: transparent;"
                )
                cat_chip = QLabel(category or "Other")
                cat_chip.setStyleSheet(
                    f"color: {cat_color}; font-size: 11px; font-weight: 700;"
                    " background: transparent;"
                )
                tc.addWidget(t)
                tc.addWidget(cat_chip)
                hdr.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
                hdr.addLayout(tc, 1)
                _s.viewLayout.addLayout(hdr)

                lbl = QLabel("Reason")
                lbl.setStyleSheet(
                    "color: #C9D1D9; font-size: 11px; font-weight: 700;"
                    " background: transparent;"
                )
                _s.viewLayout.addWidget(lbl)

                box = _QTE()
                box.setReadOnly(True)
                box.setPlainText(body)
                box.setStyleSheet(
                    "QTextEdit { background: #161B22; border: 1px solid #30363D;"
                    "  border-radius: 6px; padding: 8px 10px; color: #E6EDF3;"
                    "  font-size: 12px; }"
                )
                box.setMinimumHeight(140)
                _s.viewLayout.addWidget(box)
                _s.widget.setMinimumWidth(480)

                _s.cancelButton.hide()
                _s.yesButton.setText("Close")
                _s.yesButton.setFixedWidth(120)
                _s.yesButton.setStyleSheet(
                    "QPushButton { background: #1e63e4; border: 1px solid #1e63e4;"
                    "  color: white; border-radius: 6px; padding: 8px 22px;"
                    "  font-weight: 700; font-size: 12px; }"
                    "QPushButton:hover { background: #2a73f3; }"
                )

        _Sheet(host).exec()

    def _on_delete(self, rid: int):
        if self._confirm_delete_modal():
            delete_review(rid)
            self.reload()

    def _confirm_delete_modal(self) -> bool:
        """Fluent confirmation modal — matches the rest of the app."""
        try:
            from qfluentwidgets import MessageBoxBase
            from .tabler_icons import TablerIcon
            from PySide6.QtWidgets import QToolButton as _QTBd
            from PySide6.QtGui import QColor as _QCd
            from PySide6.QtCore import QSize as _QSd
        except Exception:
            r = QMessageBox.question(
                self, "Delete case",
                "Remove this case from the review list?",
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
                _s.widget.setObjectName("delRevCard")
                apply_fluent_modal_palette(_s, "delRevCard")
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
                t = QLabel("Remove from review")
                t.setStyleSheet(
                    "color: #E6EDF3; font-size: 15px; font-weight: 700;"
                    " background: transparent;"
                )
                s = QLabel(
                    "This entry will be deleted from the review list. "
                    "The case itself stays untouched in production."
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
                _s.yesButton.setText("   Remove")
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
