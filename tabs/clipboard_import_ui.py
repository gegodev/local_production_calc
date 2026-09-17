"""
Shared clipboard import UI helpers for Register and OT tabs.
"""

from __future__ import annotations

from .theme_palette import apply_fluent_modal_palette

from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QHBoxLayout, QPushButton
from sync.clipboard_import import parse_clipboard


def get_clipboard_case_data(standards: dict) -> dict:
    """Parse clipboard and return detected case fields."""
    return parse_clipboard(standards)


def has_detected_case_fields(data: dict) -> bool:
    """Return True if at least one core case field was detected."""
    return any(data.get(k) for k in ("case_id", "region", "tipo", "doctor"))


def show_import_confirmation(parent, data: dict) -> bool:
    """Show import confirmation dialog. Returns True when user confirms.

    Fluent-styled card matching the rest of the modals (Add comment, UE
    Target, etc.). Falls back to the legacy QDialog if qfluentwidgets is
    unavailable."""
    try:
        return _show_import_confirmation_fluent(parent, data)
    except Exception:
        return _show_import_confirmation_legacy(parent, data)


def _show_import_confirmation_fluent(parent, data: dict) -> bool:
    from qfluentwidgets import MessageBoxBase
    from PySide6.QtWidgets import (
        QFrame as _QFr, QToolButton as _QTB, QWidget as _QW,
    )
    from PySide6.QtCore import (
        Qt as _Qt, QSize as _QS, QPropertyAnimation as _QPA,
        QEasingCurve as _QEC, Property as _QProp,
    )
    from PySide6.QtGui import QColor as _QCol, QPainter as _QPn
    from tabs.tabler_icons import TablerIcon as _TI

    # Existence check runs in a background QThread — never blocks dialog open.
    case_id = (data.get("case_id") or "").strip()
    already_exists = False  # default "ready"; thread will update if otherwise

    from PySide6.QtCore import QThread as _QTh, Signal as _QSig

    class _ExistsWorker(_QTh):
        done = _QSig(bool)

        def __init__(_w, cid: str):
            super().__init__()
            _w._cid = cid

        def run(_w):
            if not _w._cid:
                _w.done.emit(False)
                return
            try:
                import sqlite3
                from db.database import DB_PATH
                uri = f"file:{DB_PATH}?mode=ro"
                conn = sqlite3.connect(uri, uri=True, timeout=2.0)
                try:
                    cur = conn.cursor()
                    cur.execute(
                        "SELECT 1 FROM cases WHERE case_id=? LIMIT 1",
                        (_w._cid,),
                    )
                    if cur.fetchone():
                        _w.done.emit(True); return
                    cur.execute(
                        "SELECT 1 FROM ot_cases WHERE case_id=? LIMIT 1",
                        (_w._cid,),
                    )
                    _w.done.emit(cur.fetchone() is not None)
                finally:
                    conn.close()
            except Exception:
                _w.done.emit(False)

    # Dynamic field list — only show rows that actually picked up a value.
    _all_rows = [
        ("Case ID",      data.get("case_id"),      "tabler_hash.svg"),
        ("Region",       data.get("region"),       "tabler_world.svg"),
        ("Type",         data.get("tipo"),         "tabler_tag.svg"),
        ("Doctor",       data.get("doctor"),       "tabler_user.svg"),
        ("Product Tier", data.get("product_tier"), "tabler_brand_databricks.svg"),
        ("Country",      data.get("country"),      "tabler_flag.svg"),
    ]
    rows = [r for r in _all_rows if r[1]]
    if not rows:
        rows = _all_rows[:4]  # show all four core rows even if empty

    class _ImportSheet(MessageBoxBase):
        def __init__(_s, host):
            super().__init__(host.window() if host is not None else None)
            try:
                _s.setMaskColor(_QCol(0, 0, 0, 170))
            except Exception:
                pass

            _s.widget.setObjectName("importCard")
            apply_fluent_modal_palette(_s, "importCard")

            _s.viewLayout.setContentsMargins(0, 8, 0, 8)
            _s.viewLayout.setSpacing(0)

            def _wrap(child, mh=12):
                w = _QW()
                lw = QVBoxLayout(w)
                lw.setContentsMargins(22, mh, 22, mh)
                lw.setSpacing(6)
                if isinstance(child, _QW):
                    lw.addWidget(child)
                else:
                    lw.addLayout(child)
                return w

            def _div():
                d = _QFr()
                d.setFixedHeight(1)
                d.setStyleSheet("background: #21262D; border: none;")
                return d

            # ── Header ──
            hdr = QHBoxLayout()
            hdr.setSpacing(10)
            ic = _QTB()
            ic.setEnabled(False)
            ic.setIcon(_TI("tabler_file_upload.svg").icon(color=_QCol("#388BFD")))
            ic.setIconSize(_QS(20, 20))
            ic.setStyleSheet(
                "background: rgba(56,139,253,0.12); border: none;"
                " border-radius: 8px; padding: 6px;"
            )
            tc = QVBoxLayout(); tc.setSpacing(2)
            ttl = QLabel("Import Case")
            ttl.setStyleSheet(
                "color: #E6EDF3; font-size: 15px; font-weight: 700;"
                " background: transparent;"
            )
            sub = QLabel("Review case details before importing.")
            sub.setStyleSheet(
                "color: #8B949E; font-size: 11px; background: transparent;"
            )
            tc.addWidget(ttl); tc.addWidget(sub)

            class _SpinX(_QTB):
                def __init__(s, *a, **kw):
                    super().__init__(*a, **kw)
                    s._rot = 0.0
                    s._anim = _QPA(s, b"rotation", s)
                    s._anim.setDuration(260)
                    s._anim.setEasingCurve(_QEC.OutCubic)
                def get_rot(s): return s._rot
                def set_rot(s, v):
                    s._rot = float(v); s.update()
                rotation = _QProp(float, get_rot, set_rot)
                def paintEvent(s, e):
                    p = _QPn(s); p.setRenderHint(_QPn.Antialiasing)
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
            cb.setIcon(_TI("tabler_x.svg").icon(color=_QCol("#8B949E")))
            cb.setIconSize(_QS(22, 22))
            cb.setCursor(_Qt.PointingHandCursor)
            cb.setFixedSize(34, 34)
            cb.setStyleSheet(
                "QToolButton { background: transparent; border: none; border-radius: 17px; }"
                "QToolButton:hover { background: rgba(255,255,255,0.08); }"
            )
            cb.clicked.connect(_s.reject)

            hdr.addWidget(ic, 0, _Qt.AlignTop)
            hdr.addLayout(tc, 1)
            hdr.addWidget(cb, 0, _Qt.AlignTop)
            _s.viewLayout.addWidget(_wrap(hdr))
            _s.viewLayout.addWidget(_div())

            # ── Body rows — 2-column grid ──
            from PySide6.QtWidgets import QGridLayout as _QGL
            body = _QW()
            bl = QVBoxLayout(body)
            bl.setContentsMargins(22, 8, 22, 8)
            bl.setSpacing(8)
            grid = _QGL()
            grid.setHorizontalSpacing(8)
            grid.setVerticalSpacing(6)

            def _build_row(label_text, value, svg):
                row = _QW()
                row.setObjectName("impRow")
                row.setStyleSheet(
                    "#impRow { background: #161B22; border: 1px solid #21262D;"
                    " border-radius: 8px; }"
                )
                rl = QHBoxLayout(row)
                rl.setContentsMargins(10, 8, 12, 8)
                rl.setSpacing(10)
                ric = _QTB()
                ric.setEnabled(False)
                ric.setIcon(_TI(svg).icon(color=_QCol("#388BFD")))
                ric.setIconSize(_QS(14, 14))
                ric.setStyleSheet(
                    "background: rgba(56,139,253,0.12); border: none;"
                    " border-radius: 6px; padding: 5px;"
                )
                lbl = QLabel(label_text)
                lbl.setStyleSheet(
                    "color: #C9D1D9; font-size: 11px; font-weight: 600;"
                    " background: transparent;"
                )
                val = QLabel(str(value) if value else "—")
                val.setStyleSheet(
                    "color: #E6EDF3; font-size: 12px; font-weight: 700;"
                    " background: transparent;"
                )
                rl.addWidget(ric)
                rl.addWidget(lbl)
                rl.addStretch()
                rl.addWidget(val)
                return row

            for i, (label_text, value, svg) in enumerate(rows):
                r, c = divmod(i, 2)
                grid.addWidget(_build_row(label_text, value, svg), r, c)
            bl.addLayout(grid)

            # Status banner — starts in "Checking…" neutral state, then
            # updates once the DB worker reports back.
            banner = _QW()
            banner.setObjectName("impStatus")
            banner.setStyleSheet(
                "#impStatus { background: rgba(139,148,158,0.10);"
                " border: 1px solid rgba(139,148,158,0.35);"
                " border-radius: 8px; }"
            )
            bnr = QHBoxLayout(banner)
            bnr.setContentsMargins(10, 8, 12, 8)
            bnr.setSpacing(10)
            bnr_ic = _QTB()
            bnr_ic.setEnabled(False)
            bnr_ic.setIcon(_TI("tabler_shield_check.svg").icon(color=_QCol("#8B949E")))
            bnr_ic.setIconSize(_QS(16, 16))
            bnr_ic.setStyleSheet("background: transparent; border: none;")
            bnr_tc = QVBoxLayout(); bnr_tc.setSpacing(1)
            bnr_t = QLabel("Checking database…")
            bnr_t.setStyleSheet(
                "color: #8B949E; font-size: 11px; font-weight: 700;"
                " background: transparent;"
            )
            bnr_b = QLabel("Looking up this case ID in your local DB.")
            bnr_b.setStyleSheet(
                "color: #C9D1D9; font-size: 11px; background: transparent;"
            )
            bnr_tc.addWidget(bnr_t); bnr_tc.addWidget(bnr_b)
            bnr.addWidget(bnr_ic, 0, _Qt.AlignTop)
            bnr.addLayout(bnr_tc, 1)
            bl.addSpacing(4)
            bl.addWidget(banner)

            # Keep references so the worker callback can mutate them.
            _s._banner = banner
            _s._banner_ic = bnr_ic
            _s._banner_t = bnr_t
            _s._banner_b = bnr_b

            _s.viewLayout.addWidget(body)
            _s.viewLayout.addWidget(_div())

            _s.widget.setMinimumWidth(640)

            # Buttons.
            _s.buttonLayout.removeWidget(_s.yesButton)
            _s.buttonLayout.removeWidget(_s.cancelButton)
            _s.buttonLayout.addStretch(1)
            _s.cancelButton.setText("Cancel")
            _s.cancelButton.setFixedWidth(120)
            _s.cancelButton.setStyleSheet(
                "QPushButton { background: transparent; border: 1px solid #30363D;"
                "  color: #E6EDF3; border-radius: 6px; padding: 8px 22px;"
                "  font-weight: 700; font-size: 12px; }"
                "QPushButton:hover { background: rgba(255,255,255,0.05);"
                "  border-color: #58606A; }"
            )
            _s.yesButton.setText("   Import Case")
            _s.yesButton.setFixedWidth(150)
            try:
                _s.yesButton.setIcon(
                    _TI("tabler_upload.svg").icon(color=_QCol("#FFFFFF"))
                )
                _s.yesButton.setIconSize(_QS(14, 14))
            except Exception:
                pass
            def _style_yes(bg, hover):
                _s.yesButton.setStyleSheet(
                    f"QPushButton {{ background: {bg};"
                    f"  border: 1px solid {bg}; color: white;"
                    "  border-radius: 6px; padding: 8px 18px;"
                    "  font-weight: 700; font-size: 12px; }}"
                    f"QPushButton:hover {{ background: {hover};"
                    f"  border-color: {hover}; }}"
                )
            _s._style_yes = _style_yes
            # Neutral blue while checking.
            _style_yes("#1e63e4", "#2a73f3")
            _s.buttonLayout.addWidget(_s.cancelButton, 0, _Qt.AlignVCenter)
            _s.buttonLayout.addWidget(_s.yesButton, 0, _Qt.AlignVCenter)

            def _on_exists_result(found: bool):
                # Update banner + primary button styling based on the
                # background DB lookup result.
                if found:
                    _s._banner.setStyleSheet(
                        "#impStatus { background: rgba(210,153,34,0.12);"
                        " border: 1px solid rgba(210,153,34,0.45);"
                        " border-radius: 8px; }"
                    )
                    _s._banner_ic.setIcon(
                        _TI("tabler_shield_check.svg").icon(color=_QCol("#D29922"))
                    )
                    _s._banner_t.setText("Case already in the database")
                    _s._banner_t.setStyleSheet(
                        "color: #D29922; font-size: 11px; font-weight: 700;"
                        " background: transparent;"
                    )
                    _s._banner_b.setText("Importing will create a duplicate row.")
                    _style_yes("#D29922", "#E8AC2D")
                else:
                    _s._banner.setStyleSheet(
                        "#impStatus { background: rgba(46,160,67,0.10);"
                        " border: 1px solid rgba(46,160,67,0.45);"
                        " border-radius: 8px; }"
                    )
                    _s._banner_ic.setIcon(
                        _TI("tabler_shield_check.svg").icon(color=_QCol("#3FB950"))
                    )
                    _s._banner_t.setText("Ready to import")
                    _s._banner_t.setStyleSheet(
                        "color: #3FB950; font-size: 11px; font-weight: 700;"
                        " background: transparent;"
                    )
                    _s._banner_b.setText("No existing case with this ID was found.")
                    _style_yes("#2EA043", "#3FB950")

            # Kick off the worker AFTER dialog is fully built.
            _s._worker = _ExistsWorker(case_id)
            _s._worker.done.connect(_on_exists_result)
            _s._worker.start()

    return bool(_ImportSheet(parent).exec())


def _show_import_confirmation_legacy(parent, data: dict) -> bool:
    """Plain QDialog fallback (original implementation)."""
    dlg = QDialog(parent)
    dlg.setWindowTitle("Import from Clipboard")
    dlg.setMinimumWidth(300)
    layout = QVBoxLayout(dlg)
    layout.setSpacing(10)
    header = QLabel("Import this case?")
    header.setStyleSheet("font-weight: bold; font-size: 13px;")
    layout.addWidget(header)
    for label_text, value in (
        ("Case ID", data.get("case_id", "-")),
        ("Region", data.get("region", "-")),
        ("Type", data.get("tipo", "-")),
        ("Doctor", data.get("doctor", "-")),
    ):
        row_lbl = QLabel(f"<b>{label_text}:</b>  {value}")
        row_lbl.setWordWrap(True)
        layout.addWidget(row_lbl)
    btn_layout = QHBoxLayout()
    btn_layout.addStretch()
    btn_cancel = QPushButton("Cancel")
    btn_import = QPushButton("Import")
    btn_import.setDefault(True)
    btn_layout.addWidget(btn_cancel)
    btn_layout.addWidget(btn_import)
    layout.addLayout(btn_layout)
    btn_cancel.clicked.connect(dlg.reject)
    btn_import.clicked.connect(dlg.accept)
    return dlg.exec() == QDialog.DialogCode.Accepted


def build_import_summary(imported_case_id: str | None, imported_region: str | None, imported_type: str | None) -> str:
    """Build concise import summary text."""
    summary_parts = [p for p in (imported_case_id, imported_region, imported_type) if p]
    return " | ".join(summary_parts) if summary_parts else "Case imported"


def get_import_not_detected_message(module_name: str) -> str:
    """Return module-specific 'not detected' feedback text."""
    mod = (module_name or "").strip().lower()
    if mod == "ot":
        return (
            "Nothing detected in clipboard.\n"
            "On the case page: press Ctrl+A then Ctrl+C, then try again."
        )
    return "Nothing detected in clipboard.\nPress Ctrl+A, Ctrl+C on the case page, then retry."


def get_import_success_message(summary: str, module_name: str) -> str:
    """Return module-specific success feedback text."""
    mod = (module_name or "").strip().lower()
    if mod == "ot":
        return f"Imported: {summary}\nClick Calculate."
    return f"Imported: {summary} - Click Calculate."


def get_import_reminder_message() -> str:
    """Reminder shown after clipboard import."""
    return (
        "Verify if the case is Stage RX or Bite Sync.\n"
        "Import currently does not auto-detect this."
    )


def apply_imported_case_data(
    data: dict,
    *,
    case_id_widget,
    region_widget,
    type_widget,
    doctor_widget,
    refresh_case_types_fn,
) -> tuple[str | None, str | None, str | None]:
    """
    Apply parsed clipboard data to form widgets.

    Returns (imported_case_id, imported_region, imported_type).
    """
    imported_case_id = None
    imported_region = None
    imported_type = None

    if data.get("case_id"):
        case_id_widget.setText(data["case_id"])
        imported_case_id = data["case_id"]

    if data.get("region"):
        idx = region_widget.findText(data["region"])
        if idx >= 0:
            region_widget.blockSignals(True)
            region_widget.setCurrentIndex(idx)
            region_widget.blockSignals(False)
            refresh_case_types_fn()
            imported_region = data["region"]

    if data.get("tipo"):
        tipo_value = data["tipo"]
        idx = type_widget.findText(tipo_value)
        # CR formats vary across standards.json snapshots — try every
        # common spelling before giving up and leaving Primary by default.
        if idx < 0 and tipo_value.lower().startswith("cr"):
            import re as _re_cr_apply
            m = _re_cr_apply.match(r"\s*CR\s*#?\s*(\d+)", tipo_value, _re_cr_apply.IGNORECASE)
            if m:
                cr_n = m.group(1)
                for variant in (
                    f"CR #{cr_n}", f"CR# {cr_n}", f"CR {cr_n}", f"CR{cr_n}",
                    f"cr #{cr_n}", f"CR-{cr_n}",
                ):
                    idx = type_widget.findText(variant)
                    if idx >= 0:
                        tipo_value = variant
                        break
                # Last resort: any item starting with "CR" wins so the
                # user at least lands on a CR option instead of Primary.
                if idx < 0:
                    for i in range(type_widget.count()):
                        if type_widget.itemText(i).strip().lower().startswith("cr"):
                            idx = i
                            tipo_value = type_widget.itemText(i)
                            break
        if idx >= 0:
            type_widget.setCurrentIndex(idx)
            imported_type = tipo_value

    if data.get("doctor"):
        doctor_widget.setText(data["doctor"])

    return imported_case_id, imported_region, imported_type
