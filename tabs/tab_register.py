import json
import os
import sys

# Add parent directory to path for direct execution
if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import (
    QWidget, QFormLayout, QComboBox, QLineEdit,
    QPushButton, QLabel, QTimeEdit, QVBoxLayout, QHBoxLayout, QGroupBox, QProgressBar,
    QDateEdit, QTextEdit, QScrollArea, QStackedWidget, QTableWidget, QTableWidgetItem,
    QHeaderView, QMessageBox, QFrame, QSizePolicy, QApplication, QDialog,
)
from PySide6.QtCore import QTime, QDate, Qt, Signal, QPropertyAnimation, QEasingCurve, QTimer, QSize
from db.database import get_connection
from .utils import (
    get_resource_path,
    calculate_case_value as _calc_cv,
    load_units_eq_data,
    get_units_per_case as _ue_lookup,
    calculate_equivalent_units,
    DAILY_BASE_MINUTES,
)
from datetime import datetime
from .downtime_manager import DowntimeManager
from .toggle_switch import ToggleSwitch
from .widgets import TimeEditWithShortcut, DateEditWithShortcut, card, pro_card, labeled_field, _icon_url
from .theme_palette import apply_fluent_modal_palette
from .clipboard_import_ui import (
    get_clipboard_case_data,
    has_detected_case_fields,
    show_import_confirmation,
    build_import_summary,
    apply_imported_case_data,
    get_import_not_detected_message,
    get_import_success_message,
    get_import_reminder_message,
)
from .theme_table_utils import (
    apply_table_theme, CLR_FG_LIGHT, CLR_FG_DARK,
    get_light_theme_colors, light_row_bg, light_header_bg, light_header_fg, mix_hex,
)


class RegisterTab(QWidget):
    case_saved = Signal()       # regular case saved/updated
    ot_saved   = Signal()       # overtime case saved/updated
    downtime_changed = Signal() # any downtime mutation (add/edit/delete/status)

    def __init__(self):
        super().__init__()
        self._mode        = "regular"   # "regular" | "overtime"
        self._editing_id  = None        # db id being edited (None = new case)
        self._import_toast = None
        self._import_toast_timer = None
        # Theme state — main emits themeChanged(is_light) and update_theme_labels
        # writes this. False until first signal arrives (app boots dark).
        self._light_mode_active = False
        self._mode_state = {
            "regular": {},
            "overtime": {},
        }

        self.load_standards()
        self.load_units_eq()

        self.case_id = QLineEdit()
        self.case_id.setPlaceholderText("Enter Case ID")
        self.case_id.textChanged.connect(self.on_case_id_changed)
        self.region = QComboBox()
        self.tipo = QComboBox()
        self.doctor = QLineEdit()
        self.doctor.setPlaceholderText("Optional")

        self.start_time = TimeEditWithShortcut()
        self.start_time.setMinimumWidth(120)
        self.start_time.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.end_time = TimeEditWithShortcut()
        self.end_time.setMinimumWidth(120)
        self.end_time.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.end_time.timeChanged.connect(self.validate_end_time)

        self.case_date = DateEditWithShortcut()
        self.case_date.setDate(QDate.currentDate())
        self.case_date.dateChanged.connect(self.on_date_changed)
        self.case_date.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        # Calculation Result KPI labels — palette-aware.
        from .theme_palette import palette as _pal_kpi
        def _mk_kpi(value_color_key: str):
            v = QLabel("-")
            v.setAlignment(Qt.AlignmentFlag.AlignCenter)
            v.setMinimumHeight(30)
            def _apply(is_light: bool, _w=v, _ck=value_color_key):
                p = _pal_kpi(is_light)
                _w.setStyleSheet(
                    f"font-size: 18px; font-weight: 500; color: {p[_ck]};"
                )
            v.apply_palette = _apply
            _apply(False)
            return v

        def _mk_kpi_lbl(text: str):
            l = QLabel(text)
            l.setAlignment(Qt.AlignmentFlag.AlignCenter)
            def _apply(is_light: bool, _w=l):
                p = _pal_kpi(is_light)
                _w.setStyleSheet(
                    f"font-size: 10px; color: {p['muted']};"
                    " font-weight: 600; letter-spacing: 0.5px;"
                )
            l.apply_palette = _apply
            _apply(False)
            return l

        self._result_eff_value = _mk_kpi("accent_2")
        _result_eff_label = _mk_kpi_lbl("Efficiency")
        self._result_val_value = _mk_kpi("good")
        _result_val_label = _mk_kpi_lbl("Case Value")
        self._result_ue_value = _mk_kpi("info")  # purple-ish in dark via override
        _result_ue_label = _mk_kpi_lbl("Units. Eq")
        # Keep the UE colour distinctive (purple) only in dark mode; palette
        # accent does the job in light mode.
        def _ue_special(is_light: bool, _w=self._result_ue_value):
            p = _pal_kpi(is_light)
            color = p["accent"] if is_light else "#A371F7"
            _w.setStyleSheet(
                f"font-size: 18px; font-weight: 500; color: {color};"
            )
        self._result_ue_value.apply_palette = _ue_special
        _ue_special(False)

        # Don't let huge result numbers push the card wider — clamp each
        # value/label to ignore its preferred-width and let the parent layout
        # split horizontally.
        for _v in (self._result_eff_value, self._result_val_value, self._result_ue_value):
            _v.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            _v.setMinimumWidth(0)
        for _l in (_result_eff_label, _result_val_label, _result_ue_label):
            _l.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            _l.setMinimumWidth(0)

        _kpi_left = QVBoxLayout()
        _kpi_left.setSpacing(2)
        _kpi_left.addWidget(self._result_eff_value)
        _kpi_left.addWidget(_result_eff_label)

        _kpi_mid = QVBoxLayout()
        _kpi_mid.setSpacing(2)
        _kpi_mid.addWidget(self._result_val_value)
        _kpi_mid.addWidget(_result_val_label)

        _kpi_right = QVBoxLayout()
        _kpi_right.setSpacing(2)
        _kpi_right.addWidget(self._result_ue_value)
        _kpi_right.addWidget(_result_ue_label)

        def _vdivider():
            d = QFrame()
            d.setFrameShape(QFrame.Shape.VLine)
            def _apply(is_light: bool, _w=d):
                p = _pal_kpi(is_light)
                _w.setStyleSheet(f"color: {p['border_strong']};")
            d.apply_palette = _apply
            _apply(False)
            return d

        _kpi_row = QHBoxLayout()
        _kpi_row.setSpacing(6)
        _kpi_row.setContentsMargins(0, 0, 0, 0)
        # Equal stretch so each KPI column claims a fair slice of the card.
        _kpi_row.addLayout(_kpi_left, 1)
        _kpi_row.addWidget(_vdivider())
        _kpi_row.addLayout(_kpi_mid, 1)
        _kpi_row.addWidget(_vdivider())
        _kpi_row.addLayout(_kpi_right, 1)

        # Small status label for errors/messages (shown below KPI row)
        self.result_label = QLabel()
        self.result_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.result_label.setWordWrap(True)
        def _apply_result_lbl(is_light: bool, _w=self.result_label):
            p = _pal_kpi(is_light)
            _w.setStyleSheet(f"font-size: 11px; color: {p['muted']};")
        self.result_label.apply_palette = _apply_result_lbl
        _apply_result_lbl(False)
        self.result_label.setMinimumHeight(20)
        self.result_label.setVisible(False)

        result_kpi_layout = QVBoxLayout()
        result_kpi_layout.setContentsMargins(2, 4, 2, 4)
        result_kpi_layout.setSpacing(8)
        result_kpi_layout.addLayout(_kpi_row)
        result_kpi_layout.addWidget(self.result_label)

        self.daily_production_label = QLabel("Daily Production: 0.00%")
        self.equivalent_units_label = QLabel("Equivalent Units: 0.00")
        self._apply_kpi_label_styles(is_light=False)

        self.region.addItems(self.standards.keys())
        self.region.currentTextChanged.connect(self.update_case_types)
        
        self.update_case_types()

        self.start_time.setTime(QTime.currentTime())
        self.end_time.setTime(QTime(0, 0))  # Empty/default value

        calc_btn = QPushButton("Calculate")
        calc_btn.setMinimumHeight(32)
        calc_btn.setMaximumHeight(34)
        calc_btn.setObjectName("accentOutline")
        def _apply_calc_btn(is_light: bool, _b=calc_btn):
            from .theme_palette import palette as _p
            p = _p(is_light)
            acc = p["accent_2"] if not is_light else p["accent"]
            _b.setStyleSheet(
                f"QPushButton#accentOutline {{ background: transparent;"
                f"  border: 1px solid {acc}; border-radius: 8px;"
                f"  color: {acc}; font-weight: 700; font-size: 11px;"
                f"  padding: 7px 16px; }}"
                f"QPushButton#accentOutline:hover {{ background: rgba(56,139,253,0.10); }}"
                f"QPushButton#accentOutline:pressed {{ background: rgba(56,139,253,0.18); }}"
            )
        calc_btn.apply_palette = _apply_calc_btn
        _apply_calc_btn(False)
        calc_btn.clicked.connect(self.calculate)

        self._save_btn = QPushButton("Save Case")
        self._save_btn.setMinimumHeight(32)
        self._save_btn.setMaximumHeight(34)
        self._save_btn.setObjectName("primary")
        self._save_btn.clicked.connect(self.save_case)
        # Floppy/save icon — matches the target design.
        try:
            from .tabler_icons import TablerIcon as _TI_save
            from PySide6.QtGui import QColor as _QColor
            self._save_btn.setIcon(_TI_save("tabler_device_floppy.svg").icon(color=_QColor("#FFFFFF")))
            from PySide6.QtCore import QSize as _QSize
            self._save_btn.setIconSize(_QSize(16, 16))
        except Exception:
            pass
        save_btn = self._save_btn

        # Form: label on LEFT, input on RIGHT (same row).
        form = QFormLayout()
        form.setVerticalSpacing(7)
        form.setHorizontalSpacing(10)
        form.setContentsMargins(0, 0, 0, 0)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)

        def _flabel(text: str) -> QLabel:
            l = QLabel(text)
            def _apply(is_light: bool, _w=l):
                p = _pal_kpi(is_light)
                _w.setStyleSheet(
                    f"color: {p['text_2']}; font-size: 11px; font-weight: 700;"
                    " background: transparent; padding-right: 4px;"
                )
            l.apply_palette = _apply
            _apply(False)
            l.setMinimumWidth(72)
            l.setMaximumWidth(82)
            return l

        form.addRow(_flabel("Case ID"), self.case_id)
        form.addRow(_flabel("Region"),  self.region)
        form.addRow(_flabel("Type"),    self.tipo)
        form.addRow(_flabel("Doctor"),  self.doctor)
        form.addRow(_flabel("Date"),    self.case_date)
        form.addRow(_flabel("Start"),   self.start_time)
        form.addRow(_flabel("End"),     self.end_time)

        for _w in (self.case_id, self.region, self.tipo, self.doctor,
                   self.case_date, self.start_time, self.end_time):
            _w.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            _w.setMinimumHeight(30)
            _w.setMaximumHeight(32)

        # Leading icons inside Date / Start / End inputs.
        try:
            from .tabler_icons import TablerIcon as _TI_field
            from PySide6.QtGui import QAction
            from PySide6.QtCore import QSize as _QS
            from PySide6.QtGui import QColor as _QC

            _cal_icon = _TI_field("tabler_calendar.svg").icon(color=_QC("#8B949E"))
            _clk_icon = _TI_field("tabler_clock.svg").icon(color=_QC("#8B949E"))
            for _ed, _icn in (
                (self.case_date, _cal_icon),
                (self.start_time, _clk_icon),
                (self.end_time, _clk_icon),
            ):
                try:
                    le = _ed.lineEdit() if hasattr(_ed, "lineEdit") else None
                    if le is not None:
                        act = QAction(_icn, "", le)
                        le.addAction(act, QLineEdit.ActionPosition.LeadingPosition)
                except Exception:
                    pass
        except Exception:
            pass

        # "Count to production?" toggle — full-width row (label spans the
        # field column so it isn't clipped, switch sits on the right).
        self.count_toggle = ToggleSwitch(checked=True)
        toggle_holder = QWidget()
        toggle_row = QHBoxLayout(toggle_holder)
        toggle_row.setContentsMargins(0, 6, 0, 0)
        toggle_row.setSpacing(8)
        count_lbl = QLabel("Count to production?")
        def _apply_count_lbl(is_light: bool, _w=count_lbl):
            p = _pal_kpi(is_light)
            _w.setStyleSheet(
                f"color: {p['text_2']}; font-size: 11px; font-weight: 700;"
                f" background: transparent;"
            )
        count_lbl.apply_palette = _apply_count_lbl
        _apply_count_lbl(False)
        toggle_row.addWidget(count_lbl)
        toggle_row.addStretch()
        # Small icon button to view/edit work segments. Only enabled when
        # editing an NC case that has 2+ accumulated segments — for a
        # fresh form or a single-session case it's disabled placeholder.
        from PySide6.QtWidgets import QToolButton as _QTB_seg
        self._segments_btn = _QTB_seg()
        self._segments_btn.setCursor(Qt.PointingHandCursor)
        self._segments_btn.setFixedSize(28, 28)
        self._segments_btn.setToolTip("View / edit work time segments")
        self._segments_btn.setEnabled(False)
        try:
            from .tabler_icons import TablerIcon as _TI_seg
            from PySide6.QtGui import QColor as _QC_seg
            from PySide6.QtCore import QSize as _QS_seg
            self._segments_btn.setIcon(
                _TI_seg("tabler_clock.svg").icon(color=_QC_seg("#8B949E"))
            )
            self._segments_btn.setIconSize(_QS_seg(15, 15))
        except Exception:
            self._segments_btn.setText("⏱")

        def _apply_seg_btn(is_light: bool, _b=self._segments_btn):
            p = _pal_kpi(is_light)
            try:
                from .tabler_icons import TablerIcon as _TI_segp
                from PySide6.QtGui import QColor as _QC_segp
                _b.setIcon(
                    _TI_segp("tabler_clock.svg").icon(color=_QC_segp(p["muted"]))
                )
            except Exception:
                pass
            hover = "rgba(0,0,0,0.06)" if is_light else "rgba(255,255,255,0.06)"
            _b.setStyleSheet(
                f"QToolButton {{ background: transparent;"
                f"  border: 1px solid {p['border_strong']};"
                f"  border-radius: 6px; padding: 0; }}"
                f"QToolButton:hover:enabled {{ background: {hover}; }}"
                f"QToolButton:disabled {{ border-color: {p['border']}; }}"
            )
        self._segments_btn.apply_palette = _apply_seg_btn
        _apply_seg_btn(False)
        self._segments_btn.clicked.connect(self._open_segments_dialog)
        toggle_row.addWidget(self._segments_btn)
        toggle_row.addWidget(self.count_toggle)
        # Use the form's spanning row form (single widget, no separate label).
        form.addRow(toggle_holder)

        # Import button — outlined, palette-aware so it works in both themes.
        import_web_btn = QPushButton("Import")
        import_web_btn.setMinimumHeight(32)
        import_web_btn.setMaximumHeight(34)

        def _restyle_import_btn(is_light: bool, _b=import_web_btn):
            try:
                from .theme_palette import palette
                p = palette(is_light)
            except Exception:
                p = {"text": "#E6EDF3", "border_strong": "#30363D",
                     "muted_2": "#58606A"}
            hover = "rgba(0,0,0,0.04)" if is_light else "rgba(255,255,255,0.04)"
            press = "rgba(0,0,0,0.08)" if is_light else "rgba(255,255,255,0.08)"
            _b.setStyleSheet(
                f"QPushButton {{ background: transparent;"
                f"  border: 1px solid {p['border_strong']};"
                f"  color: {p['text']}; border-radius: 8px; font-weight: 700;"
                f"  font-size: 11px; padding: 7px 16px; }}"
                f"QPushButton:hover {{ background: {hover};"
                f"  border-color: {p['muted_2']}; }}"
                f"QPushButton:pressed {{ background: {press}; }}"
            )
        import_web_btn.apply_palette = _restyle_import_btn  # type: ignore[attr-defined]
        try:
            from qfluentwidgets.common.style_sheet import isDarkTheme
            _restyle_import_btn(not isDarkTheme())
        except Exception:
            _restyle_import_btn(False)
        import_web_btn.setToolTip(
            "Copy all text on the case page (Ctrl+A, Ctrl+C),\n"
            "then click here or press Ctrl+Shift+I to auto-fill the fields."
        )
        import_web_btn.clicked.connect(self._on_import_case)

        # Row 1: Import + Calculate split 50/50; Row 2: full-width Save.
        _secondary_row = QHBoxLayout()
        _secondary_row.setSpacing(8)
        _secondary_row.addWidget(import_web_btn, 1)
        _secondary_row.addWidget(calc_btn, 1)

        # Small icon-only flag button — sits inline next to Save Case so
        # the Case Information card stays the same height.
        from PySide6.QtWidgets import QToolButton as _QTBrev
        review_btn = _QTBrev()
        review_btn.setCursor(Qt.PointingHandCursor)
        review_btn.setFixedSize(34, 34)
        review_btn.setToolTip("Flag this case for follow-up review")
        try:
            from .tabler_icons import TablerIcon as _TI_rev
            from PySide6.QtGui import QColor as _QC_rev
            from PySide6.QtCore import QSize as _QS_rev
            review_btn.setIcon(
                _TI_rev("tabler_flag.svg").icon(color=_QC_rev("#D29922"))
            )
            review_btn.setIconSize(_QS_rev(16, 16))
        except Exception:
            pass
        review_btn.setStyleSheet(
            "QToolButton { background: transparent; border: 1px solid #D29922;"
            "  border-radius: 6px; }"
            "QToolButton:hover { background: rgba(210,153,34,0.12); }"
        )
        review_btn.clicked.connect(self._on_add_to_review)

        # Info icon next to the flag button explaining what the flag is for.
        review_info_btn = _QTBrev()
        review_info_btn.setCursor(Qt.PointingHandCursor)
        review_info_btn.setFixedSize(22, 22)
        review_info_btn.setToolTip("What does the flag do?")
        try:
            review_info_btn.setIcon(
                _TI_rev("tabler_info_circle.svg").icon(color=_QC_rev("#6E7681"))
            )
            review_info_btn.setIconSize(_QS_rev(14, 14))
        except Exception:
            pass
        review_info_btn.setStyleSheet(
            "QToolButton { background: transparent; border: none; }"
            "QToolButton:hover { background: rgba(255,255,255,0.06);"
            "  border-radius: 4px; }"
        )
        review_info_btn.clicked.connect(self._show_review_help)

        save_row = QHBoxLayout()
        save_row.setSpacing(8)
        save_row.setContentsMargins(0, 0, 0, 0)
        save_row.addWidget(save_btn, 1)
        save_row.addWidget(review_btn, 0)
        save_row.addWidget(review_info_btn, 0)

        buttons_layout = QVBoxLayout()
        buttons_layout.setSpacing(10)
        buttons_layout.setContentsMargins(0, 4, 0, 0)
        buttons_layout.addLayout(_secondary_row)
        buttons_layout.addLayout(save_row)
        save_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        # Daily production card — gauge on the left, stats on the right,
        # then a divider + Equivalent Units + total progress bar.
        from .gauge_widget import GaugeWidget
        self._daily_gauge = GaugeWidget()
        self._daily_gauge.setFixedSize(170, 110)

        # Right-side stat list (3 rows).
        def _stat_row(label: str, color: str):
            row = QVBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(1)
            lbl = QLabel(label)
            val = QLabel("0.00%")
            def _apply(is_light: bool, _l=lbl, _v=val, _c=color):
                p = _pal_kpi(is_light)
                _l.setStyleSheet(
                    f"color: {p['muted']}; font-size: 11px; font-weight: 600;"
                    f" background: transparent;"
                )
                _v.setStyleSheet(
                    f"color: {_c}; font-size: 13px; font-weight: 700;"
                    f" background: transparent;"
                )
            lbl.apply_palette = _apply
            _apply(False)
            row.addWidget(lbl)
            row.addWidget(val)
            return row, val

        _stats_col = QVBoxLayout()
        _stats_col.setContentsMargins(0, 4, 0, 4)
        _stats_col.setSpacing(6)
        _dp_row, self._daily_prod_value = _stat_row("Daily Production", "#388BFD")
        _cs_row, self._daily_cases_value = _stat_row("Cases", "#388BFD")
        _dt_row, self._daily_dt_value = _stat_row("Downtime", "#F0883E")
        _stats_col.addLayout(_dp_row)
        _stats_col.addLayout(_cs_row)
        _stats_col.addLayout(_dt_row)
        _stats_col.addStretch()

        _top_row = QHBoxLayout()
        _top_row.setSpacing(14)
        _top_row.addWidget(self._daily_gauge, 0)
        _top_row.addLayout(_stats_col, 1)

        # Target line — "─── Target: 95% ───" (divider on each side).
        def _hline():
            d = QFrame()
            d.setFixedHeight(1)
            def _apply(is_light: bool, _w=d):
                p = _pal_kpi(is_light)
                _w.setStyleSheet(f"background: {p['border']}; border: none;")
            d.apply_palette = _apply
            _apply(False)
            d.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            return d

        self._daily_target_label = QLabel("Target: 95%")
        self._daily_target_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        def _apply_target_lbl(is_light: bool, _w=self._daily_target_label):
            p = _pal_kpi(is_light)
            color = p["accent"] if is_light else "#A371F7"
            _w.setStyleSheet(
                f"color: {color}; font-size: 11px; font-weight: 700;"
                f" background: transparent; padding: 0 10px;"
            )
        self._daily_target_label.apply_palette = _apply_target_lbl
        _apply_target_lbl(False)
        _target_row = QHBoxLayout()
        _target_row.setSpacing(0)
        _target_row.addWidget(_hline(), 1, Qt.AlignVCenter)
        _target_row.addWidget(self._daily_target_label, 0, Qt.AlignVCenter)
        _target_row.addWidget(_hline(), 1, Qt.AlignVCenter)

        # Equivalent units row
        _eu_row = QHBoxLayout()
        _eu_left = QLabel("Equivalent Units")
        self._eu_value = QLabel("0.00")
        _eu_target_lbl = QLabel("Target:")
        self._eu_target_value = QLabel("15.00")

        def _apply_eu_row(is_light: bool):
            p = _pal_kpi(is_light)
            accent_lbl = p["accent"] if is_light else "#A371F7"
            for _w in (_eu_left, _eu_target_lbl):
                _w.setStyleSheet(
                    f"color: {accent_lbl}; font-size: 11px; font-weight: 700;"
                    f" background: transparent;"
                )
            for _v in (self._eu_value, self._eu_target_value):
                _v.setStyleSheet(
                    f"color: {p['text']}; font-size: 12px; font-weight: 700;"
                    f" background: transparent;"
                )
        _eu_left.apply_palette = lambda is_light, _f=_apply_eu_row: _f(is_light)
        _apply_eu_row(False)
        _eu_row.addWidget(_eu_left)
        _eu_row.addWidget(self._eu_value)
        _eu_row.addStretch()
        _eu_row.addWidget(_eu_target_lbl)
        _eu_row.addWidget(self._eu_target_value)

        # Full-width progress bar at the bottom.
        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("%v%")
        self.progress_bar.setMinimumHeight(22)

        progress_layout = QVBoxLayout()
        progress_layout.setSpacing(10)
        progress_layout.addLayout(_top_row)
        progress_layout.addLayout(_target_row)
        progress_layout.addLayout(_eu_row)
        progress_layout.addWidget(self.progress_bar)

        try:
            from .tabler_icons import TablerIcon as _TI_dp
            _dp_icon = _TI_dp("tabler_target.svg")
        except Exception:
            _dp_icon = None

        # Gear button to configure the shift hours (regional differences).
        self._shift_settings_btn = QPushButton()
        self._shift_settings_btn.setCursor(Qt.PointingHandCursor)
        self._shift_settings_btn.setFixedSize(24, 24)
        self._shift_settings_btn.setToolTip("Configure shift hours")
        try:
            from .tabler_icons import TablerIcon as _TI_gear
            from PySide6.QtGui import QColor as _QC_gear
            from PySide6.QtCore import QSize as _QS_gear
            self._shift_settings_btn.setIcon(
                _TI_gear("tabler_settings.svg").icon(color=_QC_gear("#8B949E"))
            )
            self._shift_settings_btn.setIconSize(_QS_gear(15, 15))
        except Exception:
            pass
        def _apply_gear(is_light: bool, _b=self._shift_settings_btn):
            hover = "rgba(0,0,0,0.08)" if is_light else "rgba(255,255,255,0.08)"
            _b.setStyleSheet(
                f"QPushButton {{ background: transparent; border: none;"
                f"  border-radius: 12px; padding: 0; }}"
                f"QPushButton:hover {{ background: {hover}; }}"
            )
        self._shift_settings_btn.apply_palette = _apply_gear
        _apply_gear(False)
        self._shift_settings_btn.clicked.connect(self._open_shift_config)

        # Title reflects the current shift window — recomputed on save.
        self._daily_shift_label = "Daily Production " + self._format_shift_label()
        self.progress_group = pro_card(
            self._daily_shift_label, progress_layout,
            icon=_dp_icon, accent="#E6EDF3",
            header_extra=self._shift_settings_btn,
        )
        self.progress_group.setMinimumHeight(325)
        self.progress_group.setMaximumHeight(325)

        # Create left card (Case Information + Calculation Result)
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setSpacing(12)
        left_layout.setContentsMargins(10, 10, 10, 10)
        
        # Case Information card body = form + action buttons inside one widget.
        # case_body has Preferred-fixed vertical sizePolicy so the body never
        # gets stretched by its parent — that prevents any phantom gap
        # appearing between the form and the buttons when the card is in a
        # taller column.
        case_body = QWidget()
        case_body.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        _case_body_lay = QVBoxLayout(case_body)
        _case_body_lay.setContentsMargins(0, 0, 0, 0)
        _case_body_lay.setSpacing(12)
        _case_body_lay.addLayout(form)
        _case_body_lay.addLayout(buttons_layout)
        _case_body_lay.addSpacing(28)

        try:
            from .tabler_icons import TablerIcon as _TI_card
            _case_icon = _TI_card("tabler_file.svg")
        except Exception:
            _case_icon = None

        # Inline Cancel button — lives in the Case Information card
        # header (right side). Visible only in edit mode. Replaces the
        # old floating "EDIT MODE" banner.
        self._inline_cancel_btn = QPushButton("Cancel")
        self._inline_cancel_btn.setVisible(False)
        self._inline_cancel_btn.setCursor(Qt.PointingHandCursor)
        self._inline_cancel_btn.setFixedHeight(26)
        try:
            from .tabler_icons import TablerIcon as _TI_cx
            from PySide6.QtGui import QColor as _QC_cx
            from PySide6.QtCore import QSize as _QS_cx
            self._inline_cancel_btn.setIcon(
                _TI_cx("tabler_x.svg").icon(color=_QC_cx("#F85149"))
            )
            self._inline_cancel_btn.setIconSize(_QS_cx(12, 12))
        except Exception:
            pass
        self._inline_cancel_btn.setStyleSheet(
            "QPushButton { background: transparent; border: 1px solid #F85149;"
            "  color: #F85149; border-radius: 5px; padding: 2px 10px;"
            "  font-weight: 700; font-size: 11px; }"
            "QPushButton:hover { background: rgba(248,81,73,0.10); }"
        )
        self._inline_cancel_btn.clicked.connect(self._cancel_edit)

        self._case_info_card = case_info_card = pro_card(
            "Case Information", case_body,
            icon=_case_icon, accent="#E6EDF3",
            header_extra=self._inline_cancel_btn,
        )
        # Shave 2 px off the bottom of just this card so it sits a touch
        # tighter without affecting the other pro_cards.
        try:
            _lay = case_info_card.layout()
            if _lay is not None:
                _m = _lay.contentsMargins()
                _lay.setContentsMargins(
                    _m.left(), _m.top(), _m.right(), max(0, _m.bottom() - 2),
                )
        except Exception:
            pass
        case_info_card.setSizePolicy(QSizePolicy.Policy.Expanding,
                                     QSizePolicy.Policy.Maximum)

        left_layout.addWidget(case_info_card)

        # â"€â"€ Right panel â€" Calc Result + Comments at top, Downtime below â"€â"€
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setSpacing(12)
        right_layout.setContentsMargins(10, 10, 10, 10)

        # Calculation Result + Comments share a single horizontal row at top.
        try:
            from qfluentwidgets import FluentIcon as _FIF_calc
            _calc_icon = _FIF_calc.PIE_SINGLE
        except Exception:
            _calc_icon = None
        calc_card = pro_card("Calculation Result", result_kpi_layout,
                             icon=_calc_icon, accent="#E6EDF3")
        self._calc_card = calc_card

        # Hidden backing store so the rest of the code still reads/writes
        # comments via self.comments_input — UI surface is now two chip
        # buttons that open a modal dialog instead of an inline textarea.
        self.comments_input = QTextEdit()
        self.comments_input.hide()
        self.comments_input.textChanged.connect(
            lambda: self._refresh_comment_chips()
            if hasattr(self, "_btn_see_comment") else None
        )

        comments_chips = QWidget()
        _cc_outer = QVBoxLayout(comments_chips)
        _cc_outer.setContentsMargins(0, 4, 0, 4)
        _cc_outer.setSpacing(12)
        _cc_outer.addStretch(1)  # center vertically inside the card
        _cc_lay = QHBoxLayout()
        _cc_lay.setContentsMargins(0, 0, 0, 0)
        _cc_lay.setSpacing(8)

        def _chip_css_for(is_light: bool) -> str:
            p = _pal_kpi(is_light)
            hover = "rgba(0,0,0,0.06)" if is_light else "rgba(255,255,255,0.06)"
            press = "rgba(0,0,0,0.10)" if is_light else "rgba(255,255,255,0.10)"
            return (
                f"QPushButton {{ background: transparent;"
                f"  border: 1px solid {p['border_strong']};"
                f"  color: {p['text']}; border-radius: 14px; font-weight: 700;"
                f"  font-size: 11px; padding: 6px 14px; }}"
                f"QPushButton:hover {{ background: {hover};"
                f"  border-color: {p['muted_2']}; }}"
                f"QPushButton:pressed {{ background: {press}; }}"
                f"QPushButton:disabled {{ color: {p['muted_2']};"
                f"  border-color: {p['border']}; }}"
            )

        self._btn_add_comment = QPushButton("Add comment")
        self._btn_add_comment.setCursor(Qt.PointingHandCursor)
        self._btn_add_comment.clicked.connect(
            lambda: self._open_comment_dialog(read_only=False)
        )
        self._btn_see_comment = QPushButton("See comment")
        self._btn_see_comment.setCursor(Qt.PointingHandCursor)
        def _apply_chips(is_light: bool):
            css = _chip_css_for(is_light)
            self._btn_add_comment.setStyleSheet(css)
            self._btn_see_comment.setStyleSheet(css)
        self._btn_add_comment.apply_palette = _apply_chips
        _apply_chips(False)
        self._btn_see_comment.clicked.connect(
            lambda: self._open_comment_dialog(read_only=True)
        )
        # Initial state: nothing written, so "See comment" disabled.
        self._refresh_comment_chips()

        _cc_lay.addWidget(self._btn_add_comment)
        _cc_lay.addWidget(self._btn_see_comment)
        _cc_lay.addStretch()
        _cc_outer.addLayout(_cc_lay)

        # One-line ellipsised preview of the comment, sits under the chips.
        self._comment_preview = QLabel("")
        self._comment_preview.setStyleSheet(
            "color: #C9D1D9; font-size: 12px; background: transparent;"
            " padding-left: 2px;"
        )
        self._comment_preview.setVisible(False)
        # Don't let long comment text push the card wider — let it shrink and
        # ignore its sizeHint so the parent layout stays in control.
        self._comment_preview.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred,
        )
        self._comment_preview.setMinimumWidth(0)
        self._comment_preview.setWordWrap(False)
        _cc_outer.addWidget(self._comment_preview)
        _cc_outer.addStretch(1)  # symmetric bottom stretch — keeps block centered

        try:
            from .tabler_icons import TablerIcon as _TI_cmt
            _cmt_icon = _TI_cmt("tabler_message_circle.svg")
        except Exception:
            _cmt_icon = None
        comments_card = pro_card(
            "Comments (Optional)", comments_chips,
            icon=_cmt_icon, accent="#E6EDF3",
        )
        self._comments_card = comments_card

        # Force equal width for both cards in the row.
        for _c in (calc_card, comments_card):
            _c.setSizePolicy(QSizePolicy.Policy.Expanding,
                             QSizePolicy.Policy.Preferred)
        top_row = QHBoxLayout()
        top_row.setSpacing(12)
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.addWidget(calc_card, 1)
        top_row.addWidget(comments_card, 1)
        right_layout.addLayout(top_row)

        def _on_downtime_changed():
            self.load_daily_production()
            # Notify other tabs (Dashboard, History) so they re-query.
            self.downtime_changed.emit()
        self.downtime_manager = DowntimeManager(on_update_callback=_on_downtime_changed)
        self.downtime_manager.setMinimumHeight(280)
        self.downtime_manager.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        try:
            from .tabler_icons import TablerIcon as _TI_dt
            _dt_icon = _TI_dt("tabler_clock.svg")
        except Exception:
            _dt_icon = None
        self.downtime_card = pro_card(
            "Downtime", self.downtime_manager,
            icon=_dt_icon, accent="#E6EDF3",
        )
        self.downtime_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        # Sized to make downtime's bottom align with Case Information's bottom
        # in the left column (which is naturally ~370-420px tall).
        self.downtime_card.setMinimumHeight(340)
        self.downtime_card.setMaximumHeight(380)
        right_layout.addWidget(self.downtime_card)

        # Regular cases mini-table (for selected date)
        self.reg_day_table = QTableWidget()
        self.reg_day_table.setColumnCount(7)
        self.reg_day_table.setHorizontalHeaderLabels(
            ["CASE ID", "DOCTOR", "TYPE", "TIME", "EFF %", "VALUE %", "UE"]
        )
        self.reg_day_table.verticalHeader().setVisible(False)
        self.reg_day_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.reg_day_table.setAlternatingRowColors(False)
        self.reg_day_table.setShowGrid(True)
        self.reg_day_table.setAlternatingRowColors(False)
        self.reg_day_table.setShowGrid(True)
        # Push the scrollbar to the right outside the rounded border so it
        # doesn't clip the corner radius.
        self.reg_day_table.setViewportMargins(0, 0, 4, 0)
        # Use palette to set the table base bg so the QSS doesn't need a
        # background-color rule (which would override per-cell brushes).
        from PySide6.QtGui import QPalette, QColor as _QC_tbl
        from .theme_palette import palette as _pal_fn

        def _restyle_reg_table(is_light: bool, _tbl=self.reg_day_table):
            p = _pal_fn(is_light)
            _q = _tbl.palette()
            _q.setColor(QPalette.Base, _QC_tbl(p["base"]))
            _q.setColor(QPalette.Text, _QC_tbl(p["text"]))
            _tbl.setPalette(_q)
            _tbl.setStyleSheet(f"""
                QTableWidget {{
                    border: 1px solid {p['border']};
                    border-radius: 10px;
                    gridline-color: {p['border']};
                    outline: none;
                }}
                QTableWidget::item {{ padding: 6px 8px; }}
                QTableWidget::item:selected {{ background-color: {p['selection']}; }}
                QHeaderView {{ background: transparent; border: none; }}
                QHeaderView::section {{
                    background-color: {p['surface']};
                    color: {p['muted']};
                    padding: 8px 8px;
                    border: none;
                    border-bottom: 1px solid {p['border']};
                    font-weight: 700;
                    font-size: 10px;
                }}
                QHeaderView::section:first {{ border-top-left-radius: 10px; }}
                QHeaderView::section:last  {{ border-top-right-radius: 10px; }}
                QScrollBar:vertical {{ background: transparent; width: 6px;
                    margin: 4px 0 4px 0; }}
                QScrollBar::handle:vertical {{ background: {p['border_strong']};
                    border-radius: 3px; min-height: 24px; }}
                QScrollBar::handle:vertical:hover {{ background: {p['muted_2']}; }}
                QScrollBar::add-line:vertical,
                QScrollBar::sub-line:vertical {{ height: 0; }}
                QScrollBar::add-page:vertical,
                QScrollBar::sub-page:vertical {{ background: transparent; }}
            """)
        self.reg_day_table.apply_palette = _restyle_reg_table
        try:
            from qfluentwidgets.common.style_sheet import isDarkTheme
            _restyle_reg_table(not isDarkTheme())
        except Exception:
            _restyle_reg_table(False)
        # Reset the viewport margins — instead push the first column header's
        # left padding so "Case ID" lines up with the card title text above it.
        self.reg_day_table.setViewportMargins(0, 0, 0, 0)
        _hdr_view = self.reg_day_table.horizontalHeader()
        _hdr_view.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        # Make the table fill the card width like the Downtime table does:
        # Doctor stretches to consume any leftover horizontal space; the rest
        # stay at fixed widths so they don't clip the headers.
        _hdr = self.reg_day_table.horizontalHeader()
        _hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        _hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        _hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        _hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        _hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        _hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        _hdr.setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        _hdr.setStretchLastSection(False)
        self.reg_day_table.setColumnWidth(0, 92)   # Case ID
        self.reg_day_table.setColumnWidth(1, 132)  # Doctor (stretches; this is the min)
        self.reg_day_table.setColumnWidth(2, 86)   # Type
        self.reg_day_table.setColumnWidth(3, 56)   # Time
        self.reg_day_table.setColumnWidth(4, 60)   # Eff %
        self.reg_day_table.setColumnWidth(5, 70)   # Value %
        self.reg_day_table.setColumnWidth(6, 56)   # UE
        # Sized to fit the header + 5 rows exactly (no trailing empty space).
        self.reg_day_table.setMinimumHeight(186)
        self.reg_day_table.setMaximumHeight(206)
        self.reg_day_table.verticalHeader().setDefaultSectionSize(30)
        self.reg_day_table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        try:
            from .tabler_icons import TablerIcon as _TI_tbl
            _tbl_icon = _TI_tbl("tabler_file_analytics.svg")
            _search_icon = _TI_tbl("tabler_search.svg")
            _filter_icon = _TI_tbl("tabler_filter.svg")
        except Exception:
            _tbl_icon = _search_icon = _filter_icon = None

        # Header right side: search box + Filters pill button.
        from PySide6.QtCore import QSize as _QS_tc
        _header_extra = QWidget()
        _he_lay = QHBoxLayout(_header_extra)
        _he_lay.setContentsMargins(0, 0, 0, 0)
        _he_lay.setSpacing(8)
        self._reg_search = QLineEdit()
        self._reg_search.setPlaceholderText("Search case or doctor…")
        self._reg_search.setMinimumWidth(220)
        self._reg_search.setFixedHeight(28)
        def _restyle_reg_search(is_light: bool, _le=self._reg_search):
            p = _pal_fn(is_light)
            _le.setStyleSheet(
                f"QLineEdit {{ background: {p['surface']};"
                f"  border: 1px solid {p['border_strong']};"
                f"  border-radius: 6px; padding: 4px 12px 4px 30px;"
                f"  color: {p['text']}; font-size: 11px; }}"
                f"QLineEdit:focus {{ border-color: {p['accent_2']}; }}"
            )
        self._reg_search.apply_palette = _restyle_reg_search
        _restyle_reg_search(False)
        if _search_icon is not None:
            from PySide6.QtGui import QAction as _QA_s, QColor as _QC_s
            from PySide6.QtWidgets import QLineEdit as _QLE_s
            _act = _QA_s(_search_icon.icon(color=_QC_s("#8B949E")), "", self._reg_search)
            self._reg_search.addAction(_act, _QLE_s.ActionPosition.LeadingPosition)
        _he_lay.addWidget(self._reg_search)

        self._reg_filter_btn = QPushButton("Filters")
        self._reg_filter_btn.setFixedHeight(28)
        self._reg_filter_btn.setCursor(Qt.PointingHandCursor)

        def _restyle_reg_filter(is_light: bool, _b=self._reg_filter_btn):
            p = _pal_fn(is_light)
            _b.setStyleSheet(
                f"QPushButton {{ background: {p['surface']};"
                f"  border: 1px solid {p['border_strong']};"
                f"  border-radius: 6px; color: {p['accent_2']}; font-weight: 700;"
                f"  font-size: 11px; padding: 4px 14px; }}"
                f"QPushButton:hover {{ background: rgba(56,139,253,0.08);"
                f"  border-color: {p['accent_2']}; }}"
            )
        self._reg_filter_btn.apply_palette = _restyle_reg_filter
        _restyle_reg_filter(False)
        if _filter_icon is not None:
            from PySide6.QtGui import QColor as _QC_f
            self._reg_filter_btn.setIcon(_filter_icon.icon(color=_QC_f("#388BFD")))
            self._reg_filter_btn.setIconSize(_QS_tc(14, 14))
        _he_lay.addWidget(self._reg_filter_btn)

        # Active filter state — applied by _filter_reg_table together with
        # the search query. None = no filter for that field.
        self._reg_filter_region = None
        self._reg_filter_type = None

        self._reg_search.textChanged.connect(self._filter_reg_table)
        self._reg_filter_btn.clicked.connect(self._open_reg_filter_dialog)

        # Wrap the table + pagination footer in a vertical container so the
        # footer sits flush under the table inside the card.
        _tbl_wrap = QWidget()
        _tbl_wrap_lay = QVBoxLayout(_tbl_wrap)
        _tbl_wrap_lay.setContentsMargins(8, 0, 8, 0)
        _tbl_wrap_lay.setSpacing(6)
        # Empty-state placeholder swapped with the table when 0 cases match.
        self._reg_empty_state = self._build_reg_empty_state()
        self._reg_table_stack = QStackedWidget()
        self._reg_table_stack.addWidget(self.reg_day_table)       # index 0
        self._reg_table_stack.addWidget(self._reg_empty_state)    # index 1
        _tbl_wrap_lay.addWidget(self._reg_table_stack)

        # Pagination footer.
        _pg_row = QHBoxLayout()
        _pg_row.setContentsMargins(0, 4, 0, 0)
        _pg_row.setSpacing(8)
        self._reg_page_label = QLabel("Showing 0 of 0 cases")
        _pg_row.addWidget(self._reg_page_label)
        _pg_row.addStretch()

        self._reg_prev_btn = QPushButton("‹")
        self._reg_prev_btn.setCursor(Qt.PointingHandCursor)
        self._reg_page_btn = QPushButton("1")
        self._reg_next_btn = QPushButton("›")
        self._reg_next_btn.setCursor(Qt.PointingHandCursor)

        def _apply_pager(is_light: bool):
            p = _pal_kpi(is_light)
            arrow_css = (
                f"QPushButton {{ background: transparent; border: none;"
                f"  color: {p['muted']}; min-width: 20px; min-height: 22px;"
                f"  font-size: 16px; font-weight: 700; padding: 0; }}"
                f"QPushButton:hover {{ color: {p['text']}; }}"
                f"QPushButton:disabled {{ color: {p['muted_2']}; }}"
            )
            self._reg_page_label.setStyleSheet(
                f"color: {p['muted']}; font-size: 11px;"
                f" background: transparent;"
            )
            self._reg_prev_btn.setStyleSheet(arrow_css)
            self._reg_next_btn.setStyleSheet(arrow_css)
            self._reg_page_btn.setStyleSheet(
                f"QPushButton {{ background: transparent;"
                f"  border: 1px solid {p['accent_2']};"
                f"  border-radius: 5px; color: {p['accent_2']};"
                f"  min-width: 22px; min-height: 20px;"
                f"  font-size: 10px; font-weight: 700; padding: 0 6px; }}"
            )
        self._reg_page_label.apply_palette = _apply_pager
        _apply_pager(False)
        _pg_row.addWidget(self._reg_prev_btn)
        _pg_row.addWidget(self._reg_page_btn)
        _pg_row.addWidget(self._reg_next_btn)

        self._reg_page_size_combo = QComboBox()
        self._reg_page_size_combo.addItems(["5 / page", "10 / page", "20 / page", "50 / page"])
        def _apply_pg_combo(is_light: bool, _c=self._reg_page_size_combo):
            p = _pal_kpi(is_light)
            _c.setStyleSheet(
                f"QComboBox {{ background: {p['surface']};"
                f"  border: 1px solid {p['border_strong']};"
                f"  border-radius: 6px; padding: 2px 22px 2px 10px;"
                f"  color: {p['text']}; font-size: 11px; min-height: 24px; }}"
                f"QComboBox::drop-down {{ subcontrol-origin: padding;"
                f"  subcontrol-position: right center; width: 20px;"
                f"  border: none; }}"
                f"QComboBox::down-arrow {{ image: url({_icon_url('tabler_chevron_down.svg')});"
                f"  width: 11px; height: 11px; }}"
            )
        self._reg_page_size_combo.apply_palette = _apply_pg_combo
        _apply_pg_combo(False)
        _pg_row.addWidget(self._reg_page_size_combo)

        _tbl_wrap_lay.addLayout(_pg_row)

        self._reg_prev_btn.clicked.connect(lambda: self._reg_change_page(-1))
        self._reg_next_btn.clicked.connect(lambda: self._reg_change_page(1))
        self._reg_page_size_combo.currentTextChanged.connect(self._reg_change_page_size)
        self._reg_table_card = pro_card(
            "Today's Cases", _tbl_wrap,
            icon=_tbl_icon, accent="#E6EDF3",
            header_extra=_header_extra,
        )
        self._reg_table_card.setMinimumHeight(324)
        self._reg_table_card.setMaximumHeight(344)
        # NOTE: this card is placed in content_widget BELOW the column row
        # below, so it spans the same horizontal width as Case Info + Downtime.

        right_layout.addWidget(self.progress_group)
        right_layout.addStretch()

        # â"€â"€ Right panel â€" page 1: OT mode (OT summary + OT cases table) â"€â"€â"€â"€â"€â"€
        ot_view_widget = QWidget()
        ot_view_layout = QVBoxLayout(ot_view_widget)
        ot_view_layout.setSpacing(12)
        ot_view_layout.setContentsMargins(10, 10, 10, 10)

        # ── OT Calculation Result KPI card ──
        self._ot_result_eff_value = QLabel("-")
        self._ot_result_eff_value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._ot_result_eff_value.setStyleSheet(
            "font-size: 18px; font-weight: 500; color: #388BFD;"
        )
        _ot_eff_lbl = QLabel("Efficiency")
        _ot_eff_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        _ot_eff_lbl.setStyleSheet(
            "font-size: 10px; color: #8B949E; font-weight: 600; letter-spacing: 0.5px;"
        )

        self._ot_result_val_value = QLabel("-")
        self._ot_result_val_value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._ot_result_val_value.setStyleSheet(
            "font-size: 18px; font-weight: 500; color: #F0883E;"
        )
        _ot_val_lbl = QLabel("Case Value")
        _ot_val_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        _ot_val_lbl.setStyleSheet(
            "font-size: 10px; color: #8B949E; font-weight: 600; letter-spacing: 0.5px;"
        )

        self._ot_result_ue_value = QLabel("-")
        self._ot_result_ue_value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._ot_result_ue_value.setStyleSheet(
            "font-size: 18px; font-weight: 500; color: #A371F7;"
        )
        _ot_ue_lbl = QLabel("Units. Eq")
        _ot_ue_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        _ot_ue_lbl.setStyleSheet(
            "font-size: 10px; color: #8B949E; font-weight: 600; letter-spacing: 0.5px;"
        )

        for _v in (self._ot_result_eff_value, self._ot_result_val_value,
                    self._ot_result_ue_value):
            _v.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            _v.setMinimumWidth(0)
        for _l in (_ot_eff_lbl, _ot_val_lbl, _ot_ue_lbl):
            _l.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            _l.setMinimumWidth(0)

        _ot_kpi_eff = QVBoxLayout(); _ot_kpi_eff.setSpacing(2)
        _ot_kpi_eff.addWidget(self._ot_result_eff_value)
        _ot_kpi_eff.addWidget(_ot_eff_lbl)
        _ot_kpi_val = QVBoxLayout(); _ot_kpi_val.setSpacing(2)
        _ot_kpi_val.addWidget(self._ot_result_val_value)
        _ot_kpi_val.addWidget(_ot_val_lbl)
        _ot_kpi_ue = QVBoxLayout(); _ot_kpi_ue.setSpacing(2)
        _ot_kpi_ue.addWidget(self._ot_result_ue_value)
        _ot_kpi_ue.addWidget(_ot_ue_lbl)
        def _ot_vdiv():
            d = QFrame()
            d.setFrameShape(QFrame.Shape.VLine)
            d.setStyleSheet("color: #30363D;")
            return d

        _ot_kpi_row = QHBoxLayout()
        _ot_kpi_row.setSpacing(6)
        _ot_kpi_row.setContentsMargins(0, 0, 0, 0)
        _ot_kpi_row.addLayout(_ot_kpi_eff, 1)
        _ot_kpi_row.addWidget(_ot_vdiv())
        _ot_kpi_row.addLayout(_ot_kpi_val, 1)
        _ot_kpi_row.addWidget(_ot_vdiv())
        _ot_kpi_row.addLayout(_ot_kpi_ue, 1)
        try:
            from qfluentwidgets import FluentIcon as _FIF_ot_calc
            _ot_calc_icon = _FIF_ot_calc.PIE_SINGLE
        except Exception:
            _ot_calc_icon = None
        ot_calc_card = pro_card("Calculation Result", _ot_kpi_row,
                                 icon=_ot_calc_icon, accent="#E6EDF3")
        ot_calc_card.setMaximumHeight(125)

        # ── OT Comments card with chip buttons (Add / See) — Regular parity ──
        self.ot_comments_input = QTextEdit()
        self.ot_comments_input.hide()
        self.ot_comments_input.textChanged.connect(
            lambda: self._refresh_comment_chips()
            if hasattr(self, "_btn_see_ot_comment") else None
        )

        _ot_chips_w = QWidget()
        _ot_cc_outer = QVBoxLayout(_ot_chips_w)
        _ot_cc_outer.setContentsMargins(0, 4, 0, 4)
        _ot_cc_outer.setSpacing(12)
        _ot_cc_outer.addStretch(1)
        _ot_cc_lay = QHBoxLayout()
        _ot_cc_lay.setContentsMargins(0, 0, 0, 0)
        _ot_cc_lay.setSpacing(8)

        def _ot_chip_css_for(is_light: bool) -> str:
            p = _pal_kpi(is_light)
            hover = "rgba(0,0,0,0.06)" if is_light else "rgba(255,255,255,0.06)"
            press = "rgba(0,0,0,0.10)" if is_light else "rgba(255,255,255,0.10)"
            return (
                f"QPushButton {{ background: transparent;"
                f"  border: 1px solid {p['border_strong']};"
                f"  color: {p['text']}; border-radius: 14px; font-weight: 700;"
                f"  font-size: 11px; padding: 6px 14px; }}"
                f"QPushButton:hover {{ background: {hover};"
                f"  border-color: {p['muted_2']}; }}"
                f"QPushButton:pressed {{ background: {press}; }}"
                f"QPushButton:disabled {{ color: {p['muted_2']};"
                f"  border-color: {p['border']}; }}"
            )

        self._btn_add_ot_comment = QPushButton("Add comment")
        self._btn_add_ot_comment.setCursor(Qt.PointingHandCursor)
        self._btn_add_ot_comment.clicked.connect(
            lambda: self._open_comment_dialog(read_only=False)
        )
        self._btn_see_ot_comment = QPushButton("See comment")
        self._btn_see_ot_comment.setCursor(Qt.PointingHandCursor)
        self._btn_see_ot_comment.clicked.connect(
            lambda: self._open_comment_dialog(read_only=True)
        )
        def _apply_ot_chips(is_light: bool):
            css = _ot_chip_css_for(is_light)
            self._btn_add_ot_comment.setStyleSheet(css)
            self._btn_see_ot_comment.setStyleSheet(css)
        self._btn_add_ot_comment.apply_palette = _apply_ot_chips
        try:
            from qfluentwidgets.common.style_sheet import isDarkTheme
            _apply_ot_chips(not isDarkTheme())
        except Exception:
            _apply_ot_chips(False)

        _ot_cc_lay.addWidget(self._btn_add_ot_comment)
        _ot_cc_lay.addWidget(self._btn_see_ot_comment)
        _ot_cc_lay.addStretch()
        _ot_cc_outer.addLayout(_ot_cc_lay)

        self._ot_comment_preview = QLabel("")
        self._ot_comment_preview.setStyleSheet(
            "color: #C9D1D9; font-size: 12px; background: transparent;"
            " padding-left: 2px;"
        )
        self._ot_comment_preview.setVisible(False)
        self._ot_comment_preview.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred,
        )
        self._ot_comment_preview.setMinimumWidth(0)
        self._ot_comment_preview.setWordWrap(False)
        _ot_cc_outer.addWidget(self._ot_comment_preview)
        _ot_cc_outer.addStretch(1)

        try:
            from .tabler_icons import TablerIcon as _TI_ot_cmt
            _ot_cmt_icon = _TI_ot_cmt("tabler_message_circle.svg")
        except Exception:
            _ot_cmt_icon = None
        ot_comments_card = pro_card(
            "Comments (Optional)", _ot_chips_w,
            icon=_ot_cmt_icon, accent="#E6EDF3",
        )
        ot_comments_card.setMaximumHeight(125)

        # Equal width row — calc card on the left, comments on the right.
        for _c in (ot_calc_card, ot_comments_card):
            _c.setSizePolicy(QSizePolicy.Policy.Expanding,
                              QSizePolicy.Policy.Preferred)
        _ot_top_row = QHBoxLayout()
        _ot_top_row.setSpacing(12)
        _ot_top_row.setContentsMargins(0, 0, 0, 0)
        _ot_top_row.addWidget(ot_calc_card, 1)
        _ot_top_row.addWidget(ot_comments_card, 1)
        ot_view_layout.addLayout(_ot_top_row)
        # Reset initial state of OT chips.
        self._refresh_comment_chips()

        # ── OT Production card — matches Daily Production gauge layout ──
        from .gauge_widget import GaugeWidget as _OTGauge

        self._ot_daily_gauge = _OTGauge()
        self._ot_daily_gauge.setFixedSize(170, 110)

        self._ot_daily_prod_value = QLabel("0.00%")
        self._ot_daily_prod_value.setStyleSheet(
            "color: #F0883E; font-size: 14px; font-weight: 700; background: transparent;"
        )
        self._ot_daily_cases_value = QLabel("0")
        self._ot_daily_cases_value.setStyleSheet(
            "color: #F0883E; font-size: 14px; font-weight: 700; background: transparent;"
        )
        self._ot_daily_dt_value = QLabel("0.00%")
        self._ot_daily_dt_value.setStyleSheet(
            "color: #E89720; font-size: 14px; font-weight: 700; background: transparent;"
        )
        _ot_stats_col = QVBoxLayout()
        _ot_stats_col.setSpacing(2)
        for lbl_txt, val_w in (
            ("OT Production", self._ot_daily_prod_value),
            ("Cases", self._ot_daily_cases_value),
            ("Downtime", self._ot_daily_dt_value),
        ):
            _otl = QLabel(lbl_txt)
            _otl.setStyleSheet(
                "color: #C9D1D9; font-size: 11px; background: transparent;"
            )
            _ot_stats_col.addWidget(_otl)
            _ot_stats_col.addWidget(val_w)
        _ot_top_row_sum = QHBoxLayout()
        _ot_top_row_sum.setSpacing(10)
        _ot_top_row_sum.addWidget(self._ot_daily_gauge, 0)
        _ot_top_row_sum.addLayout(_ot_stats_col, 1)

        # "─── Target: N ───" divider line with label centered.
        def _ot_hline():
            d = QFrame()
            d.setFixedHeight(1)
            d.setStyleSheet("background: #21262D; border: none;")
            d.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            return d

        self._ot_daily_target_label = QLabel("Target: —")
        self._ot_daily_target_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._ot_daily_target_label.setStyleSheet(
            "color: #A371F7; font-size: 11px; font-weight: 700; background: transparent;"
            " padding: 0 10px;"
        )
        _ot_target_row = QHBoxLayout()
        _ot_target_row.setSpacing(0)
        _ot_target_row.addWidget(_ot_hline(), 1, Qt.AlignmentFlag.AlignVCenter)
        _ot_target_row.addWidget(self._ot_daily_target_label, 0, Qt.AlignmentFlag.AlignVCenter)
        _ot_target_row.addWidget(_ot_hline(), 1, Qt.AlignmentFlag.AlignVCenter)

        # Equivalent Units row (OT version uses orange/purple accents).
        _ot_eu_row = QHBoxLayout()
        _ot_eu_left = QLabel("OT Equivalent Units")
        _ot_eu_left.setStyleSheet(
            "color: #A371F7; font-size: 11px; font-weight: 700; background: transparent;"
        )
        self._ot_eu_value = QLabel("0.00")
        self._ot_eu_value.setStyleSheet(
            "color: #E6EDF3; font-size: 12px; font-weight: 700; background: transparent;"
        )
        _ot_eu_target_lbl = QLabel("Target:")
        _ot_eu_target_lbl.setStyleSheet(
            "color: #A371F7; font-size: 11px; font-weight: 700; background: transparent;"
        )
        self._ot_eu_target_value = QLabel("—")
        self._ot_eu_target_value.setStyleSheet(
            "color: #E6EDF3; font-size: 12px; font-weight: 700; background: transparent;"
        )
        _ot_eu_row.addWidget(_ot_eu_left)
        _ot_eu_row.addWidget(self._ot_eu_value)
        _ot_eu_row.addStretch()
        _ot_eu_row.addWidget(_ot_eu_target_lbl)
        _ot_eu_row.addWidget(self._ot_eu_target_value)

        # Progress bar matching the Daily Production style.
        self.ot_day_progress = QProgressBar()
        self.ot_day_progress.setMinimum(0)
        self.ot_day_progress.setMaximum(100)
        self.ot_day_progress.setValue(0)
        self.ot_day_progress.setTextVisible(True)
        self.ot_day_progress.setFormat("%v%")
        self.ot_day_progress.setMinimumHeight(22)

        _ot_prog_layout = QVBoxLayout()
        _ot_prog_layout.setSpacing(10)
        _ot_prog_layout.addLayout(_ot_top_row_sum)
        _ot_prog_layout.addLayout(_ot_target_row)
        _ot_prog_layout.addLayout(_ot_eu_row)
        _ot_prog_layout.addWidget(self.ot_day_progress)

        try:
            from .tabler_icons import TablerIcon as _TI_ot_dp
            _ot_dp_icon = _TI_ot_dp("tabler_target.svg")
        except Exception:
            _ot_dp_icon = None
        # Backwards-compat aliases — older code paths still reference these.
        self.ot_day_prod_label = self._ot_daily_prod_value
        self.ot_day_ue_label = self._ot_eu_value
        self._ot_summary_card = pro_card(
            "OT Daily Production", _ot_prog_layout,
            icon=_ot_dp_icon, accent="#E6EDF3",
        )
        self._ot_summary_card.setMinimumHeight(325)
        self._ot_summary_card.setMaximumHeight(325)
        ot_view_layout.addWidget(self._ot_summary_card)

        # ── Today's OT Cases — full parity with Regular's table ──
        self._ot_reg_case_ids: list = []
        self._ot_all_rows: list[dict] = []
        self.ot_reg_table = QTableWidget()
        self.ot_reg_table.setColumnCount(7)
        self.ot_reg_table.setHorizontalHeaderLabels(
            ["CASE ID", "DOCTOR", "TYPE", "TIME", "EFF %", "VALUE %", "UE"]
        )
        self.ot_reg_table.verticalHeader().setVisible(False)
        self.ot_reg_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.ot_reg_table.setAlternatingRowColors(False)
        self.ot_reg_table.setShowGrid(False)
        self.ot_reg_table.setStyleSheet("""
            QTableWidget {
                background: transparent; border: none; gridline-color: transparent;
                outline: none;
            }
            QTableWidget::item { padding: 8px 6px; border: none; }
            QTableWidget::item:hover { background-color: rgba(56,139,253,0.06); }
            QHeaderView { background: transparent; border: none; }
            QHeaderView::section {
                background-color: #161B22; color: #8B949E;
                padding: 8px 6px; border: none;
                border-bottom: 1px solid #21262D;
                font-weight: 700; font-size: 10px; letter-spacing: 0.5px;
            }
        """)
        self.ot_reg_table.horizontalHeader().setDefaultAlignment(
            Qt.AlignmentFlag.AlignCenter)
        self.ot_reg_table.verticalHeader().setDefaultSectionSize(36)
        _ot_hdr = self.ot_reg_table.horizontalHeader()
        _ot_hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        _ot_hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        _ot_hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        _ot_hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        _ot_hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        _ot_hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        _ot_hdr.setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        _ot_hdr.setStretchLastSection(False)
        self.ot_reg_table.setColumnWidth(0, 92)
        self.ot_reg_table.setColumnWidth(2, 90)
        self.ot_reg_table.setColumnWidth(3, 60)
        self.ot_reg_table.setColumnWidth(4, 64)
        self.ot_reg_table.setColumnWidth(5, 70)
        self.ot_reg_table.setColumnWidth(6, 56)
        self.ot_reg_table.setMinimumHeight(150)
        self.ot_reg_table.setSizePolicy(QSizePolicy.Policy.Expanding,
                                          QSizePolicy.Policy.Expanding)

        # Header tools row: search + filters.
        _ot_he_lay = QHBoxLayout()
        _ot_he_lay.setContentsMargins(0, 0, 0, 0)
        _ot_he_lay.setSpacing(8)
        self._ot_search = QLineEdit()
        self._ot_search.setPlaceholderText("Search case or doctor…")
        self._ot_search.setMinimumWidth(220)
        self._ot_search.setFixedHeight(28)
        self._ot_search.setStyleSheet(
            "QLineEdit { background: #161B22; border: 1px solid #30363D;"
            "  border-radius: 6px; padding: 3px 8px; color: #E6EDF3;"
            "  font-size: 11px; }"
        )
        try:
            from PySide6.QtGui import QAction as _QA_ot
            from .tabler_icons import TablerIcon as _TI_ot
            from PySide6.QtGui import QColor as _QC_ots
            _ot_search.addAction  # noqa
            _ots_icon = _TI_ot("tabler_search.svg")
            _ot_act = _QA_ot(_ots_icon.icon(color=_QC_ots("#8B949E")), "", self._ot_search)
            self._ot_search.addAction(_ot_act, QLineEdit.ActionPosition.LeadingPosition)
        except Exception:
            pass
        _ot_he_lay.addWidget(self._ot_search)

        self._ot_filter_btn = QPushButton("Filters")
        self._ot_filter_btn.setFixedHeight(28)
        self._ot_filter_btn.setCursor(Qt.PointingHandCursor)
        self._ot_filter_btn.setStyleSheet(
            "QPushButton { background: transparent; border: 1px solid #1e63e4;"
            "  color: #58A6FF; border-radius: 6px; padding: 3px 12px;"
            "  font-size: 11px; font-weight: 600; }"
            "QPushButton:hover { background: rgba(30,99,228,0.10); }"
        )
        try:
            from .tabler_icons import TablerIcon as _TI_ot2
            from PySide6.QtGui import QColor as _QC_otf
            self._ot_filter_btn.setIcon(
                _TI_ot2("tabler_filter.svg").icon(color=_QC_otf("#58A6FF"))
            )
            self._ot_filter_btn.setIconSize(QSize(14, 14))
        except Exception:
            pass
        _ot_he_lay.addWidget(self._ot_filter_btn)

        self._ot_filter_region = None
        self._ot_filter_type = None
        self._ot_search.textChanged.connect(self._filter_ot_table)
        self._ot_filter_btn.clicked.connect(self._open_ot_filter_dialog)

        # Empty-state placeholder for OT (same look as Regular).
        self._ot_empty_state = self._build_ot_empty_state()
        self._ot_table_stack = QStackedWidget()
        self._ot_table_stack.addWidget(self.ot_reg_table)
        self._ot_table_stack.addWidget(self._ot_empty_state)

        # Pagination footer: "Showing X to Y of Z" + prev/next + page size.
        _ot_pag_lay = QHBoxLayout()
        _ot_pag_lay.setContentsMargins(0, 0, 0, 0)
        _ot_pag_lay.setSpacing(8)
        self._ot_page_label = QLabel("Showing 0 of 0 cases")
        self._ot_page_label.setStyleSheet(
            "color: #8B949E; font-size: 10px;"
        )
        _ot_pag_lay.addWidget(self._ot_page_label)
        _ot_pag_lay.addStretch(1)
        _ot_arrow_css = (
            "QPushButton { background: transparent; border: none;"
            "  color: #8B949E; min-width: 20px; min-height: 22px;"
            "  font-size: 16px; font-weight: 700; padding: 0; }"
            "QPushButton:hover { color: #E6EDF3; }"
            "QPushButton:disabled { color: #4d5560; }"
        )
        self._ot_prev_btn = QPushButton("‹")
        self._ot_prev_btn.setStyleSheet(_ot_arrow_css)
        self._ot_prev_btn.setCursor(Qt.PointingHandCursor)
        self._ot_next_btn = QPushButton("›")
        self._ot_next_btn.setStyleSheet(_ot_arrow_css)
        self._ot_next_btn.setCursor(Qt.PointingHandCursor)
        self._ot_prev_btn.clicked.connect(lambda: self._ot_change_page(-1))
        self._ot_next_btn.clicked.connect(lambda: self._ot_change_page(+1))
        self._ot_page_btn = QPushButton("1")
        self._ot_page_btn.setEnabled(False)
        self._ot_page_btn.setStyleSheet(
            "QPushButton { background: transparent; border: 1px solid #388BFD;"
            "  border-radius: 5px; color: #388BFD; min-width: 22px;"
            "  min-height: 20px; font-size: 10px; font-weight: 700;"
            "  padding: 0 6px; }"
        )
        self._ot_page_size_combo = QComboBox()
        self._ot_page_size_combo.addItems(
            ["5 / page", "10 / page", "20 / page", "50 / page"]
        )
        self._ot_page_size_combo.setStyleSheet(
            "QComboBox { background: #161B22; border: 1px solid #30363D;"
            "  border-radius: 6px; padding: 2px 22px 2px 10px; color: #E6EDF3;"
            "  font-size: 11px; min-height: 24px; }"
            "QComboBox::drop-down { subcontrol-origin: padding;"
            "  subcontrol-position: right center; width: 20px; border: none; }"
            f"QComboBox::down-arrow {{ image: url({_icon_url('tabler_chevron_down.svg')});"
            "  width: 11px; height: 11px; }"
        )
        self._ot_page_size_combo.currentIndexChanged.connect(
            lambda _i: self._render_ot_page(reset_to_first=True)
        )
        _ot_pag_lay.addWidget(self._ot_prev_btn)
        _ot_pag_lay.addWidget(self._ot_page_btn)
        _ot_pag_lay.addWidget(self._ot_next_btn)
        _ot_pag_lay.addSpacing(6)
        _ot_pag_lay.addWidget(self._ot_page_size_combo)

        self._ot_current_page = 0

        # Body container — search/filter row + table stack + pagination.
        _ot_body = QVBoxLayout()
        _ot_body.setSpacing(8)
        _ot_body.setContentsMargins(0, 0, 0, 0)
        _ot_body.addLayout(_ot_he_lay)
        _ot_body.addWidget(self._ot_table_stack, 1)
        _ot_body.addLayout(_ot_pag_lay)

        _ot_body_widget = QWidget()
        _ot_body_widget.setLayout(_ot_body)
        try:
            from qfluentwidgets import FluentIcon as _FIF_otc
            _ot_table_icon = _FIF_otc.LIBRARY
        except Exception:
            _ot_table_icon = None
        _ot_table_card = pro_card("Today's OT Cases", _ot_body_widget,
                                    icon=_ot_table_icon, accent="#E6EDF3")
        _ot_table_card.setSizePolicy(QSizePolicy.Policy.Expanding,
                                       QSizePolicy.Policy.Fixed)
        _ot_table_card.setFixedHeight(360)
        ot_view_layout.addWidget(_ot_table_card)

        # ── OT Reminder card — fills the empty space below the table ──
        _ot_reminder_inner = QVBoxLayout()
        _ot_reminder_inner.setContentsMargins(0, 0, 0, 0)
        _ot_reminder_inner.setSpacing(10)

        _rem_body = QLabel(
            "Remember to log your downtime in the app, and make sure you "
            "record it under the same date you are applying for overtime."
        )
        _rem_body.setWordWrap(True)
        _rem_body.setStyleSheet(
            "color: #C9D1D9; font-size: 11px; background: transparent;"
        )
        _ot_reminder_inner.addWidget(_rem_body)

        _link_lbl = QLabel("OT submission link:")
        _link_lbl.setStyleSheet(
            "color: #8B949E; font-size: 11px; font-weight: 600;"
            " background: transparent;"
        )
        _ot_reminder_inner.addWidget(_link_lbl)

        _url_row = QHBoxLayout()
        _url_row.setSpacing(6)
        _url_text = "https://fiori.dhrdental.com/sap/bc/ui2/flp#Shell-home"
        _url_lbl = QLabel(_url_text)
        _url_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        _url_lbl.setStyleSheet(
            "QLabel { color: #58A6FF; font-size: 11px;"
            " background: #161B22; border: 1px solid #21262D;"
            " border-radius: 6px; padding: 6px 10px;"
            " font-family: Consolas, monospace; }"
        )
        _url_row.addWidget(_url_lbl, 1)

        _open_btn = QPushButton("Open")
        _open_btn.setFixedHeight(30)
        _open_btn.setCursor(Qt.PointingHandCursor)
        try:
            from .tabler_icons import TablerIcon as _TI_lk
            from PySide6.QtGui import QColor as _QC_lk
            _open_btn.setIcon(_TI_lk("tabler_world.svg").icon(color=_QC_lk("#58A6FF")))
            _open_btn.setIconSize(QSize(14, 14))
        except Exception:
            pass
        _open_btn.setStyleSheet(
            "QPushButton { background: transparent; border: 1px solid #1e63e4;"
            "  color: #58A6FF; border-radius: 6px; padding: 4px 14px;"
            "  font-weight: 700; font-size: 11px; }"
            "QPushButton:hover { background: rgba(30,99,228,0.10); }"
        )
        def _open_url():
            import webbrowser
            try:
                webbrowser.open(_url_text)
            except Exception:
                pass
        _open_btn.clicked.connect(_open_url)

        _copy_btn = QPushButton()
        _copy_btn.setMinimumSize(30, 30)
        _copy_btn.setCursor(Qt.PointingHandCursor)
        _copy_btn.setToolTip("Copy URL")
        try:
            from .tabler_icons import TablerIcon as _TI_cp
            from PySide6.QtGui import QColor as _QC_cp
            _copy_default_icon = _TI_cp("tabler_file.svg").icon(color=_QC_cp("#8B949E"))
            _copy_done_icon = _TI_cp("tabler_circle_check.svg").icon(color=_QC_cp("#3FB950"))
            _copy_btn.setIcon(_copy_default_icon)
            _copy_btn.setIconSize(QSize(14, 14))
        except Exception:
            _copy_default_icon = None
            _copy_done_icon = None
            _copy_btn.setText("⧉")
        _copy_default_css = (
            "QPushButton { background: transparent; border: 1px solid #30363D;"
            "  border-radius: 6px; color: #8B949E; padding: 4px 10px;"
            "  font-size: 11px; font-weight: 600; }"
            "QPushButton:hover { background: rgba(255,255,255,0.05); }"
        )
        _copy_done_css = (
            "QPushButton { background: rgba(63,185,80,0.10);"
            "  border: 1px solid #3FB950; color: #3FB950;"
            "  border-radius: 6px; padding: 4px 10px;"
            "  font-size: 11px; font-weight: 700; }"
        )
        _copy_btn.setStyleSheet(_copy_default_css)

        def _copy_url():
            from PySide6.QtWidgets import QApplication as _QApp
            _QApp.clipboard().setText(_url_text)
            _copy_btn.setText("  Copied")
            if _copy_done_icon is not None:
                _copy_btn.setIcon(_copy_done_icon)
            _copy_btn.setStyleSheet(_copy_done_css)

            def _revert():
                _copy_btn.setText("")
                if _copy_default_icon is not None:
                    _copy_btn.setIcon(_copy_default_icon)
                _copy_btn.setStyleSheet(_copy_default_css)
            QTimer.singleShot(1500, _revert)
        _copy_btn.clicked.connect(_copy_url)

        _url_row.addWidget(_copy_btn)
        _url_row.addWidget(_open_btn)
        _ot_reminder_inner.addLayout(_url_row)

        try:
            from .tabler_icons import TablerIcon as _TI_rem
            _rem_icon = _TI_rem("tabler_alert_triangle.svg")
        except Exception:
            _rem_icon = None
        _ot_reminder_card = pro_card(
            "Reminder", _ot_reminder_inner,
            icon=_rem_icon, accent="#E6EDF3",
        )
        ot_view_layout.addWidget(_ot_reminder_card)
        self._ot_view_layout = ot_view_layout

        ot_view_layout.addStretch()

        # â"€â"€ Stacked right panel â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
        self._right_stack = QStackedWidget()
        self._right_stack.addWidget(right_widget)    # index 0 â€" regular
        self._right_stack.addWidget(ot_view_widget)  # index 1 â€" OT
        self._right_stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        # Inner responsive container — kept as-is so the existing
        # _apply_responsive_layout still works (swaps HBox/VBox by width).
        self.content_widget = QWidget()
        self.content_layout = QHBoxLayout(self.content_widget)
        self.content_layout.setSpacing(15)
        self.content_layout.setContentsMargins(5, 5, 5, 5)
        self.content_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        self.left_widget = left_widget
        self.left_layout = left_layout
        self.right_widget = right_widget
        self.right_layout = right_layout

        # Left column tighter, right column gets the extra horizontal space.
        # AlignTop anchors both columns to the top of the row so their first
        # cards (Case Info / Calculation Result) sit on the same Y line.
        self.content_layout.addWidget(left_widget, 2, Qt.AlignmentFlag.AlignTop)
        self.content_layout.addWidget(self._right_stack, 3, Qt.AlignmentFlag.AlignTop)

        # Outer container holds just the columns row. Today's Cases now lives
        # inside the right column under the Downtime card.
        self._scroll_root = QWidget()
        _root_lay = QVBoxLayout(self._scroll_root)
        _root_lay.setContentsMargins(0, 0, 0, 0)
        _root_lay.setSpacing(0)
        _root_lay.addWidget(self.content_widget)
        # Place Today's Cases at the bottom of the right column.
        right_layout.addWidget(self._reg_table_card)
        
        # Scroll area for content
        scroll = QScrollArea()
        scroll.setWidget(self._scroll_root)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        scroll.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        
        # â"€â"€ Mode toggle bar â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
        from PySide6.QtGui import QColor as _QColor
        mode_bar_widget = QWidget()
        mode_bar_widget.setMaximumHeight(40)
        mode_bar_layout = QHBoxLayout(mode_bar_widget)
        mode_bar_layout.setContentsMargins(8, 4, 8, 4)
        mode_bar_layout.setSpacing(10)

        self._mode_label_regular = QLabel("Regular")
        def _apply_mode_reg(is_light: bool, _w=self._mode_label_regular):
            p = _pal_kpi(is_light)
            _w.setStyleSheet(
                f"font-size: 12px; font-weight: 700; color: {p['accent_2']};"
            )
        self._mode_label_regular.apply_palette = _apply_mode_reg
        _apply_mode_reg(False)

        self._mode_toggle = ToggleSwitch(
            checked=False,
            color_on=_QColor(240, 136, 62),    # orange = OT
            color_off=_QColor(23, 87, 212),    # #1757D4 = Regular (primary blue)
        )
        self._mode_toggle.toggled.connect(self._on_mode_toggled)

        self._mode_label_ot = QLabel("OT")
        def _apply_mode_ot(is_light: bool, _w=self._mode_label_ot):
            p = _pal_kpi(is_light)
            _w.setStyleSheet(
                f"font-size: 12px; font-weight: 700; color: {p['muted_2']};"
            )
        self._mode_label_ot.apply_palette = _apply_mode_ot
        _apply_mode_ot(False)

        mode_bar_layout.addStretch()
        mode_bar_layout.addWidget(self._mode_label_regular)
        mode_bar_layout.addWidget(self._mode_toggle)
        mode_bar_layout.addWidget(self._mode_label_ot)
        mode_bar_layout.addStretch()

        # â"€â"€ Edit banner â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
        self._edit_banner = QFrame()
        self._edit_banner.setFrameShape(QFrame.Shape.NoFrame)
        self._edit_banner.setFixedHeight(42)
        self._edit_banner.setVisible(False)
        self._edit_banner.setStyleSheet(
            "QFrame { background-color: #0D1117; border: 1px solid #388BFD; border-radius: 8px; }"
        )

        self._edit_accent = QFrame(self._edit_banner)
        self._edit_accent.setFixedWidth(4)
        self._edit_accent.setStyleSheet("background-color: #388BFD; border-radius: 2px;")

        _badge = QLabel("✎  EDIT MODE")
        _badge.setStyleSheet(
            "color: #388BFD; font-weight: 900; font-size: 10px; letter-spacing: 1px;"
            " background: transparent; padding: 0 6px;"
        )

        _sep = QFrame()
        _sep.setFrameShape(QFrame.Shape.VLine)
        _sep.setStyleSheet("color: #30363D;")
        _sep.setFixedWidth(1)

        self._edit_banner_label = QLabel("Editing case — save to update")
        self._edit_banner_label.setStyleSheet(
            "color: #CDD9E5; font-size: 12px; font-weight: 600; background: transparent;"
        )

        _banner_cancel_btn = QPushButton("✕  Cancel")
        _banner_cancel_btn.setFixedSize(80, 26)
        _banner_cancel_btn.setStyleSheet(
            "QPushButton { background: transparent; border: 1px solid #444C56;"
            " border-radius: 5px; color: #8B949E; font-size: 11px; font-weight: 600; }"
            " QPushButton:hover { border-color: #F85149; color: #F85149; }"
        )
        _banner_cancel_btn.clicked.connect(self._cancel_edit)

        self._edit_banner.setMaximumWidth(560)

        _banner_layout = QHBoxLayout(self._edit_banner)
        _banner_layout.setContentsMargins(0, 4, 14, 4)
        _banner_layout.setSpacing(0)
        _banner_layout.addWidget(self._edit_accent)
        _banner_layout.addSpacing(10)
        _banner_layout.addWidget(_badge)
        _banner_layout.addWidget(_sep)
        _banner_layout.addSpacing(10)
        _banner_layout.addWidget(self._edit_banner_label, 1)
        _banner_layout.addSpacing(8)
        _banner_layout.addWidget(_banner_cancel_btn)

        self._banner_pulse_state = False
        self._banner_pulse_timer = QTimer(self)
        self._banner_pulse_timer.setInterval(550)
        self._banner_pulse_timer.timeout.connect(self._pulse_edit_banner)

        # Apply initial mode styling
        self._update_mode_ui()

        # Main layout — banner is NOT here; it sits directly above the
        # Case Information card (inserted into left_layout below).
        self.final_layout = QVBoxLayout()
        self.final_layout.setContentsMargins(5, 5, 5, 5)
        self.final_layout.setSpacing(4)
        self.final_layout.addWidget(mode_bar_widget)
        self.final_layout.addWidget(scroll, 1)
        self.setLayout(self.final_layout)

        # Banner replaced by an inline Cancel button inside the Case
        # Information card header — hide the legacy banner entirely.
        self._edit_banner.hide()
        
        # Store current layout mode
        self.is_vertical = False
        # Keep Register in single-column mode unless there is ample width.
        # Current app max width is 900, so this avoids cramped two-column rendering.
        self._stack_threshold = 980
        self._apply_responsive_layout(self.width())
        
        self.load_daily_production()
        self._load_regular_day_cases()
        self._sync_regular_progress_visibility()
        self._capture_mode_state("regular")

    def _apply_kpi_label_styles(self, is_light: bool):
        """Restyle the two KPI labels (Daily Production / Equivalent Units)
        with theme-aware foreground colors so light mode keeps decent contrast."""
        if is_light:
            prod_color = "#0550AE"   # darker blue for light bg
            ue_color = "#6E40C9"     # darker purple for light bg
        else:
            prod_color = "#388BFD"
            ue_color = "#A371F7"
        if hasattr(self, "daily_production_label"):
            self.daily_production_label.setStyleSheet(
                f"font-size: 14px; font-weight: 700; color: {prod_color};"
            )
        if hasattr(self, "equivalent_units_label"):
            self.equivalent_units_label.setStyleSheet(
                f"font-size: 14px; font-weight: 700; color: {ue_color};"
            )

    def update_font_sizes(self, _new_size: int = 0):
        """Re-render tables so the QFont calls inside load_daily_production
        and _load_ot_day_cases pick up the new global scale."""
        try:
            self.load_daily_production()
        except Exception:
            pass
        try:
            self._load_ot_day_cases()
        except Exception:
            pass

    def update_theme_labels(self, is_light: bool):
        """Apply light/dark table styles while preserving per-cell badge colors."""
        from PySide6.QtGui import QColor
        # Remember the active theme so other render paths (notably the OT
        # table + OT progress bar in `_load_ot_day_cases`) pick the right
        # palette regardless of any QApplication stylesheet quirk.
        self._light_mode_active = bool(is_light)
        self._apply_kpi_label_styles(is_light)
        colors = get_light_theme_colors()
        fg_color = QColor(colors["text_primary"]) if is_light else CLR_FG_LIGHT
        light_css = (
            f' QTableWidget {{ background-color: {colors["surface_bg"]}; gridline-color: {colors["border"]}; border: 1px solid {colors["border"]}; }} '
            f' QHeaderView::section {{ background-color: {light_header_bg(colors)}; color: {light_header_fg(colors)}; border: 1px solid {colors["border"]}; padding: 5px 6px; font-weight: 700; font-size: 10px; }} '
        )
        apply_table_theme(
            self,
            is_light,
            light_append_css=light_css,
            adaptive_fg_by_bg=True,
            adaptive_default_fg=fg_color,
        )
        # Repaint current mode rows with correct foreground/background after theme change.
        if self._mode == "overtime":
            self._load_ot_day_cases()
        else:
            self._load_regular_day_cases()
        # Propagate theme change to the embedded Downtime table
        if hasattr(self, "downtime_manager") and self.downtime_manager is not None:
            try:
                self.downtime_manager.update_theme_labels(is_light)
            except Exception:
                pass

    def _is_light_mode(self) -> bool:
        """Return the last theme state pushed via update_theme_labels.

        Other tabs route theme changes through `themeChanged` → an
        instance flag flipped in `update_theme_labels`. This is the only
        source of truth that survives custom light palettes (the previous
        substring-based check on `app.styleSheet()` broke whenever the
        user customized the light palette, so OT-mode widgets ended up
        styled as dark while the rest of the UI was light).
        """
        return bool(getattr(self, "_light_mode_active", False))

    def _lookup_std_time(self, region: str | None, tipo: str | None):
        """Return the standard time (minutes) for region+type, or None."""
        if not region or not tipo:
            return None
        try:
            return self.standards[region]["Aligners"][tipo]
        except (KeyError, TypeError):
            return None

    @staticmethod
    def _paint_efficiency_cell(item, eff_value):
        from .register_helpers import paint_efficiency_cell
        paint_efficiency_cell(item, eff_value)

    @staticmethod
    def _paint_time_cell(item, tiempo_real, std_time):
        from .register_helpers import paint_time_cell
        paint_time_cell(item, tiempo_real, std_time)
    
    def resizeEvent(self, event):
        """Handle resize to switch between horizontal and vertical layout"""
        super().resizeEvent(event)
        self._apply_responsive_layout(event.size().width())
        self._reposition_import_toast()

    def _apply_responsive_layout(self, width: int):
        """Apply horizontal/vertical arrangement based on available width."""
        threshold = getattr(self, "_stack_threshold", 980)

        if width < threshold and not self.is_vertical:
            self.is_vertical = True
            self.content_layout.setDirection(QVBoxLayout.Direction.TopToBottom)
            # Set fixed width so widgets center properly and are wider
            responsive_width = min(width - 40, 600)  # Use most of available width
            self.left_widget.setFixedWidth(responsive_width)
            self._right_stack.setFixedWidth(responsive_width)
            self._sync_regular_progress_visibility()
        elif width >= threshold and self.is_vertical:
            self.is_vertical = False
            self.content_layout.setDirection(QHBoxLayout.Direction.LeftToRight)
            # Remove fixed width in horizontal mode
            self.left_widget.setMinimumWidth(0)
            self.left_widget.setMaximumWidth(16777215)
            self._right_stack.setMinimumWidth(0)
            self._right_stack.setMaximumWidth(16777215)
            self._sync_regular_progress_visibility()
        elif self.is_vertical:
            # Update width when resizing in vertical mode
            responsive_width = min(width - 40, 600)
            self.left_widget.setFixedWidth(responsive_width)
            self._right_stack.setFixedWidth(responsive_width)

    def _show_import_toast(self, message: str, duration_ms: int = 4200):
        """Bottom amber banner shown after an Import — warning style with
        title + body + 'Learn more' action + close X. Title is the first
        sentence, body is the remaining text."""
        from PySide6.QtWidgets import (
            QToolButton as _QTB, QHBoxLayout as _QH, QVBoxLayout as _QV,
            QFrame as _QFr, QPushButton as _QPB,
        )
        from PySide6.QtCore import (
            QPropertyAnimation as _QPA, QEasingCurve as _QEC, QSize,
        )
        from PySide6.QtGui import QColor as _QCol
        from .tabler_icons import TablerIcon as _TI

        # Build once.
        if self._import_toast is None:
            host = self.window()
            banner = _QFr(host)
            banner.setObjectName("importBanner")
            banner.setStyleSheet(
                "#importBanner { background: rgba(36, 22, 8, 235);"
                " border: 1px solid #D29922; border-radius: 10px; }"
                "QLabel { background: transparent; border: none; }"
            )
            lay = _QH(banner)
            lay.setContentsMargins(12, 10, 10, 10)
            lay.setSpacing(12)

            icon = _QTB()
            icon.setEnabled(False)
            icon.setIcon(_TI("tabler_alert_triangle.svg").icon(color=_QCol("#D29922")))
            icon.setIconSize(QSize(20, 20))
            icon.setStyleSheet("background: transparent; border: none;")
            lay.addWidget(icon, 0, Qt.AlignmentFlag.AlignVCenter)

            text_col = _QV()
            text_col.setSpacing(2)
            t_lbl = QLabel("")
            t_lbl.setStyleSheet(
                "color: #FAE9C7; font-size: 12px; font-weight: 700;"
            )
            b_lbl = QLabel("")
            b_lbl.setStyleSheet(
                "color: #D9C7A0; font-size: 11px;"
            )
            b_lbl.setWordWrap(True)
            text_col.addWidget(t_lbl)
            text_col.addWidget(b_lbl)
            lay.addLayout(text_col, 1)

            learn_btn = _QPB("  Learn more")
            learn_btn.setCursor(Qt.PointingHandCursor)
            learn_btn.setIcon(_TI("tabler_info_circle.svg").icon(color=_QCol("#D29922")))
            learn_btn.setIconSize(QSize(13, 13))
            learn_btn.setFixedHeight(28)
            learn_btn.setStyleSheet(
                "QPushButton { background: transparent; border: 1px solid #D29922;"
                "  color: #D29922; border-radius: 6px; padding: 4px 12px;"
                "  font-weight: 700; font-size: 11px; }"
                "QPushButton:hover { background: rgba(210,153,34,0.12); }"
            )
            lay.addWidget(learn_btn, 0, Qt.AlignmentFlag.AlignVCenter)

            close_btn = _QTB()
            close_btn.setCursor(Qt.PointingHandCursor)
            close_btn.setIcon(_TI("tabler_x.svg").icon(color=_QCol("#D9C7A0")))
            close_btn.setIconSize(QSize(16, 16))
            close_btn.setFixedSize(26, 26)
            close_btn.setStyleSheet(
                "QToolButton { background: transparent; border: none;"
                "  border-radius: 13px; }"
                "QToolButton:hover { background: rgba(255,255,255,0.06); }"
            )
            lay.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignVCenter)

            self._import_toast = banner
            self._import_toast_title = t_lbl
            self._import_toast_body = b_lbl
            close_btn.clicked.connect(banner.hide)
            learn_btn.clicked.connect(lambda: self._open_import_help())

        # Split message → first line / rest.
        lines = [ln.strip() for ln in (message or "").splitlines() if ln.strip()]
        title = lines[0] if lines else ""
        body = " ".join(lines[1:]) if len(lines) > 1 else ""
        self._import_toast_title.setText(title)
        self._import_toast_body.setText(body)

        # Anchored to the top-left of the host window with a small inset.
        host = self.window()
        host_w = host.width()
        banner = self._import_toast
        banner.setFixedWidth(min(500, max(320, int(host_w * 0.42))))
        banner.adjustSize()
        x = 2
        y = 14
        banner.setGeometry(x, y, banner.width(), banner.height())
        banner.show()
        banner.raise_()

        if self._import_toast_timer is None:
            self._import_toast_timer = QTimer(self)
            self._import_toast_timer.setSingleShot(True)
            self._import_toast_timer.timeout.connect(self._import_toast.hide)
        self._import_toast_timer.start(duration_ms)

    def _open_import_help(self):
        """Compact floating popover anchored below the Learn more button."""
        from PySide6.QtWidgets import (
            QFrame as _QF, QHBoxLayout as _QH, QVBoxLayout as _QV,
            QToolButton as _QTB,
        )
        from PySide6.QtCore import QSize, QTimer
        from PySide6.QtGui import QColor as _QCol
        from .tabler_icons import TablerIcon as _TI

        # Tear down any previous popover so we don't leak overlays.
        if getattr(self, "_import_help_popup", None) is not None:
            try:
                self._import_help_popup.hide()
                self._import_help_popup.deleteLater()
            except Exception:
                pass
            self._import_help_popup = None

        host = self.window()
        pop = _QF(host, Qt.WindowType.Popup)
        pop.setObjectName("importHelpPop")
        pop.setStyleSheet(
            "#importHelpPop { background: #101824;"
            " border: 1px solid #30363D; border-radius: 10px; }"
            "QLabel { background: transparent; border: none; }"
        )
        lay = _QH(pop)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(10)

        ic = _QTB()
        ic.setEnabled(False)
        ic.setIcon(_TI("tabler_info_circle.svg").icon(color=_QCol("#58A6FF")))
        ic.setIconSize(QSize(16, 16))
        ic.setStyleSheet("background: transparent; border: none;")
        lay.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)

        col = _QV()
        col.setSpacing(2)
        t = QLabel("Stage RX / Bite Sync")
        t.setStyleSheet(
            "color: #58A6FF; font-size: 11px; font-weight: 700;"
        )
        b = QLabel(
            "Need manual selection — clipboard import doesn't flag them."
        )
        b.setWordWrap(True)
        b.setStyleSheet("color: #C9D1D9; font-size: 11px;")
        col.addWidget(t)
        col.addWidget(b)
        lay.addLayout(col, 1)

        pop.setFixedWidth(320)
        pop.adjustSize()

        # Anchor below the Learn more button inside the import banner.
        if self._import_toast and self._import_toast.isVisible():
            try:
                # The 3rd widget added is the Learn more button (index 2 in lay).
                banner_lay = self._import_toast.layout()
                learn_btn = banner_lay.itemAt(2).widget() if banner_lay else None
            except Exception:
                learn_btn = None
        else:
            learn_btn = None

        if learn_btn is not None:
            gpos = learn_btn.mapToGlobal(learn_btn.rect().bottomLeft())
            pop.move(gpos.x() - 4, gpos.y() + 6)
        else:
            # Fallback: top-left of host window.
            gpos = host.mapToGlobal(host.rect().topLeft())
            pop.move(gpos.x() + 12, gpos.y() + 80)

        pop.show()
        self._import_help_popup = pop
        QTimer.singleShot(7000, pop.hide)

    def _reposition_import_toast(self):
        if not self._import_toast or not self._import_toast.isVisible():
            return
        margin = 14
        x = max(margin, self.width() - self._import_toast.width() - margin)
        y = margin
        self._import_toast.move(x, y)

    def _capture_mode_state(self, mode: str):
        """Capture form values for one mode so Regular and OT drafts stay independent."""
        self._mode_state[mode] = {
            "case_id": self.case_id.text(),
            "region": self.region.currentText(),
            "tipo": self.tipo.currentText(),
            "doctor": self.doctor.text(),
            "date": self.case_date.date().toString("yyyy-MM-dd"),
            "start": self.start_time.time().toString("HH:mm"),
            "end": self.end_time.time().toString("HH:mm"),
            "count": self.count_toggle.isChecked(),
            "comments": self._comments_widget_for_mode(mode).toPlainText(),
        }

    def _comments_widget_for_mode(self, mode: str) -> QTextEdit:
        if mode == "overtime" and hasattr(self, "ot_comments_input"):
            return self.ot_comments_input
        return self.comments_input

    def _restore_mode_state(self, mode: str):
        """Restore previously captured values for a mode, or clear if empty/new."""
        state = self._mode_state.get(mode) or {}
        self.case_id.setText(state.get("case_id", ""))
        region_value = state.get("region", "")
        if region_value:
            idx = self.region.findText(region_value)
            if idx >= 0:
                self.region.setCurrentIndex(idx)
        else:
            if self.region.count() > 0:
                self.region.setCurrentIndex(0)
        self.update_case_types()
        tipo_value = state.get("tipo", "")
        if tipo_value:
            idx = self.tipo.findText(tipo_value)
            if idx >= 0:
                self.tipo.setCurrentIndex(idx)
        else:
            if self.tipo.count() > 0:
                self.tipo.setCurrentIndex(0)
        self.doctor.setText(state.get("doctor", ""))
        date_value = state.get("date", "")
        if date_value:
            self.case_date.setDate(QDate.fromString(date_value, "yyyy-MM-dd"))
        else:
            self.case_date.setDate(QDate.currentDate())
        start_value = state.get("start")
        end_value = state.get("end")
        self.start_time.setTime(QTime.fromString(start_value, "HH:mm") if start_value else QTime.currentTime())
        self.end_time.setTime(QTime.fromString(end_value, "HH:mm") if end_value else QTime(0, 0))
        self.count_toggle.setChecked(bool(state.get("count", True)))
        self._comments_widget_for_mode(mode).setText(state.get("comments", ""))
        self._reset_result_kpi()

    def _sync_regular_progress_visibility(self):
        """
        Keep progress cards in the correct container for the active mode.

        Regular: uses `progress_group`.
        OT: uses `_ot_summary_card`.
        In vertical mode, active card becomes sticky at the bottom (outside the scroll area).
        """
        if not hasattr(self, "final_layout") or not hasattr(self, "right_layout"):
            return
        if not hasattr(self, "_ot_summary_card") or not hasattr(self, "_ot_view_layout"):
            return

        is_ot = self._mode == "overtime"

        # Daily Production card lives in the LEFT column under Case Info
        # in BOTH modes — OT mode shows the same total daily production as
        # context. The OT-specific summary card is suppressed entirely.
        self.right_layout.removeWidget(self.progress_group)
        self.final_layout.removeWidget(self.progress_group)
        if hasattr(self, "left_layout"):
            self.left_layout.removeWidget(self.progress_group)

        self._ot_summary_card.setVisible(False)
        self.progress_group.setVisible(True)
        if hasattr(self, "left_layout"):
            self.left_layout.addWidget(self.progress_group)
        else:
            self.final_layout.addWidget(self.progress_group, 0)

    def load_standards(self):
        from .utils import load_standards_data
        self.standards = load_standards_data()

    def load_units_eq(self):
        """Load units equivalency for production calculation"""
        self.units_eq = load_units_eq_data()

    def get_units_per_case(self, region, case_type):
        """Return UE value for a specific region+case_type."""
        return _ue_lookup(self.units_eq, region, case_type)
    
    def on_case_id_changed(self, text):
        """Auto-set start time when Case ID is first entered"""
        if text and len(text) == 1:  # First character entered
            self.start_time.setTime(QTime.currentTime())
    
    def update_case_types(self):
        region = self.region.currentText()
        if region and region in self.standards:
            self.tipo.clear()
            self.tipo.addItems(self.standards[region]["Aligners"].keys())
    
    def validate_end_time(self):
        """Ensure end_time is not less than start_time"""
        if self.end_time.time() < self.start_time.time():
            self.end_time.blockSignals(True)
            self.end_time.setTime(self.start_time.time())
            self.end_time.blockSignals(False)
    
    def calculate_case_value(self, std_time):
        """Calculate fixed percentage value for a case based on standard time."""
        return _calc_cv(std_time)

    def get_daily_downtime(self, date=None):
        """Get total downtime minutes for given date (or today if not specified)"""
        conn = get_connection()
        cursor = conn.cursor()
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")
        
        cursor.execute("""
            SELECT SUM(duracion)
            FROM downtimes
            WHERE fecha = ? AND (status = 'approved' OR status IS NULL)
        """, (date,))
        
        result = cursor.fetchone()
        conn.close()
        
        total_downtime = result[0] if result[0] else 0.0
        return total_downtime

    # â"€â"€ Result KPI helpers â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

    def _case_id_widget(self, case_id: str, status_color: str):
        from .register_helpers import case_id_widget
        return case_id_widget(case_id, status_color)

    def _format_shift_label(self) -> str:
        """Return '(HH AM/PM - HH AM/PM)' string for the current shift."""
        try:
            from sync.app_config import load_config
            cfg = load_config() or {}
            start_h = int(cfg.get("shift_start_hour", 6))
            end_h = int(cfg.get("shift_end_hour", 15))
        except Exception:
            start_h, end_h = 6, 15

        def _fmt(h):
            suf = "AM" if h < 12 else "PM"
            disp = h if h == 12 else h % 12
            if disp == 0:
                disp = 12
            return f"{disp}:00 {suf}"

        return f"({_fmt(start_h)} - {_fmt(end_h)})"

    def _open_shift_config(self):
        """Gear icon on the Daily Production card.

        Routes to the OT-limits dialog when in OT mode, otherwise opens
        the regular shift-hours dialog."""
        try:
            from sync.app_config import load_config, save_config
            if self._mode == "overtime":
                from .ot_limits_dialog import open_ot_limits_config
                result = open_ot_limits_config(self)
                if result is not None:
                    cfg = load_config() or {}
                    cfg.update(result)
                    save_config(cfg)
                    self._apply_ot_to_daily_production()
                return
            from .shift_config_dialog import open_shift_config
            result = open_shift_config(self)
            if result is not None:
                cfg = load_config() or {}
                cfg.update(result)
                save_config(cfg)
                new_lbl = "Daily Production " + self._format_shift_label()
                self._daily_shift_label = new_lbl
                title_widget = self.progress_group.findChild(QLabel, "proCardTitle")
                if title_widget is not None:
                    title_widget.setText(new_lbl.upper())
                self.load_daily_production()
            return
        except Exception as exc:
            log_event("tab_register", f"shift config open failed: {exc}", level="WARN")

    def _build_reg_empty_state(self):
        from .register_helpers import build_empty_state
        return build_empty_state(
            "No cases yet",
            "Add a case from the Case Information panel to see it here.",
        )

    def _reg_change_page(self, delta: int):
        self._reg_page += delta
        self._render_reg_page()

    def _reg_change_page_size(self, text: str):
        try:
            n = int(text.split()[0])
        except Exception:
            n = 5
        self._reg_page_size = max(1, n)
        self._reg_page = 0
        self._render_reg_page()

    def _refresh_pagination_footer(self, total: int):
        page_size = max(1, getattr(self, "_reg_page_size", 6))
        if total == 0:
            start = end = 0
        else:
            start = self._reg_page * page_size + 1
            end = min(total, (self._reg_page + 1) * page_size)
        self._reg_page_label.setText(f"Showing {start} to {end} of {total} cases")
        self._reg_page_btn.setText(str(self._reg_page + 1))
        max_page = max(0, (total - 1) // page_size)
        self._reg_prev_btn.setEnabled(self._reg_page > 0)
        self._reg_next_btn.setEnabled(self._reg_page < max_page)

    def _filter_reg_table(self, query: str = None):
        """Re-render the page with the new filter set. Resets to page 1."""
        self._reg_page = 0
        self._render_reg_page()

    def _today_unique_regions_types(self):
        """Return (regions_set, region_to_types_dict) from cached row data
        — works regardless of pagination state."""
        regions = set()
        rt = {}
        for row in getattr(self, "_reg_all_rows", []) or []:
            _cid, _doc, tipo, *_rest, region, _cp = row
            if region:
                regions.add(region)
                rt.setdefault(region, set())
                if tipo:
                    rt[region].add(tipo)
        return regions, rt

    def _open_reg_filter_dialog(self):
        """Lightweight popup anchored under the Filter button to choose
        Region + Type from values present in today's rows only."""
        regions_set, rt = self._today_unique_regions_types()
        regions = sorted(regions_set)
        all_types = sorted({t for s in rt.values() for t in s})

        popup = QFrame(self, Qt.Popup)
        popup.setObjectName("filterPopup")
        try:
            from qfluentwidgets.common.style_sheet import isDarkTheme
            from .theme_palette import palette
            _p = palette(not isDarkTheme())
        except Exception:
            _p = {"surface": "#161B22", "base": "#0D1117",
                  "border_strong": "#30363D", "text": "#E6EDF3",
                  "muted": "#8B949E", "accent": "#1757D4",
                  "accent_2": "#1F6FEB"}
        popup.setStyleSheet(
            f"#filterPopup {{ background-color: {_p['surface']};"
            f"  border: 1px solid {_p['border_strong']};"
            f"  border-radius: 10px; }}"
            f"QLabel {{ color: {_p['muted']}; font-size: 10px;"
            f"  font-weight: 700; background: transparent; padding: 0; }}"
            f"QComboBox {{ background: {_p['base']};"
            f"  border: 1px solid {_p['border_strong']};"
            f"  border-radius: 6px; padding: 4px 22px 4px 8px;"
            f"  color: {_p['text']}; font-size: 11px; min-height: 24px; }}"
            f"QComboBox::drop-down {{ subcontrol-origin: padding;"
            f"  subcontrol-position: right center; width: 20px;"
            f"  border: none; }}"
            f"QComboBox::down-arrow {{ image: url({_icon_url('tabler_chevron_down.svg')});"
            f"  width: 12px; height: 12px; }}"
            f"QPushButton {{ border-radius: 6px; padding: 6px 12px;"
            f"  font-size: 11px; font-weight: 700; }}"
            f"QPushButton#apply {{ background: {_p['accent']};"
            f"  border: 1px solid {_p['accent']}; color: white; }}"
            f"QPushButton#apply:hover {{ background: {_p['accent_2']}; }}"
            f"QPushButton#clear {{ background: transparent;"
            f"  border: 1px solid {_p['border_strong']};"
            f"  color: {_p['text']}; }}"
            f"QPushButton#clear:hover {{ background: rgba(0,0,0,0.05); }}"
        )
        lay = QVBoxLayout(popup)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(6)

        lay.addWidget(QLabel("REGION"))
        region_combo = QComboBox()
        region_combo.addItem("All")
        for r in regions:
            region_combo.addItem(r)
        if self._reg_filter_region in regions:
            region_combo.setCurrentText(self._reg_filter_region)
        lay.addWidget(region_combo)

        lay.addSpacing(4)
        lay.addWidget(QLabel("TYPE"))
        type_combo = QComboBox()

        def _refresh_types():
            type_combo.clear()
            type_combo.addItem("All")
            sel = region_combo.currentText()
            if sel == "All":
                for t in all_types:
                    type_combo.addItem(t)
            else:
                for t in sorted(rt.get(sel, [])):
                    type_combo.addItem(t)
            if self._reg_filter_type:
                idx = type_combo.findText(self._reg_filter_type)
                if idx >= 0:
                    type_combo.setCurrentIndex(idx)
        _refresh_types()
        region_combo.currentTextChanged.connect(lambda _t: _refresh_types())
        lay.addWidget(type_combo)

        lay.addSpacing(8)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        clear_btn = QPushButton("Clear")
        clear_btn.setObjectName("clear")
        apply_btn = QPushButton("Apply")
        apply_btn.setObjectName("apply")
        btn_row.addWidget(clear_btn)
        btn_row.addStretch()
        btn_row.addWidget(apply_btn)
        lay.addLayout(btn_row)

        def _apply():
            r_txt = region_combo.currentText()
            t_txt = type_combo.currentText()
            self._reg_filter_region = None if r_txt == "All" else r_txt
            self._reg_filter_type = None if t_txt == "All" else t_txt
            self._filter_reg_table()
            self._reg_filter_btn.setText(
                "Filters •" if (self._reg_filter_region or self._reg_filter_type)
                else "Filters"
            )
            popup.close()

        def _clear():
            self._reg_filter_region = None
            self._reg_filter_type = None
            self._filter_reg_table()
            self._reg_filter_btn.setText("Filters")
            popup.close()

        apply_btn.clicked.connect(_apply)
        clear_btn.clicked.connect(_clear)

        # Position popup right below the Filter button.
        btn = self._reg_filter_btn
        pos = btn.mapToGlobal(btn.rect().bottomRight())
        popup.adjustSize()
        popup.move(pos.x() - popup.width(), pos.y() + 6)
        popup.show()

    def _refresh_comment_chips(self):
        """Sync chip states + preview line. Updates both the Regular and OT
        chip sets so each mode reflects its own comment store."""
        from PySide6.QtGui import QFontMetrics

        def _sync(text: str, add_btn, see_btn, preview_lbl):
            has_text = bool(text.strip())
            if see_btn is not None:
                see_btn.setEnabled(has_text)
            if add_btn is not None:
                add_btn.setText("Edit comment" if has_text else "Add comment")
            if preview_lbl is None:
                return
            if has_text:
                single = " ".join(text.split())
                width = max(120, preview_lbl.width() or 220)
                fm = QFontMetrics(preview_lbl.font())
                preview_lbl.setText(
                    fm.elidedText(single, Qt.TextElideMode.ElideRight, width)
                )
                preview_lbl.setToolTip(text)
                preview_lbl.setVisible(True)
            else:
                preview_lbl.setVisible(False)
                preview_lbl.setText("")
                preview_lbl.setToolTip("")

        # Regular chips.
        _sync(self.comments_input.toPlainText(),
              getattr(self, "_btn_add_comment", None),
              getattr(self, "_btn_see_comment", None),
              getattr(self, "_comment_preview", None))
        # OT chips (only exist after the OT view is built).
        if hasattr(self, "_btn_add_ot_comment"):
            _sync(self.ot_comments_input.toPlainText(),
                  self._btn_add_ot_comment,
                  self._btn_see_ot_comment,
                  getattr(self, "_ot_comment_preview", None))

    def _open_comment_dialog(self, read_only: bool):
        """Open the Fluent comment modal — implementation lives in
        ``tabs.comment_dialog`` so this file stays focused on the form."""
        from .comment_dialog import open_comment_dialog
        target = self._comments_widget_for_mode(self._mode)
        if open_comment_dialog(self, target, read_only=read_only):
            self._refresh_comment_chips()

    def _show_result(self, efficiency: float, case_value: float, color: str):
        """Update the KPI boxes (Efficiency / Case Value / Units. Eq)."""
        self._result_eff_value.setText(self._compact_pct(efficiency))
        self._result_eff_value.setStyleSheet(
            f"font-size: 18px; font-weight: 500; color: {color};"
        )
        self._result_val_value.setText(f"{case_value:.3f}%")
        # Light autofit only — keep font readable (min 14px); abbreviation
        # above handles the worst overflow cases.
        self._autofit_kpi_font(self._result_eff_value, color, min_px=14)
        self._autofit_kpi_font(self._result_val_value, "#3FB950", min_px=14)
        # Units. Eq — use the proper helper that supports both legacy
        # base-rate model and explicit per-case-type rates. case_value comes
        # in as a percentage (e.g. 95.0 means 95%) but the helper expects
        # the raw percentage too.
        try:
            region = self.region.currentText()
            tipo = self.tipo.currentText()
            count = 1 if self.count_toggle.isChecked() else 0
            ue = calculate_equivalent_units(
                self.units_eq, region, tipo, case_value, count=count,
            )
            self._result_ue_value.setText(f"{ue:.2f}")
        except Exception:
            self._result_ue_value.setText("-")
        self._autofit_kpi_font(self._result_ue_value, "#A371F7", min_px=14)
        self.result_label.setVisible(False)

        # Mirror values into the OT view's KPI card so both modes show the
        # same calculation result. Uses the same font-autofit logic.
        if hasattr(self, "_ot_result_eff_value"):
            self._ot_result_eff_value.setText(self._result_eff_value.text())
            self._ot_result_eff_value.setStyleSheet(self._result_eff_value.styleSheet())
            self._ot_result_val_value.setText(self._result_val_value.text())
            self._ot_result_val_value.setStyleSheet(
                self._result_val_value.styleSheet().replace("#3FB950", "#F0883E")
            )
            self._ot_result_ue_value.setText(self._result_ue_value.text())
            self._ot_result_ue_value.setStyleSheet(self._result_ue_value.styleSheet())

    @staticmethod
    def _compact_pct(value: float) -> str:
        """Format a percentage compactly so the KPI label always fits.
        Examples: 95.4 → '95.4%', 1240 → '1.24k%', 14112 → '14.1k%'."""
        try:
            v = float(value)
        except Exception:
            return "-"
        a = abs(v)
        if a >= 1_000_000:
            return f"{v/1_000_000:.2f}M%"
        if a >= 10_000:
            return f"{v/1000:.1f}k%"
        if a >= 1_000:
            return f"{v/1000:.2f}k%"
        return f"{v:.1f}%"

    def _autofit_kpi_font(self, label, color: str, max_px: int = 18, min_px: int = 10):
        """Shrink the label's font-size until its text fits the available
        width (or hits min_px). Keeps long values like 14112.9% from clipping."""
        from PySide6.QtGui import QFontMetrics, QFont
        text = label.text()
        if not text:
            return
        avail = max(label.width() - 6, 40)
        size = max_px
        f = QFont(label.font())
        while size > min_px:
            f.setPixelSize(size)
            if QFontMetrics(f).horizontalAdvance(text) <= avail:
                break
            size -= 1
        label.setStyleSheet(
            f"font-size: {size}px; font-weight: 500; color: {color};"
        )

    def _set_result_status(self, msg: str, color: str = "#8B949E"):
        """Show status as a floating top-right InfoBar (Fluent toast).

        InfoBar comes from qfluentwidgets — handles its own animation,
        timing and close button. Avoids the home-rolled toast issues.
        The inline result_label stays hidden but keeps the message text
        so anything reading from it sees the latest status.
        """
        self.result_label.setText(msg)
        self.result_label.setVisible(False)
        try:
            from qfluentwidgets import InfoBar, InfoBarPosition, InfoBarIcon
            level = self._color_to_level(color)
            icon = {
                "success": InfoBarIcon.SUCCESS,
                "warning": InfoBarIcon.WARNING,
                "error":   InfoBarIcon.ERROR,
                "info":    InfoBarIcon.INFORMATION,
            }[level]
            InfoBar.new(
                icon=icon,
                title="",
                content=msg,
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP_RIGHT,
                duration=6000,
                parent=self.window(),
            )
        except Exception as exc:
            # Fallback to inline label if InfoBar is unavailable.
            self.result_label.setText(msg)
            self.result_label.setStyleSheet(f"font-size: 11px; color: {color};")
            self.result_label.setVisible(True)

    @staticmethod
    def _color_to_level(color: str) -> str:
        c = (color or "").upper()
        if c in ("#F85149", "#FF4D4F"):
            return "error"
        if c in ("#D29922", "#FFA726", "#F0883E"):
            return "warning"
        if c in ("#3FB950", "#2EA043", "#10893E"):
            return "success"
        return "info"

    def _reset_result_kpi(self):
        """Reset KPI boxes to blank state."""
        self._result_eff_value.setText("-")
        self._result_eff_value.setStyleSheet("font-size: 18px; font-weight: 500; color: #388BFD;")
        self._result_val_value.setText("-")
        self._result_val_value.setStyleSheet("font-size: 18px; font-weight: 500; color: #3FB950;")
        self._result_ue_value.setText("-")
        self._result_ue_value.setStyleSheet("font-size: 18px; font-weight: 500; color: #A371F7;")
        self.result_label.setVisible(False)

    def _std_for_case(self, region: str, tipo: str) -> float:
        """Look up the standard time effective on the case's date.

        Falls back to the latest std (``self.standards``) if the snapshot
        lookup yields nothing — guarantees Calculate / Save never crash
        on a missing key."""
        try:
            fecha = self.case_date.date().toString("yyyy-MM-dd")
        except Exception:
            fecha = ""
        try:
            from .utils import get_standards_snapshot_for_date
            snap = get_standards_snapshot_for_date(fecha) if fecha else None
            if snap:
                aligners = (snap.get(region, {}) or {}).get("Aligners", {}) or {}
                if tipo in aligners:
                    return float(aligners[tipo])
        except Exception:
            pass
        return float(self.standards[region]["Aligners"][tipo])

    def calculate(self):
        region = self.region.currentText()
        tipo = self.tipo.currentText()

        if not region or not tipo:
            return

        # Auto-set end time to now
        self.end_time.blockSignals(True)
        self.end_time.setTime(QTime.currentTime())
        self.end_time.blockSignals(False)

        # Use the standards snapshot effective on the case's date — so
        # editing an old case still calculates against the std that was
        # active back then, not today's latest.
        std_time = self._std_for_case(region, tipo)
        case_value = self.calculate_case_value(std_time)

        start = self.start_time.time()
        end = self.end_time.time()

        real_minutes = start.secsTo(end) / 60
        if real_minutes <= 0:
            self._set_result_status("Invalid time", "#F85149")
            return

        efficiency = (std_time / real_minutes) * 100

        # Determine status and color
        if efficiency >= 100:
            color = "#3FB950"
        elif efficiency >= 95:
            color = "#D29922"
        else:
            color = "#F85149"

        self._show_result(efficiency, case_value, color)

    def on_date_changed(self):
        """Called when the date picker changes - reload production and downtime for that date"""
        selected_date = self.case_date.date().toString("yyyy-MM-dd")
        self.downtime_manager.set_date(selected_date)
        self.load_daily_production()
        self._load_regular_day_cases()
        if self._mode == "overtime":
            self._load_ot_day_cases()

    def load_daily_production(self):
        # Use selected date from picker instead of today
        selected_date = self.case_date.date().toString("yyyy-MM-dd")
        try:
            conn = get_connection()
            cursor = conn.cursor()

            # Get total case values for selected date (only count_production = 1)
            cursor.execute("""
                SELECT SUM(case_value)
                FROM cases
                WHERE fecha = ? AND (count_production = 1 OR count_production IS NULL)
            """, (selected_date,))

            result = cursor.fetchone()
            total_cases = result[0] if result[0] else 0.0

            # Get cases by region+type for equivalent units calculation
            cursor.execute("""
                SELECT region, tipo_caso, COUNT(*), SUM(case_value)
                FROM cases
                WHERE fecha = ? AND (count_production = 1 OR count_production IS NULL)
                GROUP BY region, tipo_caso
            """, (selected_date,))

            region_cases = cursor.fetchall()
            conn.close()
        except Exception as exc:
            # DB locked or unavailable — keep prior labels rather than blanking the UI
            print(f"[RegisterTab] load_daily_production query failed: {exc}")
            return

        # Calculate equivalent units supporting both legacy and per-type UE models
        total_equivalent_units = 0.0
        for region, case_type, count, sum_cv in region_cases:
            if count and sum_cv:
                total_equivalent_units += calculate_equivalent_units(
                    self.units_eq,
                    region,
                    case_type,
                    sum_cv,
                    count=count,
                )
        
        # Get total downtime and calculate as production value
        total_downtime = self.get_daily_downtime(selected_date)
        downtime_value = (total_downtime / DAILY_BASE_MINUTES) * 100 if total_downtime > 0 else 0
        
        # Total production = cases + downtime (both count as production)
        total_production = total_cases + downtime_value
        
        # Resolve effective targets for this date (UE target may vary by date)
        try:
            from sync.daily_performance import (
                PRODUCTION_TARGET_PCT, get_ue_target_for_date,
            )
            prod_target = PRODUCTION_TARGET_PCT
            ue_target = get_ue_target_for_date(selected_date)
        except Exception:
            prod_target = 95.0
            ue_target = 14.0

        display_label = f"Daily Production: {total_production:.2f}%"
        if total_downtime > 0:
            display_label += f" (Cases: {total_cases:.2f}% + Downtime: {downtime_value:.2f}%)"
        display_label += f"  •  Target: {prod_target:.0f}%"

        self.daily_production_label.setText(display_label)

        self.equivalent_units_label.setText(
            f"Equivalent Units: {total_equivalent_units:.2f}  •  Target: {ue_target:.2f}"
        )

        # Update the new gauge + stat widgets in the redesigned card.
        # Gauge mirrors the "Daily Production" stat directly so both numbers
        # agree. The target itself is shown in the "Target: NN%" label below.
        if hasattr(self, "_daily_gauge"):
            try:
                self._daily_gauge.setValue(total_production)
            except Exception:
                pass
        if hasattr(self, "_daily_prod_value"):
            self._daily_prod_value.setText(f"{total_production:.2f}%")
        if hasattr(self, "_daily_cases_value"):
            self._daily_cases_value.setText(f"{total_cases:.2f}%")
        if hasattr(self, "_daily_dt_value"):
            self._daily_dt_value.setText(f"{downtime_value:.2f}%")
        if hasattr(self, "_daily_target_label"):
            self._daily_target_label.setText(f"Target: {prod_target:.0f}%")
        if hasattr(self, "_eu_value"):
            self._eu_value.setText(f"{total_equivalent_units:.2f}")
        if hasattr(self, "_eu_target_value"):
            self._eu_target_value.setText(f"{ue_target:.2f}")
        
        # Update progress bar with animation - NO CAP, allow any value
        self.progress_bar.setMaximum(max(100, int(total_production) + 10))
        
        # Animate the progress bar
        self.animate_progress_bar(int(total_production))
        
        # Color based on performance threshold
        if total_production < 95:
            bar_color = "#F85149"   # red
        elif total_production < 100:
            bar_color = "#D29922"   # amber
        else:
            bar_color = "#3FB950"   # green

        # Adapt track + text colors to active theme so the bar doesn't show
        # a dark slab on a light background.
        try:
            from qfluentwidgets.common.style_sheet import isDarkTheme
            from .theme_palette import palette
            _p = palette(not isDarkTheme())
            track_bg = _p["raised"]
            text_fg = _p["text"]
        except Exception:
            track_bg = "#21262D"
            text_fg = "#E6EDF3"

        self.progress_bar.setStyleSheet(f"""
            QProgressBar {{
                background-color: {track_bg};
                border: none;
                border-radius: 6px;
                text-align: center;
                min-height: 24px;
                color: {text_fg};
                font-weight: 700;
                font-size: 11px;
            }}
            QProgressBar::chunk {{
                background-color: {bar_color};
                border-radius: 6px;
            }}
        """)
        
        return total_production

    def animate_progress_bar(self, target_value):
        """Animate the progress bar to the target value"""
        current_value = self.progress_bar.value()
        
        if not hasattr(self, '_progress_animation'):
            self._progress_animation = QPropertyAnimation(self.progress_bar, b"value")
            self._progress_animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        
        self._progress_animation.stop()
        self._progress_animation.setDuration(500)  # 500ms smoother animation
        self._progress_animation.setStartValue(current_value)
        self._progress_animation.setEndValue(target_value)
        self._progress_animation.start()

    def load_case_for_edit(self, db_id):
        """Load a regular case from database into form for editing."""
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT case_id, region, tipo_caso, doctor, fecha, hora_inicio, hora_fin, count_production, comments
            FROM cases WHERE id = ?
        """, (db_id,))
        row = cursor.fetchone()
        conn.close()

        if not row:
            return

        self.switch_to_regular_mode()
        self._editing_id = db_id

        self.case_id.setText(row[0])
        region_idx = self.region.findText(row[1])
        if region_idx >= 0:
            self.region.setCurrentIndex(region_idx)
        self.update_case_types()
        type_idx = self.tipo.findText(row[2])
        if type_idx >= 0:
            self.tipo.setCurrentIndex(type_idx)

        self.doctor.setText(row[3] if row[3] else "")
        self.case_date.setDate(QDate.fromString(row[4], "yyyy-MM-dd"))
        self.start_time.setTime(QTime.fromString(row[5], "HH:mm"))
        self.end_time.setTime(QTime.fromString(row[6], "HH:mm"))

        count_prod = row[7] if row[7] is not None else 1
        self.count_toggle.setChecked(bool(count_prod))
        self.comments_input.setText(row[8] if row[8] else "")

        self._edit_banner_label.setText(f"Case  {row[0]}")
        self._show_edit_banner(is_ot=False)
        self._reset_result_kpi()
        # NC cases coming back from reprocess get a follow-up prompt for
        # an additional time segment. Single-segment cases stay simple.
        self._editing_table_name = "cases"
        # NC case coming back from reprocess → auto-stack the previous
        # start/end into the segments table and reset the form to a
        # fresh session window. The user keeps editing in Case
        # Information; total real time accumulates in segments.
        if not bool(count_prod):
            self._auto_stack_prior_session(
                db_id, "cases",
                prior_fecha=row[4], prior_start=row[5], prior_end=row[6],
            )
        self._refresh_segments_btn_state()

    def load_ot_case_for_edit(self, db_id: int):
        """Load an OT case from ot_cases into the form for editing."""
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT case_id, region, tipo_caso, doctor, fecha, hora_inicio, hora_fin, count_production, comments
            FROM ot_cases WHERE id = ?
        """, (db_id,))
        row = cursor.fetchone()
        conn.close()

        if not row:
            return

        self.switch_to_ot_mode()
        self._editing_id = db_id

        self.case_id.setText(row[0])
        region_idx = self.region.findText(row[1])
        if region_idx >= 0:
            self.region.setCurrentIndex(region_idx)
        self.update_case_types()
        type_idx = self.tipo.findText(row[2])
        if type_idx >= 0:
            self.tipo.setCurrentIndex(type_idx)

        self.doctor.setText(row[3] if row[3] else "")
        self.case_date.setDate(QDate.fromString(row[4], "yyyy-MM-dd"))
        self.start_time.setTime(QTime.fromString(row[5], "HH:mm"))
        self.end_time.setTime(QTime.fromString(row[6], "HH:mm"))

        count_prod = row[7] if row[7] is not None else 1
        self.count_toggle.setChecked(bool(count_prod))
        if hasattr(self, "ot_comments_input"):
            self.ot_comments_input.setText(row[8] if row[8] else "")

        self._edit_banner_label.setText(f"Case  {row[0]}")
        self._show_edit_banner(is_ot=True)
        self._reset_result_kpi()
        self._editing_table_name = "ot_cases"
        if not bool(count_prod):
            self._auto_stack_prior_session(
                db_id, "ot_cases",
                prior_fecha=row[4], prior_start=row[5], prior_end=row[6],
            )
        self._refresh_segments_btn_state()

    # ──────────────────────────────────────────────────────────────────
    # Multi-segment work time tracking (NC reprocess flow)
    # ──────────────────────────────────────────────────────────────────

    def _current_editing_table(self) -> str:
        """Return 'cases' or 'ot_cases' for the active edit, or '' if none."""
        return getattr(self, "_editing_table_name", "") or ""

    def _refresh_segments_btn_state(self):
        """Enable the clock-segments button when the case in edit mode has
        at least one extra segment beyond the implicit start/end pair."""
        btn = getattr(self, "_segments_btn", None)
        if btn is None:
            return
        editing_id = getattr(self, "_editing_id", None)
        table = self._current_editing_table()
        if not editing_id or not table:
            btn.setEnabled(False)
            return
        from db.database import list_case_segments
        segs = list_case_segments(int(editing_id), table)
        btn.setEnabled(len(segs) >= 1)

    def _auto_stack_prior_session(self, case_db_id: int, table_name: str,
                                    *, prior_fecha: str,
                                    prior_start: str, prior_end: str):
        """Push the case's existing hora_inicio/hora_fin into the segments
        table and reset the Start/End form fields so the user can record
        the current return session over a clean window."""
        from db.database import (
            list_case_segments, add_case_segment, _segment_duration_minutes,
        )
        # Skip when the row carries no useful range (defensive guard).
        if not prior_start or not prior_end:
            return
        if _segment_duration_minutes(prior_start, prior_end) <= 0:
            return
        # Avoid duplicate stacking — only stack when the row's current
        # values aren't already the latest segment row.
        segs = list_case_segments(int(case_db_id), table_name)
        already = bool(segs) and (
            segs[-1].get("fecha") == prior_fecha
            and segs[-1].get("hora_inicio") == prior_start
            and segs[-1].get("hora_fin") == prior_end
        )
        if not already:
            add_case_segment(
                int(case_db_id), table_name,
                prior_fecha or "", prior_start, prior_end,
                note="auto-stacked on edit",
            )
        # Reset the visible Start/End so the user captures the new session.
        try:
            self.start_time.setTime(QTime.currentTime())
            self.end_time.setTime(QTime(0, 0))
        except Exception:
            pass
        # Friendly toast so the user sees the stacking happened.
        try:
            self._set_result_status(
                f"Previous session ({prior_start} – {prior_end}) archived "
                "in Work time segments. Log this return session in Start / End.",
                "#58A6FF",
            )
        except Exception:
            pass

    def _maybe_prompt_new_segment(self):
        """LEGACY — kept for backwards compatibility but no longer used.
        Auto-stacking via :meth:`_auto_stack_prior_session` replaced the
        explicit prompt."""
        editing_id = getattr(self, "_editing_id", None)
        table = self._current_editing_table()
        if not editing_id or not table:
            return
        try:
            from qfluentwidgets import MessageBoxBase
        except Exception:
            MessageBoxBase = None
        if MessageBoxBase is None:
            return
        from PySide6.QtGui import QColor as _QC_seg
        from PySide6.QtCore import QSize as _QS_seg
        from PySide6.QtWidgets import QToolButton as _QTB_segp

        class _AskNewSegmentSheet(MessageBoxBase):
            def __init__(_s, h):
                super().__init__(h.window())
                try:
                    _s.setMaskColor(_QC_seg(0, 0, 0, 170))
                except Exception:
                    pass
                _s.widget.setObjectName("askSegCard")
                apply_fluent_modal_palette(_s, "askSegCard")
                _s.viewLayout.setContentsMargins(22, 18, 22, 12)
                _s.viewLayout.setSpacing(10)
                hdr = QHBoxLayout(); hdr.setSpacing(10)
                try:
                    from .tabler_icons import TablerIcon as _TI_s
                    ic = _QTB_segp(); ic.setEnabled(False)
                    ic.setIcon(_TI_s("tabler_clock.svg").icon(color=_QC_seg("#58A6FF")))
                    ic.setIconSize(_QS_seg(22, 22))
                    ic.setStyleSheet(
                        "background: rgba(56,139,253,0.14); border: none;"
                        " border-radius: 10px; padding: 6px;"
                    )
                    hdr.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
                except Exception:
                    pass
                tc = QVBoxLayout(); tc.setSpacing(2)
                t = QLabel("Add new work segment?")
                t.setStyleSheet(
                    "color: #E6EDF3; font-size: 15px; font-weight: 700;"
                    " background: transparent;"
                )
                sub = QLabel(
                    "This case is marked as not counting for production "
                    "(internal reprocess / doctor review). Would you like to "
                    "log a new work segment for this return session?"
                )
                sub.setWordWrap(True)
                sub.setStyleSheet(
                    "color: #8B949E; font-size: 11px; background: transparent;"
                )
                tc.addWidget(t); tc.addWidget(sub)
                hdr.addLayout(tc, 1)
                _s.viewLayout.addLayout(hdr)
                _s.widget.setMinimumWidth(440)
                _s.cancelButton.setText("Not now")
                _s.yesButton.setText("Add segment")

        dlg = _AskNewSegmentSheet(self)
        if dlg.exec():
            self._open_segments_dialog(start_in_add_mode=True)

    def _open_segments_dialog(self, start_in_add_mode: bool = False):
        """Modal that lists existing time segments for the case in edit and
        lets the user add / edit / delete entries."""
        editing_id = getattr(self, "_editing_id", None)
        table = self._current_editing_table()
        if not editing_id or not table:
            return
        try:
            from qfluentwidgets import MessageBoxBase
        except Exception:
            return
        from db.database import (
            list_case_segments, add_case_segment, update_case_segment,
            delete_case_segment, get_case_total_minutes,
        )
        from PySide6.QtGui import QColor as _QC_sm
        from PySide6.QtCore import QSize as _QS_sm
        from PySide6.QtWidgets import (
            QToolButton as _QTB_sm, QTimeEdit as _QTE_sm,
            QTableWidget as _QTW_sm, QTableWidgetItem as _QTI_sm,
            QHeaderView as _QHV_sm,
        )

        tab = self  # outer reference for helpers below

        class _SegSheet(MessageBoxBase):
            def __init__(_s, h):
                super().__init__(h.window())
                try:
                    _s.setMaskColor(_QC_sm(0, 0, 0, 170))
                except Exception:
                    pass
                _s.widget.setObjectName("segCard")
                apply_fluent_modal_palette(_s, "segCard")
                _s.viewLayout.setContentsMargins(22, 18, 22, 8)
                _s.viewLayout.setSpacing(8)

                hdr = QHBoxLayout(); hdr.setSpacing(10)
                try:
                    from .tabler_icons import TablerIcon as _TI_sm
                    ic = _QTB_sm(); ic.setEnabled(False)
                    ic.setIcon(_TI_sm("tabler_clock.svg").icon(color=_QC_sm("#58A6FF")))
                    ic.setIconSize(_QS_sm(22, 22))
                    ic.setStyleSheet(
                        "background: rgba(56,139,253,0.14); border: none;"
                        " border-radius: 10px; padding: 6px;"
                    )
                    hdr.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
                except Exception:
                    pass
                tc = QVBoxLayout(); tc.setSpacing(2)
                t = QLabel("Work time segments")
                t.setStyleSheet(
                    "color: #E6EDF3; font-size: 15px; font-weight: 700;"
                    " background: transparent;"
                )
                _s._total_lbl = QLabel("")
                _s._total_lbl.setStyleSheet(
                    "color: #8B949E; font-size: 11px; background: transparent;"
                )
                tc.addWidget(t); tc.addWidget(_s._total_lbl)
                hdr.addLayout(tc, 1)
                _s.viewLayout.addLayout(hdr)

                # ── Add Segment section (first, with explanation) ──
                add_card = QFrame()
                add_card.setObjectName("addSegCard")
                add_card.setStyleSheet(
                    "#addSegCard { background: rgba(56,139,253,0.06);"
                    "  border: 1px solid rgba(56,139,253,0.35);"
                    "  border-radius: 10px; }"
                    "QLabel { background: transparent; border: none; }"
                )
                av = QVBoxLayout(add_card)
                av.setContentsMargins(14, 10, 14, 10)
                av.setSpacing(6)
                _add_title = QLabel("Add segment")
                _add_title.setStyleSheet(
                    "color: #58A6FF; font-size: 12px; font-weight: 800;"
                    " letter-spacing: 0.5px;"
                )
                _add_help = QLabel(
                    "Each segment records one return session for this case. "
                    "Total work time = sum of every segment's start→end. Use "
                    "this whenever the case comes back from reprocess / "
                    "doctor review so the real effort is captured."
                )
                _add_help.setWordWrap(True)
                _add_help.setStyleSheet(
                    "color: #C9D1D9; font-size: 11px;"
                )
                av.addWidget(_add_title)
                av.addWidget(_add_help)

                # Build the add-segment inputs (Date+Start+End) inside the
                # explanation card. DateEditWithShortcut already ships the
                # chevron + calendar icon styling used by the Case card.
                add_row = QHBoxLayout(); add_row.setSpacing(8)
                _s._add_date = DateEditWithShortcut()
                _s._add_date.setDate(QDate.currentDate())
                _s._add_date.setMinimumHeight(30)
                _s._add_start = TimeEditWithShortcut()
                _s._add_start.setDisplayFormat("HH:mm")
                _s._add_start.setTime(QTime.currentTime())
                _s._add_start.setMinimumHeight(30)
                _s._add_end = TimeEditWithShortcut()
                _s._add_end.setDisplayFormat("HH:mm")
                _s._add_end.setTime(QTime.currentTime())
                _s._add_end.setMinimumHeight(30)
                # Reuse the pro_card input styling for chevron + calendar
                # icons so this modal matches the Case Information card.
                try:
                    from .widgets import _icon_url as _icu_seg
                    _chev_seg = _icu_seg("tabler_chevron_down.svg")
                    _cal_seg = _icu_seg("tabler_calendar.svg")
                    _clk_seg = _icu_seg("tabler_clock.svg")
                except Exception:
                    _chev_seg = _cal_seg = _clk_seg = ""
                _input_css_seg = (
                    "QDateEdit, QTimeEdit {"
                    "  background-color: #161B22; border: 1px solid #30363D;"
                    "  border-radius: 6px; padding: 4px 22px 4px 10px;"
                    "  color: #E6EDF3; font-size: 12px; min-height: 28px; }"
                    "QDateEdit:focus, QTimeEdit:focus {"
                    "  border-bottom: 2px solid #388BFD; }"
                    "QDateEdit::drop-down, QTimeEdit::drop-down {"
                    "  subcontrol-origin: padding;"
                    "  subcontrol-position: right center;"
                    "  width: 20px; border: none; }"
                    f"QDateEdit::down-arrow, QTimeEdit::down-arrow {{"
                    f"  image: url({_chev_seg});"
                    "  width: 11px; height: 11px; }"
                    "QDateEdit::up-button, QDateEdit::down-button,"
                    "QTimeEdit::up-button, QTimeEdit::down-button {"
                    "  width: 0; border: none; }"
                )
                for w in (_s._add_date, _s._add_start, _s._add_end):
                    w.setStyleSheet(_input_css_seg)
                # Guarantee the Date field can fit "yyyy-MM-dd" + chevron.
                _s._add_date.setMinimumWidth(140)
                _s._add_start.setMinimumWidth(90)
                _s._add_end.setMinimumWidth(90)

                for w, lbl, stretch in (
                    (_s._add_date, "Date", 2),
                    (_s._add_start, "Start", 1),
                    (_s._add_end, "End", 1),
                ):
                    col = QVBoxLayout(); col.setSpacing(2)
                    l = QLabel(lbl)
                    l.setStyleSheet(
                        "color: #8B949E; font-size: 10px; font-weight: 600;"
                        " background: transparent;"
                    )
                    col.addWidget(l); col.addWidget(w)
                    add_row.addLayout(col, stretch)

                _add_btn = QPushButton("Add segment")
                _add_btn.setCursor(Qt.PointingHandCursor)
                _add_btn.setMinimumHeight(30)
                _add_btn.setStyleSheet(
                    "QPushButton { background: #1e63e4; color: white;"
                    "  border: none; border-radius: 6px; padding: 6px 14px;"
                    "  font-weight: 700; font-size: 11px; }"
                    "QPushButton:hover { background: #2a73f3; }"
                )
                add_row.addWidget(_add_btn, 0, Qt.AlignmentFlag.AlignBottom)
                av.addLayout(add_row)
                _s.viewLayout.addWidget(add_card)

                # ── Existing segments table ──
                _s._tbl = _QTW_sm()
                _s._tbl.setColumnCount(5)
                _s._tbl.setHorizontalHeaderLabels(
                    ["Date", "Start", "End", "Duration", ""]
                )
                _s._tbl.verticalHeader().setVisible(False)
                _s._tbl.setEditTriggers(_QTW_sm.EditTrigger.NoEditTriggers)
                _s._tbl.setSelectionBehavior(_QTW_sm.SelectionBehavior.SelectRows)
                _s._tbl.setMinimumHeight(180)
                _s._tbl.horizontalHeader().setSectionResizeMode(
                    _QHV_sm.ResizeMode.Stretch
                )
                _s._tbl.horizontalHeader().setStretchLastSection(False)
                _s._tbl.setColumnWidth(4, 48)
                _s._tbl.horizontalHeader().setSectionResizeMode(
                    4, _QHV_sm.ResizeMode.Fixed
                )
                _s._tbl.verticalHeader().setDefaultSectionSize(34)
                _s.viewLayout.addWidget(_s._tbl)

                def _refresh():
                    segs = list_case_segments(int(editing_id), table)
                    _s._tbl.setRowCount(len(segs))
                    for i, seg in enumerate(segs):
                        for col, key in enumerate(
                            ("fecha", "hora_inicio", "hora_fin")
                        ):
                            it = _QTI_sm(str(seg.get(key) or ""))
                            it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                            it.setData(Qt.ItemDataRole.UserRole, seg["id"])
                            _s._tbl.setItem(i, col, it)
                        from db.database import _segment_duration_minutes as _dm
                        dur = _dm(seg["hora_inicio"], seg["hora_fin"])
                        h, m = divmod(int(dur), 60)
                        it_dur = _QTI_sm(f"{h}h {m:02d}m" if h else f"{m} min")
                        it_dur.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                        _s._tbl.setItem(i, 3, it_dur)
                        # Delete button per row.
                        del_btn = _QTB_sm()
                        del_btn.setCursor(Qt.PointingHandCursor)
                        del_btn.setFixedSize(24, 24)
                        try:
                            from .tabler_icons import TablerIcon as _TI_d
                            del_btn.setIcon(
                                _TI_d("tabler_trash.svg").icon(color=_QC_sm("#F85149"))
                            )
                            del_btn.setIconSize(_QS_sm(13, 13))
                        except Exception:
                            del_btn.setText("X")
                        del_btn.setStyleSheet(
                            "QToolButton { background: transparent;"
                            "  border: 1px solid #F85149; border-radius: 4px;"
                            "  padding: 0; margin: 0; }"
                            "QToolButton:hover { background: rgba(248,81,73,0.10); }"
                        )
                        del_btn.clicked.connect(
                            lambda _=False, sid=seg["id"]: (
                                delete_case_segment(sid), _refresh()
                            )
                        )
                        _del_wrap = QWidget()
                        _del_wrap.setStyleSheet("background: transparent;")
                        _dwl = QHBoxLayout(_del_wrap)
                        _dwl.setContentsMargins(0, 0, 0, 0)
                        _dwl.setSpacing(0)
                        _dwl.addStretch(1)
                        _dwl.addWidget(del_btn, 0, Qt.AlignmentFlag.AlignCenter)
                        _dwl.addStretch(1)
                        _s._tbl.setCellWidget(i, 4, _del_wrap)
                    total = get_case_total_minutes(int(editing_id), table)
                    th, tm = divmod(int(total), 60)
                    _s._total_lbl.setText(
                        f"{len(segs)} segment(s) · Total: "
                        f"{th}h {tm:02d}m" if th else
                        f"{len(segs)} segment(s) · Total: {tm} min"
                    )
                    tab._refresh_segments_btn_state()

                def _do_add():
                    add_case_segment(
                        int(editing_id), table,
                        _s._add_date.date().toString("yyyy-MM-dd"),
                        _s._add_start.time().toString("HH:mm"),
                        _s._add_end.time().toString("HH:mm"),
                    )
                    _refresh()
                _add_btn.clicked.connect(_do_add)
                _refresh()
                _s.widget.setMinimumWidth(620)

                _s.cancelButton.hide()
                _s.yesButton.setText("Close")

                if start_in_add_mode:
                    _s._add_start.setFocus()

        dlg = _SegSheet(self)
        dlg.exec()

    def _update_mode_ui(self):
        """Sync toggle label colors, save button, and right panel to self._mode."""
        is_ot = self._mode == "overtime"

        # Update label emphasis
        if hasattr(self, '_mode_label_regular'):
            self._mode_label_regular.setStyleSheet(
                "font-size: 12px; font-weight: 700; color: #8B949E;"
                if is_ot else
                "font-size: 12px; font-weight: 700; color: #388BFD;"
            )
        if hasattr(self, '_mode_label_ot'):
            self._mode_label_ot.setStyleSheet(
                "font-size: 12px; font-weight: 700; color: #F0883E;"
                if is_ot else
                "font-size: 12px; font-weight: 700; color: #6E7681;"
            )

        # Sync toggle position without re-firing the signal
        if hasattr(self, '_mode_toggle') and self._mode_toggle.isChecked() != is_ot:
            self._mode_toggle.blockSignals(True)
            self._mode_toggle.setChecked(is_ot)
            self._mode_toggle.blockSignals(False)

        # Save button — Fluent-styled. Color encodes mode (OT=orange, Reg=blue).
        if is_ot:
            self._save_btn.setText("Save Case")
            self._save_btn.setStyleSheet("""
                QPushButton { background-color: #F0883E; border: 1px solid #F0883E;
                              color: white; border-radius: 8px; font-weight: 700;
                              font-size: 11px; padding: 7px 16px; }
                QPushButton:hover { background-color: #FF9849; border-color: #FF9849; }
                QPushButton:pressed { background-color: #D97834; }
            """)
        else:
            self._save_btn.setText("Save Case")
            self._save_btn.setStyleSheet("""
                QPushButton { background-color: #1757D4; border: 1px solid #1757D4;
                              color: white; border-radius: 8px; font-weight: 700;
                              font-size: 11px; padding: 7px 16px; }
                QPushButton:hover { background-color: #388BFD; border-color: #388BFD; }
                QPushButton:pressed { background-color: #1158C7; }
            """)

        # Right panel stack
        if hasattr(self, '_right_stack'):
            self._right_stack.setCurrentIndex(1 if is_ot else 0)
        self._sync_regular_progress_visibility()

    def _on_mode_toggled(self, checked: bool):
        """Called when the user flips the mode toggle.

        Rapid clicks used to re-enter switch_to_*_mode while the previous
        switch was still running (capture_state → restore_state → reload),
        leaving the Daily Production card populated with the OLD mode's
        data. Guard with a reentrancy flag + coalescing queue so only the
        latest requested mode wins."""
        target = "overtime" if checked else "regular"
        if getattr(self, "_mode_switching", False):
            # A switch is already in-flight; remember the latest target and
            # let the running switch drain into it once it finishes.
            self._pending_mode = target
            return
        self._mode_switching = True
        try:
            if target == "overtime":
                self.switch_to_ot_mode()
            else:
                self.switch_to_regular_mode()
        finally:
            self._mode_switching = False
        # If the user clicked again while we were switching, settle on the
        # latest target.
        pending = getattr(self, "_pending_mode", None)
        if pending is not None and pending != self._mode:
            self._pending_mode = None
            # Re-fire the toggle handler once via the event loop so the
            # widget state has time to settle (avoids painting glitches).
            QTimer.singleShot(0, lambda: self._on_mode_toggled(pending == "overtime"))
        else:
            self._pending_mode = None

    def switch_to_ot_mode(self):
        """Switch the form to overtime mode and load OT cases."""
        if self._mode == "regular":
            self._capture_mode_state("regular")
        self._mode = "overtime"
        self._editing_id = None
        self._edit_banner.setVisible(False)
        self._set_edit_focus(False)
        self._update_mode_ui()
        self._restore_mode_state("overtime")
        self._load_ot_day_cases()
        # Re-skin the Daily Production card to show OT-specific numbers.
        self._apply_ot_to_daily_production()

    def switch_to_regular_mode(self):
        """Switch the form to regular mode."""
        if self._mode == "overtime":
            self._capture_mode_state("overtime")
        self._mode = "regular"
        self._editing_id = None
        self._edit_banner.setVisible(False)
        self._set_edit_focus(False)
        self._update_mode_ui()
        self._restore_mode_state("regular")
        self._load_regular_day_cases()
        # Restore the Daily Production card to regular numbers.
        self.load_daily_production()
        self._restore_daily_production_title()

    def _restore_daily_production_title(self):
        """Reset the Daily Production card title to the regular shift label."""
        title_widget = self.progress_group.findChild(QLabel, "proCardTitle")
        if title_widget is not None and hasattr(self, "_daily_shift_label"):
            title_widget.setText(self._daily_shift_label.upper())

    def _apply_ot_to_daily_production(self):
        """Populate the Daily Production card with OT-specific totals — same
        widgets, different data source. Called when entering OT mode and
        whenever the OT cases list reloads while in OT mode."""
        from PySide6.QtGui import QColor as _QColOT
        selected_date = self.case_date.date().toString("yyyy-MM-dd")
        try:
            conn = get_connection()
            cur = conn.cursor()
            cur.execute(
                "SELECT case_value, region, tipo_caso, count_production "
                "FROM ot_cases WHERE fecha = ?", (selected_date,)
            )
            rows = cur.fetchall()
            conn.close()
        except Exception:
            rows = []

        # Aggregate OT production % (sum of case_value across counting cases).
        total_ot_pct = sum((r[0] or 0.0) for r in rows
                           if (r[3] in (1, None)))
        total_ot_cases = len(rows)
        # OT equivalent units — same formula as the OT loader, per
        # (region, type) group.
        from collections import defaultdict
        groups = defaultdict(lambda: {"sum_cv": 0.0, "count": 0})
        for cv, region, tipo, count_prod in rows:
            if count_prod in (1, None):
                g = groups[(region or "", tipo or "")]
                g["sum_cv"] += (cv or 0.0)
                g["count"] += 1
        total_ue = 0.0
        for (region, tipo), g in groups.items():
            if g["count"]:
                total_ue += calculate_equivalent_units(
                    self.units_eq, region, tipo, g["sum_cv"], count=g["count"],
                )

        # OT day-of-week → max hours (config-driven, default 3h weekday / 8h Sat).
        from datetime import date as _date
        from sync.app_config import load_config
        cfg = load_config() or {}
        wk_max = float(cfg.get("ot_max_weekday_hours", 3))
        sat_max = float(cfg.get("ot_max_saturday_hours", 8))
        try:
            dt = _date.fromisoformat(selected_date)
            is_saturday = dt.weekday() == 5
        except Exception:
            is_saturday = False
        max_hours = sat_max if is_saturday else wk_max
        # Case value % is computed against DAILY_BASE_MINUTES, so the OT
        # max expressed in the same units is:
        ot_max_pct_of_base = (max_hours * 60.0 / DAILY_BASE_MINUTES) * 100.0
        # Gauge fills to 100% when total OT reaches the max.
        gauge_value = (
            (total_ot_pct / ot_max_pct_of_base) * 100.0
            if ot_max_pct_of_base > 0 else 0.0
        )
        # Cap at 200% so absurd values don't overflow the donut math.
        gauge_value = max(0.0, min(gauge_value, 200.0))

        # Title — switch to "OT Daily Production" while in OT mode, include max.
        title_widget = self.progress_group.findChild(QLabel, "proCardTitle")
        if title_widget is not None:
            day_word = "SAT" if is_saturday else "MON-FRI"
            title_widget.setText(
                f"OT DAILY PRODUCTION ({int(max_hours)}H MAX • {day_word})"
            )

        # Reuse the same widgets the regular loader writes to.
        if hasattr(self, "_daily_prod_value"):
            self._daily_prod_value.setText(f"{gauge_value:.2f}%")
            self._daily_prod_value.setStyleSheet(
                "color: #F0883E; font-size: 14px; font-weight: 700;"
                " background: transparent;"
            )
        if hasattr(self, "_daily_cases_value"):
            self._daily_cases_value.setText(str(total_ot_cases))
            self._daily_cases_value.setStyleSheet(
                "color: #F0883E; font-size: 14px; font-weight: 700;"
                " background: transparent;"
            )
        if hasattr(self, "_daily_dt_value"):
            self._daily_dt_value.setText("—")
        if hasattr(self, "_daily_target_label"):
            self._daily_target_label.setText(
                f"Target: 100% ({int(max_hours)}h max)"
            )
        if hasattr(self, "_eu_value"):
            self._eu_value.setText(f"{total_ue:.2f}")
        if hasattr(self, "_eu_target_value"):
            self._eu_target_value.setText("—")
        if hasattr(self, "_daily_gauge"):
            try:
                self._daily_gauge.setValue(gauge_value)
            except Exception:
                pass
        if hasattr(self, "progress_bar"):
            try:
                # Cancel any pending REG-mode animation that would otherwise
                # ride over the OT value (was producing wrong bar fills).
                anim = getattr(self, "_progress_animation", None)
                if anim is not None:
                    anim.stop()
                self.progress_bar.setMaximum(max(100, int(gauge_value) + 10))
                self.progress_bar.setValue(int(gauge_value))
            except Exception:
                pass

    def _load_regular_day_cases(self):
        """Fetch the day's regular cases from DB and render the first page."""
        selected_date = self.case_date.date().toString("yyyy-MM-dd")
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT case_id, doctor, tipo_caso, tiempo_real, efficiency, case_value, region, count_production
            FROM cases WHERE fecha = ? ORDER BY id DESC
        """, (selected_date,))
        self._reg_all_rows = cursor.fetchall()
        conn.close()
        if not hasattr(self, "_reg_page"):
            self._reg_page = 0
        if not hasattr(self, "_reg_page_size"):
            self._reg_page_size = 5
        self._render_reg_page()

    def _apply_reg_filters(self):
        """Return the subset of `_reg_all_rows` that matches active filters."""
        rows = getattr(self, "_reg_all_rows", []) or []
        q = ""
        try:
            q = (self._reg_search.text() or "").strip().lower()
        except AttributeError:
            pass
        rf = getattr(self, "_reg_filter_region", None)
        tf = getattr(self, "_reg_filter_type", None)

        def keep(row):
            case_id, doctor, tipo, *_, region, _cp = row
            if rf and (region or "") != rf:
                return False
            if tf and (tipo or "") != tf:
                return False
            if q:
                cid_l = str(case_id or "").lower()
                doc_l = str(doctor or "").lower()
                if q not in cid_l and q not in doc_l:
                    return False
            return True

        return [r for r in rows if keep(r)]

    def _render_reg_page(self):
        """Paint the current page slice of filtered rows into the table."""
        from PySide6.QtGui import QColor, QBrush, QFont
        is_light = self._is_light_mode()
        colors = get_light_theme_colors()

        filtered = self._apply_reg_filters()
        page_size = max(1, getattr(self, "_reg_page_size", 6))
        max_page = max(0, (len(filtered) - 1) // page_size)
        if self._reg_page > max_page:
            self._reg_page = max_page
        if self._reg_page < 0:
            self._reg_page = 0
        start = self._reg_page * page_size
        page_rows = filtered[start:start + page_size]

        self.reg_day_table.setRowCount(len(page_rows))
        for i, (case_id, doctor, tipo, tiempo, eff, cv, region, count_prod) in enumerate(page_rows):
            counts = count_prod if count_prod is not None else 1
            # Uniform table bg — the colour strip on the Case ID cell carries
            # the row-level status hint, no need for full-row tinting.
            if is_light:
                bg = light_row_bg(i, colors)
            else:
                bg = QColor("#0D1117")
            bg_brush = QBrush(bg)
            fg_brush = QBrush(CLR_FG_DARK if is_light else CLR_FG_LIGHT)
            bold = QFont()
            bold.setBold(True)
            ue_val = calculate_equivalent_units(self.units_eq, region or "", tipo or "", cv or 0.0, count=1)

            # Look up the expected std_time for this case so we can colour the
            # Time cell the same way Eff% gets coloured (green = within
            # standard, red = exceeded).
            std_time = self._lookup_std_time(region, tipo)

            vals = [
                str(case_id or ""),
                str(doctor or ""),
                str(tipo or ""),
                f"{tiempo:.0f}" if tiempo else "-",
                f"{eff:.0f}" if eff else "-",
                f"{cv:.2f}" if cv else "-",
                f"{ue_val:.2f}",
            ]
            # Determine the row-level status colour from efficiency (same
            # thresholds the Eff% painter uses).
            try:
                _e = float(eff) if eff is not None else 0.0
            except (TypeError, ValueError):
                _e = 0.0
            if _e >= 100:
                _status_color = "#3FB950"   # green
            elif _e >= 95:
                _status_color = "#D29922"   # amber
            else:
                _status_color = "#F85149"   # red

            from PySide6.QtGui import QFont as _QF_doc
            for col, text in enumerate(vals):
                # Col 0 is replaced by a setCellWidget below — keep an empty
                # item so the underlying default text doesn't shine through
                # the custom widget, but preserve the region UserRole + the
                # tooltip / searchable text.
                display_text = "" if col == 0 else text
                is_empty_doctor = (col == 1 and not text.strip())
                if is_empty_doctor:
                    display_text = "—  no doctor"
                item = QTableWidgetItem(display_text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setBackground(bg_brush)
                if is_empty_doctor:
                    # Muted italic placeholder for missing doctor names.
                    from PySide6.QtGui import QBrush as _QB_d, QColor as _QC_d
                    item.setForeground(_QB_d(_QC_d("#6E7681")))
                    f_it = _QF_doc(item.font())
                    f_it.setItalic(True)
                    f_it.setPixelSize(11)
                    item.setFont(f_it)
                else:
                    item.setForeground(fg_brush)
                item.setToolTip(text)
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, region or "")
                    # Stash the case_id text on UserRole+1 so the search
                    # filter can still match against it (it looks at item.text
                    # which is now empty).
                    item.setData(Qt.ItemDataRole.UserRole + 1, str(case_id or ""))
                if col == 3 and tiempo and std_time:
                    self._paint_time_cell(item, tiempo, std_time)
                if col == 4 and eff:
                    self._paint_efficiency_cell(item, eff)
                self.reg_day_table.setItem(i, col, item)

            # Replace the Case ID cell with a custom widget that has a
            # coloured left strip indicating overall row status.
            self.reg_day_table.setCellWidget(
                i, 0, self._case_id_widget(str(case_id or ""), _status_color)
            )
        # Toggle table vs empty-state placeholder.
        if hasattr(self, "_reg_table_stack"):
            total_rows = len(getattr(self, "_reg_all_rows", []) or [])
            self._reg_table_stack.setCurrentIndex(0 if total_rows else 1)

        # Refresh pagination footer text + prev/next state.
        if hasattr(self, "_reg_page_label"):
            self._refresh_pagination_footer(len(filtered))

    def _load_ot_day_cases(self):
        """Populate the embedded OT table — caches rows then renders via
        the filter/pagination pipeline (matches Today's Cases behaviour)."""
        from PySide6.QtGui import QColor, QBrush, QFont
        is_light = self._is_light_mode()
        colors = get_light_theme_colors()
        selected_date = self.case_date.date().toString("yyyy-MM-dd")
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, case_id, doctor, tipo_caso, tiempo_real, efficiency, case_value, region, count_production
            FROM ot_cases WHERE fecha = ? ORDER BY id DESC
        """, (selected_date,))
        rows = cursor.fetchall()
        # Cache for filter/render passes.
        self._ot_all_rows = []
        for db_id, case_id, doctor, tipo, tiempo, eff, cv, region, count_prod in rows:
            ue_val = calculate_equivalent_units(
                self.units_eq, region or "", tipo or "", cv or 0.0, count=1
            )
            std_time = self._lookup_std_time(region, tipo)
            self._ot_all_rows.append({
                "id": db_id, "case_id": case_id or "",
                "doctor": doctor or "", "tipo": tipo or "",
                "region": region or "",
                "tiempo": tiempo, "eff": eff, "cv": cv,
                "count_prod": count_prod if count_prod is not None else 1,
                "ue": ue_val, "std_time": std_time,
            })

        # Also compute summary stats
        cursor.execute("""
            SELECT SUM(case_value), region, tipo_caso, COUNT(*), SUM(case_value)
            FROM ot_cases
            WHERE fecha = ? AND (count_production = 1 OR count_production IS NULL)
            GROUP BY region, tipo_caso
        """, (selected_date,))
        region_rows = cursor.fetchall()
        conn.close()

        # Update summary labels
        total_ot_pct = sum((r[6] or 0.0) for r in rows if r[8] in (1, None)) or 0.0
        total_ue = 0.0
        for _, region, case_type, count, sum_cv in region_rows:
            if count and sum_cv:
                total_ue += calculate_equivalent_units(self.units_eq, region, case_type, sum_cv, count=count)

        if hasattr(self, "_ot_daily_prod_value"):
            self._ot_daily_prod_value.setText(f"{total_ot_pct:.2f}%")
        if hasattr(self, "_ot_daily_cases_value"):
            self._ot_daily_cases_value.setText(str(len(rows)))
        if hasattr(self, "_ot_daily_dt_value"):
            self._ot_daily_dt_value.setText("0.00%")
        if hasattr(self, "_ot_eu_value"):
            self._ot_eu_value.setText(f"{total_ue:.2f}")
        if hasattr(self, "_ot_eu_target_value"):
            self._ot_eu_target_value.setText("—")
        if hasattr(self, "_ot_daily_target_label"):
            self._ot_daily_target_label.setText("Target: —")
        if hasattr(self, "_ot_daily_gauge"):
            try:
                self._ot_daily_gauge.setValue(total_ot_pct)
            except Exception:
                pass
        self.ot_day_progress.setMaximum(max(100, int(total_ot_pct) + 10))
        self.ot_day_progress.setValue(int(total_ot_pct))
        if total_ot_pct < 10:
            bar_color = "#444C56"
        elif total_ot_pct < 25:
            bar_color = "#F0883E"
        else:
            bar_color = "#3FB950"
        self.ot_day_progress.setStyleSheet(f"""
            QProgressBar {{
                background-color: {"#F3F4F6" if is_light else "#21262D"};
                border: 1px solid {colors["border"] if is_light else "#21262D"};
                border-radius: 6px;
                text-align: center;
                min-height: 24px;
                color: {colors["text_primary"] if is_light else "#E6EDF3"};
                font-weight: 700;
                font-size: 11px;
            }}
            QProgressBar::chunk {{ background-color: {bar_color}; border-radius: 6px; }}
        """)
        if is_light:
            self.ot_reg_table.setStyleSheet(f"""
                QTableWidget {{ gridline-color: {colors["border"]}; border: 1px solid {colors["border"]}; background-color: {colors["surface_bg"]}; }}
                QHeaderView::section {{
                    background-color: {light_header_bg(colors)};
                    color: {light_header_fg(colors)};
                    border: 1px solid {colors["border"]};
                    padding: 5px 6px;
                    font-weight: 700;
                    font-size: 10px;
                }}
            """)
        else:
            self.ot_reg_table.setStyleSheet("""
                QTableWidget { gridline-color: #21262D; border: none; }
                QHeaderView::section {
                    background-color: #161B22;
                    color: #8B949E;
                    border: none;
                    border-bottom: 1px solid #30363D;
                    padding: 5px 6px;
                    font-weight: 700;
                    font-size: 10px;
                }
            """)

        # Render through the filter/pagination pipeline.
        if hasattr(self, "_ot_search"):
            self._render_ot_page(reset_to_first=True)
        else:
            # Initial setup race — fall back to direct populate.
            self.ot_reg_table.setRowCount(0)

        # While in OT mode, mirror OT totals onto the Daily Production card.
        if self._mode == "overtime" and hasattr(self, "_daily_gauge"):
            self._apply_ot_to_daily_production()

    def _filter_ot_table(self, *_):
        self._render_ot_page(reset_to_first=True)

    def _open_ot_filter_dialog(self):
        """Region/Type filter popup for the OT table — same style as the
        Today's Cases filter."""
        rows = self._ot_all_rows
        regions = sorted({r.get("region") for r in rows if r.get("region")})
        all_types = sorted({r.get("tipo") for r in rows if r.get("tipo")})
        # Map region → set of types present for that region.
        rt: dict[str, set] = {}
        for r in rows:
            reg = r.get("region")
            tpo = r.get("tipo")
            if reg and tpo:
                rt.setdefault(reg, set()).add(tpo)

        popup = QFrame(self, Qt.Popup)
        popup.setObjectName("filterPopup")
        try:
            from qfluentwidgets.common.style_sheet import isDarkTheme
            from .theme_palette import palette
            _p = palette(not isDarkTheme())
        except Exception:
            _p = {"surface": "#161B22", "base": "#0D1117",
                  "border_strong": "#30363D", "text": "#E6EDF3",
                  "muted": "#8B949E", "accent": "#1757D4",
                  "accent_2": "#1F6FEB"}
        popup.setStyleSheet(
            f"#filterPopup {{ background-color: {_p['surface']};"
            f"  border: 1px solid {_p['border_strong']};"
            f"  border-radius: 10px; }}"
            f"QLabel {{ color: {_p['muted']}; font-size: 10px;"
            f"  font-weight: 700; background: transparent; padding: 0; }}"
            f"QComboBox {{ background: {_p['base']};"
            f"  border: 1px solid {_p['border_strong']};"
            f"  border-radius: 6px; padding: 4px 22px 4px 8px;"
            f"  color: {_p['text']}; font-size: 11px; min-height: 24px; }}"
            f"QComboBox::drop-down {{ subcontrol-origin: padding;"
            f"  subcontrol-position: right center; width: 20px;"
            f"  border: none; }}"
            f"QComboBox::down-arrow {{ image: url({_icon_url('tabler_chevron_down.svg')});"
            f"  width: 12px; height: 12px; }}"
            f"QPushButton {{ border-radius: 6px; padding: 6px 12px;"
            f"  font-size: 11px; font-weight: 700; }}"
            f"QPushButton#apply {{ background: {_p['accent']};"
            f"  border: 1px solid {_p['accent']}; color: white; }}"
            f"QPushButton#apply:hover {{ background: {_p['accent_2']}; }}"
            f"QPushButton#clear {{ background: transparent;"
            f"  border: 1px solid {_p['border_strong']};"
            f"  color: {_p['text']}; }}"
            f"QPushButton#clear:hover {{ background: rgba(0,0,0,0.05); }}"
        )
        lay = QVBoxLayout(popup)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(6)

        lay.addWidget(QLabel("REGION"))
        region_combo = QComboBox()
        region_combo.addItem("All")
        for r in regions:
            region_combo.addItem(r)
        if self._ot_filter_region in regions:
            region_combo.setCurrentText(self._ot_filter_region)
        lay.addWidget(region_combo)

        lay.addSpacing(4)
        lay.addWidget(QLabel("TYPE"))
        type_combo = QComboBox()

        def _refresh_types():
            type_combo.clear()
            type_combo.addItem("All")
            sel = region_combo.currentText()
            if sel == "All":
                for t in all_types:
                    type_combo.addItem(t)
            else:
                for t in sorted(rt.get(sel, [])):
                    type_combo.addItem(t)
            if self._ot_filter_type:
                idx = type_combo.findText(self._ot_filter_type)
                if idx >= 0:
                    type_combo.setCurrentIndex(idx)
        _refresh_types()
        region_combo.currentTextChanged.connect(lambda _t: _refresh_types())
        lay.addWidget(type_combo)

        lay.addSpacing(8)
        btn_row = QHBoxLayout(); btn_row.setSpacing(8)
        clear_btn = QPushButton("Clear")
        clear_btn.setObjectName("clear")
        apply_btn = QPushButton("Apply")
        apply_btn.setObjectName("apply")
        btn_row.addWidget(clear_btn)
        btn_row.addStretch()
        btn_row.addWidget(apply_btn)
        lay.addLayout(btn_row)

        def _apply():
            r_txt = region_combo.currentText()
            t_txt = type_combo.currentText()
            self._ot_filter_region = None if r_txt == "All" else r_txt
            self._ot_filter_type = None if t_txt == "All" else t_txt
            self._render_ot_page(reset_to_first=True)
            self._ot_filter_btn.setText(
                "Filters •" if (self._ot_filter_region or self._ot_filter_type)
                else "Filters"
            )
            popup.close()

        def _clear():
            self._ot_filter_region = None
            self._ot_filter_type = None
            self._render_ot_page(reset_to_first=True)
            self._ot_filter_btn.setText("Filters")
            popup.close()

        apply_btn.clicked.connect(_apply)
        clear_btn.clicked.connect(_clear)

        btn = self._ot_filter_btn
        pos = btn.mapToGlobal(btn.rect().bottomRight())
        popup.adjustSize()
        popup.move(pos.x() - popup.width(), pos.y() + 6)
        popup.show()

    def _build_ot_empty_state(self):
        from .register_helpers import build_empty_state
        return build_empty_state(
            "No OT cases today",
            "Save an OT case from the Case Information panel to see it here.",
        )

    def _ot_change_page(self, delta: int):
        self._ot_current_page = max(0, self._ot_current_page + delta)
        self._render_ot_page()

    def _ot_page_size(self) -> int:
        if not hasattr(self, "_ot_page_size_combo"):
            return 5
        txt = self._ot_page_size_combo.currentText()
        if "All" in txt:
            return 10_000
        try:
            return int(txt.split()[0])
        except Exception:
            return 5

    def _apply_ot_filters(self):
        q = (self._ot_search.text() or "").strip().lower() if hasattr(self, "_ot_search") else ""
        out = []
        for r in self._ot_all_rows:
            if self._ot_filter_region and r.get("region") != self._ot_filter_region:
                continue
            if self._ot_filter_type and r.get("tipo") != self._ot_filter_type:
                continue
            if q:
                hay = f"{r.get('case_id','')} {r.get('doctor','')}".lower()
                if q not in hay:
                    continue
            out.append(r)
        return out

    def _render_ot_page(self, *, reset_to_first: bool = False):
        from PySide6.QtGui import QColor, QBrush, QFont
        is_light = self._is_light_mode()
        colors = get_light_theme_colors()
        filtered = self._apply_ot_filters()
        if reset_to_first:
            self._ot_current_page = 0
        page_size = self._ot_page_size()
        total = len(filtered)
        if total == 0 and hasattr(self, "_ot_table_stack"):
            self._ot_table_stack.setCurrentIndex(1)
        else:
            self._ot_table_stack.setCurrentIndex(0)
        max_page = max(0, (total - 1) // page_size)
        if self._ot_current_page > max_page:
            self._ot_current_page = max_page
        start = self._ot_current_page * page_size
        end = min(start + page_size, total)
        page = filtered[start:end]

        self._ot_reg_case_ids = [r["id"] for r in page]
        self.ot_reg_table.setRowCount(len(page))
        for i, r in enumerate(page):
            counts = r.get("count_prod", 1)
            if counts == 0:
                bg = QColor("#E9D8A6") if is_light else QColor(180, 150, 50)
            else:
                bg = (light_row_bg(i, colors) if is_light
                      else (QColor(13, 17, 23) if i % 2 == 0 else QColor(17, 22, 29)))
            bg_brush = QBrush(bg)
            fg_brush = QBrush(CLR_FG_DARK if is_light else CLR_FG_LIGHT)
            tiempo = r.get("tiempo")
            eff = r.get("eff")
            cv = r.get("cv")
            case_id = r.get("case_id", "")
            doctor = r.get("doctor", "")
            vals = [
                str(case_id),
                str(doctor),
                str(r.get("tipo", "")),
                f"{tiempo:.0f}" if tiempo else "-",
                f"{eff:.0f}" if eff else "-",
                f"{cv:.2f}" if cv else "-",
                f"{r.get('ue', 0):.2f}",
            ]

            # Compute status color from efficiency — drives the left strip.
            try:
                _e = float(eff) if eff is not None else 0.0
            except (TypeError, ValueError):
                _e = 0.0
            if _e >= 100:
                _status_color = "#3FB950"   # green
            elif _e >= 95:
                _status_color = "#D29922"   # amber
            else:
                _status_color = "#F85149"   # red

            from PySide6.QtGui import QFont as _QF_ot
            for col, text in enumerate(vals):
                # Col 0 is replaced by setCellWidget below — keep the item
                # empty so the default text doesn't show through.
                display_text = "" if col == 0 else text
                is_empty_doctor = (col == 1 and not text.strip())
                if is_empty_doctor:
                    display_text = "—  no doctor"
                item = QTableWidgetItem(display_text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setBackground(bg_brush)
                if is_empty_doctor:
                    from PySide6.QtGui import QBrush as _QB_otd, QColor as _QC_otd
                    item.setForeground(_QB_otd(_QC_otd("#6E7681")))
                    f_it = _QF_ot(item.font())
                    f_it.setItalic(True)
                    f_it.setPixelSize(11)
                    item.setFont(f_it)
                else:
                    item.setForeground(fg_brush)
                item.setToolTip(text)
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, r.get("region") or "")
                    item.setData(Qt.ItemDataRole.UserRole + 1, str(case_id))
                if col == 3 and tiempo and r.get("std_time"):
                    self._paint_time_cell(item, tiempo, r["std_time"])
                if col == 4 and eff:
                    self._paint_efficiency_cell(item, eff)
                self.ot_reg_table.setItem(i, col, item)

            # Replace the Case ID cell with the colored-strip widget so the
            # row gets the same status-bar styling as Today's Cases.
            self.ot_reg_table.setCellWidget(
                i, 0, self._case_id_widget(str(case_id), _status_color)
            )

        # Footer.
        if total == 0:
            self._ot_page_label.setText("Showing 0 of 0 cases")
        else:
            self._ot_page_label.setText(
                f"Showing {start + 1} to {end} of {total} cases"
            )
        self._ot_page_btn.setText(str(self._ot_current_page + 1))
        self._ot_prev_btn.setEnabled(self._ot_current_page > 0)
        self._ot_next_btn.setEnabled(self._ot_current_page < max_page)

    def _delete_selected_ot_case(self):
        """Delete the OT case selected in the embedded OT table."""
        row = self.ot_reg_table.currentRow()
        if row < 0 or row >= len(self._ot_reg_case_ids):
            return
        db_id = self._ot_reg_case_ids[row]
        case_id_text = self.ot_reg_table.item(row, 0).text() if self.ot_reg_table.item(row, 0) else str(db_id)
        reply = QMessageBox.question(
            self, "Confirm Delete",
            f"Delete OT case '{case_id_text}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM ot_cases WHERE id = ?", (db_id,))
            conn.commit()
            conn.close()
            self._load_ot_day_cases()
            self.ot_saved.emit()

    def _show_edit_banner(self, is_ot: bool):
        """Apply mode-specific color and start pulse animation."""
        color = "#F0883E" if is_ot else "#388BFD"
        self._edit_accent.setStyleSheet(f"background-color: {color}; border-radius: 2px;")
        badge_text = "\u270e  EDIT MODE  \u2022  OT" if is_ot else "\u270e  EDIT MODE"
        # Find the badge label (first QLabel child of banner)
        for w in self._edit_banner.findChildren(QLabel):
            if "EDIT MODE" in (w.text() or ""):
                w.setText(badge_text)
                w.setStyleSheet(
                    f"color: {color}; font-weight: 900; font-size: 10px;"
                    " letter-spacing: 1px; background: transparent; padding: 0 6px;"
                )
                break
        self._edit_banner.setStyleSheet(
            f"QFrame {{ background-color: #0D1117; border: 1px solid {color}; border-radius: 8px; }}"
        )
        self._banner_pulse_state = False
        self._banner_color = color
        # Banner deprecated — inline Cancel button + dimming carry edit
        # mode now. Keep banner hidden permanently.
        self._edit_banner.setVisible(False)
        self._set_edit_focus(True)

    def _pulse_edit_banner(self):
        """Pulse: alternate between full-opacity and ~half-opacity border at 550ms."""
        from PySide6.QtGui import QColor
        color = getattr(self, "_banner_color", "#388BFD")
        c = QColor(color)
        if self._banner_pulse_state:
            border_css = f"rgba({c.red()},{c.green()},{c.blue()},0.45)"
        else:
            border_css = f"rgba({c.red()},{c.green()},{c.blue()},1.0)"
        self._edit_banner.setStyleSheet(
            f"QFrame {{ background-color: #0D1117; border: 1px solid {border_css}; border-radius: 8px; }}"
        )
        self._banner_pulse_state = not self._banner_pulse_state

    def _cancel_edit(self):
        """Cancel edit — clear editing state and reset the form."""
        self._editing_id = None
        self._banner_pulse_timer.stop()
        self._edit_banner.setVisible(False)
        self._set_edit_focus(False)
        self.case_id.clear()
        self.doctor.clear()
        self._comments_widget_for_mode(self._mode).clear()
        self.count_toggle.setChecked(True)
        self.end_time.blockSignals(True)
        self.end_time.setTime(QTime(0, 0))
        self.end_time.blockSignals(False)
        self._reset_result_kpi()
        self._capture_mode_state(self._mode)
        self.case_id.setFocus()

    # â"€â"€ Web Import â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

    def _set_edit_focus(self, active: bool):
        """Edit-mode focus aid: dim every panel except Case Information
        and put the keyboard cursor on the Case ID field so the user can
        start typing immediately. Cleared when edit ends."""
        from PySide6.QtWidgets import QGraphicsOpacityEffect
        if not hasattr(self, "_edit_dim_targets"):
            self._edit_dim_targets = None
        if active:
            targets = []
            for name in (
                "_calc_card",
                "_comments_card",
                "downtime_card",
                "_reg_table_card",
                "_ot_summary_card",
                "progress_group",
            ):
                w = getattr(self, name, None)
                if w is not None and w not in targets:
                    targets.append(w)
            self._edit_dim_targets = targets
            for w in targets:
                try:
                    eff = QGraphicsOpacityEffect(w)
                    eff.setOpacity(0.15)
                    w.setGraphicsEffect(eff)
                    w.setEnabled(False)
                except Exception:
                    pass
            try:
                self.case_id.setFocus()
                self.case_id.selectAll()
            except Exception:
                pass
            if hasattr(self, "_inline_cancel_btn"):
                self._inline_cancel_btn.setVisible(True)
        else:
            for w in (self._edit_dim_targets or []):
                try:
                    w.setGraphicsEffect(None)
                    w.setEnabled(True)
                except Exception:
                    pass
            self._edit_dim_targets = None
            if hasattr(self, "_inline_cancel_btn"):
                self._inline_cancel_btn.setVisible(False)

    def _on_add_to_review(self):
        """Open a Fluent modal asking for the review reason + category,
        then add the currently-entered case to the review queue. The
        case stays in cases / ot_cases — this only creates a tracking
        entry."""
        case_id = (self.case_id.text() or "").strip()
        if not case_id:
            self._set_result_status(
                "Enter a Case ID before flagging for review.", "#D29922",
            )
            return
        result = self._prompt_review_reason(case_id)
        if result is None:
            return  # cancelled
        reason, category = result
        try:
            from .tab_review import add_case_to_review
            add_case_to_review(
                case_id=case_id,
                doctor=(self.doctor.text() or "").strip(),
                region=self.region.currentText() if self.region.count() else "",
                tipo_caso=self.tipo.currentText() if self.tipo.count() else "",
                fecha=self.case_date.date().toString("yyyy-MM-dd"),
                comment="",
                reason=reason.strip(),
                category=category,
            )
            self._set_result_status(
                f"Case {case_id} added to Cases For Review.", "#3FB950",
            )
        except Exception as exc:
            self._set_result_status(
                f"Could not add to review: {exc}", "#F85149",
            )

    def _show_review_help(self):
        """Explains what the flag-for-review button does."""
        try:
            from qfluentwidgets import MessageBoxBase
            from .tabler_icons import TablerIcon
            from PySide6.QtWidgets import QToolButton as _QTBh
            from PySide6.QtGui import QColor as _QCh
            from PySide6.QtCore import QSize as _QSh
        except Exception:
            QMessageBox.information(
                self, "Cases For Review",
                "Use this flag to mark cases that need follow-up "
                "(software glitch, doctor inquiry, etc).",
            )
            return

        host = self

        class _Sheet(MessageBoxBase):
            def __init__(_s, h):
                super().__init__(h.window() if h is not None else None)
                try:
                    _s.setMaskColor(_QCh(0, 0, 0, 170))
                except Exception:
                    pass
                _s.widget.setObjectName("revHelpCard")
                apply_fluent_modal_palette(_s, "revHelpCard")
                _s.viewLayout.setContentsMargins(22, 18, 22, 12)
                _s.viewLayout.setSpacing(12)

                hdr = QHBoxLayout(); hdr.setSpacing(12)
                ic = _QTBh(); ic.setEnabled(False)
                ic.setIcon(TablerIcon("tabler_flag.svg").icon(color=_QCh("#D29922")))
                ic.setIconSize(_QSh(22, 22))
                ic.setStyleSheet(
                    "background: rgba(210,153,34,0.14); border: none;"
                    " border-radius: 10px; padding: 6px;"
                )
                tc = QVBoxLayout(); tc.setSpacing(2)
                t = QLabel("Cases For Review")
                t.setStyleSheet(
                    "color: #E6EDF3; font-size: 15px; font-weight: 700;"
                    " background: transparent;"
                )
                tc.addWidget(t)
                hdr.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
                hdr.addLayout(tc, 1)
                _s.viewLayout.addLayout(hdr)

                body = QLabel(
                    "<p>This flag is for keeping a private list of cases "
                    "you want to revisit — it does <b>not</b> change the "
                    "regular production flow.</p>"
                    "<p>Common uses:</p>"
                    "<ul>"
                    "<li><b>Software Issue</b> — use this to track bugs "
                    "you spot in the production software.</li>"
                    "<li><b>Doctor Inquiry</b> — the doctor requested "
                    "something unusual, needs clarification, or you want "
                    "a colleague to double-check.</li>"
                    "<li><b>Other</b> — anything else worth tracking.</li>"
                    "</ul>"
                    "<p>Flagged cases stay in <i>cases</i> / <i>ot_cases</i> "
                    "and keep contributing to your production numbers. "
                    "They just also appear in the <b>Review</b> tab as a "
                    "convenient list.</p>"
                )
                body.setWordWrap(True)
                body.setStyleSheet(
                    "color: #C9D1D9; font-size: 12px;"
                    " background: transparent;"
                )
                _s.viewLayout.addWidget(body)
                _s.widget.setMinimumWidth(480)

                _s.cancelButton.hide()
                _s.yesButton.setText("Got it")
                _s.yesButton.setFixedWidth(120)
                _s.yesButton.setStyleSheet(
                    "QPushButton { background: #1e63e4; border: 1px solid #1e63e4;"
                    "  color: white; border-radius: 6px; padding: 8px 22px;"
                    "  font-weight: 700; font-size: 12px; }"
                    "QPushButton:hover { background: #2a73f3; }"
                )

        _Sheet(host).exec()

    def _prompt_review_reason(self, case_id: str):
        """Fluent modal that captures the review reason + category.
        Returns (reason, category) tuple on accept, or None on cancel."""
        try:
            from qfluentwidgets import MessageBoxBase
            from .tabler_icons import TablerIcon
            from PySide6.QtWidgets import (
                QFrame as _QF, QTextEdit as _QTE, QToolButton as _QTB,
                QButtonGroup as _QBG,
            )
            from PySide6.QtGui import QColor as _QC2
            from PySide6.QtCore import QSize as _QS2
        except Exception:
            from PySide6.QtWidgets import QInputDialog
            text, ok = QInputDialog.getMultiLineText(
                self, "Add to review",
                f"Why does case {case_id} need review?", "",
            )
            return (text, "Other") if ok else None

        host = self

        class _Sheet(MessageBoxBase):
            def __init__(_s, h):
                super().__init__(h.window() if h is not None else None)
                _s.result_text: str | None = None
                try:
                    _s.setMaskColor(_QC2(0, 0, 0, 170))
                except Exception:
                    pass
                _s.widget.setObjectName("revCard")
                apply_fluent_modal_palette(_s, "revCard")
                _s.viewLayout.setContentsMargins(22, 18, 22, 12)
                _s.viewLayout.setSpacing(12)

                hdr = QHBoxLayout(); hdr.setSpacing(12)
                ic = _QTB(); ic.setEnabled(False)
                ic.setIcon(TablerIcon("tabler_flag.svg").icon(color=_QC2("#D29922")))
                ic.setIconSize(_QS2(22, 22))
                ic.setStyleSheet(
                    "background: rgba(210,153,34,0.14); border: none;"
                    " border-radius: 10px; padding: 6px;"
                )
                tc = QVBoxLayout(); tc.setSpacing(2)
                t = QLabel("Add to review")
                t.setStyleSheet(
                    "color: #E6EDF3; font-size: 15px; font-weight: 700;"
                    " background: transparent;"
                )
                s = QLabel(f"Flag case {case_id} for follow-up.")
                s.setStyleSheet(
                    "color: #8B949E; font-size: 11px; background: transparent;"
                )
                tc.addWidget(t); tc.addWidget(s)
                hdr.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
                hdr.addLayout(tc, 1)
                _s.viewLayout.addLayout(hdr)

                # Category chips: Software Issue / Doctor Inquiry / Other.
                cat_lbl = QLabel("Category")
                cat_lbl.setStyleSheet(
                    "color: #C9D1D9; font-size: 11px; font-weight: 700;"
                    " background: transparent;"
                )
                _s.viewLayout.addWidget(cat_lbl)

                _s.selected_category = "Other"

                def _make_cat_chip(text, value, accent):
                    b = QPushButton(text)
                    b.setCheckable(True)
                    b.setCursor(Qt.PointingHandCursor)
                    b.setFixedHeight(28)
                    b.setStyleSheet(
                        "QPushButton { background: #161B22; border: 1px solid #30363D;"
                        "  color: #C9D1D9; border-radius: 6px; padding: 0 12px;"
                        "  font-size: 11px; font-weight: 600; }"
                        "QPushButton:hover { border-color: #58606A; }"
                        f"QPushButton:checked {{ background: rgba(0,0,0,0.0);"
                        f"  border-color: {accent}; color: {accent}; }}"
                    )
                    return b

                _s.chip_sw = _make_cat_chip("Software Issue", "Software Issue", "#F85149")
                _s.chip_dr = _make_cat_chip("Doctor Inquiry", "Doctor Inquiry", "#58A6FF")
                _s.chip_ot = _make_cat_chip("Other", "Other", "#D29922")
                _s.chip_ot.setChecked(True)

                cat_grp = _QBG(_s)
                cat_grp.setExclusive(True)
                cat_grp.addButton(_s.chip_sw)
                cat_grp.addButton(_s.chip_dr)
                cat_grp.addButton(_s.chip_ot)

                def _on_cat(btn, value):
                    if btn.isChecked():
                        _s.selected_category = value
                _s.chip_sw.clicked.connect(
                    lambda _=False: _on_cat(_s.chip_sw, "Software Issue")
                )
                _s.chip_dr.clicked.connect(
                    lambda _=False: _on_cat(_s.chip_dr, "Doctor Inquiry")
                )
                _s.chip_ot.clicked.connect(
                    lambda _=False: _on_cat(_s.chip_ot, "Other")
                )

                cat_row = QHBoxLayout(); cat_row.setSpacing(6)
                cat_row.addWidget(_s.chip_sw)
                cat_row.addWidget(_s.chip_dr)
                cat_row.addWidget(_s.chip_ot)
                cat_row.addStretch(1)
                _s.viewLayout.addLayout(cat_row)

                lbl = QLabel("Reason")
                lbl.setStyleSheet(
                    "color: #C9D1D9; font-size: 11px; font-weight: 700;"
                    " background: transparent;"
                )
                _s.viewLayout.addWidget(lbl)

                _s.editor = _QTE()
                _s.editor.setPlaceholderText(
                    "e.g. needs doctor review, software glitch, type unclear…"
                )
                _s.editor.setStyleSheet(
                    "QTextEdit { background: #161B22; border: 1px solid #30363D;"
                    "  border-radius: 6px; padding: 6px 8px; color: #E6EDF3;"
                    "  font-size: 11px; }"
                )
                _s.editor.setMinimumHeight(110)
                _s.viewLayout.addWidget(_s.editor)

                _s.widget.setMinimumWidth(460)

                _s.cancelButton.setText("Cancel")
                _s.cancelButton.setFixedWidth(120)
                _s.cancelButton.setStyleSheet(
                    "QPushButton { background: transparent;"
                    "  border: 1px solid #30363D; color: #E6EDF3;"
                    "  border-radius: 6px; padding: 8px 22px;"
                    "  font-weight: 700; font-size: 12px; }"
                    "QPushButton:hover { background: rgba(255,255,255,0.05); }"
                )
                _s.yesButton.setText("   Add to review")
                _s.yesButton.setFixedWidth(160)
                _s.yesButton.setStyleSheet(
                    "QPushButton { background: #D29922; border: 1px solid #D29922;"
                    "  color: white; border-radius: 6px; padding: 8px 22px;"
                    "  font-weight: 700; font-size: 12px; }"
                    "QPushButton:hover { background: #DBAB3F; }"
                )
                try:
                    _s.yesButton.setIcon(
                        TablerIcon("tabler_flag.svg").icon(color=_QC2("#FFFFFF"))
                    )
                    _s.yesButton.setIconSize(_QS2(14, 14))
                except Exception:
                    pass

                def _accept():
                    _s.result_text = _s.editor.toPlainText().strip()
                    _s.accept()
                try:
                    _s.yesButton.clicked.disconnect()
                except Exception:
                    pass
                _s.yesButton.clicked.connect(_accept)

        dlg = _Sheet(host)
        if dlg.exec():
            return (dlg.result_text or "", getattr(dlg, "selected_category", "Other"))
        return None

    def _on_import_case(self):
        """
        Read the clipboard, show a confirmation dialog with the detected data,
        and fill the fields only if the user confirms.
        """
        data = get_clipboard_case_data(self.standards)

        module_name = "ot" if self._mode == "overtime" else "regular"

        # Nothing found - show error directly, no dialog
        if not has_detected_case_fields(data):
            self._set_result_status(
                get_import_not_detected_message(module_name),
                "#D29922"
            )
            return

        if not show_import_confirmation(self, data):
            return

        # Extra confirmation popup when user is in OT mode (intentional choice).
        if self._mode == "overtime":
            if not self._confirm_ot_import_time(QTime.currentTime(), True):
                return

        imported_case_id, imported_region, imported_type = apply_imported_case_data(
            data,
            case_id_widget=self.case_id,
            region_widget=self.region,
            type_widget=self.tipo,
            doctor_widget=self.doctor,
            refresh_case_types_fn=self.update_case_types,
        )

        # Stash CR count + Product Tier from clipboard so the next Save Case
        # persists them into the new DB columns. Cleared inside _save_case.
        _cr = None
        _tipo_str = (data.get("tipo") or "")
        if _tipo_str.lower().startswith("cr"):
            import re as _re_cr_i
            _m = _re_cr_i.match(r"\s*CR\s*#?\s*(\d+)", _tipo_str, _re_cr_i.IGNORECASE)
            if _m:
                try:
                    _cr = int(_m.group(1))
                except ValueError:
                    _cr = None
        self._last_import_meta = {
            "cr_count": _cr,
            "product_tier": (data.get("product_tier") or "").strip(),
        }

        # Update date to today in case the app has been open since a previous day
        self.case_date.setDate(QDate.currentDate())
        self.start_time.setTime(QTime.currentTime())

        summary = build_import_summary(imported_case_id, imported_region, imported_type)
        success_color = "#F0883E" if self._mode == "overtime" else "#3FB950"
        self._set_result_status(get_import_success_message(summary, module_name), success_color)
        self._capture_mode_state(self._mode)

        self._show_import_toast(
            get_import_reminder_message(),
            duration_ms=4200,
        )

    def _confirm_ot_import_time(self, now: QTime, in_regular_hours: bool) -> bool:
        """Confirmation popup when importing in OT mode.

        Returns True if the user confirmed, False to abort.
        """
        title = "Confirmar importación OT"
        text = (
            "El caso se importará como OT.\n\n"
            "¿Seguro desea proseguir?"
        )
        reply = QMessageBox.question(
            self, title, text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    def _on_load_db(self):
        """Pick a cases.db (Fluent picker, with native fallback) and merge
        its rows into the current DB."""
        from .db_picker_dialog import pick_database_file
        path = pick_database_file(self)
        if not path:
            return

        try:
            from db.database import _merge_from
            counts = _merge_from(path)
        except Exception as exc:
            QMessageBox.critical(self, "Import failed", f"Could not import:\n{exc}")
            return

        total = sum(counts.values())
        if total == 0:
            QMessageBox.information(
                self, "Nothing new",
                "No new records found — all rows already exist in the current database.",
            )
        else:
            QMessageBox.information(
                self, "Import complete",
                f"Imported from:\n{path}\n\n"
                f"  • Regular cases:  {counts['cases']}\n"
                f"  • OT cases:       {counts['ot_cases']}\n"
                f"  • Downtimes:      {counts['downtimes']}\n\n"
                f"Total new records: {total}",
            )
        if total > 0:
            self.load_daily_production()
            self._load_regular_day_cases()
            self.case_saved.emit()

    def save_case(self):
        # Auto-set end time to now if the user never changed it from the default 00:00
        if self.end_time.time() == QTime(0, 0):
            self.end_time.blockSignals(True)
            self.end_time.setTime(QTime.currentTime())
            self.end_time.blockSignals(False)

        region = self.region.currentText()
        tipo = self.tipo.currentText()
        case_id = self.case_id.text()
        doctor = self.doctor.text().strip()
        case_date = self.case_date.date().toString("yyyy-MM-dd")

        start = self.start_time.time()
        end = self.end_time.time()

        tiempo_real = start.secsTo(end) / 60
        if tiempo_real <= 0:
            self._set_result_status("Invalid time", "#F85149")
            return

        # Subtract only breaks the user confirmed they took today
        from tabs.breaks_dialog import calculate_break_overlap
        break_mins = calculate_break_overlap(start.toString("HH:mm"), end.toString("HH:mm"), fecha=case_date)
        tiempo_real -= break_mins
        if tiempo_real <= 0:
            self._set_result_status("Case falls entirely within break time", "#F85149")
            return

        if not case_id.strip():
            self._set_result_status("Enter Case ID", "#D29922")
            return

        std_time = self._std_for_case(region, tipo)

        # When finalising a case that previously cycled through reprocess
        # (NC → return → NC → …), the segments table holds every prior
        # work window. The "real" time for efficiency is the SUM of all
        # those segments plus the current Start/End shown on the form.
        count_production = 1 if self.count_toggle.isChecked() else 0
        if self._editing_id and count_production == 1:
            try:
                from db.database import list_case_segments
                _editing_table = "ot_cases" if self._mode == "overtime" else "cases"
                _segs = list_case_segments(int(self._editing_id), _editing_table)
                if _segs:
                    _seg_mins = sum(
                        max(0, (
                            int(s["hora_fin"].split(":")[0]) * 60
                            + int(s["hora_fin"].split(":")[1])
                        ) - (
                            int(s["hora_inicio"].split(":")[0]) * 60
                            + int(s["hora_inicio"].split(":")[1])
                        ))
                        for s in _segs
                    )
                    tiempo_real += _seg_mins
            except Exception as exc:
                log_event(
                    "register",
                    f"segment-total accumulation failed: {exc}",
                    level="WARN",
                )

        efficiency = (std_time / tiempo_real) * 100
        estado = "OK" if efficiency >= 100 else "LOW"
        case_value = self.calculate_case_value(std_time)

        conn = get_connection()
        cursor = conn.cursor()

        # Get toggle and comments values
        comments = self._comments_widget_for_mode(self._mode).toPlainText().strip()

        # Determine target table based on current mode
        sql_table = "ot_cases" if self._mode == "overtime" else "cases"
        is_ot = self._mode == "overtime"

        # Metadata captured from the last clipboard import (cr_count and
        # product_tier). Cleared after the case is saved so subsequent
        # manual entries don't inherit stale values.
        _import_meta = getattr(self, "_last_import_meta", {}) or {}
        cr_count = _import_meta.get("cr_count")
        product_tier = _import_meta.get("product_tier", "") or ""
        # Detect the CR number directly from the tipo string ("CR #2") so
        # cases captured before this column existed still classify correctly.
        if cr_count is None and tipo:
            import re as _re_cr
            m = _re_cr.match(r"\s*CR\s*#?\s*(\d+)\s*$", tipo, _re_cr.IGNORECASE)
            if m:
                try:
                    cr_count = int(m.group(1))
                except ValueError:
                    cr_count = None

        if self._editing_id:
            cursor.execute(f"""
                UPDATE {sql_table} SET
                    case_id = ?, region = ?, tipo_caso = ?,
                    doctor = ?, fecha = ?, hora_inicio = ?, hora_fin = ?,
                    tiempo_real = ?, std_time = ?, efficiency = ?, estado = ?, case_value = ?,
                    count_production = ?, comments = ?,
                    cr_count = COALESCE(?, cr_count),
                    product_tier = CASE WHEN ? = '' THEN product_tier ELSE ? END
                WHERE id = ?
            """, (
                case_id, region, tipo,
                doctor if doctor else "", case_date,
                start.toString("HH:mm"), end.toString("HH:mm"),
                tiempo_real, std_time, efficiency, estado, case_value,
                count_production, comments,
                cr_count, product_tier, product_tier,
                self._editing_id
            ))
            self._editing_id = None
            self._banner_pulse_timer.stop()
            self._edit_banner.setVisible(False)
            self._set_edit_focus(False)
            action = "Updated"
        else:
            cursor.execute(f"""
                INSERT INTO {sql_table} (
                    case_id, region, tipo_caso,
                    doctor, fecha, hora_inicio, hora_fin,
                    tiempo_real, std_time, efficiency, estado, case_value,
                    count_production, comments, cr_count, product_tier
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                case_id, region, tipo,
                doctor if doctor else "", case_date,
                start.toString("HH:mm"), end.toString("HH:mm"),
                tiempo_real, std_time, efficiency, estado, case_value,
                count_production, comments, cr_count, product_tier,
            ))
            action = "Saved"
        # Clear the captured import metadata so the next save doesn't reuse it.
        self._last_import_meta = {}

        conn.commit()
        conn.close()

        # Show success in KPI boxes and clear status
        result_color = "#F0883E" if is_ot else "#3FB950"
        self._show_result(efficiency, case_value, result_color)
        summary = f"{case_id} | {region} | {tipo}"
        # Defer the toast + heavy DB reload so the Save Case button repaints
        # and the user's next click isn't queued behind these.
        QTimer.singleShot(
            0,
            lambda m=f"{action}: {summary}", c=result_color: self._set_result_status(m, c),
        )
        QTimer.singleShot(60, self.load_daily_production)

        # Full form reset — leaves a clean slate until the next Import / manual entry.
        self.case_id.clear()
        self.doctor.clear()
        self._comments_widget_for_mode(self._mode).clear()
        self.count_toggle.setChecked(True)  # Reset toggle to ON

        # Reset region / type combos to their first item
        for combo in (self.region, self.tipo):
            combo.blockSignals(True)
            if combo.count() > 0:
                combo.setCurrentIndex(0)
            combo.blockSignals(False)
        # Rebuild the type list to match the freshly-selected region
        self.update_case_types()

        # Date back to today, start time back to current time
        self.case_date.blockSignals(True)
        self.case_date.setDate(QDate.currentDate())
        self.case_date.blockSignals(False)
        self.start_time.blockSignals(True)
        self.start_time.setTime(QTime.currentTime())
        self.start_time.blockSignals(False)

        # Clear end time - set to midnight (00:00)
        self.end_time.blockSignals(True)
        self.end_time.setTime(QTime(0, 0))
        self.end_time.blockSignals(False)

        # Emit signal to notify other tabs (deferred so UI yields first).
        if is_ot:
            QTimer.singleShot(120, self.ot_saved.emit)
            QTimer.singleShot(200, self._load_ot_day_cases)
        else:
            QTimer.singleShot(120, self.case_saved.emit)
            QTimer.singleShot(200, self._load_regular_day_cases)

        self._capture_mode_state(self._mode)

        # Auto-focus Case ID for the next entry
        self.case_id.setFocus()

