from PySide6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout, QComboBox, QLineEdit,
    QPushButton, QDateEdit, QTableWidget, QTableWidgetItem, QHeaderView,
    QMessageBox, QApplication, QFrame,
)
from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtGui import QColor, QFont, QBrush
from . import font_scale
from db.database import get_connection
from datetime import datetime, timedelta
from collections import Counter
from .utils import (
    load_units_eq_data,
    get_units_per_case as _ue_lookup,
    calculate_equivalent_units,
    DAILY_BASE_MINUTES,
)
from .theme_table_utils import (
    apply_table_theme, CLR_FG_LIGHT, CLR_FG_DARK,
    get_light_theme_colors, light_row_bg, light_header_bg, light_header_fg, mix_hex,
)
from .theme_palette import apply_fluent_modal_palette


class ProductionTab(QWidget):
    case_updated = Signal()  # Signal emitted when a case is edited/deleted
    
    def __init__(self):
        super().__init__()
        self.all_cases = []
        self.case_db_ids = {}  # Map table rows to database IDs
        self.current_mode = "reg"  # "reg" for regular cases, "ot" for OT cases
        self.current_page = 1
        self.days_per_page = 2  # Show 2 complete day blocks per page
        self.total_pages = 1
        self.filtered_cases = []  # Store filtered cases for pagination
        # Theme state — main emits themeChanged(is_light) and update_theme_labels
        # writes this. Defaults to False so dark colours are used at boot
        # (the app always starts in dark mode regardless of OS palette).
        self._light_mode_active = False
        self._collapsed_days = set()
        self._day_case_rows = {}
        self._day_chevrons = {}
        self.load_units_eq()
        self.init_ui()
        self.load_regions_and_types()
        self.load_data()
    
    def load_units_eq(self):
        """Load units equivalency for production calculation"""
        self.units_eq = load_units_eq_data()

    def get_units_per_case(self, region, case_type):
        """Return UE value for a specific region+case_type."""
        return _ue_lookup(self.units_eq, region, case_type)

    def calculate_units_eq(self, region, case_value, case_type=None):
        """Return UE for one case supporting both UE models."""
        try:
            return calculate_equivalent_units(
                self.units_eq,
                region,
                case_type,
                case_value,
                count=1,
            )
        except Exception:
            return 0.0

    def init_ui(self):
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(16, 14, 16, 10)
        main_layout.setSpacing(12)

        # Title.
        title = QLabel("Production & Percentages")
        title.setStyleSheet(
            "color: #E6EDF3; font-size: 18px; font-weight: 800;"
            " letter-spacing: 0.3px;"
        )
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(title)

        # ── KPI card builders ──
        from PySide6.QtWidgets import QFrame as _QF, QToolButton as _QTB
        try:
            from .tabler_icons import TablerIcon as _TI_kpi
            from PySide6.QtGui import QColor as _QC_kpi
            from PySide6.QtCore import QSize as _QS_kpi
        except Exception:
            _TI_kpi = None

        _kpi_card_css = (
            "#kpiCard { background: #0D1117; border: 1px solid #21262D;"
            " border-radius: 10px; }"
            "QLabel { background: transparent; border: none; }"
        )

        def _kpi_card(label_text: str, value_widget: QLabel, accent: str,
                     icon_svg: str | None = None):
            card = _QF()
            card.setObjectName("kpiCard")
            card.setStyleSheet(_kpi_card_css)
            h = QHBoxLayout(card)
            h.setContentsMargins(14, 10, 14, 10)
            h.setSpacing(10)
            if icon_svg and _TI_kpi is not None:
                # We use a QLabel + QPixmap (not a disabled QToolButton) so
                # the icon keeps its rendered color instead of Qt's
                # gray-disabled tint.
                from PySide6.QtGui import QColor as _QCk
                ic_lbl = QLabel()
                ic_lbl.setFixedSize(34, 34)
                ic_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                ic_lbl.setStyleSheet(
                    "background: transparent; border: none;"
                )

                def _apply_kpi_icon(is_light: bool, _l=ic_lbl, _svg=icon_svg):
                    try:
                        from .theme_palette import palette
                        col = palette(is_light)["text"]
                    except Exception:
                        col = "#FFFFFF" if not is_light else "#1F2328"
                    _l.setPixmap(
                        _TI_kpi(_svg).icon(color=_QCk(col)).pixmap(20, 20)
                    )
                ic_lbl.apply_palette = _apply_kpi_icon
                try:
                    from qfluentwidgets.common.style_sheet import isDarkTheme
                    _apply_kpi_icon(not isDarkTheme())
                except Exception:
                    _apply_kpi_icon(False)
                h.addWidget(ic_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
            col = QVBoxLayout(); col.setSpacing(0)
            lbl = QLabel(label_text)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(
                "color: #8B949E; font-size: 11px; font-weight: 600;"
            )
            # Numbers stay neutral (white) — the OK/LOW card keeps colours.
            value_widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
            value_widget.setStyleSheet(
                "color: #E6EDF3; font-size: 18px; font-weight: 500;"
            )
            col.addWidget(lbl)
            col.addWidget(value_widget)
            wrap = QVBoxLayout()
            wrap.addStretch(1)
            wrap.addLayout(col)
            wrap.addStretch(1)
            outer = QHBoxLayout()
            outer.addStretch(1)
            outer.addLayout(wrap)
            outer.addStretch(1)
            h.addLayout(outer, 1)
            return card

        def _kpi_ok_low_card(ok_value: QLabel, low_value: QLabel):
            card = _QF()
            card.setObjectName("kpiCard")
            card.setStyleSheet(_kpi_card_css)
            h = QHBoxLayout(card)
            h.setContentsMargins(14, 10, 14, 10)
            h.setSpacing(14)

            def _side(label_text, value_widget, accent):
                col = QVBoxLayout(); col.setSpacing(0)
                lbl = QLabel(label_text)
                lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                lbl.setStyleSheet(
                    "color: #8B949E; font-size: 11px; font-weight: 600;"
                )
                value_widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
                value_widget.setStyleSheet(
                    f"color: {accent}; font-size: 18px; font-weight: 500;"
                )
                col.addWidget(lbl)
                col.addWidget(value_widget)
                return col

            h.addLayout(_side("OK", ok_value, "#3FB950"), 1)
            divider = _QF()
            divider.setFixedWidth(1)
            divider.setStyleSheet("background: #21262D; border: none;")
            h.addWidget(divider)
            h.addLayout(_side("LOW", low_value, "#F85149"), 1)
            return card

        # ── Mode buttons (Regular / Overtime) — standalone pills, 10px gap ──
        mode_row = QHBoxLayout()
        mode_row.setSpacing(10)
        mode_row.addStretch()
        self.btn_reg = QPushButton("Regular")
        self.btn_reg.setCursor(Qt.PointingHandCursor)
        self.btn_reg.setFixedHeight(32)
        self.btn_reg.setMinimumWidth(108)
        self.btn_reg.clicked.connect(self.switch_to_reg)
        self.btn_ot = QPushButton("Overtime")
        self.btn_ot.setCursor(Qt.PointingHandCursor)
        self.btn_ot.setFixedHeight(32)
        self.btn_ot.setMinimumWidth(108)
        self.btn_ot.clicked.connect(self.switch_to_ot)
        mode_row.addWidget(self.btn_reg)
        mode_row.addWidget(self.btn_ot)
        mode_row.addStretch()
        main_layout.addLayout(mode_row)

        # ── KPI cards row — 4 cards (last is a 2-up OK/LOW split) ──
        self.stats_avg = QLabel("—")
        self.stats_total = QLabel("—")
        self.stats_ok = QLabel("—")
        # Equivalent Units total (new card).
        self.stats_ue = QLabel("—")
        # OK / LOW split card values.
        self.stats_ok_count = QLabel("—")
        self.stats_low_count = QLabel("—")
        # Hidden legacy text aggregator so the old setText path still works.
        self.stats_low = QLabel("")
        self.stats_low.hide()

        kpi_row = QHBoxLayout(); kpi_row.setSpacing(10)
        # Every card gets equal width — distribute the row evenly.
        kpi_row.addWidget(_kpi_card(
            "Average Efficiency", self.stats_avg, "#58A6FF",
            "tabler_chart_bar.svg",
        ), 1)
        kpi_row.addWidget(_kpi_card(
            "Cases", self.stats_total, "#A371F7",
            "tabler_clipboard_text.svg",
        ), 1)
        kpi_row.addWidget(_kpi_card(
            "Value", self.stats_ok, "#3FB950",
            "tabler_percentage_30.svg",
        ), 1)
        kpi_row.addWidget(_kpi_card(
            "Equivalent Units", self.stats_ue, "#E89720",
            "tabler_congruent_to.svg",
        ), 1)
        # OK / LOW split card — no icons, just labels + numbers.
        kpi_row.addWidget(_kpi_ok_low_card(
            self.stats_ok_count, self.stats_low_count
        ), 1)
        main_layout.addLayout(kpi_row)

        # Style applied via _refresh_mode_buttons_style.
        self._refresh_mode_buttons_style()

        # ── Filter bar — single row: Date range / Region / Type / Doctor ──
        try:
            from .widgets import _icon_url as _icu_p
            _chev_p = _icu_p("tabler_chevron_down.svg")
        except Exception:
            _chev_p = ""
        _input_css_p = (
            "QLineEdit, QDateEdit, QComboBox { background: #161B22;"
            " border: 1px solid #30363D; border-radius: 6px;"
            " padding: 4px 8px; color: #E6EDF3; font-size: 11px;"
            " min-height: 26px; }"
            "QComboBox::drop-down, QDateEdit::drop-down {"
            " subcontrol-origin: padding; subcontrol-position: right center;"
            " width: 22px; border: none; }"
            f"QComboBox::down-arrow, QDateEdit::down-arrow {{"
            f" image: url({_chev_p}); width: 12px; height: 12px; }}"
        )

        def _col_label(text):
            l = QLabel(text)
            l.setStyleSheet(
                "color: #C9D1D9; font-size: 12px; font-weight: 700;"
                " background: transparent;"
            )
            return l

        filter_card = QFrame()
        filter_card.setObjectName("filterCard")
        filter_card.setStyleSheet(
            "#filterCard { background: #0D1117; border: 1px solid #21262D;"
            " border-radius: 10px; }"
            "QLabel { background: transparent; border: none; }"
        )
        fb = QHBoxLayout(filter_card)
        fb.setContentsMargins(14, 12, 14, 16)
        fb.setSpacing(12)

        # ── Date range column ──
        date_col = QVBoxLayout(); date_col.setSpacing(2)
        date_col.addWidget(_col_label("Date range"))
        # Tiny labels under "Date range" indicating which input is From
        # and which is To.
        _date_sublbls = QHBoxLayout(); _date_sublbls.setSpacing(6)
        for _t in ("From", "To"):
            _sl = QLabel(_t)
            _sl.setStyleSheet(
                "color: #8B949E; font-size: 9px; font-weight: 600;"
                " letter-spacing: 0.5px; background: transparent;"
            )
            _date_sublbls.addWidget(_sl, 1)
            if _t == "From":
                _spacer_lbl = QLabel("")
                _spacer_lbl.setFixedWidth(8)
                _date_sublbls.addWidget(_spacer_lbl)
        date_col.addLayout(_date_sublbls)
        date_row = QHBoxLayout(); date_row.setSpacing(6)
        def _attach_calendar_icon(date_edit):
            try:
                from .tabler_icons import TablerIcon as _TIp_c
                from PySide6.QtGui import QAction as _QAp_c, QColor as _QCp_c
                le = date_edit.lineEdit() if hasattr(date_edit, "lineEdit") else None
                if le is None:
                    return
                act = _QAp_c(
                    _TIp_c("tabler_calendar.svg").icon(color=_QCp_c("#8B949E")),
                    "", le,
                )
                le.addAction(act, QLineEdit.ActionPosition.LeadingPosition)
            except Exception:
                pass

        from .widgets import DateEditWithShortcut as _DateEditP
        self.date_from = _DateEditP()
        self.date_from.setDate(QDate.currentDate())
        self.date_from.setMinimumWidth(155)
        self.date_from.setFixedHeight(28)
        self.date_from.setStyleSheet(_input_css_p)
        self.date_from.dateChanged.connect(self._on_date_range_changed)
        _attach_calendar_icon(self.date_from)
        sep_lbl = QLabel("–")
        sep_lbl.setStyleSheet("color: #8B949E; font-size: 13px;")
        self.date_to = _DateEditP()
        self.date_to.setDate(QDate.currentDate())
        self.date_to.setMinimumWidth(155)
        self.date_to.setFixedHeight(28)
        self.date_to.setStyleSheet(_input_css_p)
        self.date_to.dateChanged.connect(self._on_date_range_changed)
        _attach_calendar_icon(self.date_to)
        date_row.addWidget(self.date_from)
        date_row.addWidget(sep_lbl)
        date_row.addWidget(self.date_to)
        date_col.addLayout(date_row)
        fb.addLayout(date_col, 0)
        fb.addSpacing(24)

        # Region/Type combos are hidden — they're still used as the data
        # source for filtering but the user picks values through the popup
        # opened by the Filters button on the right.
        self.filter_region = QComboBox(); self.filter_region.hide()
        self.filter_region.currentTextChanged.connect(self.on_filter_changed)
        self.filter_type = QComboBox(); self.filter_type.hide()
        self.filter_type.currentTextChanged.connect(self.on_filter_changed)
        # New filterable metadata picked through the popup.
        self._filter_product_tier = ""
        self._filter_cr = ""  # "", "any-cr", or specific "1", "2", etc.

        # ── Doctor search ──
        doc_col = QVBoxLayout(); doc_col.setSpacing(2)
        doc_col.addWidget(_col_label("Doctor"))
        self.filter_doctor = QLineEdit()
        self.filter_doctor.setPlaceholderText("Search doctor or case ID…")
        self.filter_doctor.setStyleSheet(_input_css_p)
        self.filter_doctor.setMinimumWidth(220)
        self.filter_doctor.setMaximumWidth(280)
        self.filter_doctor.setFixedHeight(28)
        self.filter_doctor.textChanged.connect(self.on_filter_changed)
        try:
            from .tabler_icons import TablerIcon as _TIp_search
            from PySide6.QtGui import QAction as _QAp_s, QColor as _QCp_s
            _search_act = _QAp_s(
                _TIp_search("tabler_search.svg").icon(color=_QCp_s("#8B949E")),
                "", self.filter_doctor,
            )
            self.filter_doctor.addAction(
                _search_act, QLineEdit.ActionPosition.TrailingPosition
            )
        except Exception:
            pass
        doc_col.addWidget(self.filter_doctor)
        fb.addLayout(doc_col, 0)
        fb.addSpacing(20)

        # ── Comments toggles (with / without) ──
        # State: "" = no filter, "with" = only commented, "without" = only blank.
        self._filter_comments = ""

        def _toggle_chip(text, key, icon_svg):
            btn = QPushButton("  " + text)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(28)
            try:
                from .tabler_icons import TablerIcon as _TIp_c
                from PySide6.QtGui import QColor as _QCp_c
                from PySide6.QtCore import QSize as _QSp_c
                btn.setIcon(_TIp_c(icon_svg).icon(color=_QCp_c("#8B949E")))
                btn.setIconSize(_QSp_c(14, 14))
            except Exception:
                pass
            btn.setStyleSheet(
                "QPushButton { background: #161B22; border: 1px solid #30363D;"
                "  color: #C9D1D9; border-radius: 6px; padding: 0 12px;"
                "  font-size: 11px; font-weight: 600; }"
                "QPushButton:hover { border-color: #58606A; }"
                "QPushButton:checked { background: rgba(56,139,253,0.14);"
                "  border-color: #388BFD; color: #58A6FF; }"
            )
            return btn

        self._chip_with = _toggle_chip("With comments", "with",
                                       "tabler_message_circle.svg")
        self._chip_without = _toggle_chip("Without comments", "without",
                                          "tabler_message_off.svg")
        cmts_col = QVBoxLayout(); cmts_col.setSpacing(2)
        cmts_col.addWidget(_col_label("Comments"))
        cmts_row = QHBoxLayout(); cmts_row.setSpacing(6)
        cmts_row.addWidget(self._chip_with)
        cmts_row.addWidget(self._chip_without)
        cmts_col.addLayout(cmts_row)
        fb.addLayout(cmts_col, 0)

        def _on_chip(target_key):
            if target_key == "with":
                if self._chip_with.isChecked():
                    self._chip_without.setChecked(False)
                    self._filter_comments = "with"
                else:
                    self._filter_comments = ""
            else:
                if self._chip_without.isChecked():
                    self._chip_with.setChecked(False)
                    self._filter_comments = "without"
                else:
                    self._filter_comments = ""
            self.on_filter_changed()

        self._chip_with.clicked.connect(lambda: _on_chip("with"))
        self._chip_without.clicked.connect(lambda: _on_chip("without"))

        fb.addStretch(1)

        # ── Filters button (right edge, decorative outline) ──
        filters_btn = QPushButton("  Filters")
        filters_btn.setCursor(Qt.PointingHandCursor)
        filters_btn.setFixedHeight(28)
        filters_btn.setMinimumWidth(96)
        try:
            from .tabler_icons import TablerIcon as _TIp_f
            from PySide6.QtGui import QColor as _QCp_f
            from PySide6.QtCore import QSize as _QSp_f
            filters_btn.setIcon(
                _TIp_f("tabler_filter.svg").icon(color=_QCp_f("#58A6FF"))
            )
            filters_btn.setIconSize(_QSp_f(14, 14))
        except Exception:
            pass
        filters_btn.setStyleSheet(
            "QPushButton { background: transparent; border: 1px solid #1e63e4;"
            "  color: #58A6FF; border-radius: 6px; padding: 4px 14px;"
            "  font-weight: 700; font-size: 11px; }"
            "QPushButton:hover { background: rgba(30,99,228,0.10); }"
        )
        # Click opens the Region / Type / Product Tier / CR # popup.
        filters_btn.clicked.connect(self._open_filter_popup)
        self._filters_btn = filters_btn
        # Right edge: Reset (top) + Filters (bottom).
        reset_btn = QPushButton("  Reset")
        reset_btn.setCursor(Qt.PointingHandCursor)
        reset_btn.setFixedHeight(28)
        reset_btn.setMinimumWidth(96)
        try:
            from .tabler_icons import TablerIcon as _TIp_r
            from PySide6.QtGui import QColor as _QCp_r
            from PySide6.QtCore import QSize as _QSp_r
            reset_btn.setIcon(
                _TIp_r("tabler_refresh.svg").icon(color=_QCp_r("#8B949E"))
            )
            reset_btn.setIconSize(_QSp_r(12, 12))
        except Exception:
            pass
        reset_btn.setStyleSheet(
            "QPushButton { background: transparent; border: 1px solid #30363D;"
            "  color: #C9D1D9; border-radius: 6px; padding: 4px 14px;"
            "  font-weight: 700; font-size: 11px; }"
            "QPushButton:hover { background: rgba(255,255,255,0.05);"
            "  color: #E6EDF3; }"
        )
        reset_btn.clicked.connect(self._reset_filters)

        right_col = QVBoxLayout(); right_col.setSpacing(4)
        right_col.addWidget(reset_btn)
        right_col.addWidget(filters_btn)
        fb.addLayout(right_col, 0)

        main_layout.addWidget(filter_card)

        # Table — modern dark-themed look.
        self.table = QTableWidget()
        self.table.setAlternatingRowColors(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.setColumnCount(10)
        self.table.setHorizontalHeaderLabels([
            "CASE ID", "DOCTOR", "REGION", "TYPE", "START", "END",
            "TIME", "EFF %", "VALUE %", "UE",
        ])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(True)
        self.table.setGridStyle(Qt.PenStyle.SolidLine)
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)

        default_table_style = (
            "QTableWidget {"
            "  background: #0D1117; border: none;"
            "  gridline-color: #21262D; outline: none;"
            "  color: #E6EDF3;"
            "  alternate-background-color: transparent; }"
            "QTableWidget::item { padding: 2px 6px;"
            "  border-right: 1px solid #21262D;"
            "  border-bottom: 1px solid #21262D; }"
            "QTableWidget::item:selected {"
            "  background-color: rgba(56,139,253,0.30); color: #E6EDF3; }"
            "QHeaderView { background: transparent; border: none; }"
            "QHeaderView::section {"
            "  background-color: #161B22; color: #8B949E;"
            "  padding: 10px 6px; border: none;"
            "  border-right: 1px solid #21262D;"
            "  border-bottom: 1px solid #21262D;"
            "  font-weight: 700; font-size: 10px;"
            "  letter-spacing: 0.5px; }"
        )
        self.table.setStyleSheet(default_table_style)
        self.table._saved_style = default_table_style
        
        # Column sizing — let each column stretch proportionally so the
        # table fills the same horizontal span as the filter card above.
        header = self.table.horizontalHeader()
        for c in range(self.table.columnCount()):
            header.setSectionResizeMode(c, QHeaderView.ResizeMode.Stretch)
        header.setStretchLastSection(False)
        # Re-sync day header widget widths whenever a column resizes so
        # the teal band always spans the full table.
        header.sectionResized.connect(
            lambda *_a: self._sync_day_header_widths()
        )

        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.table.setMinimumHeight(200)

        # Table now stretches to its container — no centered HBox.
        table_container = QHBoxLayout()
        table_container.addWidget(self.table)
        main_layout.addLayout(table_container, 1)  # Stretch factor 1 to fill space
        
        # Edit/Delete buttons
        action_buttons_layout = QHBoxLayout()
        action_buttons_layout.addStretch()
        
        self.edit_btn = QPushButton("  Edit")
        self.edit_btn.setCursor(Qt.PointingHandCursor)
        self.edit_btn.setFixedHeight(30)
        self.edit_btn.setMinimumWidth(96)
        self.edit_btn.clicked.connect(self.edit_selected_case)
        try:
            from .tabler_icons import TablerIcon as _TI_ed
            from PySide6.QtGui import QColor as _QC_ed
            from PySide6.QtCore import QSize as _QS_ed
            self.edit_btn.setIcon(
                _TI_ed("tabler_pencil.svg").icon(color=_QC_ed("#E6EDF3"))
            )
            self.edit_btn.setIconSize(_QS_ed(14, 14))
            self.delete_btn = QPushButton("  Delete")
            self.delete_btn.setCursor(Qt.PointingHandCursor)
            self.delete_btn.setFixedHeight(30)
            self.delete_btn.setMinimumWidth(96)
            self.delete_btn.setIcon(
                _TI_ed("tabler_trash.svg").icon(color=_QC_ed("#FFFFFF"))
            )
            self.delete_btn.setIconSize(_QS_ed(14, 14))
        except Exception:
            self.delete_btn = QPushButton("Delete")
            self.delete_btn.setFixedHeight(30)
            self.delete_btn.setMinimumWidth(96)
        self.edit_btn.setStyleSheet(
            "QPushButton { background: #161B22; border: 1px solid #30363D;"
            "  color: #E6EDF3; border-radius: 6px; padding: 4px 14px;"
            "  font-weight: 700; font-size: 11px; }"
            "QPushButton:hover { background: rgba(255,255,255,0.05); }"
        )
        self.delete_btn.setStyleSheet(
            "QPushButton { background: #F85149; border: 1px solid #F85149;"
            "  color: white; border-radius: 6px; padding: 4px 14px;"
            "  font-weight: 700; font-size: 11px; }"
            "QPushButton:hover { background: #FF6B61; }"
        )
        self.delete_btn.clicked.connect(self.delete_selected_case)
        
        # Status hint shown while waiting for a row click after Edit/Delete.
        self._action_hint_lbl = QLabel("")
        self._action_hint_lbl.hide()
        action_buttons_layout.addWidget(self._action_hint_lbl)
        action_buttons_layout.addSpacing(12)
        action_buttons_layout.addWidget(self.edit_btn)
        action_buttons_layout.addWidget(self.delete_btn)
        action_buttons_layout.addStretch()
        main_layout.addLayout(action_buttons_layout)
        
        # Pagination footer — full pager: Prev / numbered pages / ... /
        # Last / Next, plus a "Show N per page" selector on the right.
        pagination_layout = QHBoxLayout()
        pagination_layout.setSpacing(6)
        pagination_layout.setContentsMargins(0, 4, 0, 0)

        self._pager_btn_css = (
            "QPushButton { background: #161B22; border: 1px solid #30363D;"
            "  color: #C9D1D9; border-radius: 6px;"
            "  padding: 4px 10px; min-width: 28px; min-height: 26px;"
            "  font-size: 11px; font-weight: 700; }"
            "QPushButton:hover { background: rgba(255,255,255,0.05);"
            "  color: #E6EDF3; }"
            "QPushButton:disabled { color: #4d5560; border-color: #21262D; }"
        )
        self._pager_btn_active_css = (
            "QPushButton { background: transparent; border: 1px solid #388BFD;"
            "  color: #388BFD; border-radius: 6px;"
            "  padding: 4px 10px; min-width: 28px; min-height: 26px;"
            "  font-size: 11px; font-weight: 800; }"
        )

        self.btn_prev = QPushButton("‹ Prev")
        self.btn_prev.setCursor(Qt.PointingHandCursor)
        self.btn_prev.setStyleSheet(self._pager_btn_css)
        self.btn_prev.clicked.connect(self.prev_page)
        pagination_layout.addWidget(self.btn_prev)

        # Container that holds the numeric page buttons — rebuilt every
        # time the page count or current page changes.
        self._pager_buttons_row = QHBoxLayout()
        self._pager_buttons_row.setSpacing(4)
        pagination_layout.addLayout(self._pager_buttons_row)

        self.btn_next = QPushButton("Next ›")
        self.btn_next.setCursor(Qt.PointingHandCursor)
        self.btn_next.setStyleSheet(self._pager_btn_css)
        self.btn_next.clicked.connect(self.next_page)
        pagination_layout.addWidget(self.btn_next)

        pagination_layout.addStretch(1)

        # Page-size selector.
        ps_lbl = QLabel("Show")
        ps_lbl.setStyleSheet(
            "color: #8B949E; font-size: 11px; background: transparent;"
        )
        pagination_layout.addWidget(ps_lbl)
        self.page_size_combo = QComboBox()
        for n in (2, 5, 10, 25, 50):
            self.page_size_combo.addItem(str(n), n)
        idx = self.page_size_combo.findData(self.days_per_page)
        if idx >= 0:
            self.page_size_combo.setCurrentIndex(idx)
        self.page_size_combo.setFixedHeight(28)
        self.page_size_combo.setMinimumWidth(64)
        self.page_size_combo.setStyleSheet(
            "QComboBox { background: #161B22; border: 1px solid #30363D;"
            "  border-radius: 6px; padding: 2px 8px; color: #E6EDF3;"
            "  font-size: 11px; }"
        )
        self.page_size_combo.currentIndexChanged.connect(self._on_page_size_changed)
        pagination_layout.addWidget(self.page_size_combo)
        ps_suffix = QLabel("per page")
        ps_suffix.setStyleSheet(
            "color: #8B949E; font-size: 11px; background: transparent;"
        )
        pagination_layout.addWidget(ps_suffix)
        main_layout.addLayout(pagination_layout)
        
        # Add bottom spacing to match top
        main_layout.addSpacing(10)

        self.setLayout(main_layout)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        width = event.size().width()
        self.table.setColumnHidden(7, width < 725)
        self._sync_day_header_widths()

    def showEvent(self, event):
        """When the tab becomes visible again, re-sync header band widths —
        a tab that was hidden while data was rebuilt has stale geometry."""
        super().showEvent(event)
        from PySide6.QtCore import QTimer as _QT_se
        _QT_se.singleShot(0, self._sync_day_header_widths)
        _QT_se.singleShot(80, self._sync_day_header_widths)

    def _on_page_size_changed(self, _idx: int):
        """User picked a new 'Show N per page' value."""
        try:
            self.days_per_page = int(self.page_size_combo.currentData())
        except Exception:
            return
        self.current_page = 1
        self.filter_data()

    def _rebuild_pager(self):
        """Repopulate the numeric page buttons (1 2 3 … N) based on the
        current page count + active page."""
        row = self._pager_buttons_row
        while row.count():
            item = row.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)

        total = max(1, int(getattr(self, "total_pages", 1) or 1))
        cur = max(1, int(getattr(self, "current_page", 1) or 1))

        def _btn(label, page=None, active=False, enabled=True):
            b = QPushButton(str(label))
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(
                self._pager_btn_active_css if active else self._pager_btn_css
            )
            if not enabled:
                b.setEnabled(False)
            if page is not None and enabled:
                b.clicked.connect(lambda _=False, p=page: self._goto_page(p))
            return b

        # Build list of page numbers + ellipsis tokens.
        # Show: first, current-1, current, current+1, last (plus ellipses).
        pages: list = []
        if total <= 7:
            pages = list(range(1, total + 1))
        else:
            pages.append(1)
            if cur > 3:
                pages.append("…")
            for p in range(max(2, cur - 1), min(total - 1, cur + 1) + 1):
                pages.append(p)
            if cur < total - 2:
                pages.append("…")
            pages.append(total)

        for p in pages:
            if p == "…":
                lbl = QLabel("…")
                lbl.setStyleSheet(
                    "color: #8B949E; font-size: 12px; padding: 0 6px;"
                    " background: transparent;"
                )
                row.addWidget(lbl)
            else:
                row.addWidget(_btn(p, page=p, active=(p == cur)))

    def _goto_page(self, page: int):
        page = max(1, min(int(page), int(getattr(self, "total_pages", 1) or 1)))
        if page == self.current_page:
            return
        self.current_page = page
        self.filter_data()

    def _toggle_day(self, key: str):
        """Hide/show the case rows for a day-group without rebuilding the
        whole table (avoids the visual jump that comes with a full
        filter_data run). Also flips the chevron icon."""
        if not hasattr(self, "_collapsed_days"):
            self._collapsed_days = set()
        collapsing = key not in self._collapsed_days
        if collapsing:
            self._collapsed_days.add(key)
        else:
            self._collapsed_days.discard(key)
        case_rows = (getattr(self, "_day_case_rows", {}) or {}).get(key, [])
        for r in case_rows:
            self.table.setRowHidden(r, collapsing)
        # Swap chevron icon on the header widget for this day.
        btn = (getattr(self, "_day_chevrons", {}) or {}).get(key)
        if btn is not None:
            try:
                from .tabler_icons import TablerIcon as _TI_t2
                svg = ("tabler_chevron_right.svg" if collapsing
                       else "tabler_chevron_down.svg")
                btn.setIcon(_TI_t2(svg).icon(color=QColor("#C9D1D9")))
            except Exception:
                pass
        # Hiding rows shifts every following row up; re-sync the header
        # widget Y positions so the band hugs its row exactly.
        from PySide6.QtCore import QTimer as _QT_td
        _QT_td.singleShot(0, self._sync_day_header_widths)
        _QT_td.singleShot(40, self._sync_day_header_widths)

    def _sync_day_header_widths(self):
        """Resize day-group header widgets to span the full table
        viewport — Qt doesn't stretch a cellWidget across a setSpan
        region. Use viewport().width() (most authoritative) and fall back
        to the sum of section sizes if the viewport hasn't laid out yet."""
        widgets = getattr(self, "_day_hdr_widgets", None)
        if not widgets:
            return
        total = self.table.viewport().width()
        if total <= 0:
            # Fallback: sum visible columns.
            for c in range(self.table.columnCount()):
                if not self.table.isColumnHidden(c):
                    total += self.table.columnWidth(c)
        if total <= 0:
            return
        for row_idx, w in widgets:
            try:
                w.setMinimumWidth(total)
                w.setMaximumWidth(total)
                # Anchor the widget at viewport x=0 so it reaches both
                # the left and right edges of the row (cellWidget normally
                # starts at col 0's visualRect.x which has padding).
                y = self.table.rowViewportPosition(row_idx)
                h = self.table.rowHeight(row_idx)
                w.setGeometry(0, y, total, h)
            except Exception:
                pass

    def load_regions_and_types(self):
        """Populate Region/Type filters with only the values present in the
        cases that fall within the current date range — values that no
        longer apply get filtered out (e.g. a region that hasn't been used
        in the last week is hidden when filtering by 'last 7 days')."""
        table_name = "cases" if self.current_mode == "reg" else "ot_cases"
        d_from = self.date_from.date().toString("yyyy-MM-dd") \
            if hasattr(self, "date_from") else None
        d_to = self.date_to.date().toString("yyyy-MM-dd") \
            if hasattr(self, "date_to") else None

        # Remember what the user had selected so the refresh doesn't reset it
        # to "All" every time the date range changes.
        prev_region = self.filter_region.currentText() \
            if hasattr(self, "filter_region") else "All"
        prev_type = self.filter_type.currentText() \
            if hasattr(self, "filter_type") else "All"

        conn = get_connection()
        cursor = conn.cursor()
        params: list = []
        where = ""
        if d_from and d_to:
            where = " WHERE fecha BETWEEN ? AND ?"
            params = [d_from, d_to]

        cursor.execute(
            f"SELECT DISTINCT region FROM {table_name}{where}"
            " ORDER BY region", params,
        )
        regions = [r[0] for r in cursor.fetchall() if r[0]]
        cursor.execute(
            f"SELECT DISTINCT tipo_caso FROM {table_name}{where}"
            " ORDER BY tipo_caso", params,
        )
        types = [r[0] for r in cursor.fetchall() if r[0]]
        conn.close()

        self.filter_region.blockSignals(True)
        self.filter_region.clear()
        self.filter_region.addItem("All")
        self.filter_region.addItems(regions)
        idx = self.filter_region.findText(prev_region)
        self.filter_region.setCurrentIndex(idx if idx >= 0 else 0)
        self.filter_region.blockSignals(False)

        self.filter_type.blockSignals(True)
        self.filter_type.clear()
        self.filter_type.addItem("All")
        self.filter_type.addItems(types)
        idx = self.filter_type.findText(prev_type)
        self.filter_type.setCurrentIndex(idx if idx >= 0 else 0)
        self.filter_type.blockSignals(False)

    def _open_filter_popup(self):
        """Filter popup anchored under the Filters button — mirrors the
        Today's Cases filter (Region + Type) and adds Product Tier + CR #."""
        try:
            from .widgets import _icon_url as _icu_fp
            _chev = _icu_fp("tabler_chevron_down.svg")
        except Exception:
            _chev = ""

        # Build dropdown values from DB constrained to the current date range
        # so the popup only offers values that can actually return cases.
        d_from = self.date_from.date().toString("yyyy-MM-dd")
        d_to = self.date_to.date().toString("yyyy-MM-dd")
        table = "cases" if self.current_mode == "reg" else "ot_cases"
        try:
            conn = get_connection()
            cur = conn.cursor()
            cur.execute(
                f"SELECT DISTINCT region FROM {table} WHERE fecha BETWEEN ? AND ?"
                " ORDER BY region", (d_from, d_to),
            )
            regions = [r[0] for r in cur.fetchall() if r[0]]
            cur.execute(
                f"SELECT DISTINCT tipo_caso FROM {table}"
                " WHERE fecha BETWEEN ? AND ? ORDER BY tipo_caso",
                (d_from, d_to),
            )
            types = [r[0] for r in cur.fetchall() if r[0]]
            try:
                cur.execute(
                    f"SELECT DISTINCT product_tier FROM {table}"
                    " WHERE fecha BETWEEN ? AND ?"
                    " AND product_tier IS NOT NULL AND product_tier != ''"
                    " ORDER BY product_tier", (d_from, d_to),
                )
                tiers = [r[0] for r in cur.fetchall() if r[0]]
            except Exception:
                tiers = []
            try:
                cur.execute(
                    f"SELECT DISTINCT cr_count FROM {table}"
                    " WHERE fecha BETWEEN ? AND ?"
                    " AND cr_count IS NOT NULL ORDER BY cr_count",
                    (d_from, d_to),
                )
                crs = [int(r[0]) for r in cur.fetchall() if r[0] is not None]
            except Exception:
                crs = []
            conn.close()
        except Exception:
            regions, types, tiers, crs = [], [], [], []

        popup = QFrame(self, Qt.Popup)
        popup.setObjectName("filterPopup")
        popup.setStyleSheet(
            "#filterPopup { background-color: #161B22; border: 1px solid #30363D;"
            "  border-radius: 10px; }"
            "QLabel { color: #8B949E; font-size: 10px; font-weight: 700;"
            "  background: transparent; padding: 0; }"
            "QComboBox { background: #0D1117; border: 1px solid #30363D;"
            "  border-radius: 6px; padding: 4px 22px 4px 8px; color: #E6EDF3;"
            "  font-size: 11px; min-height: 26px; }"
            "QComboBox::drop-down { subcontrol-origin: padding;"
            "  subcontrol-position: right center; width: 22px; border: none; }"
            f"QComboBox::down-arrow {{ image: url({_chev});"
            "  width: 12px; height: 12px; }"
            "QPushButton { border-radius: 6px; padding: 6px 12px;"
            "  font-size: 11px; font-weight: 700; }"
            "QPushButton#apply { background: #1757D4; border: 1px solid #1757D4;"
            "  color: white; }"
            "QPushButton#apply:hover { background: #1F6FEB; }"
            "QPushButton#clear { background: transparent; border: 1px solid #30363D;"
            "  color: #E6EDF3; }"
            "QPushButton#clear:hover { background: rgba(255,255,255,0.05); }"
        )
        lay = QVBoxLayout(popup)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(6)

        def _combo(values, current, all_label="All"):
            c = QComboBox()
            c.addItem(all_label)
            for v in values:
                c.addItem(str(v))
            if current:
                idx = c.findText(str(current))
                if idx >= 0:
                    c.setCurrentIndex(idx)
            return c

        lay.addWidget(QLabel("REGION"))
        region_combo = _combo(regions, self.filter_region.currentText())
        lay.addWidget(region_combo)

        lay.addSpacing(2)
        lay.addWidget(QLabel("TYPE"))
        type_combo = _combo(types, self.filter_type.currentText())
        lay.addWidget(type_combo)

        lay.addSpacing(2)
        lay.addWidget(QLabel("PRODUCT TIER"))
        tier_combo = _combo(tiers, self._filter_product_tier)
        lay.addWidget(tier_combo)

        lay.addSpacing(2)
        lay.addWidget(QLabel("CR #"))
        cr_combo = QComboBox()
        cr_combo.addItem("All")
        cr_combo.addItem("Any CR")  # match any CR row
        for n in crs:
            cr_combo.addItem(f"CR #{n}")
        if self._filter_cr == "any":
            cr_combo.setCurrentIndex(1)
        elif self._filter_cr:
            idx = cr_combo.findText(f"CR #{self._filter_cr}")
            if idx >= 0:
                cr_combo.setCurrentIndex(idx)
        lay.addWidget(cr_combo)

        lay.addSpacing(8)
        btn_row = QHBoxLayout(); btn_row.setSpacing(8)
        clear_btn = QPushButton("Clear"); clear_btn.setObjectName("clear")
        apply_btn = QPushButton("Apply"); apply_btn.setObjectName("apply")
        btn_row.addWidget(clear_btn)
        btn_row.addStretch()
        btn_row.addWidget(apply_btn)
        lay.addLayout(btn_row)

        def _apply():
            r_txt = region_combo.currentText()
            t_txt = type_combo.currentText()
            tier_txt = tier_combo.currentText()
            cr_txt = cr_combo.currentText()
            self.filter_region.blockSignals(True)
            self.filter_region.setCurrentText(r_txt if r_txt else "All")
            self.filter_region.blockSignals(False)
            self.filter_type.blockSignals(True)
            self.filter_type.setCurrentText(t_txt if t_txt else "All")
            self.filter_type.blockSignals(False)
            self._filter_product_tier = "" if tier_txt == "All" else tier_txt
            if cr_txt == "All":
                self._filter_cr = ""
            elif cr_txt == "Any CR":
                self._filter_cr = "any"
            elif cr_txt.startswith("CR #"):
                self._filter_cr = cr_txt[4:]
            else:
                self._filter_cr = ""
            active = (r_txt and r_txt != "All") or (t_txt and t_txt != "All") \
                or self._filter_product_tier or self._filter_cr
            self._filters_btn.setText("  Filters •" if active else "  Filters")
            self.on_filter_changed()
            popup.close()

        def _clear():
            self.filter_region.blockSignals(True)
            self.filter_region.setCurrentIndex(0)
            self.filter_region.blockSignals(False)
            self.filter_type.blockSignals(True)
            self.filter_type.setCurrentIndex(0)
            self.filter_type.blockSignals(False)
            self._filter_product_tier = ""
            self._filter_cr = ""
            self._filters_btn.setText("  Filters")
            self.on_filter_changed()
            popup.close()

        apply_btn.clicked.connect(_apply)
        clear_btn.clicked.connect(_clear)

        btn = self._filters_btn
        pos = btn.mapToGlobal(btn.rect().bottomRight())
        popup.adjustSize()
        popup.move(pos.x() - popup.width(), pos.y() + 6)
        popup.show()

    def _on_date_range_changed(self):
        """When the user picks a new date range, re-query the DB for that
        window (load_data is now date-bounded, not a full-history fetch),
        which also rebuilds the Region/Type dropdowns and re-applies filters."""
        self.current_page = 1
        self.load_data()

    def _build_case_id_cell(self, *, case_id, suffix, comment,
                             text_color, bold, italic):
        """Cell widget for column 0 — both children vertically centered;
        no slot reserved when there's no comment so the bare case_id is
        centred on the cell."""
        from PySide6.QtWidgets import (
            QWidget as _QW, QHBoxLayout as _QH, QLabel as _QL,
            QToolButton as _QTB, QSizePolicy as _SP,
        )
        wrap = _QW()
        wrap.setStyleSheet("background: transparent; border: none;")
        wrap.setSizePolicy(_SP.Policy.Expanding, _SP.Policy.Expanding)
        h = _QH(wrap)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(4)
        h.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        h.addStretch(1)

        lbl = _QL(f"{case_id}{suffix}")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        f = QFont(); f.setBold(bold); f.setItalic(italic)
        lbl.setFont(f)
        lbl.setStyleSheet(
            f"color: {text_color}; background: transparent;"
        )

        if comment:
            try:
                from .tabler_icons import TablerIcon as _TI_cm
                from PySide6.QtCore import QSize as _QS_cm
                btn = _QTB()
                btn.setCursor(Qt.PointingHandCursor)
                btn.setFixedSize(20, 20)
                btn.setIcon(
                    _TI_cm("tabler_message_circle.svg").icon(color=QColor("#58A6FF"))
                )
                btn.setIconSize(_QS_cm(14, 14))
                btn.setToolTip(comment)
                btn.setStyleSheet(
                    "QToolButton { background: transparent; border: none; }"
                    "QToolButton:hover { background: rgba(56,139,253,0.12);"
                    "  border-radius: 4px; }"
                )
                btn.clicked.connect(
                    lambda _=False, cid=case_id, c=comment:
                        self._show_comment_popup(cid, c)
                )
                h.addWidget(btn, 0, Qt.AlignmentFlag.AlignVCenter)
            except Exception:
                pass

        h.addWidget(lbl, 0, Qt.AlignmentFlag.AlignVCenter)
        h.addStretch(1)
        return wrap

    def _show_comment_popup(self, case_id: str, comment: str):
        """Fluent modal showing the full comment text for a case."""
        try:
            from qfluentwidgets import MessageBoxBase
            from .tabler_icons import TablerIcon
            from PySide6.QtWidgets import (
                QFrame as _QF, QTextEdit as _QTE, QToolButton as _QTB,
            )
            from PySide6.QtCore import QSize as _QSz
        except Exception:
            QMessageBox.information(self, f"Comment · {case_id}", comment)
            return

        host = self

        class _Sheet(MessageBoxBase):
            def __init__(_s, h):
                super().__init__(h.window() if h is not None else None)
                try:
                    _s.setMaskColor(QColor(0, 0, 0, 170))
                except Exception:
                    pass
                _s.widget.setObjectName("cmtCard")
                apply_fluent_modal_palette(_s, "cmtCard")
                _s.viewLayout.setContentsMargins(22, 18, 22, 12)
                _s.viewLayout.setSpacing(12)

                hdr = QHBoxLayout(); hdr.setSpacing(12)
                ic = _QTB(); ic.setEnabled(False)
                ic.setIcon(TablerIcon("tabler_message_circle.svg").icon(color=QColor("#58A6FF")))
                ic.setIconSize(_QSz(22, 22))
                ic.setStyleSheet(
                    "background: rgba(56,139,253,0.14); border: none;"
                    " border-radius: 10px; padding: 6px;"
                )
                tc = QVBoxLayout(); tc.setSpacing(2)
                t = QLabel("Comment")
                t.setStyleSheet(
                    "color: #E6EDF3; font-size: 15px; font-weight: 700;"
                    " background: transparent;"
                )
                s = QLabel(f"Case {case_id}")
                s.setStyleSheet(
                    "color: #8B949E; font-size: 11px; background: transparent;"
                )
                tc.addWidget(t); tc.addWidget(s)
                hdr.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
                hdr.addLayout(tc, 1)
                _s.viewLayout.addLayout(hdr)

                box = _QTE()
                box.setReadOnly(True)
                box.setPlainText(comment)
                box.setStyleSheet(
                    "QTextEdit { background: #161B22; border: 1px solid #30363D;"
                    "  border-radius: 6px; padding: 8px 10px; color: #E6EDF3;"
                    "  font-size: 12px; }"
                )
                box.setMinimumHeight(120)
                _s.viewLayout.addWidget(box)
                _s.widget.setMinimumWidth(460)

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

    def _build_day_header_widget(self, *, fecha, cases_count, value_html,
                                  units, time_min, breakdown_text):
        """Day-group header row that mirrors the Today's Cases look:
        calendar icon + date + cases + value + units + time on a top row,
        with a type breakdown line below."""
        from PySide6.QtWidgets import QSizePolicy as _SP_h
        wrap = QFrame()
        wrap.setObjectName("dayHdr")
        wrap.setAutoFillBackground(True)
        wrap.setSizePolicy(_SP_h.Policy.Expanding, _SP_h.Policy.Expanding)
        wrap.setStyleSheet(
            "#dayHdr { background: rgba(63,184,175,0.12);"
            "  border: none;"
            "  border-top: 1px solid rgba(63,184,175,0.30);"
            "  border-bottom: 1px solid rgba(63,184,175,0.30); }"
            "QLabel { background: transparent; border: none; }"
        )
        v = QVBoxLayout(wrap)
        v.setContentsMargins(14, 6, 14, 6)
        v.setSpacing(2)

        top = QHBoxLayout(); top.setSpacing(10)
        try:
            from .tabler_icons import TablerIcon as _TI_h
            ic_lbl = QLabel()
            ic_lbl.setFixedSize(16, 16)
            ic_lbl.setPixmap(
                _TI_h("tabler_calendar.svg")
                .icon(color=QColor("#3FB8AF")).pixmap(16, 16)
            )
            top.addWidget(ic_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
        except Exception:
            pass

        date_lbl = QLabel(str(fecha))
        date_lbl.setStyleSheet(
            "color: #E6EDF3; font-size: 12px; font-weight: 700;"
        )
        top.addWidget(date_lbl)

        cases_lbl = QLabel(f"{cases_count} cases")
        cases_lbl.setStyleSheet("color: #8B949E; font-size: 11px;")
        top.addWidget(cases_lbl)

        value_lbl = QLabel(value_html)
        value_lbl.setTextFormat(Qt.TextFormat.RichText)
        value_lbl.setStyleSheet("color: #E6EDF3; font-size: 11px;")
        top.addWidget(value_lbl)

        units_lbl = QLabel(
            f"<span style='color:#8B949E'>Units:</span> "
            f"<b style='color:#E6EDF3'>{units:.2f}</b>"
        )
        units_lbl.setTextFormat(Qt.TextFormat.RichText)
        units_lbl.setStyleSheet("font-size: 11px;")
        top.addWidget(units_lbl)

        time_lbl = QLabel(
            f"<span style='color:#8B949E'>Time:</span> "
            f"<b style='color:#E6EDF3'>{time_min:.0f}m</b>"
        )
        time_lbl.setTextFormat(Qt.TextFormat.RichText)
        time_lbl.setStyleSheet("font-size: 11px;")
        top.addWidget(time_lbl)

        top.addStretch(1)
        v.addLayout(top)

        if breakdown_text:
            sub_lbl = QLabel(breakdown_text)
            sub_lbl.setStyleSheet(
                "color: #8B949E; font-size: 10px; padding-left: 26px;"
            )
            v.addWidget(sub_lbl)

        return wrap

    def _reset_filters(self):
        """Reset every filter — Region/Type/Doctor + Product Tier + CR
        + Comments chips. Date range stays put."""
        try:
            self.filter_region.blockSignals(True)
            self.filter_type.blockSignals(True)
            self.filter_doctor.blockSignals(True)
            self.filter_region.setCurrentIndex(0)
            self.filter_type.setCurrentIndex(0)
            self.filter_doctor.clear()
        finally:
            self.filter_region.blockSignals(False)
            self.filter_type.blockSignals(False)
            self.filter_doctor.blockSignals(False)
        self._filter_product_tier = ""
        self._filter_cr = ""
        self._filter_comments = ""
        if hasattr(self, "_chip_with"):
            self._chip_with.setChecked(False)
        if hasattr(self, "_chip_without"):
            self._chip_without.setChecked(False)
        if hasattr(self, "_filters_btn"):
            self._filters_btn.setText("  Filters")
        self.on_filter_changed()

    def _refresh_mode_buttons_style(self):
        """Re-paint the Regular/Overtime standalone pill buttons."""
        is_reg = self.current_mode == "reg"
        reg_active_css = (
            "QPushButton { background: #1e63e4; border: 1px solid #1e63e4;"
            " color: white; border-radius: 10px;"
            " font-weight: 700; font-size: 12px; padding: 0 18px; }"
            "QPushButton:hover { background: #2a73f3; }"
        )
        ot_active_css = (
            "QPushButton { background: #F0883E; border: 1px solid #F0883E;"
            " color: white; border-radius: 10px;"
            " font-weight: 700; font-size: 12px; padding: 0 18px; }"
            "QPushButton:hover { background: #F49852; }"
        )
        inactive_css = (
            "QPushButton { background: #161B22; border: 1px solid #21262D;"
            " color: #C9D1D9; border-radius: 10px;"
            " font-weight: 600; font-size: 12px; padding: 0 18px; }"
            "QPushButton:hover { background: rgba(255,255,255,0.05);"
            " border-color: #58606A; }"
        )
        self.btn_reg.setStyleSheet(reg_active_css if is_reg else inactive_css)
        self.btn_ot.setStyleSheet(ot_active_css if not is_reg else inactive_css)

    def switch_to_reg(self):
        """Switch to Regular production cases"""
        self.current_mode = "reg"
        self._refresh_mode_buttons_style()
        self.load_data()

    def switch_to_ot(self):
        """Switch to OT cases"""
        self.current_mode = "ot"
        self._refresh_mode_buttons_style()
        self.load_data()

    def load_data(self):
        # Bounded to the currently selected date range — pushing the filter
        # into SQL (idx_cases_fecha / idx_ot_cases_fecha already exist) keeps
        # this fast regardless of total history size, instead of fetching
        # every row ever saved on every single case/OT save.
        d_from = self.date_from.date().toString("yyyy-MM-dd") \
            if hasattr(self, "date_from") else None
        d_to = self.date_to.date().toString("yyyy-MM-dd") \
            if hasattr(self, "date_to") else None

        try:
            conn = get_connection()
            cursor = conn.cursor()
            table = "cases" if self.current_mode == "reg" else "ot_cases"
            where = " WHERE fecha BETWEEN ? AND ?" if (d_from and d_to) else ""
            params = [d_from, d_to] if (d_from and d_to) else []

            # `cr_count` and `product_tier` are new optional columns —
            # older DBs may lack them. COALESCE returns sensible defaults.
            cursor.execute(f"""
                SELECT id, case_id, doctor, region, tipo_caso, fecha, hora_inicio, hora_fin,
                       tiempo_real, efficiency, estado, case_value, count_production, comments,
                       cr_count,
                       COALESCE(product_tier, '')
                FROM {table}{where}
                ORDER BY id DESC
            """, params)

            self.all_cases = cursor.fetchall()
            conn.close()
        except Exception as exc:
            # DB momentarily locked — keep last successful snapshot, do not wipe the table
            print(f"[ProductionTab] load_data failed: {exc}")
            return

        self.load_regions_and_types()
        self.filter_data()

    def filter_data(self):
        # Reset header widget refs — about to repopulate the table.
        self._day_hdr_widgets = []
        date_from = self.date_from.date().toString("yyyy-MM-dd")
        date_to = self.date_to.date().toString("yyyy-MM-dd")
        region_filter = self.filter_region.currentText()
        type_filter = self.filter_type.currentText()
        doctor_filter = self.filter_doctor.text().lower()
        tier_filter = getattr(self, "_filter_product_tier", "") or ""
        cr_filter = getattr(self, "_filter_cr", "") or ""
        cmts_filter = getattr(self, "_filter_comments", "") or ""

        filtered = []
        for row in self.all_cases:
            fecha = row[5]
            if fecha < date_from or fecha > date_to:
                continue
            if region_filter != "All" and row[3] != region_filter:
                continue
            if type_filter != "All" and row[4] != type_filter:
                continue
            if doctor_filter:
                doc = (row[2] or "").lower()
                cid = str(row[1] or "").lower()
                if doctor_filter not in doc and doctor_filter not in cid:
                    continue
            # Comments toggle.
            if cmts_filter:
                row_cmts = (row[13] if len(row) > 13 else "") or ""
                has_cmts = bool(row_cmts.strip())
                if cmts_filter == "with" and not has_cmts:
                    continue
                if cmts_filter == "without" and has_cmts:
                    continue

            # Product Tier (index 15, optional — '' in legacy rows).
            if tier_filter:
                row_tier = (row[15] if len(row) > 15 else "") or ""
                if row_tier != tier_filter:
                    continue
            # CR # — "any" matches any non-null cr_count.
            if cr_filter:
                row_cr = row[14] if len(row) > 14 else None
                if cr_filter == "any":
                    if row_cr is None:
                        continue
                else:
                    try:
                        if int(row_cr) != int(cr_filter):
                            continue
                    except (TypeError, ValueError):
                        continue
            filtered.append(row)
        
        # Store filtered cases for pagination
        self.filtered_cases = filtered

        # Group ALL filtered cases by date first, then paginate by date blocks
        all_grouped = {}
        for row in filtered:
            fecha = row[5]
            if fecha not in all_grouped:
                all_grouped[fecha] = []
            all_grouped[fecha].append(row)

        self._all_dates_sorted = sorted(all_grouped.keys(), reverse=True)
        total_dates = len(self._all_dates_sorted)
        self.total_pages = max(1, (total_dates + self.days_per_page - 1) // self.days_per_page)

        if self.current_page > self.total_pages:
            self.current_page = self.total_pages
        if self.current_page < 1:
            self.current_page = 1

        self._rebuild_pager()
        self.btn_prev.setEnabled(self.current_page > 1)
        self.btn_next.setEnabled(self.current_page < self.total_pages)

        # Get dates for current page
        date_start = (self.current_page - 1) * self.days_per_page
        date_end = date_start + self.days_per_page
        page_dates = self._all_dates_sorted[date_start:date_end]

        # Build grouped dict for only the current page's dates
        grouped = {d: all_grouped[d] for d in page_dates}
        
        # Calculate stats from ALL filtered items (not just current page)
        # Only include cases where count_production != 0
        prod_filtered = [r for r in filtered if (r[12] if (len(r) > 12 and r[12] is not None) else 1) != 0]
        total_cases = len(prod_filtered)
        ok_count = sum(1 for row in prod_filtered if row[10] == "OK")  # estado at index 10
        low_count = total_cases - ok_count
        total_value = sum(row[11] for row in prod_filtered)  # case_value at index 11
        avg_efficiency = sum(row[9] for row in prod_filtered) / total_cases if total_cases > 0 else 0  # efficiency at index 9

        # Compute total UE — sum of equivalent units across counted cases.
        # Group by (region, type) so the UE calc respects per-case-type rates.
        from collections import defaultdict
        _ue_groups = defaultdict(lambda: {"sum_cv": 0.0, "count": 0})
        for row in prod_filtered:
            try:
                # row fields: ... region(3), tipo_caso(4), ..., case_value(11)
                region = row[3] or ""
                tipo = row[4] or ""
                cv = row[11] or 0.0
            except Exception:
                continue
            g = _ue_groups[(region, tipo)]
            g["sum_cv"] += cv
            g["count"] += 1
        total_ue = 0.0
        for (region, tipo), g in _ue_groups.items():
            if g["count"]:
                try:
                    total_ue += calculate_equivalent_units(
                        self.units_eq, region, tipo, g["sum_cv"], count=g["count"],
                    )
                except Exception:
                    pass

        # KPI card values: no prefix — card header carries the metric name.
        self.stats_avg.setText(f"{avg_efficiency:,.2f}%")
        self.stats_total.setText(f"{total_cases:,}")
        self.stats_ok.setText(f"{total_value:,.2f}%")
        if hasattr(self, "stats_ue"):
            self.stats_ue.setText(f"{total_ue:,.2f}")
        if hasattr(self, "stats_ok_count"):
            self.stats_ok_count.setText(f"{ok_count:,}")
        if hasattr(self, "stats_low_count"):
            self.stats_low_count.setText(f"{low_count:,}")
        self.stats_low.setText(f"OK {ok_count} / LOW {low_count}")

        # Restore collapsed state: hide case rows for days that the user
        # had collapsed previously. Read fresh from self in case the
        # earlier local was skipped due to an empty filter result.
        _collapsed = getattr(self, "_collapsed_days", set()) or set()
        for key, case_rows in (self._day_case_rows or {}).items():
            if key in _collapsed:
                for r in case_rows:
                    self.table.setRowHidden(r, True)

        # Stretch day-group header widgets to the table viewport — fire
        # multiple times so we catch the final size after layout settles.
        from PySide6.QtCore import QTimer as _QT_sync
        _QT_sync.singleShot(0, self._sync_day_header_widths)
        _QT_sync.singleShot(50, self._sync_day_header_widths)
        _QT_sync.singleShot(200, self._sync_day_header_widths)

        # Theme detection — read the value pushed by update_theme_labels
        # (which is wired to main's themeChanged signal). The previous
        # palette-based check was unreliable because QApplication.setStyleSheet
        # does NOT change the palette; on machines where the OS theme is
        # light, palette.lightness() returned light even when the app was
        # in dark mode, so foreground colours collapsed to dark-on-dark.
        current_is_light = bool(getattr(self, "_light_mode_active", False))
        light_colors = get_light_theme_colors()

        sorted_dates = sorted(grouped.keys(), reverse=True)

        # Pre-fetch downtime for all page dates in one query
        _dt_map = {}
        if sorted_dates:
            conn_dt = get_connection()
            try:
                cur_dt = conn_dt.cursor()
                placeholders = ",".join("?" for _ in sorted_dates)
                cur_dt.execute(
                    f"SELECT fecha, SUM(duracion) FROM downtimes "
                    f"WHERE fecha IN ({placeholders}) AND (status = 'approved' OR status IS NULL) "
                    f"GROUP BY fecha",
                    sorted_dates,
                )
                _dt_map = {r[0]: r[1] for r in cur_dt.fetchall()}
            finally:
                conn_dt.close()

        # Always allocate space for ALL rows (1 header per day + every
        # case row). Collapsed groups hide their case rows via
        # setRowHidden so toggling doesn't rebuild the table.
        collapsed = getattr(self, "_collapsed_days", set()) or set()
        total_rows = 0
        for f, cases in grouped.items():
            total_rows += 1 + len(cases)
        self._day_case_rows = {}
        self._day_chevrons = {}

        # Reset any previous row spans/content before drawing current page.
        self.table.clearSpans()
        self.table.clearContents()
        self.table.setRowCount(total_rows)

        # Clear the row to db_id mapping
        self.case_db_ids = {}
        row_idx = 0

        for fecha in sorted_dates:
            # Calculate daily totals - only include cases that count to production
            prod_cases = [c for c in grouped[fecha] if (c[12] if (len(c) > 12 and c[12] is not None) else 1) != 0]
            daily_value = sum(case[11] for case in prod_cases)
            daily_cases = len(prod_cases)
            daily_units_eq = sum(self.calculate_units_eq(case[3], case[11], case[4]) for case in prod_cases)
            daily_time_sum = sum((case[8] or 0) for case in prod_cases)

            # Add downtime credit (production % only — UE no longer includes downtime)
            dt_mins = _dt_map.get(fecha, 0) or 0
            dt_value = (dt_mins / DAILY_BASE_MINUTES) * 100 if dt_mins > 0 else 0

            total_value_day = daily_value + dt_value

            # Theme colors for header rows
            if current_is_light:
                date_bg = QColor(light_header_bg(light_colors))
                date_fg = QColor(light_header_fg(light_colors))
            else:
                date_bg = QColor(75, 75, 85)
                date_fg = QColor(220, 220, 220)
            header_font = QFont()
            header_font.setBold(True)

            # ── Day-group header (matches Today's Cases) ──
            # One spanned row holds a custom widget with icon + two text rows.
            type_counts = Counter(case[4] or "Unknown" for case in prod_cases)
            breakdown_parts = [f"{t}: {c}" for t, c in sorted(type_counts.items())]
            breakdown_text = "    ".join(breakdown_parts) if breakdown_parts else ""

            if dt_mins > 0:
                value_html = (
                    f"<span style='color:#E6EDF3'>Value:</span> "
                    f"<b style='color:#E6EDF3'>{total_value_day:.2f}%</b> "
                    f"<span style='color:#8B949E'>"
                    f"(Cases: {daily_value:.2f}% + DT: {dt_value:.2f}%)</span>"
                )
            else:
                value_html = (
                    f"<span style='color:#8B949E'>Value:</span> "
                    f"<b style='color:#E6EDF3'>{daily_value:.2f}%</b>"
                )

            # Build the header line. Use item-based rendering (not a
            # cellWidget) because cellWidget on a spanned row does not
            # stretch reliably and the colored band ends up clipped.
            if dt_mins > 0:
                line1 = (
                    f"   📅  {fecha}     {daily_cases} cases     "
                    f"Value: {total_value_day:.2f}% (Cases: {daily_value:.2f}% + DT: {dt_value:.2f}%)     "
                    f"Units: {daily_units_eq:.2f}     Time: {daily_time_sum:.0f}m"
                )
            else:
                line1 = (
                    f"   📅  {fecha}     {daily_cases} cases     "
                    f"Value: {daily_value:.2f}%     "
                    f"Units: {daily_units_eq:.2f}     Time: {daily_time_sum:.0f}m"
                )

            # Day-group banner — use palette accent in light mode, navy
            # in dark mode so the band still pops against the table bg.
            try:
                from qfluentwidgets.common.style_sheet import isDarkTheme
                from .theme_palette import palette as _p_band
                _band_pal = _p_band(not isDarkTheme())
                if isDarkTheme():
                    band_bg = QColor("#15233D")
                    band_fg = QColor("#E6EDF3")
                else:
                    band_bg = QColor(_band_pal["accent"])
                    band_fg = QColor("#FFFFFF")
            except Exception:
                band_bg = QColor("#15233D")
                band_fg = QColor("#E6EDF3")
            header_font = QFont()
            header_font.setBold(True)
            header_font.setPointSize(font_scale.scale_pt(10))

            # Build header as a custom widget — guarantees the colored band
            # paints across the full width regardless of column stretch.
            # WA_StyledBackground + an explicit objectName makes QSS bg
            # actually paint on a QFrame.
            from PySide6.QtCore import Qt as _Qt_h
            hdr_widget = QFrame()
            hdr_widget.setObjectName("dayBand")
            hdr_widget.setAttribute(_Qt_h.WidgetAttribute.WA_StyledBackground, True)
            hdr_widget.setAutoFillBackground(True)
            hdr_widget.setStyleSheet(
                f"#dayBand {{ background-color: {band_bg.name()};"
                "  border: none;"
                "  border-bottom: 1px solid #0D1117; }"
                "QLabel { background: transparent; color: #E6EDF3;"
                "  font-size: 11px; }"
            )
            # Expand-collapse toggle (right-side chevron).
            row_key = f"day:{fecha}"
            if not hasattr(self, "_collapsed_days"):
                self._collapsed_days = set()
            is_collapsed = row_key in self._collapsed_days

            hv = QHBoxLayout(hdr_widget)
            hv.setContentsMargins(14, 4, 14, 4)
            hv.setSpacing(10)
            # Centered text column (stretch left + right).
            hv.addStretch(1)
            text_col = QVBoxLayout(); text_col.setSpacing(2)
            top_lbl = QLabel(line1.replace("📅  ", ""))
            tf = QFont(); tf.setBold(True); tf.setPointSize(font_scale.scale_pt(10))
            top_lbl.setFont(tf)
            top_lbl.setAlignment(_Qt_h.AlignmentFlag.AlignCenter)
            top_lbl.setStyleSheet("color: #E6EDF3; background: transparent;")
            text_col.addWidget(top_lbl)
            if breakdown_text:
                sub_lbl = QLabel(breakdown_text)
                sf = QFont(); sf.setPointSize(font_scale.scale_pt(8))
                sub_lbl.setFont(sf)
                sub_lbl.setAlignment(_Qt_h.AlignmentFlag.AlignCenter)
                sub_lbl.setStyleSheet("color: #B0B8C0; background: transparent;")
                text_col.addWidget(sub_lbl)
            hv.addLayout(text_col, 0)
            hv.addStretch(1)
            # Chevron toggle pinned to right.
            try:
                from .tabler_icons import TablerIcon as _TI_t
                from PySide6.QtWidgets import QToolButton as _QTB_t
                from PySide6.QtCore import QSize as _QSt
                chev_btn = _QTB_t()
                chev_btn.setCursor(_Qt_h.CursorShape.PointingHandCursor)
                chev_svg = ("tabler_chevron_down.svg" if not is_collapsed
                            else "tabler_chevron_right.svg")
                chev_btn.setIcon(_TI_t(chev_svg).icon(color=QColor("#C9D1D9")))
                chev_btn.setIconSize(_QSt(16, 16))
                chev_btn.setFixedSize(24, 24)
                chev_btn.setStyleSheet(
                    "QToolButton { background: transparent; border: none; }"
                    "QToolButton:hover { background: rgba(255,255,255,0.08);"
                    "  border-radius: 4px; }"
                )
                chev_btn.clicked.connect(
                    lambda _=False, k=row_key: self._toggle_day(k)
                )
                hv.addWidget(chev_btn, 0, _Qt_h.AlignmentFlag.AlignVCenter)
                self._day_chevrons[row_key] = chev_btn
            except Exception:
                pass

            self.table.setSpan(row_idx, 0, 1, 10)
            self.table.setCellWidget(row_idx, 0, hdr_widget)
            self.table.setRowHeight(row_idx, 54 if breakdown_text else 32)
            if not hasattr(self, "_day_hdr_widgets"):
                self._day_hdr_widgets = []
            # Track (row, widget) so the sync helper can read the merged
            # visualRect for that specific row.
            self._day_hdr_widgets.append((row_idx, hdr_widget))
            row_idx += 1

            # Track case rows for this day so toggle can hide/show them
            # without rebuilding the table.
            day_rows: list = []
            self._day_case_rows[row_key] = day_rows

            # Case rows for this date - zebra striping within each date group
            for case_idx, case in enumerate(grouped[fecha]):
                day_rows.append(row_idx)
                # Store mapping from table row to database id
                self.case_db_ids[row_idx] = case[0]  # id at index 0
                
                # Check if case counts for production (count_production at index 12)
                # Default to 1 if None or not present
                counts_for_production = case[12] if (len(case) > 12 and case[12] is not None) else 1

                if current_is_light:
                    bg_color = light_row_bg(case_idx, light_colors)
                else:
                    bg_color = QColor(43, 43, 43) if (case_idx % 2 == 0) else QColor(55, 55, 55)

                # Cases that don't count for production — soft orange tint
                # so they stand out at a glance.
                if counts_for_production == 0:
                    bg_color = QColor("#3A2E1F") if not current_is_light else QColor("#FFE9CC")

                bg_brush = QBrush(bg_color)
                # Lock case row height so wrapped doctor names don't push
                # rows taller than their neighbours.
                self.table.setRowHeight(row_idx, 32)

                # Case ID - Bold (case_id at index 1)
                # Determine text color for row based on theme; dim grey for non-counting cases
                if counts_for_production == 0:
                    text_color = QColor("#A15C00") if current_is_light else QColor("#F0883E")
                else:
                    text_color = CLR_FG_DARK if current_is_light else CLR_FG_LIGHT

                comment = (case[13] if len(case) > 13 else "") or ""
                suffix_text = " (NC)" if counts_for_production == 0 else ""

                # Underlying item — empty display so it doesn't bleed
                # through the cell widget. Real case_id lives in UserRole.
                case_id_item = QTableWidgetItem("")
                case_id_item.setData(Qt.ItemDataRole.UserRole, str(case[1]))
                case_id_item.setBackground(bg_brush)
                case_id_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                bold_font = QFont()
                bold_font.setBold(True)
                if counts_for_production == 0:
                    bold_font.setItalic(True)
                case_id_item.setFont(bold_font)
                case_id_item.setForeground(QBrush(text_color))
                if comment.strip():
                    case_id_item.setToolTip(comment.strip())
                self.table.setItem(row_idx, 0, case_id_item)
                self.table.setCellWidget(
                    row_idx, 0,
                    self._build_case_id_cell(
                        case_id=str(case[1]),
                        suffix=suffix_text,
                        comment=comment.strip(),
                        text_color=text_color.name(),
                        bold=True,
                        italic=(counts_for_production == 0),
                    ),
                )
                
                # Doctor — bold + elided so it stays on one line and the
                # row keeps a consistent height. Full name in tooltip.
                _full_doc = str(case[2] or "").strip()
                _is_empty_doc = not _full_doc
                if _is_empty_doc:
                    _elide_doc = "—  no doctor"
                else:
                    _elide_doc = (_full_doc if len(_full_doc) <= 14
                                  else _full_doc[:13] + "…")
                doctor_item = QTableWidgetItem(_elide_doc)
                doctor_item.setToolTip(_full_doc or "—  no doctor")
                doctor_item.setBackground(bg_brush)
                doctor_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if _is_empty_doc:
                    # Muted italic placeholder for missing doctor names.
                    from PySide6.QtGui import QFont as _QF_doc
                    _f_doc = _QF_doc(doctor_item.font())
                    _f_doc.setItalic(True)
                    doctor_item.setFont(_f_doc)
                    doctor_item.setForeground(QBrush(QColor("#6E7681")))
                else:
                    doctor_item.setFont(bold_font)
                    doctor_item.setForeground(QBrush(text_color))
                self.table.setItem(row_idx, 1, doctor_item)
                
                # Other columns - centered (indices shifted)
                # Get std_time for comparison (need to load from standards)
                tiempo_real = case[8]  # Time at index 8
                
                other_items = [
                    str(case[3]),           # Region (index 3)
                    str(case[4]),           # Type (index 4)
                    str(case[6]),           # Start (index 6)
                    str(case[7]),           # End (index 7)
                ]
                
                for col, text in enumerate(other_items, start=2):
                    item = QTableWidgetItem(text)
                    item.setBackground(bg_brush)
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    item.setForeground(QBrush(text_color))
                    self.table.setItem(row_idx, col, item)
                
                # Time column — colour the TEXT (cell bg stays neutral so
                # the grid keeps the Today's Cases look).
                time_item = QTableWidgetItem(f"{tiempo_real:.0f}")
                time_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                time_item.setBackground(QBrush(bg_color))
                std_time_val = case[9] if False else None  # case tuple has no std_time
                # Std time isn't in the SELECT — derive from real + estado.
                if case[10] == "OK":
                    time_item.setForeground(QBrush(QColor("#3FB950")))
                else:
                    time_item.setForeground(QBrush(QColor("#F85149")))
                self.table.setItem(row_idx, 6, time_item)

                # Efficiency — colour the TEXT (green/amber/red), no bg.
                try:
                    eff_val = float(case[9])
                except Exception:
                    eff_val = None
                efficiency_item = QTableWidgetItem(f"{case[9]:.0f}%")
                efficiency_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                efficiency_item.setBackground(QBrush(bg_color))
                if eff_val is not None:
                    if eff_val >= 100:
                        efficiency_item.setForeground(QBrush(QColor("#3FB950")))
                    elif eff_val >= 95:
                        efficiency_item.setForeground(QBrush(QColor("#D29922")))
                    else:
                        efficiency_item.setForeground(QBrush(QColor("#F85149")))
                else:
                    if case[10] == "OK":
                        efficiency_item.setForeground(QBrush(QColor("#3FB950")))
                    else:
                        efficiency_item.setForeground(QBrush(QColor("#F85149")))
                self.table.setItem(row_idx, 7, efficiency_item)
                
                # Case Value - no color, same background as other columns
                value_item = QTableWidgetItem(f"{case[11]:.1f}%")
                value_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                value_item.setBackground(QBrush(bg_color))
                value_item.setForeground(QBrush(text_color))
                self.table.setItem(row_idx, 8, value_item)
                
                # Units Equivalent - calculated from region and case_value
                units_eq = self.calculate_units_eq(case[3], case[11], case[4])  # region, case_value, tipo_caso
                units_eq_item = QTableWidgetItem(f"{units_eq:.2f}")
                units_eq_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                units_eq_item.setBackground(QBrush(bg_color))
                units_eq_item.setForeground(QBrush(text_color))
                self.table.setItem(row_idx, 9, units_eq_item)
                
                row_idx += 1

        # Tooltip pass: show full content on hover for truncated cells.
        for r in range(self.table.rowCount()):
            for c in range(self.table.columnCount()):
                item = self.table.item(r, c)
                if not item:
                    continue
                text = item.text() or ""
                if text and not item.toolTip():
                    item.setToolTip(text)

    def _arm_action(self, mode: str):
        """Enter 'pick a case' mode for Edit or Delete. The next click on
        a case row triggers the action — until then we don't need an
        already-selected row."""
        if getattr(self, "_action_mode", None) == mode:
            self._action_mode = None
            self._refresh_action_buttons()
            self._show_action_hint("")
            return
        self._action_mode = mode
        self._refresh_action_buttons()
        msg = ("Please select a case to edit" if mode == "edit"
               else "Please select a case to delete")
        self._show_action_hint(msg, mode)
        if not getattr(self, "_action_click_wired", False):
            self.table.cellClicked.connect(self._on_table_click_for_action)
            self._action_click_wired = True

    def _show_action_hint(self, text: str, mode: str = ""):
        """Show/hide a small status pill between the action buttons and
        the pagination row letting the user know the table is waiting
        for a row click."""
        if not hasattr(self, "_action_hint_lbl"):
            return
        if not text:
            self._action_hint_lbl.hide()
            return
        color = "#388BFD" if mode == "edit" else "#F85149"
        self._action_hint_lbl.setStyleSheet(
            f"QLabel {{ background: rgba(56,139,253,0.10);"
            f"  border: 1px solid {color}; color: {color};"
            "  border-radius: 6px; padding: 4px 12px;"
            "  font-weight: 700; font-size: 11px; }"
        )
        self._action_hint_lbl.setText(text)
        self._action_hint_lbl.show()

    def _refresh_action_buttons(self):
        """Visually highlight the armed button so the user knows they're
        in 'pick a case' mode."""
        armed = getattr(self, "_action_mode", None)
        # Edit button — invert when armed.
        if armed == "edit":
            self.edit_btn.setStyleSheet(
                "QPushButton { background: #1e63e4; border: 1px solid #1e63e4;"
                "  color: white; border-radius: 6px; padding: 4px 14px;"
                "  font-weight: 700; font-size: 11px; }"
            )
        else:
            self.edit_btn.setStyleSheet(
                "QPushButton { background: #161B22; border: 1px solid #30363D;"
                "  color: #E6EDF3; border-radius: 6px; padding: 4px 14px;"
                "  font-weight: 700; font-size: 11px; }"
                "QPushButton:hover { background: rgba(255,255,255,0.05); }"
            )
        # Delete button — darken when armed (already red).
        if armed == "delete":
            self.delete_btn.setStyleSheet(
                "QPushButton { background: #B62A23; border: 1px solid #B62A23;"
                "  color: white; border-radius: 6px; padding: 4px 14px;"
                "  font-weight: 700; font-size: 11px; }"
            )
        else:
            self.delete_btn.setStyleSheet(
                "QPushButton { background: #F85149; border: 1px solid #F85149;"
                "  color: white; border-radius: 6px; padding: 4px 14px;"
                "  font-weight: 700; font-size: 11px; }"
                "QPushButton:hover { background: #FF6B61; }"
            )

    def _on_table_click_for_action(self, row: int, _col: int):
        """When the user is in Edit/Delete pick mode, treat the next click
        on a case row as the target."""
        armed = getattr(self, "_action_mode", None)
        if not armed:
            return
        if row not in self.case_db_ids:
            return  # ignore day-header / breakdown / empty rows
        self.table.setCurrentCell(row, 0)
        # Clear armed state first so the action methods reading
        # _action_mode behave normally.
        self._action_mode = None
        self._refresh_action_buttons()
        self._show_action_hint("")
        if armed == "edit":
            self._do_edit_for_row(row)
        elif armed == "delete":
            self._do_delete_for_row(row)

    def edit_selected_case(self):
        """Always require an explicit row pick after clicking Edit."""
        self._arm_action("edit")

    def _do_edit_for_row(self, row: int):
        if row not in self.case_db_ids:
            return
        db_id = self.case_db_ids[row]
        self.editing_case_id = db_id
        self.editing_mode = self.current_mode
        self.case_updated.emit()

    def delete_selected_case(self):
        """Always require an explicit row pick after clicking Delete."""
        self._arm_action("delete")

    def _do_delete_for_row(self, selected_row: int):
        if selected_row not in self.case_db_ids:
            return

        db_id = self.case_db_ids[selected_row]
        _cid_item = self.table.item(selected_row, 0)
        case_id_text = ""
        if _cid_item is not None:
            _ud = _cid_item.data(Qt.ItemDataRole.UserRole)
            case_id_text = str(_ud) if _ud else (_cid_item.text() or "")

        if not self._confirm_delete_case(case_id_text):
            return

        conn = get_connection()
        cursor = conn.cursor()
        table_name = "cases" if self.current_mode == "reg" else "ot_cases"
        cursor.execute(f"DELETE FROM {table_name} WHERE id = ?", (db_id,))
        conn.commit()
        conn.close()

        self.load_data()
        self.case_updated.emit()

    def _confirm_delete_case(self, case_id_text: str) -> bool:
        """Fluent modal confirmation. Returns True on confirm."""
        try:
            from qfluentwidgets import MessageBoxBase
            from .tabler_icons import TablerIcon
            from PySide6.QtWidgets import (
                QFrame as _QF, QToolButton as _QTB,
            )
            from PySide6.QtGui import QColor as _QC2
            from PySide6.QtCore import QSize as _QS2
        except Exception:
            reply = QMessageBox.question(
                self, "Confirm Delete",
                f"Delete case '{case_id_text}'?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            return reply == QMessageBox.StandardButton.Yes

        host = self

        class _Sheet(MessageBoxBase):
            def __init__(_s, h):
                super().__init__(h.window() if h is not None else None)
                try:
                    _s.setMaskColor(_QC2(0, 0, 0, 170))
                except Exception:
                    pass
                _s.widget.setObjectName("delCard")
                apply_fluent_modal_palette(_s, "delCard")
                _s.viewLayout.setContentsMargins(22, 18, 22, 12)
                _s.viewLayout.setSpacing(12)

                hdr = QHBoxLayout(); hdr.setSpacing(12)
                ic = _QTB(); ic.setEnabled(False)
                ic.setIcon(TablerIcon("tabler_alert_triangle.svg").icon(color=_QC2("#F85149")))
                ic.setIconSize(_QS2(22, 22))
                ic.setStyleSheet(
                    "background: rgba(248,81,73,0.14); border: none;"
                    " border-radius: 10px; padding: 6px;"
                )
                tc = QVBoxLayout(); tc.setSpacing(2)
                t = QLabel("Delete case")
                t.setStyleSheet(
                    "color: #E6EDF3; font-size: 15px; font-weight: 700;"
                    " background: transparent;"
                )
                s = QLabel("This removes the case from the database.")
                s.setStyleSheet(
                    "color: #8B949E; font-size: 11px; background: transparent;"
                )
                tc.addWidget(t); tc.addWidget(s)
                hdr.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
                hdr.addLayout(tc, 1)
                _s.viewLayout.addLayout(hdr)

                pill = _QF()
                pill.setStyleSheet(
                    "QFrame { background: #161B22; border: none;"
                    " border-radius: 10px; }"
                    "QLabel { background: transparent; }"
                )
                pl = QHBoxLayout(pill)
                pl.setContentsMargins(14, 10, 14, 10)
                pl.setSpacing(8)
                pl.addWidget(QLabel("Case ID"))
                cid = QLabel(str(case_id_text))
                cid.setStyleSheet(
                    "color: #F85149; font-size: 14px; font-weight: 800;"
                    " font-family: 'Consolas','Menlo',monospace;"
                )
                pl.addStretch(1)
                pl.addWidget(cid)
                _s.viewLayout.addWidget(pill)

                _s.widget.setMinimumWidth(420)

                _s.cancelButton.setText("Cancel")
                _s.cancelButton.setFixedWidth(120)
                _s.cancelButton.setStyleSheet(
                    "QPushButton { background: transparent;"
                    "  border: 1px solid #30363D; color: #E6EDF3;"
                    "  border-radius: 6px; padding: 8px 22px;"
                    "  font-weight: 700; font-size: 12px; }"
                    "QPushButton:hover { background: rgba(255,255,255,0.05); }"
                )
                _s.yesButton.setText("   Delete")
                _s.yesButton.setFixedWidth(140)
                _s.yesButton.setStyleSheet(
                    "QPushButton { background: #F85149; border: 1px solid #F85149;"
                    "  color: white; border-radius: 6px; padding: 8px 22px;"
                    "  font-weight: 700; font-size: 12px; }"
                    "QPushButton:hover { background: #FF6B61; }"
                )
                try:
                    _s.yesButton.setIcon(
                        TablerIcon("tabler_trash.svg").icon(color=_QC2("#FFFFFF"))
                    )
                    _s.yesButton.setIconSize(_QS2(14, 14))
                except Exception:
                    pass

        return bool(_Sheet(host).exec())

    def prev_page(self):
        """Go to previous page"""
        if self.current_page > 1:
            self.current_page -= 1
            self.filter_data()
    
    def next_page(self):
        """Go to next page"""
        if self.current_page < self.total_pages:
            self.current_page += 1
            self.filter_data()
    
    def reset_to_first_page(self):
        """Reset to first page when filters change"""
        self.current_page = 1
    
    def on_filter_changed(self):
        """Called when any filter changes - reset to first page and filter"""
        self.current_page = 1
        self.filter_data()

    def update_font_sizes(self, _new_size: int = 0):
        """Re-render the table so QFont calls inside filter_data pick up the
        new global scale."""
        try:
            self.filter_data()
        except Exception:
            pass

    def update_theme_labels(self, is_light: bool):
        """Adjust widgets when theme changes.

        - In light mode: use user-configured light palette for table, buttons and stats.
        - In dark mode: restore the previous dark styles.
        """
        # Remember the active theme so filter_data() picks the right
        # foreground/background palette every time it re-renders rows.
        self._light_mode_active = bool(is_light)
        colors = get_light_theme_colors()
        # Foreground color for table items: dark text on light theme, white on dark
        fg_color = QColor(colors["text_primary"]) if is_light else CLR_FG_LIGHT

        # Title color: use dark primary text in light mode, original blue in dark
        try:
            for lbl in self.findChildren(QLabel):
                if lbl.text().strip() == "Production & Percentages":
                    if is_light:
                        lbl.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {colors['text_primary']};")
                    else:
                        lbl.setStyleSheet("font-size: 16px; font-weight: bold; color: #4aa3ff;")
                    break
        except Exception:
            pass

        # Light-mode stylesheet to match Downtime visuals
        light_bg_css = (
            f' QTableWidget {{ background-color: {colors["surface_bg"]}; gridline-color: {colors["border"]}; border: 1px solid {colors["border"]}; }} '
            f' QHeaderView::section {{ background-color: {light_header_bg(colors)}; color: {light_header_fg(colors)}; border: 1px solid {colors["border"]}; padding: 6px; }} '
        )

        apply_table_theme(
            self,
            is_light,
            light_append_css=light_bg_css,
            adaptive_fg_by_bg=True,
            adaptive_default_fg=fg_color,
        )

        # Re-render rows so each cell picks up the new theme's text colour
        # (apply_table_theme alone updates existing items but our render
        # also paints status-driven badges; safer to repopulate).
        try:
            self.filter_data()
        except Exception:
            pass

        # Adjust Regular / OT button colors to match light theme preferences
        try:
            if is_light:
                if self.current_mode == 'reg':
                    self.btn_reg.setStyleSheet("""
                        QPushButton { background-color: """ + colors["accent"] + """; color: white; border: none; border-radius: 4px; font-weight: bold; }
                    """)
                    self.btn_ot.setStyleSheet(
                        f"QPushButton {{ background-color: {colors['button_bg']}; color: {colors['text_primary']}; "
                        f"border: 1px solid {colors['border']}; border-radius: 4px; font-weight: bold; }}"
                    )
                else:
                    self.btn_ot.setStyleSheet("""
                        QPushButton { background-color: #FF9800; color: white; border: none; border-radius: 4px; font-weight: bold; }
                    """)
                    self.btn_reg.setStyleSheet(
                        f"QPushButton {{ background-color: {colors['button_bg']}; color: {colors['text_primary']}; "
                        f"border: 1px solid {colors['border']}; border-radius: 4px; font-weight: bold; }}"
                    )

                stat_light_style = (
                    f'padding: 6px 12px; border: 1px solid {colors["border"]}; '
                    f'border-radius: 4px; background-color: {colors["surface_bg"]}; color: {colors["text_primary"]}; font-size: 11px;'
                )
                for stat in [self.stats_avg, self.stats_total, self.stats_ok, self.stats_low]:
                    try:
                        stat.setStyleSheet(stat_light_style)
                    except Exception:
                        pass
            else:
                # Dark-mode: restore previously saved styles for stats and buttons
                for stat in [self.stats_avg, self.stats_total, self.stats_ok, self.stats_low]:
                    try:
                        base = getattr(stat, '_saved_style', '')
                        if base:
                            stat.setStyleSheet(base)
                    except Exception:
                        pass

                # Restore regular/ot button styles according to mode
                if self.current_mode == 'reg':
                    self.btn_reg.setStyleSheet("""
                        QPushButton { background-color: #4aa3ff; color: white; border: none; border-radius: 4px; font-weight: bold; }
                    """)
                    self.btn_ot.setStyleSheet("""
                        QPushButton { background-color: #3c3c3c; color: white; border: 1px solid #5a5a5a; border-radius: 4px; font-weight: bold; }
                        QPushButton:hover { background-color: #4a4a4a; }
                    """)
                else:
                    self.btn_ot.setStyleSheet("""
                        QPushButton { background-color: #FF9800; color: white; border: none; border-radius: 4px; font-weight: bold; }
                    """)
                    self.btn_reg.setStyleSheet("""
                        QPushButton { background-color: #3c3c3c; color: white; border: 1px solid #5a5a5a; border-radius: 4px; font-weight: bold; }
                        QPushButton:hover { background-color: #4a4a4a; }
                    """)
        except Exception:
            pass


