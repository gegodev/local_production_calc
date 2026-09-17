from __future__ import annotations

from .theme_palette import apply_fluent_modal_palette

import random

from PySide6.QtCore import Qt, Signal, QThread, QSize
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QGridLayout, QLabel, QPushButton, QFrame,
    QColorDialog, QMessageBox, QCheckBox, QComboBox, QHBoxLayout,
    QSizePolicy, QToolButton,
)

from sync.app_config import load_config, save_config
from db.database import discover_and_merge_background_dbs


DEFAULT_LIGHT_COLORS = {
    "base_bg": "#F6F8FA",
    "surface_bg": "#FFFFFF",
    "text_primary": "#1F2328",
    "text_muted": "#656D76",
    "border": "#D0D7DE",
    "accent": "#0969DA",
    "selection_bg": "#DDF4FF",
    "button_bg": "#EAEEF2",
}


# Curated light-mode palettes. Each preset is a full color set — text kept dark
# enough for readable contrast on the chosen background.
PALETTE_PRESETS: dict[str, dict[str, str]] = {
    "GitHub Light": dict(DEFAULT_LIGHT_COLORS),
    "Warm Cream": {
        "base_bg": "#FBF7EF", "surface_bg": "#FFFDF7",
        "text_primary": "#2A241A", "text_muted": "#6B5E4A",
        "border": "#E3D9C4", "accent": "#B45309",
        "selection_bg": "#FBE8C4", "button_bg": "#F1E8D3",
    },
    "Mint Fresh": {
        "base_bg": "#EFFBF5", "surface_bg": "#FFFFFF",
        "text_primary": "#0B2A20", "text_muted": "#4B6B5E",
        "border": "#C2E6D6", "accent": "#0F766E",
        "selection_bg": "#C9F0E0", "button_bg": "#DEEFE6",
    },
    "Soft Lavender": {
        "base_bg": "#F4F1FB", "surface_bg": "#FFFFFF",
        "text_primary": "#22163E", "text_muted": "#5E557A",
        "border": "#D7CEEC", "accent": "#7C3AED",
        "selection_bg": "#E3D6FB", "button_bg": "#E8E2F4",
    },
    "Rose Blush": {
        "base_bg": "#FDF2F6", "surface_bg": "#FFFFFF",
        "text_primary": "#2D0E1F", "text_muted": "#775061",
        "border": "#F0CFDC", "accent": "#BE185D",
        "selection_bg": "#FBD9E7", "button_bg": "#F4DDE5",
    },
    "Ocean Breeze": {
        "base_bg": "#EEF6FB", "surface_bg": "#FFFFFF",
        "text_primary": "#0E2636", "text_muted": "#4A6A7C",
        "border": "#C7DEEC", "accent": "#0284C7",
        "selection_bg": "#CDE8F7", "button_bg": "#DCEAF2",
    },
    "Forest Sage": {
        "base_bg": "#F1F6EE", "surface_bg": "#FFFFFF",
        "text_primary": "#1B2A14", "text_muted": "#55664A",
        "border": "#CFDCC1", "accent": "#2F855A",
        "selection_bg": "#D9EAC7", "button_bg": "#E1EAD6",
    },
    "Slate Steel": {
        "base_bg": "#EFF2F6", "surface_bg": "#FFFFFF",
        "text_primary": "#1A2330", "text_muted": "#5B6572",
        "border": "#CBD3DD", "accent": "#475569",
        "selection_bg": "#D8DFE8", "button_bg": "#E2E7ED",
    },
    "Peach Sorbet": {
        "base_bg": "#FFF3EB", "surface_bg": "#FFFFFF",
        "text_primary": "#2E180A", "text_muted": "#7A5640",
        "border": "#F3D3BA", "accent": "#EA580C",
        "selection_bg": "#FCDBBE", "button_bg": "#F5DFC9",
    },
    "Honey Gold": {
        "base_bg": "#FAF5E6", "surface_bg": "#FFFDF5",
        "text_primary": "#2A2108", "text_muted": "#6B5F33",
        "border": "#EAD79A", "accent": "#CA8A04",
        "selection_bg": "#F5E7B0", "button_bg": "#EEE3B8",
    },
    "Arctic": {
        "base_bg": "#F2F8FA", "surface_bg": "#FFFFFF",
        "text_primary": "#0D2430", "text_muted": "#4A6976",
        "border": "#CCDDE4", "accent": "#0891B2",
        "selection_bg": "#D5EEF5", "button_bg": "#DDE9EE",
    },
    "Dusty Rose": {
        "base_bg": "#F6EEEF", "surface_bg": "#FFFFFF",
        "text_primary": "#2A1617", "text_muted": "#6F5052",
        "border": "#E6CFD2", "accent": "#9F1239",
        "selection_bg": "#F2D6DB", "button_bg": "#ECD8DA",
    },
    "Graphite Paper": {
        "base_bg": "#F3F4F6", "surface_bg": "#FCFCFD",
        "text_primary": "#111827", "text_muted": "#4B5563",
        "border": "#D1D5DB", "accent": "#111827",
        "selection_bg": "#E5E7EB", "button_bg": "#E5E7EB",
    },
    "Iris Indigo": {
        "base_bg": "#F0F2FB", "surface_bg": "#FFFFFF",
        "text_primary": "#101438", "text_muted": "#4B5280",
        "border": "#CED3F0", "accent": "#4338CA",
        "selection_bg": "#DCE0FB", "button_bg": "#E0E3F3",
    },
    "Lemon Zest": {
        "base_bg": "#FFFBE6", "surface_bg": "#FFFEF5",
        "text_primary": "#2A2600", "text_muted": "#6B6428",
        "border": "#EFE48E", "accent": "#A16207",
        "selection_bg": "#F8EFA8", "button_bg": "#F2EBB3",
    },
}


_FIELD_ORDER = [
    "base_bg",
    "surface_bg",
    "text_primary",
    "text_muted",
    "border",
    "accent",
    "selection_bg",
    "button_bg",
]

_LABELS = {
    "base_bg": "App Background",
    "surface_bg": "Cards & Panels",
    "text_primary": "Main Text",
    "text_muted": "Secondary Text",
    "border": "Lines & Borders",
    "accent": "Primary Action Color",
    "selection_bg": "Selection Highlight",
    "button_bg": "Secondary Buttons",
}


def _wrap_pixmap_to_icon(pm):
    from PySide6.QtGui import QIcon
    return QIcon(pm)


class _DbScanThread(QThread):
    done = Signal(bool, str)

    def run(self):
        try:
            msg = discover_and_merge_background_dbs(max_seconds=20)
            self.done.emit(True, msg or "No new DB data found to merge.")
        except Exception as exc:
            self.done.emit(False, str(exc))


# ── Live preview widget ─────────────────────────────────────────────────────
class _ThemePreview(QFrame):
    """A miniature dashboard mock-up that re-skins itself when colors change.

    Intentionally compact and built from plain QSS-styled widgets so it stays
    pixel-cheap and updates instantly. Not interactive."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("themePreviewRoot")
        self.setMinimumSize(280, 380)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._build()

    def _build(self):
        from .tabler_icons import TablerIcon
        self._TI = TablerIcon
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        # PREVIEW header label outside the mock-up frame.
        self._hdr = QLabel("PREVIEW")
        self._hdr.setObjectName("previewHdr")
        root.addWidget(self._hdr)

        # Card container shaped like a tiny app window.
        self._mock = QFrame()
        self._mock.setObjectName("previewMock")
        mock_lay = QHBoxLayout(self._mock)
        mock_lay.setContentsMargins(0, 0, 0, 0)
        mock_lay.setSpacing(0)

        # ── Sidebar with mini Tabler icons ──
        self._sidebar = QFrame()
        self._sidebar.setObjectName("previewSidebar")
        self._sidebar.setFixedWidth(42)
        sb_lay = QVBoxLayout(self._sidebar)
        sb_lay.setContentsMargins(4, 14, 4, 14)
        sb_lay.setSpacing(8)

        self._sidebar_icons = []  # (button, svg, active?)
        sidebar_specs = [
            ("tabler_pencil_plus.svg", True),
            ("tabler_brand_databricks.svg", False),
            ("tabler_file_analytics.svg", False),
            ("tabler_history.svg", False),
            ("tabler_file_time.svg", False),
            ("tabler_database.svg", False),
            ("tabler_settings.svg", False),
        ]
        for svg, active in sidebar_specs:
            btn = QToolButton()
            btn.setEnabled(False)
            btn.setFixedSize(28, 26)
            btn.setIconSize(QSize(14, 14))
            btn.setObjectName("previewSbActive" if active else "previewSbItem")
            sb_lay.addWidget(btn, 0, Qt.AlignmentFlag.AlignHCenter)
            self._sidebar_icons.append((btn, svg, active))
        sb_lay.addStretch()
        mock_lay.addWidget(self._sidebar)

        # ── Main content ──
        self._main = QFrame()
        self._main.setObjectName("previewMain")
        m_lay = QVBoxLayout(self._main)
        m_lay.setContentsMargins(14, 14, 14, 14)
        m_lay.setSpacing(10)

        # Top header: title + bell + dots
        top_row = QHBoxLayout()
        top_row.setSpacing(8)
        self._page_title = QLabel("Dashboard")
        self._page_title.setObjectName("previewPageTitle")
        top_row.addWidget(self._page_title)
        top_row.addStretch()
        self._bell_btn = QToolButton()
        self._bell_btn.setEnabled(False)
        self._bell_btn.setFixedSize(20, 20)
        self._bell_btn.setIconSize(QSize(14, 14))
        self._bell_btn.setObjectName("previewSbItem")
        self._dots_lbl = QLabel("⋮")
        self._dots_lbl.setObjectName("previewDots")
        top_row.addWidget(self._bell_btn)
        top_row.addWidget(self._dots_lbl)
        m_lay.addLayout(top_row)

        # ── "Today's Performance" card ──
        perf_card = QFrame()
        perf_card.setObjectName("previewPerfCard")
        perf_lay = QVBoxLayout(perf_card)
        perf_lay.setContentsMargins(10, 10, 10, 10)
        perf_lay.setSpacing(8)

        self._perf_title = QLabel("Today's Performance")
        self._perf_title.setObjectName("previewSectionTitle")
        perf_lay.addWidget(self._perf_title)

        gauge_row = QHBoxLayout()
        gauge_row.setSpacing(10)
        self._gauge = _MiniSemiGauge()
        self._gauge.setFixedSize(108, 70)
        gauge_row.addWidget(self._gauge, 0)

        stats_col = QVBoxLayout()
        stats_col.setSpacing(3)
        self._stat1 = QLabel("Daily Production")
        self._stat1.setObjectName("previewStatLbl")
        self._stat1v = QLabel("96.78%")
        self._stat1v.setObjectName("previewStatVal")
        self._stat2 = QLabel("Cases")
        self._stat2.setObjectName("previewStatLbl")
        self._stat2v = QLabel("96.78%")
        self._stat2v.setObjectName("previewStatVal")
        self._stat3 = QLabel("Downtime")
        self._stat3.setObjectName("previewStatLbl")
        self._stat3v = QLabel("0.00%")
        self._stat3v.setObjectName("previewStatValAlt")
        for w in (self._stat1, self._stat1v, self._stat2, self._stat2v,
                  self._stat3, self._stat3v):
            stats_col.addWidget(w)
        stats_col.addStretch()
        gauge_row.addLayout(stats_col, 1)
        perf_lay.addLayout(gauge_row)
        m_lay.addWidget(perf_card)

        # ── Recent Cases card ──
        recent_card = QFrame()
        recent_card.setObjectName("previewRecent")
        rc_lay = QVBoxLayout(recent_card)
        rc_lay.setContentsMargins(10, 8, 10, 8)
        rc_lay.setSpacing(4)

        self._recent_title = QLabel("Recent Cases")
        self._recent_title.setObjectName("previewSectionTitle")
        rc_lay.addWidget(self._recent_title)

        # Column header
        hdr_row = QHBoxLayout()
        hdr_row.setSpacing(4)
        for col in ("Case ID", "Doctor", "Eff %", "UE"):
            h = QLabel(col)
            h.setObjectName("previewTblHdr")
            hdr_row.addWidget(h, 1)
        rc_lay.addLayout(hdr_row)

        # Two sample rows.
        self._eff_cells = []
        for case_id, doc, eff in (("123", "Dr. Smith", "199%"),
                                  ("124", "Dr. Johnson", "201%")):
            row = QHBoxLayout()
            row.setSpacing(4)
            cid = QLabel(case_id); cid.setObjectName("previewCellBold")
            doc_lbl = QLabel(doc); doc_lbl.setObjectName("previewCell")
            eff_lbl = QLabel(eff); eff_lbl.setObjectName("previewCellEff")
            ue_lbl = QLabel("1.21" if case_id == "123" else "1.18")
            ue_lbl.setObjectName("previewCell")
            row.addWidget(cid, 1)
            row.addWidget(doc_lbl, 1)
            row.addWidget(eff_lbl, 1)
            row.addWidget(ue_lbl, 1)
            rc_lay.addLayout(row)
            self._eff_cells.append(eff_lbl)
        m_lay.addWidget(recent_card)

        # ── Primary + secondary buttons ──
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self._btn_primary = QLabel("  Primary Action  ")
        self._btn_primary.setObjectName("previewPrimary")
        self._btn_primary.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._btn_secondary = QLabel("  Secondary Action  ")
        self._btn_secondary.setObjectName("previewSecondary")
        self._btn_secondary.setAlignment(Qt.AlignmentFlag.AlignCenter)
        btn_row.addWidget(self._btn_primary, 1)
        btn_row.addWidget(self._btn_secondary, 1)
        m_lay.addLayout(btn_row)
        m_lay.addStretch()
        mock_lay.addWidget(self._main, 1)

        root.addWidget(self._mock, 1)

        # Tip card (styled like the other modal Tip blocks).
        tip = QFrame()
        tip.setObjectName("previewTipCard")
        tip_lay = QHBoxLayout(tip)
        tip_lay.setContentsMargins(12, 10, 12, 10)
        tip_lay.setSpacing(8)
        bulb = QToolButton()
        bulb.setEnabled(False)
        try:
            bulb.setIcon(self._TI("tabler_bulb.svg").icon(color=QColor("#388BFD")))
        except Exception:
            pass
        bulb.setIconSize(QSize(16, 16))
        bulb.setStyleSheet("background: transparent; border: none;")
        tip_col = QVBoxLayout()
        tip_col.setSpacing(1)
        tip_title = QLabel("Tip")
        tip_title.setObjectName("previewTipTitle")
        tip_body = QLabel(
            "Pick a preset or customize each color to match your brand "
            "and improve visual clarity."
        )
        tip_body.setObjectName("previewTipBody")
        tip_body.setWordWrap(True)
        tip_col.addWidget(tip_title)
        tip_col.addWidget(tip_body)
        tip_lay.addWidget(bulb, 0, Qt.AlignTop)
        tip_lay.addLayout(tip_col, 1)
        root.addWidget(tip)

    def apply_colors(self, c: dict):
        accent = c.get("accent", "#0969DA")
        text_primary = c.get("text_primary", "#1F2328")
        text_muted = c.get("text_muted", "#656D76")
        base_bg = c.get("base_bg", "#F6F8FA")
        surface_bg = c.get("surface_bg", "#FFFFFF")
        border = c.get("border", "#D0D7DE")
        button_bg = c.get("button_bg", "#EAEEF2")

        self._gauge.set_colors(accent, text_primary, border)

        # Re-color sidebar Tabler icons.
        for btn, svg, active in self._sidebar_icons:
            color = accent if active else text_muted
            try:
                btn.setIcon(self._TI(svg).icon(color=QColor(color)))
            except Exception:
                pass
        try:
            self._bell_btn.setIcon(
                self._TI("tabler_alert_triangle.svg").icon(color=QColor(text_muted))
            )
        except Exception:
            pass

        css = f"""
        #themePreviewRoot {{
            background: #0D1117;
            border: 1px solid #21262D;
            border-radius: 12px;
        }}
        #previewHdr {{
            color: #8B949E; font-size: 10px; font-weight: 800;
            letter-spacing: 1.2px;
        }}
        #previewTipCard {{
            background: rgba(56,139,253,0.08);
            border: 1px solid rgba(56,139,253,0.30);
            border-radius: 10px;
        }}
        #previewTipTitle {{
            color: #58A6FF; font-size: 11px; font-weight: 700;
            background: transparent;
        }}
        #previewTipBody {{
            color: #C9D1D9; font-size: 11px; background: transparent;
        }}
        #previewMock {{
            background: {base_bg};
            border: 1px solid {border};
            border-radius: 10px;
        }}
        #previewSidebar {{
            background: {surface_bg};
            border-right: 1px solid {border};
            border-top-left-radius: 10px;
            border-bottom-left-radius: 10px;
        }}
        #previewSbItem {{
            background: transparent;
            border: none;
            border-radius: 6px;
        }}
        #previewSbActive {{
            background: rgba(0,0,0,0.06);
            border: 1px solid {border};
            border-radius: 6px;
        }}
        #previewMain {{
            background: {base_bg};
            border-top-right-radius: 10px;
            border-bottom-right-radius: 10px;
        }}
        #previewPageTitle {{
            color: {text_primary};
            font-size: 13px; font-weight: 700;
        }}
        #previewDots {{
            color: {text_muted}; font-size: 14px; font-weight: 700;
            padding: 0 4px;
        }}
        #previewPerfCard, #previewRecent {{
            background: {surface_bg};
            border: 1px solid {border};
            border-radius: 8px;
        }}
        #previewSectionTitle {{
            color: {text_primary};
            font-size: 11px; font-weight: 700;
        }}
        #previewStatLbl {{ color: {text_muted}; font-size: 9px; }}
        #previewStatVal {{
            color: {accent}; font-size: 11px; font-weight: 700;
        }}
        #previewStatValAlt {{
            color: #E89720; font-size: 11px; font-weight: 700;
        }}
        #previewTblHdr {{
            color: {text_muted}; font-size: 9px; font-weight: 700;
        }}
        #previewCellBold {{
            color: {text_primary};
            font-size: 10px; font-weight: 700;
        }}
        #previewCell {{ color: {text_muted}; font-size: 10px; }}
        #previewCellEff {{
            color: #2EA043; font-size: 10px; font-weight: 700;
        }}
        #previewPrimary {{
            background: {accent}; color: #FFFFFF;
            font-size: 11px; font-weight: 700;
            padding: 6px 10px; border-radius: 6px;
        }}
        #previewSecondary {{
            background: {button_bg};
            color: {text_primary};
            font-size: 11px; font-weight: 600;
            padding: 6px 10px; border-radius: 6px;
            border: 1px solid {border};
        }}
        """
        self.setStyleSheet(css)


class _MiniSemiGauge(QFrame):
    """Tiny semi-circle gauge — mirrors the real GaugeWidget in
    gauge_widget.py but at a fixed sample value (102% by default)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._accent = QColor("#0969DA")
        self._text_color = QColor("#1F2328")
        self._border = QColor("#D0D7DE")
        self._value = 102.0  # sample value for the preview
        self.setAttribute(Qt.WA_StyledBackground, False)

    def set_colors(self, accent: str, text_primary: str, border: str):
        self._accent = QColor(accent or "#0969DA")
        self._text_color = QColor(text_primary or "#1F2328")
        self._border = QColor(border or "#D0D7DE")
        self.update()

    def paintEvent(self, _):
        from PySide6.QtGui import QPainter, QPen, QFont
        from PySide6.QtCore import QRectF
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        margin = 4
        avail_w = self.width() - margin * 2
        avail_h = (self.height() - margin) * 2
        side = max(0, min(avail_w, avail_h))
        x = (self.width() - side) / 2
        rect = QRectF(x, margin, side, side)
        thickness = max(6, side // 14)
        rect_inner = rect.adjusted(thickness / 2, thickness / 2,
                                    -thickness / 2, -thickness / 2)

        START_ANGLE = 180 * 16
        FULL_SWEEP = -180 * 16

        bg_pen = QPen(self._border)
        bg_pen.setWidth(thickness)
        bg_pen.setCapStyle(Qt.RoundCap)
        p.setPen(bg_pen)
        p.drawArc(rect_inner, START_ANGLE, FULL_SWEEP)

        capped = max(0.0, min(self._value, 200.0))
        fg_pen = QPen(self._accent)
        fg_pen.setWidth(thickness)
        fg_pen.setCapStyle(Qt.RoundCap)
        p.setPen(fg_pen)
        ratio = min(1.0, capped / 100.0)
        p.drawArc(rect_inner, START_ANGLE, int(FULL_SWEEP * ratio))

        # Center text
        arc_diam_y = rect.center().y()
        big = QFont(self.font())
        big.setPixelSize(max(13, int(side / 7)))
        big.setWeight(QFont.DemiBold)
        p.setPen(self._text_color)
        p.setFont(big)
        big_h = side * 0.22
        big_rect = QRectF(rect.x(), arc_diam_y - big_h - 2,
                          rect.width(), big_h)
        p.drawText(big_rect, Qt.AlignHCenter | Qt.AlignBottom,
                   f"{int(round(self._value))}%")

        small = QFont(self.font())
        small.setPixelSize(max(8, int(side / 18)))
        p.setFont(small)
        # Subtitle picks a slightly muted tone derived from text_color.
        muted = QColor(self._text_color)
        muted.setAlpha(140)
        p.setPen(muted)
        sub_rect = QRectF(rect.x(), arc_diam_y - big_h - 2 + big_h - 4,
                          rect.width(), side * 0.13)
        p.drawText(sub_rect, Qt.AlignHCenter | Qt.AlignTop, "of target")


# ── Color row helper widget ─────────────────────────────────────────────────
class _ColorRow(QWidget):
    edit_requested = Signal(str)  # emits color key

    def __init__(self, key: str, label: str, parent=None):
        super().__init__(parent)
        self._key = key

        self.setObjectName("colorRowWrap")
        self.setStyleSheet(
            "#colorRowWrap { background: #161B22; border: 1px solid #21262D;"
            " border-radius: 8px; }"
        )

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(10)

        self._swatch = QFrame()
        self._swatch.setFixedSize(28, 28)
        self._swatch.setStyleSheet(
            "background: #FFFFFF; border: 1px solid #30363D; border-radius: 6px;"
        )
        lay.addWidget(self._swatch)

        self._label = QLabel(label)
        self._label.setStyleSheet(
            "color: #C9D1D9; font-size: 11px; font-weight: 600;"
            " background: transparent;"
        )
        lay.addWidget(self._label, 1)

        self._hex = QLabel("#FFFFFF")
        # Hex label uses palette text — muted was too pale to read in light
        # mode on the user-configured surface bg.
        try:
            from qfluentwidgets.common.style_sheet import isDarkTheme
            from .theme_palette import palette as _hp
            _hex_col = _hp(not isDarkTheme())["text_2"]
        except Exception:
            _hex_col = "#8B949E"
        self._hex.setStyleSheet(
            f"color: {_hex_col}; font-size: 10px;"
            f" font-family: 'Consolas','Menlo',monospace; background: transparent;"
        )
        lay.addWidget(self._hex)

        self._edit_btn = QToolButton()
        self._edit_btn.setAutoRaise(True)
        self._edit_btn.setCursor(Qt.PointingHandCursor)
        self._edit_btn.setFixedSize(28, 28)
        self._edit_btn.setToolTip("Pick a color")
        try:
            from .tabler_icons import TablerIcon
            self._edit_btn.setIcon(TablerIcon("tabler_pencil.svg").icon(color=QColor("#FFFFFF")))
            self._edit_btn.setIconSize(QSize(14, 14))
        except Exception:
            self._edit_btn.setText("✎")
        # Filled accent circle bg — matches the mockup.
        self._edit_btn.setStyleSheet(
            "QToolButton { background: #1e63e4; border: 0;"
            " border-radius: 14px; padding: 0; }"
            "QToolButton:hover { background: #2a73f3; }"
        )
        self._edit_btn.clicked.connect(lambda: self.edit_requested.emit(self._key))
        lay.addWidget(self._edit_btn)

    def set_color(self, hex_color: str):
        self._swatch.setStyleSheet(
            f"background: {hex_color}; border: 1px solid #30363D; border-radius: 4px;"
        )
        self._hex.setText(hex_color.upper())


# ── Main inner widget ───────────────────────────────────────────────────────
class ThemeConfigTab(QWidget):
    theme_colors_changed = Signal(dict)
    accepted = Signal()

    def __init__(self):
        super().__init__()
        self._colors = dict(DEFAULT_LIGHT_COLORS)
        self._auto_discover_dbs = True
        self._rows: dict[str, _ColorRow] = {}
        self._scan_thread: _DbScanThread | None = None
        self._load_from_config()
        self._init_ui()
        self._refresh_swatches()

    def _load_from_config(self):
        cfg = load_config()
        incoming = cfg.get("light_theme_colors", {}) or {}
        if isinstance(incoming, dict):
            for key, val in incoming.items():
                if key in self._colors and isinstance(val, str) and val.strip():
                    self._colors[key] = val.strip().upper()
        self._auto_discover_dbs = bool(cfg.get("auto_discover_dbs", True))
        self._active_preset_name = cfg.get("light_palette_name") or "GitHub Light"

    def _init_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        # ── Left column: live preview ──
        self._preview = _ThemePreview()
        root.addWidget(self._preview, 1)

        # ── Right column: controls ──
        right_col = QVBoxLayout()
        right_col.setSpacing(10)
        right_col.setContentsMargins(0, 0, 0, 0)

        # Preset chooser row.
        preset_row = QHBoxLayout()
        preset_row.setSpacing(8)
        preset_lbl = QLabel("Preset")
        preset_lbl.setStyleSheet(
            "color: #C9D1D9; font-size: 11px; font-weight: 700;"
            " background: transparent;"
        )
        preset_row.addWidget(preset_lbl)
        self.cmb_preset = QComboBox()
        self.cmb_preset.addItems(list(PALETTE_PRESETS.keys()))
        self.cmb_preset.setMinimumWidth(160)
        self.cmb_preset.setFixedHeight(30)
        try:
            from .widgets import _icon_url as _icu
            _chev = _icu("tabler_chevron_down.svg")
        except Exception:
            _chev = ""
        self.cmb_preset.setStyleSheet(
            "QComboBox { background: #161B22; border: 1px solid #30363D;"
            "  border-radius: 6px; padding: 4px 24px 4px 10px; color: #E6EDF3;"
            "  font-size: 11px; }"
            "QComboBox::drop-down { subcontrol-origin: padding;"
            "  subcontrol-position: right center; width: 22px; border: none; }"
            f"QComboBox::down-arrow {{ image: url({_chev});"
            "  width: 12px; height: 12px; }"
        )
        # Decorate each preset with a tiny colored circle icon matching its
        # accent — gives the dropdown a glanceable palette feel.
        from PySide6.QtGui import QPixmap, QPainter as _QPnt, QBrush as _QBr
        for i, (pname, pdata) in enumerate(PALETTE_PRESETS.items()):
            pm = QPixmap(14, 14)
            pm.fill(Qt.transparent)
            pn = _QPnt(pm)
            pn.setRenderHint(_QPnt.Antialiasing)
            pn.setPen(Qt.NoPen)
            pn.setBrush(_QBr(QColor(pdata.get("accent", "#888"))))
            pn.drawEllipse(1, 1, 12, 12)
            pn.end()
            self.cmb_preset.setItemIcon(i, _wrap_pixmap_to_icon(pm))
        self.cmb_preset.setIconSize(QSize(14, 14))

        idx = self.cmb_preset.findText(self._active_preset_name)
        if idx >= 0:
            self.cmb_preset.setCurrentIndex(idx)
        # Selecting a preset previews it instantly. Changes only persist
        # once the user clicks "Save & Apply" / "Save".
        self.cmb_preset.currentIndexChanged.connect(lambda _i: self._apply_preset())
        preset_row.addWidget(self.cmb_preset, 1)

        right_col.addLayout(preset_row)

        # Color rows.
        rows_wrap = QWidget()
        rows_lay = QVBoxLayout(rows_wrap)
        rows_lay.setContentsMargins(0, 4, 0, 4)
        rows_lay.setSpacing(2)
        for key in _FIELD_ORDER:
            row = _ColorRow(key, _LABELS.get(key, key))
            row.edit_requested.connect(self._pick_color)
            rows_lay.addWidget(row)
            self._rows[key] = row
        right_col.addWidget(rows_wrap)

        # Reset / Generate / Save & Apply row.
        ops_row = QHBoxLayout()
        ops_row.setSpacing(8)
        try:
            from .tabler_icons import TablerIcon as _TI_ops
        except Exception:
            _TI_ops = None

        try:
            from qfluentwidgets.common.style_sheet import isDarkTheme
            from .theme_palette import palette as _tcp
            _icol = _tcp(not isDarkTheme())["text"]
        except Exception:
            _icol = "#E6EDF3"

        btn_reset = QPushButton("  Reset Defaults")
        btn_reset.setStyleSheet(self._secondary_btn_css())
        btn_reset.setFixedHeight(32)
        btn_reset.setCursor(Qt.PointingHandCursor)
        if _TI_ops is not None:
            btn_reset.setIcon(_TI_ops("tabler_refresh.svg").icon(color=QColor(_icol)))
            btn_reset.setIconSize(QSize(14, 14))
        btn_reset.clicked.connect(self._reset_defaults)

        btn_gen = QPushButton("  Generate Palette")
        btn_gen.setStyleSheet(self._secondary_btn_css())
        btn_gen.setFixedHeight(32)
        btn_gen.setCursor(Qt.PointingHandCursor)
        if _TI_ops is not None:
            btn_gen.setIcon(_TI_ops("tabler_image_generation.svg").icon(color=QColor(_icol)))
            btn_gen.setIconSize(QSize(14, 14))
        btn_gen.clicked.connect(self._generate_palette)

        btn_apply = QPushButton("  Save & Apply")
        btn_apply.setStyleSheet(self._primary_btn_css())
        btn_apply.setFixedHeight(32)
        btn_apply.setCursor(Qt.PointingHandCursor)
        if _TI_ops is not None:
            btn_apply.setIcon(_TI_ops("tabler_circle_check.svg").icon(color=QColor("#FFFFFF")))
            btn_apply.setIconSize(QSize(14, 14))
        btn_apply.clicked.connect(self._save_and_apply)
        ops_row.addWidget(btn_reset)
        ops_row.addWidget(btn_gen)
        ops_row.addStretch()
        ops_row.addWidget(btn_apply)
        right_col.addLayout(ops_row)

        # Divider then Data Discovery.
        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet("background: #21262D; border: none;")
        right_col.addSpacing(6)
        right_col.addWidget(divider)
        right_col.addSpacing(4)

        try:
            from .tabler_icons import TablerIcon as _TI_dd
        except Exception:
            _TI_dd = None

        # Title row — database icon + label.
        dd_title_row = QHBoxLayout()
        dd_title_row.setSpacing(6)
        dd_icon = QToolButton()
        dd_icon.setEnabled(False)
        if _TI_dd is not None:
            dd_icon.setIcon(_TI_dd("tabler_database.svg").icon(color=QColor("#8B949E")))
        dd_icon.setIconSize(QSize(14, 14))
        dd_icon.setStyleSheet("background: transparent; border: none;")
        dd_title = QLabel("Data Discovery")
        dd_title.setStyleSheet(
            "color: #C9D1D9; font-size: 11px; font-weight: 700;"
            " background: transparent;"
        )
        dd_title_row.addWidget(dd_icon)
        dd_title_row.addWidget(dd_title)
        dd_title_row.addStretch()
        right_col.addLayout(dd_title_row)

        # Toggle + label + Scan Now button.
        dd_row = QHBoxLayout()
        dd_row.setSpacing(8)
        try:
            from .toggle_switch import ToggleSwitch
            self._dd_toggle = ToggleSwitch()
            # Shrink instance: 50x25 → 36x18.
            self._dd_toggle._W = 36
            self._dd_toggle._H = 18
            self._dd_toggle._CIRCLE = 12
            self._dd_toggle._ON_X = self._dd_toggle._W - self._dd_toggle._CIRCLE - self._dd_toggle._OFF_X
            self._dd_toggle.setFixedSize(self._dd_toggle._W, self._dd_toggle._H)
            # Re-seed the circle position using the *new* dimensions so the
            # initial paint isn't using the class-level (50px) ON_X.
            self._dd_toggle._circle_position = (
                self._dd_toggle._ON_X if self._dd_toggle._checked
                else self._dd_toggle._OFF_X
            )
            self._dd_toggle.update()
            self._dd_toggle.setChecked(self._auto_discover_dbs)
        except Exception:
            # Fallback to a checkbox if ToggleSwitch isn't available.
            self._dd_toggle = QCheckBox()
            self._dd_toggle.setChecked(self._auto_discover_dbs)
        # Compatibility alias — _save_and_apply / commit read this name.
        self.chk_auto_discover = self._dd_toggle

        dd_label = QLabel("Auto-discover and merge DB files on app startup")
        dd_label.setStyleSheet(
            "color: #C9D1D9; font-size: 11px; background: transparent;"
        )
        dd_row.addWidget(self._dd_toggle, 0)
        dd_row.addWidget(dd_label, 1)

        btn_scan = QPushButton("  Scan Now")
        btn_scan.setFixedHeight(30)
        btn_scan.setCursor(Qt.PointingHandCursor)
        # Outlined accent style — matches the mockup's blue text button.
        btn_scan.setStyleSheet(
            "QPushButton { background: transparent; border: 1px solid #1e63e4;"
            "  color: #58A6FF; border-radius: 8px; padding: 4px 14px;"
            "  font-weight: 700; font-size: 11px; }"
            "QPushButton:hover { background: rgba(30,99,228,0.10); }"
        )
        if _TI_dd is not None:
            btn_scan.setIcon(_TI_dd("tabler_search.svg").icon(color=QColor("#58A6FF")))
            btn_scan.setIconSize(QSize(14, 14))
        btn_scan.clicked.connect(self._scan_now)
        dd_row.addWidget(btn_scan)
        right_col.addLayout(dd_row)

        self.lbl_scan_status = QLabel("")
        self.lbl_scan_status.setStyleSheet(
            "color: #8B949E; font-size: 10px; background: transparent;"
        )
        right_col.addWidget(self.lbl_scan_status)

        right_col.addStretch()

        root.addLayout(right_col, 1)

    def _secondary_btn_css(self, size=11):
        return (
            "QPushButton { background: transparent; border: 1px solid #30363D;"
            f"  border-radius: 8px; color: #E6EDF3; font-weight: 700;"
            f"  font-size: {size}px; padding: 6px 14px; }}"
            "QPushButton:hover { background: rgba(255,255,255,0.05);"
            "  border-color: #58606A; }"
        )

    def _primary_btn_css(self):
        return (
            "QPushButton { background: #1e63e4; border: 1px solid #1e63e4;"
            "  color: white; border-radius: 8px; padding: 6px 16px;"
            "  font-weight: 700; font-size: 11px; }"
            "QPushButton:hover { background: #2a73f3; }"
            "QPushButton:pressed { background: #154fbb; }"
        )

    def _refresh_swatches(self):
        for key, color in self._colors.items():
            row = self._rows.get(key)
            if row:
                row.set_color(color)
        if hasattr(self, "_preview"):
            self._preview.apply_colors(self._colors)

    def _pick_color(self, key: str):
        current = QColor(self._colors.get(key, "#FFFFFF"))
        picked = QColorDialog.getColor(current, self, f"Choose {_LABELS.get(key, key)}")
        if not picked.isValid():
            return
        self._colors[key] = picked.name().upper()
        self._refresh_swatches()

    def _reset_defaults(self):
        self._colors = dict(DEFAULT_LIGHT_COLORS)
        self._active_preset_name = "GitHub Light"
        idx = self.cmb_preset.findText(self._active_preset_name)
        if idx >= 0:
            self.cmb_preset.setCurrentIndex(idx)
        self._refresh_swatches()

    def _apply_preset(self):
        name = self.cmb_preset.currentText()
        preset = PALETTE_PRESETS.get(name)
        if not preset:
            return
        self._colors = {k: v.upper() for k, v in preset.items()}
        self._active_preset_name = name
        self._refresh_swatches()

    @staticmethod
    def _mix(c1: str, c2: str, t: float) -> str:
        a = QColor(c1)
        b = QColor(c2)
        r = int(a.red() + (b.red() - a.red()) * t)
        g = int(a.green() + (b.green() - a.green()) * t)
        bl = int(a.blue() + (b.blue() - a.blue()) * t)
        return QColor(r, g, bl).name().upper()

    def _generate_palette(self):
        """Build a fresh light palette from a random hue.

        Picks an HSL hue uniformly, then derives a deep accent + tinted
        neutrals. Avoids muddy yellows/greens by skipping a narrow band of
        the hue wheel where dark accents tend to look bilious."""
        # Skip H ∈ (50°, 130°) so accents land in blue/red/violet/rose/etc.
        # — bands that read well as "primary action" colors on light bg.
        for _ in range(8):
            hue = random.randint(0, 359)
            if not (50 <= hue <= 130):
                break

        # Deep, saturated accent for buttons + active highlights.
        accent_q = QColor.fromHsl(hue, random.randint(170, 220),
                                  random.randint(70, 110))
        accent = accent_q.name().upper()

        # Text contrast: pick a near-black with a hint of the same hue.
        text_q = QColor.fromHsl(hue, 40, random.randint(20, 35))
        text_primary = text_q.name().upper()
        text_muted_q = QColor.fromHsl(hue, 30, random.randint(95, 115))
        text_muted = text_muted_q.name().upper()

        # Backgrounds: very light tints of the accent hue.
        base_bg = self._mix(accent, "#FFFFFF", random.uniform(0.90, 0.96))
        surface_bg = "#FFFFFF" if random.random() < 0.65 else self._mix(
            accent, "#FFFFFF", 0.98
        )
        border = self._mix(accent, "#D0D7DE", random.uniform(0.75, 0.90))
        selection_bg = self._mix(accent, "#FFFFFF", random.uniform(0.70, 0.85))
        button_bg = self._mix(accent, "#EAEEF2", random.uniform(0.78, 0.90))

        self._colors = {
            "accent": accent,
            "surface_bg": surface_bg,
            "text_primary": text_primary,
            "text_muted": text_muted,
            "base_bg": base_bg,
            "border": border,
            "selection_bg": selection_bg,
            "button_bg": button_bg,
        }
        # Clear active preset name — this isn't a preset anymore.
        self._active_preset_name = "Custom"
        self._refresh_swatches()

    def commit(self):
        """Persist + emit. Used by external Save button in the wrapper modal."""
        cfg = load_config()
        cfg["light_theme_colors"] = dict(self._colors)
        cfg["light_palette_name"] = (
            getattr(self, "_active_preset_name", None)
            or self.cmb_preset.currentText()
        )
        cfg["auto_discover_dbs"] = bool(self.chk_auto_discover.isChecked())
        save_config(cfg)
        self.theme_colors_changed.emit(dict(self._colors))

    def _save_and_apply(self):
        self.commit()
        # Subtle status update — no modal popup, the parent dialog usually
        # closes right after Save anyway.
        self.lbl_scan_status.setText("Saved and applied.")

    def _scan_now(self):
        if self._scan_thread and self._scan_thread.isRunning():
            return
        self.lbl_scan_status.setText("Scanning…")
        self._scan_thread = _DbScanThread()
        self._scan_thread.done.connect(self._on_scan_done)
        self._scan_thread.start()

    def _on_scan_done(self, ok: bool, message: str):
        self.lbl_scan_status.setText(message if ok else f"Scan failed: {message}")
        self._show_scan_result(ok, message)

    def _show_scan_result(self, ok: bool, message: str):
        """Fluent modal summarizing the scan outcome."""
        try:
            from qfluentwidgets import MessageBoxBase
            from .tabler_icons import TablerIcon
        except Exception:
            return

        # Parse the summary into structured rows. Pattern:
        # "Auto-merge: N source(s), +X cases, +Y OT, +Z downtimes."
        import re
        m = re.search(
            r"(\d+) source.*?\+(\d+) cases.*?\+(\d+) OT.*?\+(\d+) downtime",
            message or "",
        )
        if m:
            stats = {
                "Sources merged": m.group(1),
                "Cases added":    m.group(2),
                "OT cases added": m.group(3),
                "Downtimes added": m.group(4),
            }
            success = True
        else:
            stats = {}
            # When merging found nothing the message comes back empty.
            success = ok and not (message or "").startswith("Scan failed")

        class _ScanSheet(MessageBoxBase):
            def __init__(_s, host):
                super().__init__(host.window() if host is not None else None)
                try:
                    _s.setMaskColor(QColor(0, 0, 0, 170))
                except Exception:
                    pass
                _s.widget.setObjectName("scanCard")
                apply_fluent_modal_palette(_s, "scanCard")
                _s.viewLayout.setContentsMargins(0, 8, 0, 8)
                _s.viewLayout.setSpacing(0)

                def _wrap(child, m=22):
                    w = QWidget()
                    lw = QVBoxLayout(w)
                    lw.setContentsMargins(m, 14, m, 14)
                    lw.setSpacing(8)
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

                # Header: db icon + title + close.
                hdr = QHBoxLayout()
                hdr.setSpacing(10)
                icon_btn = QToolButton()
                icon_btn.setEnabled(False)
                tint = "#388BFD" if ok else "#F85149"
                if stats:
                    tint = "#3FB950"
                icon_btn.setIcon(TablerIcon("tabler_database.svg").icon(color=QColor(tint)))
                icon_btn.setIconSize(QSize(20, 20))
                icon_btn.setStyleSheet(
                    f"background: rgba({QColor(tint).red()},{QColor(tint).green()},{QColor(tint).blue()},0.12);"
                    " border: none; border-radius: 8px; padding: 6px;"
                )
                t_col = QVBoxLayout(); t_col.setSpacing(2)
                if not ok:
                    title_text = "Scan failed"
                    sub_text = "Could not finish the discovery scan."
                elif stats:
                    title_text = "Scan complete"
                    sub_text = "Discovered and merged DB sources from other locations."
                else:
                    title_text = "Nothing to merge"
                    sub_text = "No external DB sources with new data were found."
                ttl = QLabel(title_text)
                ttl.setStyleSheet(
                    "color: #E6EDF3; font-size: 15px; font-weight: 700;"
                    " background: transparent;"
                )
                sub = QLabel(sub_text)
                sub.setWordWrap(True)
                sub.setStyleSheet(
                    "color: #8B949E; font-size: 11px; background: transparent;"
                )
                t_col.addWidget(ttl); t_col.addWidget(sub)

                close_btn = QToolButton()
                close_btn.setIcon(TablerIcon("tabler_x.svg").icon(color=QColor("#8B949E")))
                close_btn.setIconSize(QSize(22, 22))
                close_btn.setCursor(Qt.PointingHandCursor)
                close_btn.setFixedSize(34, 34)
                close_btn.setStyleSheet(
                    "QToolButton { background: transparent; border: none; border-radius: 17px; }"
                    "QToolButton:hover { background: rgba(255,255,255,0.08); }"
                )
                close_btn.clicked.connect(_s.reject)

                hdr.addWidget(icon_btn, 0, Qt.AlignTop)
                hdr.addLayout(t_col, 1)
                hdr.addWidget(close_btn, 0, Qt.AlignTop)
                _s.viewLayout.addWidget(_wrap(hdr))
                _s.viewLayout.addWidget(_div())

                # Body: stat rows or raw message.
                body = QWidget()
                bl = QVBoxLayout(body)
                bl.setContentsMargins(22, 16, 22, 16)
                bl.setSpacing(8)

                if stats:
                    for k, v in stats.items():
                        row = QFrame()
                        row.setStyleSheet(
                            "QFrame { background: #161B22; border: 1px solid #21262D;"
                            " border-radius: 8px; }"
                            "QLabel { background: transparent; border: none; }"
                        )
                        rl = QHBoxLayout(row)
                        rl.setContentsMargins(12, 8, 12, 8)
                        kl = QLabel(k)
                        kl.setStyleSheet("color: #C9D1D9; font-size: 11px; font-weight: 600;")
                        vl = QLabel(str(v))
                        vl.setStyleSheet(
                            "color: #58A6FF; font-size: 13px; font-weight: 700;"
                            " font-family: 'Consolas','Menlo',monospace;"
                        )
                        rl.addWidget(kl, 1)
                        rl.addWidget(vl, 0, Qt.AlignRight)
                        bl.addWidget(row)
                else:
                    info = QLabel(message or "No new DB data found to merge.")
                    info.setWordWrap(True)
                    info.setStyleSheet(
                        "color: #C9D1D9; font-size: 12px; background: transparent;"
                    )
                    bl.addWidget(info)

                _s.viewLayout.addWidget(body)
                _s.viewLayout.addWidget(_div())

                _s.widget.setMinimumWidth(440)

                # Single OK button — replace cancel.
                _s.buttonLayout.removeWidget(_s.yesButton)
                _s.buttonLayout.removeWidget(_s.cancelButton)
                _s.cancelButton.hide()
                _s.buttonLayout.addStretch(1)
                _s.yesButton.setText("OK")
                _s.yesButton.setFixedWidth(120)
                _s.yesButton.setStyleSheet(
                    "QPushButton { background: #1e63e4; border: 1px solid #1e63e4;"
                    "  color: white; border-radius: 6px; padding: 8px 22px;"
                    "  font-weight: 700; font-size: 12px; }"
                    "QPushButton:hover { background: #2a73f3; }"
                )
                _s.buttonLayout.addWidget(_s.yesButton, 0, Qt.AlignVCenter)

        _ScanSheet(self).exec()


# ── Modal wrapper (Fluent style) ────────────────────────────────────────────
def build_theme_dialog(parent=None):
    """Return a Fluent MessageBoxBase modal wrapping ThemeConfigTab."""
    try:
        from qfluentwidgets import MessageBoxBase
        from .tabler_icons import TablerIcon
    except Exception:
        return None

    from PySide6.QtCore import (
        QPropertyAnimation, QEasingCurve, Property,
    )
    from PySide6.QtGui import QPainter

    class LightThemeDialog(MessageBoxBase):
        def __init__(self, host):
            super().__init__(host.window() if host is not None else None)
            try:
                self.setMaskColor(QColor(0, 0, 0, 170))
            except Exception:
                pass

            self.widget.setObjectName("ltCard")
            apply_fluent_modal_palette(self, "ltCard")

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

            # Header (palette icon + title + close).
            header_row = QHBoxLayout()
            header_row.setSpacing(10)
            icon_btn = QToolButton()
            icon_btn.setEnabled(False)
            icon_btn.setIcon(TablerIcon("tabler_palette.svg").icon(color=QColor("#388BFD")))
            icon_btn.setIconSize(QSize(20, 20))
            icon_btn.setStyleSheet(
                "background: rgba(56,139,253,0.12); border: none;"
                " border-radius: 8px; padding: 6px;"
            )
            title_col = QVBoxLayout()
            title_col.setSpacing(2)
            t = QLabel("Light Theme Colors")
            t.setStyleSheet(
                "color: #E6EDF3; font-size: 15px; font-weight: 700;"
                " background: transparent;"
            )
            s = QLabel(
                "Customize your light theme to match your style. "
                "Changes apply globally."
            )
            s.setWordWrap(True)
            s.setStyleSheet(
                "color: #8B949E; font-size: 11px; background: transparent;"
            )
            title_col.addWidget(t)
            title_col.addWidget(s)

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

            # Body — the ThemeConfigTab widget.
            body_w = QWidget()
            body_lay = QVBoxLayout(body_w)
            body_lay.setContentsMargins(22, 14, 22, 14)
            self.theme_widget = ThemeConfigTab()
            body_lay.addWidget(self.theme_widget)
            self.viewLayout.addWidget(body_w)
            self.viewLayout.addWidget(_div())

            self.widget.setMinimumWidth(960)
            self.widget.setMinimumHeight(560)

            # Bottom Cancel / Save buttons.
            self.buttonLayout.removeWidget(self.yesButton)
            self.buttonLayout.removeWidget(self.cancelButton)
            self.buttonLayout.addStretch(1)
            self.yesButton.setText("   Save")
            self.cancelButton.setText("Cancel")
            self.cancelButton.setFixedWidth(120)
            self.yesButton.setFixedWidth(120)
            self.cancelButton.setStyleSheet(
                "QPushButton { background: transparent; border: 1px solid #30363D;"
                "  color: #E6EDF3; border-radius: 6px; padding: 8px 22px;"
                "  font-weight: 700; font-size: 12px; }"
                "QPushButton:hover { background: rgba(255,255,255,0.05);"
                "  border-color: #58606A; }"
            )
            self.yesButton.setStyleSheet(
                "QPushButton { background: #1e63e4; border: 1px solid #1e63e4;"
                "  color: white; border-radius: 6px; padding: 8px 22px;"
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

            # Wire the bottom Save to the inner ThemeConfigTab.commit().
            self.yesButton.clicked.disconnect()
            self.yesButton.clicked.connect(self._on_save)

        def _on_save(self):
            self.theme_widget.commit()
            self.accept()

    return LightThemeDialog(parent)
