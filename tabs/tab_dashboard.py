"""Dashboard Tab — charts (pure Qt), KPI cards, filters, and insights panel.

All charts are drawn with QPainter — no matplotlib / Pillow required.
"""
from __future__ import annotations
from .theme_palette import apply_fluent_modal_palette

import json
import math
import os
from collections import defaultdict

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QDateEdit, QComboBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QFrame, QSizePolicy, QScrollArea, QGroupBox,
    QGridLayout, QBoxLayout, QStackedWidget, QButtonGroup,
    QDialog, QFileDialog, QMessageBox, QTabWidget,
)
from PySide6.QtCore import QDate, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QBrush

from db.database import get_connection
from . import font_scale
from .utils import (
    get_resource_path,
    load_units_eq_data,
    get_units_per_case,
    calculate_equivalent_units,
)

# ---------------------------------------------------------------------------
# Palette (mutable — swapped in place by _apply_chart_palette on theme change)
# ---------------------------------------------------------------------------
_BG    = QColor("#0D1117")
_AX    = QColor("#0D1117")
_GRID  = QColor("#21262D")
_TEXT  = QColor("#C9D1D9")
_MUTED = QColor("#8B949E")
_BLUE  = QColor("#4aa3ff")
_ORANGE = QColor("#f5a623")
_GREEN  = QColor("#4CAF50")
_RED    = QColor("#e05c5c")


_DARK_CHART_COLORS = {
    "bg": "#0D1117", "ax": "#0D1117", "grid": "#21262D",
    "text": "#C9D1D9", "muted": "#8B949E",
}
_LIGHT_CHART_COLORS = {
    "bg": "#FFFFFF", "ax": "#F6F8FA", "grid": "#D0D7DE",
    "text": "#1F2328", "muted": "#656D76",
}


def _apply_chart_palette(is_light: bool) -> None:
    """Mutate the module-level chart QColor objects in place so subsequent
    paintEvent calls pick up the new theme without rebuilding widgets."""
    c = _LIGHT_CHART_COLORS if is_light else _DARK_CHART_COLORS
    for color_obj, key in (
        (_BG, "bg"), (_AX, "ax"), (_GRID, "grid"),
        (_TEXT, "text"), (_MUTED, "muted"),
    ):
        nc = QColor(c[key])
        color_obj.setRgb(nc.red(), nc.green(), nc.blue())

SERIES_COLORS = [
    QColor("#4aa3ff"), QColor("#f5a623"), QColor("#4CAF50"), QColor("#e07b54"),
    QColor("#9b59b6"), QColor("#1abc9c"), QColor("#e74c3c"), QColor("#3498db"),
    QColor("#e5e50a"), QColor("#e056a0"), QColor("#00bcd4"), QColor("#ff7043"),
    QColor("#8bc34a"), QColor("#ab47bc"), QColor("#26a69a"),
]


# ---------------------------------------------------------------------------
# Shared chart helpers
# ---------------------------------------------------------------------------

def _paint_bg(painter: QPainter, widget: QWidget):
    painter.fillRect(widget.rect(), _BG)


def _paint_no_data(widget: QWidget):
    p = QPainter(widget)
    p.fillRect(widget.rect(), _BG)
    p.setPen(QPen(_MUTED))
    f = QFont()
    f.setPointSize(font_scale.scale_pt(9))
    p.setFont(f)
    p.drawText(widget.rect(), Qt.AlignmentFlag.AlignCenter, "No data")
    p.end()


def _draw_axes(painter: QPainter, rc: QRect):
    painter.fillRect(rc, _AX)
    painter.setPen(QPen(_GRID, 1))
    painter.drawRect(rc)


def _draw_h_grid(painter: QPainter, rc: QRect, max_val: float, lines: int = 4):
    f = QFont()
    f.setPointSize(font_scale.scale_pt(7))
    painter.setFont(f)
    for i in range(lines + 1):
        frac = i / lines
        y = rc.bottom() - int(frac * rc.height())
        pen = QPen(_GRID)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawLine(rc.x(), y, rc.right(), y)
        label = f"{max_val * frac:.0f}"
        painter.setPen(QPen(_MUTED))
        painter.drawText(
            QRect(0, y - 8, rc.x() - 3, 16),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            label,
        )


def _draw_legend(painter: QPainter, series: list, rc: QRect):
    f = QFont()
    f.setPointSize(font_scale.scale_pt(7))
    painter.setFont(f)
    fm = QFontMetrics(f)
    x = rc.x()
    y = rc.top() + 4
    for name, color, _ in series:
        painter.setBrush(QBrush(color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRect(x, y, 10, 10)
        painter.setPen(QPen(_TEXT))
        painter.drawText(x + 13, y + 9, name)
        x += 13 + fm.horizontalAdvance(name) + 12
        if x > rc.right() - 50:
            x = rc.x()
            y += 16


def _label_stride(n: int, max_labels: int = 10) -> int:
    """Return stride for x-axis labels so long ranges stay readable."""
    if n <= 0:
        return 1
    return max(1, math.ceil(n / max_labels))


# ---------------------------------------------------------------------------
# Base chart widget
# ---------------------------------------------------------------------------

class _ChartBase(QWidget):
    PAD_T, PAD_R, PAD_B, PAD_L = 16, 16, 44, 54

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(200)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )

    def _plot_rect(self) -> QRect:
        return QRect(
            self.PAD_L,
            self.PAD_T,
            self.width() - self.PAD_L - self.PAD_R,
            self.height() - self.PAD_T - self.PAD_B,
        )


# ---------------------------------------------------------------------------
# Stacked bar chart  (daily UE : REG vs OT)
# ---------------------------------------------------------------------------

class StackedBarChart(_ChartBase):
    """series = list of (name: str, color: QColor, values: list[float])"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._x_labels: list[str] = []
        self._series: list[tuple[str, QColor, list[float]]] = []

    def set_data(self, x_labels, series):
        self._x_labels = list(x_labels)
        self._series = list(series)
        self.update()

    def paintEvent(self, _event):
        if not self._x_labels:
            _paint_no_data(self)
            return

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rc = self._plot_rect()
        _paint_bg(p, self)
        _draw_axes(p, rc)

        n = len(self._x_labels)
        slot = rc.width() / max(n, 1)
        bar_w = max(1, int(slot * 0.72))
        if bar_w > 40:
            bar_w = 40
        totals = [
            sum(s[2][i] for s in self._series if i < len(s[2]))
            for i in range(n)
        ]
        max_val = max(totals) if totals else 1.0
        if max_val == 0:
            max_val = 1.0

        _draw_h_grid(p, rc, max_val)

        f = QFont()
        f.setPointSize(font_scale.scale_pt(7))
        p.setFont(f)
        stride = _label_stride(n, max_labels=10)
        val_stride = _label_stride(n, max_labels=12)

        for i, lbl in enumerate(self._x_labels):
            x = rc.x() + int(i * slot + (slot - bar_w) / 2)
            bottom = rc.bottom()
            acc = 0.0
            for _name, color, vals in self._series:
                v = vals[i] if i < len(vals) else 0.0
                h = int(v / max_val * rc.height())
                if h > 0:
                    p.setBrush(QBrush(color))
                    p.setPen(Qt.PenStyle.NoPen)
                    y_top = bottom - int((acc + v) / max_val * rc.height())
                    p.drawRect(x, y_top, bar_w, h)
                acc += v
            # Show total UE above selected bars for readability.
            if acc > 0 and bar_w >= 8 and (i == n - 1 or i % val_stride == 0):
                y_top_total = bottom - int(acc / max_val * rc.height())
                y_text = max(rc.y() + 2, y_top_total - 12)
                p.setPen(QPen(_TEXT))
                p.drawText(
                    QRect(x - 10, y_text, max(22, bar_w + 20), 12),
                    Qt.AlignmentFlag.AlignCenter,
                    f"{acc:.1f}",
                )
            # x-axis label
            if i == 0 or i == n - 1 or i % stride == 0:
                p.setPen(QPen(_MUTED))
                p.drawText(
                    QRect(x - 10, rc.bottom() + 4, bar_w + 20, 20),
                    Qt.AlignmentFlag.AlignCenter,
                    lbl,
                )

        _draw_legend(p, self._series, rc)
        p.end()


# ---------------------------------------------------------------------------
# Line chart  (efficiency % trend)
# ---------------------------------------------------------------------------

class LineChart(_ChartBase):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._x_labels: list[str] = []
        self._values: list[float] = []
        self._ref_line: float | None = None

    def set_data(self, x_labels, values, ref_line=None):
        self._x_labels = list(x_labels)
        self._values = list(values)
        self._ref_line = ref_line
        self.update()

    def paintEvent(self, _event):
        if not self._values:
            _paint_no_data(self)
            return

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rc = self._plot_rect()
        _paint_bg(p, self)
        _draw_axes(p, rc)

        raw_values = [max(0.0, float(v or 0.0)) for v in self._values]
        plot_values = list(raw_values)
        capped = False
        if len(raw_values) >= 8:
            sorted_vals = sorted(raw_values)
            idx = int((len(sorted_vals) - 1) * 0.90)
            p90 = sorted_vals[idx]
            cap = max(300.0, p90 * 3.0, float(self._ref_line or 0.0))
            if any(v > cap for v in raw_values):
                plot_values = [min(v, cap) for v in raw_values]
                capped = True

        all_v = list(plot_values)
        if self._ref_line is not None:
            all_v.append(self._ref_line)
        max_val = max(all_v) if all_v else 1.0
        if max_val == 0:
            max_val = 1.0

        _draw_h_grid(p, rc, max_val)

        n = len(self._values)
        xs = [
            rc.x() + int((i / max(n - 1, 1)) * rc.width())
            for i in range(n)
        ]
        ys = [
            rc.bottom() - int(v / max_val * rc.height())
            for v in plot_values
        ]

        # Reference line
        if self._ref_line is not None:
            ry = rc.bottom() - int(self._ref_line / max_val * rc.height())
            pen = QPen(_GREEN)
            pen.setWidth(1)
            pen.setStyle(Qt.PenStyle.DotLine)
            p.setPen(pen)
            p.drawLine(rc.x(), ry, rc.right(), ry)

        # Trend line
        pen = QPen(_ORANGE)
        pen.setWidth(2)
        p.setPen(pen)
        for i in range(len(xs) - 1):
            p.drawLine(xs[i], ys[i], xs[i + 1], ys[i + 1])

        # X labels
        f = QFont()
        f.setPointSize(font_scale.scale_pt(7))
        p.setFont(f)
        p.setPen(QPen(_MUTED))
        stride = _label_stride(len(xs), max_labels=10)
        for i, (x, lbl) in enumerate(zip(xs, self._x_labels)):
            if i == 0 or i == len(xs) - 1 or i % stride == 0:
                p.drawText(
                    QRect(x - 24, rc.bottom() + 4, 48, 20),
                    Qt.AlignmentFlag.AlignCenter,
                    lbl,
                )
        if capped:
            p.setPen(QPen(_MUTED))
            p.drawText(
                QRect(rc.x(), rc.y() - 12, rc.width(), 12),
                Qt.AlignmentFlag.AlignRight,
                "Scaled for outliers",
            )
        p.end()


# ---------------------------------------------------------------------------
# Pie / Donut chart
# ---------------------------------------------------------------------------

class PieChart(_ChartBase):
    PAD_T, PAD_R, PAD_B, PAD_L = 8, 8, 8, 8
    LEGEND_H = 84

    def __init__(self, hole: float = 0.0, parent=None):
        super().__init__(parent)
        self._hole = hole   # 0 = full pie,  0.5 = donut
        self._labels: list[str] = []
        self._values: list[float] = []

    def set_data(self, labels, values):
        self._labels = list(labels)
        self._values = list(values)
        self.update()

    def paintEvent(self, _event):
        if not self._values or sum(self._values) == 0:
            _paint_no_data(self)
            return

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        _paint_bg(p, self)

        total = sum(self._values)
        w, h = self.width(), self.height()
        margin = 12
        side = min(w - margin * 2, h - self.LEGEND_H - margin * 2)
        side = max(side, 20)
        ox = margin + (w - margin * 2 - side) // 2
        oy = margin
        rect = QRectF(ox, oy, side, side)

        start_angle = 90 * 16          # Qt units = 1/16 degree
        for i, (lbl, val) in enumerate(zip(self._labels, self._values)):
            span = int(val / total * 360 * 16)
            color = SERIES_COLORS[i % len(SERIES_COLORS)]
            p.setBrush(QBrush(color))
            p.setPen(QPen(_BG, 1.5))
            p.drawPie(rect, start_angle, -span)    # negative = clockwise
            start_angle -= span

        # Donut hole
        if self._hole > 0:
            inner = side * self._hole
            ix = ox + (side - inner) / 2
            iy = oy + (side - inner) / 2
            p.setBrush(QBrush(_BG))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QRectF(ix, iy, inner, inner))

        # Legend
        f = QFont()
        f.setPointSize(font_scale.scale_pt(7))
        p.setFont(f)
        lx = margin
        ly = h - self.LEGEND_H + 4
        col_w = max(60, (w - margin * 2) // 2)
        for i, (lbl, val) in enumerate(zip(self._labels, self._values)):
            color = SERIES_COLORS[i % len(SERIES_COLORS)]
            cx = lx + (i % 2) * col_w
            cy = ly + (i // 2) * 17
            if cy + 14 > h:
                break
            p.setBrush(QBrush(color))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRect(cx, cy + 3, 10, 10)
            p.setPen(QPen(_TEXT))
            pct = val / total * 100
            short_lbl = lbl[:14] if len(lbl) > 14 else lbl
            p.drawText(
                QRect(cx + 14, cy, col_w - 16, 17),
                Qt.AlignmentFlag.AlignVCenter,
                f"{short_lbl} {pct:.0f}% ({val:.1f})",
            )
        p.end()


# ---------------------------------------------------------------------------
# KPI card
# ---------------------------------------------------------------------------

class _DonutChart(QWidget):
    """Lightweight donut chart with center total + legend below.

    Slices = list of (label, value, color). Renders a hollow circle with
    proportional arc widths, the label "% of {total}" at the centre, and
    a colour-coded legend underneath."""

    def __init__(self, title: str, total_label: str = "", parent=None):
        super().__init__(parent)
        self._title = title
        self._total_label = total_label
        self._slices: list = []
        self.setMinimumHeight(220)
        from PySide6.QtWidgets import QSizePolicy
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_data(self, slices: list, total_label: str = ""):
        self._slices = list(slices or [])
        if total_label:
            self._total_label = total_label
        self.update()

    def apply_palette(self, is_light: bool):
        """Trigger a repaint so palette-resolved text colours refresh."""
        self.update()

    def paintEvent(self, _e):
        from PySide6.QtGui import QPainter, QPen
        from PySide6.QtCore import QRectF
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Donut area sits in the top portion; legend below. Reserve
        # extra bottom padding so the legend never touches the card edge.
        rect = self.rect()
        legend_lines = max(2, len(self._slices))
        legend_reserve = 22 + legend_lines * 18 + 24  # extra bottom padding
        donut_h = max(110, rect.height() - legend_reserve)
        side = min(rect.width() - 60, donut_h)
        donut_rect = QRectF(
            rect.center().x() - side / 2,
            12,
            side,
            side,
        )
        thickness = max(14, int(side * 0.15))
        inner_rect = donut_rect.adjusted(thickness, thickness, -thickness, -thickness)

        total = sum(max(0.0, float(s[1])) for s in self._slices) or 0.0
        if total <= 0:
            # Empty state placeholder
            pen = QPen(QColor("#21262D")); pen.setWidth(thickness)
            p.setPen(pen)
            p.drawArc(donut_rect, 0, 360 * 16)
        else:
            start_angle = 90 * 16  # start at top
            for _lbl, val, color in self._slices:
                if val is None or val <= 0:
                    continue
                span = int(-(float(val) / total) * 360 * 16)
                pen = QPen(QColor(color)); pen.setWidth(thickness)
                pen.setCapStyle(Qt.PenCapStyle.FlatCap)
                p.setPen(pen)
                p.drawArc(donut_rect, start_angle, span)
                start_angle += span

        # Center label — use palette text so it reads in both themes.
        try:
            from qfluentwidgets.common.style_sheet import isDarkTheme
            from .theme_palette import palette as _dp
            _donut_pal = _dp(not isDarkTheme())
        except Exception:
            _donut_pal = {"text": "#E6EDF3", "text_2": "#C9D1D9"}
        p.setPen(QColor(_donut_pal["text"]))
        font = p.font(); font.setBold(True); font.setPointSize(10)
        p.setFont(font)
        if self._total_label:
            p.drawText(donut_rect, Qt.AlignmentFlag.AlignCenter, self._total_label)

        # Legend below the donut.
        legend_top = donut_rect.bottom() + 14
        line_h = 18
        for i, (lbl, val, color) in enumerate(self._slices):
            y = legend_top + i * line_h
            if y > rect.height() - 4:
                break
            # Color swatch.
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(color))
            p.drawRoundedRect(QRectF(20, y, 10, 10), 2, 2)
            # Text: "REG  54%  (48.00)"
            pct = (float(val) / total * 100.0) if total > 0 else 0.0
            p.setPen(QColor(_donut_pal["text_2"]))
            font = p.font(); font.setBold(False); font.setPointSize(9)
            p.setFont(font)
            p.drawText(
                QRectF(36, y - 2, rect.width() - 40, line_h),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                f"{lbl}",
            )
            p.setPen(QColor(_donut_pal["text"]))
            p.drawText(
                QRectF(20, y - 2, rect.width() - 24, line_h),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                f"{pct:.0f}%   ({float(val):.2f})",
            )
        p.end()


class _DonutCard(QFrame):
    """Card frame wrapping a _DonutChart with a header."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("donutCard")
        self.setStyleSheet(
            "#donutCard { background: #0D1117;"
            "  border: 1px solid #21262D; border-radius: 12px; }"
            "QLabel { background: transparent; color: #C9D1D9; }"
        )
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(6)
        hdr = QLabel(title)
        hdr.setStyleSheet(
            "color: #C9D1D9; font-size: 12px; font-weight: 700;"
        )
        self._sub = QLabel("")
        self._sub.setStyleSheet("color: #8B949E; font-size: 10px;")
        v.addWidget(hdr)
        v.addWidget(self._sub)
        self.chart = _DonutChart(title, parent=self)
        v.addWidget(self.chart, 1)

    def set_data(self, slices: list, subtitle: str = ""):
        if subtitle:
            self._sub.setText(subtitle)
        total = sum(max(0.0, float(s[1])) for s in slices) if slices else 0.0
        self.chart.set_data(slices, total_label=f"{total:.2f}")


class _PersonalKpiTile(QFrame):
    """Big KPI tile matching the Personal dashboard target: icon (tinted
    square) + UPPERCASE label + huge value + subtle subtitle."""

    def __init__(self, label: str, subtitle: str, icon_svg: str,
                 accent: str = "#58A6FF", parent=None):
        super().__init__(parent)
        self.setObjectName("personalKpi")
        self.setStyleSheet(
            "#personalKpi { background: #0D1117; border: 1px solid #21262D;"
            "  border-radius: 12px; }"
            "QLabel { background: transparent; border: none; }"
        )
        self._accent = accent
        self.setFixedHeight(62)

        h = QHBoxLayout(self)
        h.setContentsMargins(14, 8, 14, 8)
        h.setSpacing(0)

        col = QVBoxLayout(); col.setSpacing(0)
        col.setContentsMargins(0, 0, 0, 0)
        col.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title_lbl = QLabel(label.upper())
        self._title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title_lbl.setStyleSheet(
            "color: #8B949E; font-size: 11px; font-weight: 700;"
            " letter-spacing: 0.5px;"
        )
        col.addWidget(self._title_lbl)
        self._val_lbl = QLabel("—")
        self._val_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._val_lbl.setStyleSheet(
            f"color: {accent}; font-size: 17px; font-weight: 800;"
        )
        col.addWidget(self._val_lbl)
        sub = QLabel(subtitle)
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setStyleSheet(
            "color: #6E7681; font-size: 10px;"
        )
        col.addWidget(sub)
        h.addLayout(col, 1)

    def set_value(self, v: str):
        self._val_lbl.setText(str(v))

    def apply_theme(self, is_light: bool):
        # Kept for API compatibility with _Card; the dark style is fixed.
        pass

    def update_font_sizes(self):
        pass


class _Card(QFrame):
    def __init__(self, title: str, value: str = "—", accent: str = "#4aa3ff", parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumWidth(110)
        self._accent = accent
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(8)

        self._title_lbl = QLabel(title)
        self._title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._val_lbl = QLabel(value)
        self._val_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._val_base_pt = 16
        self._apply_val_font()

        self.apply_theme(is_light=False)

        lay.addWidget(self._title_lbl)
        lay.addWidget(self._val_lbl)

    def set_value(self, v: str):
        self._val_lbl.setText(v)

    def _apply_val_font(self):
        f = QFont()
        f.setPointSize(font_scale.scale_pt(self._val_base_pt))
        f.setBold(True)
        self._val_lbl.setFont(f)

    def update_font_sizes(self):
        self._apply_val_font()

    def apply_theme(self, is_light: bool):
        title_color = "#57606A" if is_light else "#8B949E"
        self._title_lbl.setStyleSheet(
            f"color: {title_color}; font-size: 10px; font-weight: 700; "
            "letter-spacing: 0.5px;"
        )
        self._val_lbl.setStyleSheet(f"color: {self._accent};")


# ---------------------------------------------------------------------------
# Advice / insight item
# ---------------------------------------------------------------------------

_ADVICE_ICON  = {"warning": "⚠", "info": "ℹ", "good": "✓", "tip": "💡"}
_ADVICE_COLOR = {"warning": "#f5a623", "info": "#4aa3ff", "good": "#4CAF50", "tip": "#e07b54"}


class _AdviceItem(QFrame):
    """Insights row card — dark slate with colored icon badge on left
    and a muted chevron on the right (placeholder for future expand)."""

    def __init__(self, kind: str, text: str, parent=None):
        super().__init__(parent)
        color = _ADVICE_COLOR.get(kind, "#4aa3ff")
        self.setObjectName("adviceItem")
        self.setStyleSheet(
            "#adviceItem { background: #161B22;"
            "  border: 1px solid #21262D; border-radius: 8px; }"
            "QLabel { background: transparent; border: none; }"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(12)

        # Colored badge — solid background tinted to kind colour.
        badge = QLabel(_ADVICE_ICON.get(kind, "•"))
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFixedSize(22, 22)
        badge.setStyleSheet(
            f"background: {color}; color: #FFFFFF;"
            "  border-radius: 11px; font-size: 11px; font-weight: 700;"
        )

        msg = QLabel(text)
        msg.setWordWrap(True)
        msg.setStyleSheet("color: #C9D1D9; font-size: 11px;")

        lay.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
        lay.addWidget(msg, 1)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

class _ChartCard(QFrame):
    """Fluent dark card wrapping a chart, matching _DonutCard look:
    bold title + muted subtitle on top, chart below. setTitle() accepts
    either 'Title' or 'Title (subtitle)' — anything inside parens or
    after a ' — ' is shown as the smaller subtitle line."""

    def __init__(self, title: str, widget: QWidget, parent=None):
        super().__init__(parent)
        self.setObjectName("chartCard")
        self.setStyleSheet(
            "#chartCard { background: #0D1117;"
            "  border: 1px solid #21262D; border-radius: 12px; }"
            "QLabel { background: transparent; }"
        )
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(2)
        self._title_lbl = QLabel()
        self._title_lbl.setStyleSheet(
            "color: #C9D1D9; font-size: 12px; font-weight: 700;"
        )
        self._sub_lbl = QLabel()
        self._sub_lbl.setStyleSheet(
            "color: #8B949E; font-size: 10px;"
        )
        v.addWidget(self._title_lbl)
        v.addWidget(self._sub_lbl)
        v.addSpacing(4)
        try:
            widget.setStyleSheet(
                (widget.styleSheet() or "") + " background: transparent;"
            )
        except Exception:
            pass
        v.addWidget(widget, 1)
        self.setTitle(title)

    def setTitle(self, text: str):  # noqa: N802  (keep QGroupBox parity)
        head, sub = text, ""
        if "(" in text and text.rstrip().endswith(")"):
            i = text.find("(")
            head = text[:i].rstrip()
            sub = text[i + 1:-1].strip()
        elif " — " in text:
            head, sub = text.split(" — ", 1)
        self._title_lbl.setText(head)
        self._sub_lbl.setText(sub)
        self._sub_lbl.setVisible(bool(sub))


def _chart_group(title: str, widget: QWidget):
    """Backward-compatible alias returning a _ChartCard."""
    return _ChartCard(title, widget)


def _make_table(headers: list[str]) -> QTableWidget:
    t = QTableWidget()
    t.setColumnCount(len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    t.setAlternatingRowColors(True)
    t.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    t.verticalHeader().setVisible(False)
    t.setMinimumHeight(160)
    return t


def _fill_row(table: QTableWidget, row: int, values: list[str]):
    for col, val in enumerate(values):
        item = QTableWidgetItem(str(val))
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        table.setItem(row, col, item)


# ---------------------------------------------------------------------------
# Designer-day modal (used from the Team view)
# ---------------------------------------------------------------------------

try:
    from qfluentwidgets import MessageBoxBase as _MBB
except Exception:
    _MBB = QDialog  # fallback


class _DesignerDayDialog(_MBB):
    """Modal showing one designer's full Case Detail for a date.

    Uses MessageBoxBase so it matches the rest of the app's modals:
    backdrop mask + rounded dark card + standard button row. Falls back
    to a plain QDialog if qfluentwidgets isn't available."""

    def __init__(self, designer: str, target_date: str, detail: dict, parent=None):
        super().__init__(parent.window() if parent is not None else None)

        is_fluent = _MBB is not QDialog
        if is_fluent:
            try:
                self.setMaskColor(QColor(0, 0, 0, 170))
            except Exception:
                pass
            self.widget.setObjectName("dsgnCard")
            apply_fluent_modal_palette(self, "dsgnCard")
            host_layout = self.viewLayout
            host_layout.setContentsMargins(22, 18, 22, 12)
            host_layout.setSpacing(12)
        else:
            self.setWindowTitle(f"{designer} — {target_date}")
            self.resize(980, 560)
            host_layout = QVBoxLayout(self)
            host_layout.setContentsMargins(20, 18, 20, 14)
            host_layout.setSpacing(12)

        # Common QSS — tables, tabs, labels.
        self.setStyleSheet(self.styleSheet() + """
            QLabel { color: #E6EDF3; background: transparent; }
            QTableWidget {
                background-color: #0D1117; gridline-color: transparent;
                color: #E6EDF3; border: 1px solid #21262D;
                border-radius: 10px; outline: none;
            }
            QTableWidget::item { padding: 6px 8px; border: none; }
            QTableWidget::item:selected {
                background-color: rgba(56,139,253,0.14); color: #E6EDF3;
            }
            QHeaderView { background: transparent; border: none; }
            QHeaderView::section {
                background-color: #161B22; color: #8B949E;
                padding: 8px 8px; border: none;
                border-bottom: 1px solid #21262D;
                font-weight: 700; font-size: 10px;
                letter-spacing: 0.5px;
            }
            QTabWidget::pane {
                border: 1px solid #21262D; border-radius: 8px;
                background: transparent; top: -1px;
            }
            QTabBar { qproperty-drawBase: 0; }
            QTabBar::tab {
                background: transparent; color: #8B949E;
                padding: 8px 18px; border: 1px solid transparent;
                border-bottom: none; font-weight: 700; font-size: 11px;
                min-width: 90px;
            }
            QTabBar::tab:hover { color: #E6EDF3; }
            QTabBar::tab:selected {
                background: #161B22; color: #58A6FF;
                border: 1px solid #21262D; border-bottom: 1px solid #161B22;
                border-top-left-radius: 8px; border-top-right-radius: 8px;
            }
        """)

        # Header: avatar + title + subtitle.
        hdr_row = QHBoxLayout(); hdr_row.setSpacing(12)
        # Avatar initials (First initial + Last initial).
        full = (designer or "").strip()
        first_i = last_i = ""
        if "," in full:
            last, _, first = full.partition(",")
            first_i = first.strip()[:1].upper()
            last_i = last.strip()[:1].upper()
        else:
            parts = full.split()
            if len(parts) >= 2:
                first_i = parts[0][:1].upper()
                last_i = parts[-1][:1].upper()
            elif parts:
                first_i = parts[0][:1].upper()
        initials = (first_i + last_i) or "?"

        avatar = QLabel(initials)
        avatar.setFixedSize(46, 46)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setStyleSheet(
            "QLabel { background: rgba(56,139,253,0.14); color: #58A6FF;"
            "  font-size: 14px; font-weight: 800; border-radius: 23px;"
            "  border: 1px solid #1F6FEB; }"
        )
        hdr_row.addWidget(avatar, 0, Qt.AlignmentFlag.AlignVCenter)

        title_block = QVBoxLayout(); title_block.setSpacing(2)
        title = QLabel(designer)
        title.setStyleSheet(
            "color: #E6EDF3; font-size: 17px; font-weight: 800;"
            " background: transparent;"
        )
        title_block.addWidget(title)
        sub_row = QHBoxLayout(); sub_row.setSpacing(6)
        try:
            from .tabler_icons import TablerIcon as _TI_dd
            _ic = QLabel()
            _ic.setFixedSize(12, 12)
            _ic.setPixmap(
                _TI_dd("tabler_calendar.svg").icon(color=QColor("#8B949E")).pixmap(12, 12)
            )
            sub_row.addWidget(_ic, 0, Qt.AlignmentFlag.AlignVCenter)
        except Exception:
            pass
        sub = QLabel(target_date)
        sub.setStyleSheet(
            "color: #8B949E; font-size: 11px; background: transparent;"
        )
        sub_row.addWidget(sub)
        sub_row.addStretch(1)
        title_block.addLayout(sub_row)
        hdr_row.addLayout(title_block, 1)
        host_layout.addLayout(hdr_row)

        # KPI tiles row.
        if detail.get("found"):
            reg_cases = detail.get("cases") or []
            ot_cases = detail.get("ot_cases") or []
            all_cases = reg_cases + ot_cases
            total_cases = len(all_cases)
            dt_total = sum((d.get("duracion") or d.get("duration") or 0)
                           for d in (detail.get("downtimes") or []))
            def _as_float(v):
                try:
                    if v is None or v == "":
                        return 0.0
                    return float(str(v).replace("%", "").strip())
                except (TypeError, ValueError):
                    return 0.0
            values = [
                _as_float(c.get("case_value") or c.get("value"))
                for c in all_cases
            ]
            best_value = max(values) if values else 0.0

            # Sum of Equivalent Units across every case (Reg + OT).
            try:
                from .utils import (
                    load_units_eq_data, calculate_equivalent_units,
                )
                _ue = load_units_eq_data() or {}
                total_ue = 0.0
                for c in all_cases:
                    region = c.get("region") or ""
                    tipo = c.get("type") or ""
                    val = _as_float(c.get("case_value") or c.get("value"))
                    try:
                        total_ue += calculate_equivalent_units(
                            _ue, region, tipo, val, count=1,
                        )
                    except Exception:
                        pass
            except Exception:
                total_ue = 0.0

            kpi_row = QHBoxLayout(); kpi_row.setSpacing(10)
            kpi_row.addWidget(self._kpi_tile(
                "Total Cases", str(total_cases), "cases",
                "tabler_clipboard_text.svg", "#A371F7",
            ), 1)
            kpi_row.addWidget(self._kpi_tile(
                "Total Downtime", f"{int(dt_total)}", "min",
                "tabler_clock_off.svg", "#F85149",
            ), 1)
            kpi_row.addWidget(self._kpi_tile(
                "Sum UND.EQ", f"{total_ue:.2f}", "units",
                "tabler_congruent_to.svg", "#E89720",
            ), 1)
            kpi_row.addWidget(self._kpi_tile(
                "Best Value", f"{best_value:.2f}%", "highest",
                "tabler_trending_up.svg", "#3FB950",
            ), 1)
            host_layout.addLayout(kpi_row)

        if not detail.get("found"):
            warn = QLabel(
                "No daily file found for this designer on this date.\n"
                "Either they haven't synced today, or their OneDrive copy "
                "hasn't reached this machine yet."
            )
            warn.setStyleSheet("color: #F0883E;")
            warn.setWordWrap(True)
            host_layout.addWidget(warn)
        else:
            tabs = QTabWidget()
            case_headers = [
                "Case ID", "Region", "Type", "Tier", "CR #", "Doctor",
                "Start", "End", "Std (min)", "Actual (min)",
                "Value (%)", "UE", "Status", "Comments",
            ]
            cases_tbl = self._build_table(case_headers)
            self._fill_cases(cases_tbl, detail["cases"], detail["ot_cases"])
            tabs.addTab(cases_tbl, f"Cases ({len(detail['cases']) + len(detail['ot_cases'])})")

            dt_headers = ["Start", "End", "Duration (min)", "Reason"]
            dt_tbl = self._build_table(dt_headers)
            self._fill_downtime(dt_tbl, detail["downtimes"])
            tabs.addTab(dt_tbl, f"Downtime ({len(detail['downtimes'])})")
            host_layout.addWidget(tabs, 1)

        # Footer buttons.
        if is_fluent:
            self.widget.setMinimumWidth(960)
            self.widget.setMinimumHeight(540)
            self.cancelButton.hide()
            self.yesButton.setText("Close")
            self.yesButton.setFixedWidth(120)
            self.yesButton.setStyleSheet(
                "QPushButton { background: #1e63e4; border: 1px solid #1e63e4;"
                "  color: white; border-radius: 6px; padding: 8px 22px;"
                "  font-weight: 700; font-size: 12px; }"
                "QPushButton:hover { background: #2a73f3; }"
            )
        else:
            bottom = QHBoxLayout()
            bottom.addStretch()
            btn_close = QPushButton("Close")
            btn_close.clicked.connect(self.accept)
            bottom.addWidget(btn_close)
            host_layout.addLayout(bottom)

    @staticmethod
    def _units_eq_cache():
        """Load + cache units_eq once per dialog session."""
        cached = getattr(_DesignerDayDialog, "_ue_cached", None)
        if cached is not None:
            return cached
        try:
            from .utils import load_units_eq_data
            _DesignerDayDialog._ue_cached = load_units_eq_data() or {}
        except Exception:
            _DesignerDayDialog._ue_cached = {}
        return _DesignerDayDialog._ue_cached

    @staticmethod
    def _region_chip(region: str):
        """Coloured chip badge for the Region column."""
        from PySide6.QtWidgets import QWidget as _QW, QHBoxLayout as _QH, QLabel as _QL
        palette = [
            "#1F6FEB", "#A371F7", "#3FB950", "#D29922", "#F0883E",
            "#00B5D8", "#E84393", "#7C4DFF", "#9CCC65", "#FF6B61",
        ]
        idx = sum(ord(c) for c in (region or "")) % len(palette)
        color = palette[idx]
        rgb = QColor(color)
        wrap = _QW()
        wrap.setStyleSheet("background: transparent;")
        h = _QH(wrap)
        h.setContentsMargins(0, 0, 0, 0); h.setSpacing(0)
        h.addStretch(1)
        chip = _QL(region)
        chip.setStyleSheet(
            f"QLabel {{ background: rgba({rgb.red()},{rgb.green()},{rgb.blue()},0.14);"
            f"  color: {color}; border: 1px solid {color};"
            f"  border-radius: 4px; padding: 2px 10px;"
            f"  font-size: 10px; font-weight: 700; }}"
        )
        h.addWidget(chip, 0, Qt.AlignmentFlag.AlignVCenter)
        h.addStretch(1)
        return wrap

    @staticmethod
    def _kpi_tile(title: str, value: str, suffix: str,
                  icon_svg: str, accent: str):
        """Compact KPI card with a fixed height so the 4 tiles always
        line up — label centered horizontally above, value + suffix
        baseline-aligned below."""
        from PySide6.QtWidgets import (
            QFrame as _QF, QVBoxLayout as _QV, QLabel as _QL,
            QHBoxLayout as _QH, QSizePolicy as _SP,
        )
        card = _QF()
        card.setObjectName("kpiTile")
        card.setFixedHeight(68)
        card.setSizePolicy(_SP.Policy.Expanding, _SP.Policy.Fixed)
        card.setStyleSheet(
            "#kpiTile { background: #0D1117; border: 1px solid #21262D;"
            "  border-radius: 10px; }"
            "QLabel { background: transparent; border: none; }"
        )
        col = _QV(card)
        col.setContentsMargins(14, 10, 14, 10)
        col.setSpacing(4)
        col.setAlignment(Qt.AlignmentFlag.AlignCenter)

        t = _QL(title.upper())
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        t.setStyleSheet(
            "color: #8B949E; font-size: 9px; font-weight: 700;"
            " letter-spacing: 0.5px;"
        )
        col.addWidget(t)

        # Value row centered. Suffix sits to the right of the value with
        # baseline alignment so '4.70% average' looks balanced.
        row_v = _QH(); row_v.setSpacing(4)
        row_v.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row_v.addStretch(1)
        v = _QL(value)
        v.setStyleSheet(
            f"color: {accent}; font-size: 18px; font-weight: 800;"
        )
        row_v.addWidget(v, 0, Qt.AlignmentFlag.AlignVCenter)
        if suffix:
            sx = _QL(suffix)
            sx.setStyleSheet("color: #6E7681; font-size: 10px;")
            row_v.addWidget(sx, 0, Qt.AlignmentFlag.AlignVCenter)
        row_v.addStretch(1)
        col.addLayout(row_v)
        return card

    @staticmethod
    def _build_table(headers: list[str]) -> QTableWidget:
        t = QTableWidget()
        t.setColumnCount(len(headers))
        t.setHorizontalHeaderLabels(headers)
        # All columns stretch evenly — no single one hoarding the width.
        for c in range(t.columnCount()):
            t.horizontalHeader().setSectionResizeMode(
                c, QHeaderView.ResizeMode.Stretch,
            )
        t.horizontalHeader().setStretchLastSection(False)
        t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        t.setAlternatingRowColors(True)
        t.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        t.verticalHeader().setVisible(False)
        return t

    @staticmethod
    def _fill_cases(tbl: QTableWidget, reg_cases: list[dict], ot_cases: list[dict]):
        rows = [(c, "Reg") for c in reg_cases] + [(c, "OT") for c in ot_cases]
        tbl.setRowCount(len(rows))
        for i, (c, label) in enumerate(rows):
            # cr_count + product_tier may be missing on older synced
            # files — fallback to empty so the columns hide cleanly via
            # _hide_blank_columns.
            _cr = c.get("cr_count")
            cr_label = f"#{_cr}" if _cr not in (None, "", 0, "0") else ""

            # Per-case Equivalent Units (computed lazily once units_eq
            # is loaded).
            ue_str = ""
            try:
                _ue_map = _DesignerDayDialog._units_eq_cache()
                from .utils import calculate_equivalent_units as _ce
                _v = c.get("value")
                try:
                    _v = float(str(_v).replace("%", "").strip())
                except (TypeError, ValueError, AttributeError):
                    _v = 0.0
                _ue_val = _ce(
                    _ue_map,
                    c.get("region") or "",
                    c.get("type") or "",
                    _v, count=1,
                )
                ue_str = f"{_ue_val:.2f}" if _ue_val else ""
            except Exception:
                ue_str = ""

            vals = [
                c.get("case_id", ""),
                c.get("region", ""),
                c.get("type", ""),
                c.get("product_tier", "") or c.get("tier", ""),
                cr_label,
                c.get("doctor", ""),
                c.get("start", ""),
                c.get("end", ""),
                c.get("std", ""),
                c.get("actual", ""),
                c.get("value", ""),
                ue_str,
                c.get("status", ""),
                c.get("comments", ""),
            ]
            # Columns now: 0=Case ID, 1=Region, 2=Type, 3=Tier, 4=CR #,
            # 5=Doctor, 6=Start, 7=End, 8=Std, 9=Actual, 10=Value, 11=UE,
            # 12=Status, 13=Comments.
            comments_col = 13
            for col, val in enumerate(vals):
                item = QTableWidgetItem(str(val) if val is not None else "")
                if col != comments_col:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if col == 0 and label == "OT":
                    item.setForeground(QBrush(QColor("#A371F7")))
                # Region col → coloured chip widget.
                if col == 1 and val:
                    item.setText("")
                    item.setData(Qt.ItemDataRole.UserRole, str(val))
                    tbl.setItem(i, col, item)
                    tbl.setCellWidget(i, col, _DesignerDayDialog._region_chip(str(val)))
                    continue
                tbl.setItem(i, col, item)
        _DesignerDayDialog._hide_blank_columns(tbl)

    @staticmethod
    def _fill_downtime(tbl: QTableWidget, downtimes: list[dict]):
        tbl.setRowCount(len(downtimes))
        for i, d in enumerate(downtimes):
            vals = [
                d.get("start", ""),
                d.get("end", ""),
                d.get("duration", ""),
                d.get("reason", ""),
            ]
            for col, val in enumerate(vals):
                item = QTableWidgetItem(str(val) if val is not None else "")
                if col != 3:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                tbl.setItem(i, col, item)
        _DesignerDayDialog._hide_blank_columns(tbl)

    @staticmethod
    def _hide_blank_columns(tbl: QTableWidget):
        """Hide any column whose every cell is empty / dash. Also checks
        the cellWidget + UserRole so columns rendered as custom widgets
        (e.g. Region chip) don't get hidden by mistake."""
        rows = tbl.rowCount()
        if rows == 0:
            return
        for col in range(tbl.columnCount()):
            empty = True
            for r in range(rows):
                it = tbl.item(r, col)
                txt = (it.text().strip() if it is not None else "")
                if not txt and it is not None:
                    ud = it.data(Qt.ItemDataRole.UserRole)
                    if ud:
                        txt = str(ud).strip()
                if not txt and tbl.cellWidget(r, col) is not None:
                    # Column uses a custom widget — keep visible.
                    txt = "_"
                if txt and txt not in ("—", "-", "0", "0.00", "0.000%"):
                    empty = False
                    break
            if empty:
                tbl.setColumnHidden(col, True)


# ---------------------------------------------------------------------------
# Dashboard Tab
# ---------------------------------------------------------------------------

class DashboardTab(QWidget):

    def __init__(self):
        super().__init__()
        self._standards: dict = {}
        self._load_metadata()
        self._init_ui()
        self.refresh()

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def _load_metadata(self):
        try:
            path = get_resource_path(os.path.join("data", "standards.json"))
            with open(path, "r", encoding="utf-8") as fh:
                self._standards = json.load(fh)
        except Exception:
            self._standards = {}

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _init_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Top bar with view toggle (Personal | Team) ──────────────
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(20, 14, 20, 6)
        top_bar.setSpacing(8)

        # Title block: title + subtitle stacked, no vertical stretches
        # so the bar stays compact.
        title_block = QVBoxLayout()
        title_block.setSpacing(2)
        title_block.setContentsMargins(0, 0, 0, 0)
        self.title_label = QLabel("Dashboard")
        self.title_label.setStyleSheet(
            "color: #E6EDF3; font-size: 18px; font-weight: 800;"
            " letter-spacing: 0.3px;"
        )
        title_block.addWidget(self.title_label)
        subtitle = QLabel("Overview of production performance")
        subtitle.setStyleSheet(
            "color: #8B949E; font-size: 11px; background: transparent;"
        )
        title_block.addWidget(subtitle)
        top_bar.addLayout(title_block)
        top_bar.addStretch()

        self.btn_view_team = QPushButton("Team")
        self.btn_view_personal = QPushButton("Personal")
        for b in (self.btn_view_team, self.btn_view_personal):
            b.setCheckable(True)
            b.setMinimumWidth(82)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        # Default: Team
        self.btn_view_team.setChecked(True)
        view_group = QButtonGroup(self)
        view_group.setExclusive(True)
        view_group.addButton(self.btn_view_team, 0)
        view_group.addButton(self.btn_view_personal, 1)
        view_group.idClicked.connect(self._on_view_changed)
        self._view_group = view_group
        top_bar.addWidget(self.btn_view_team)
        top_bar.addWidget(self.btn_view_personal)
        outer.addLayout(top_bar)
        self._apply_view_button_styles()

        # ── Stacked container: index 0 = team, 1 = personal ────────────
        self._view_stack = QStackedWidget()
        outer.addWidget(self._view_stack)

        team_page = self._build_team_page()
        self._view_stack.addWidget(team_page)

        # The original personal dashboard goes inside its own page
        personal_page = QWidget()
        personal_outer = QVBoxLayout(personal_page)
        personal_outer.setContentsMargins(0, 0, 0, 0)
        self._view_stack.addWidget(personal_page)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        personal_outer.addWidget(scroll)

        container = QWidget()
        container.setMinimumWidth(440)
        scroll.setWidget(container)

        root = QVBoxLayout(container)
        root.setContentsMargins(20, 18, 20, 22)
        root.setSpacing(18)

        # ── Filters card (Fluent dark) ─────────────────────────────────
        try:
            from .widgets import _icon_url as _icu_pf
            _chev_pf = _icu_pf("tabler_chevron_down.svg")
        except Exception:
            _chev_pf = ""
        fb = QFrame()
        fb.setObjectName("personalFilters")
        fb.setStyleSheet(
            "#personalFilters { background: #0D1117;"
            "  border: 1px solid #21262D; border-radius: 12px; }"
            "QLabel { background: transparent; color: #C9D1D9;"
            " font-size: 11px; font-weight: 600; }"
            "QLineEdit, QDateEdit, QComboBox { background: #161B22;"
            " border: 1px solid #30363D; border-radius: 6px;"
            " padding: 4px 26px 4px 8px; color: #E6EDF3; font-size: 11px;"
            " min-height: 26px; }"
            "QDateEdit::drop-down, QComboBox::drop-down {"
            " subcontrol-origin: padding; subcontrol-position: right center;"
            " width: 22px; border: none; }"
            f"QDateEdit::down-arrow, QComboBox::down-arrow {{"
            f" image: url({_chev_pf}); width: 12px; height: 12px; }}"
        )
        flay = QVBoxLayout(fb)
        flay.setContentsMargins(16, 12, 16, 12)
        flay.setSpacing(10)

        # ── Header row: filter icon + "Filters" ──
        hdr_row = QHBoxLayout(); hdr_row.setSpacing(6)
        try:
            from .tabler_icons import TablerIcon as _TI_pf
            _ic_lbl = QLabel(); _ic_lbl.setFixedSize(14, 14)
            _ic_lbl.setPixmap(
                _TI_pf("tabler_filter.svg").icon(color=QColor("#58A6FF")).pixmap(14, 14)
            )
            hdr_row.addWidget(_ic_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
        except Exception:
            pass
        _h_title = QLabel("Filters")
        _h_title.setStyleSheet(
            "color: #58A6FF; font-size: 12px; font-weight: 800;"
        )
        hdr_row.addWidget(_h_title)
        hdr_row.addStretch(1)
        flay.addLayout(hdr_row)

        # ── Body: left grid (inputs) + right Quick column ──
        body = QHBoxLayout(); body.setSpacing(20)
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)

        try:
            from .widgets import DateEditWithShortcut as _DateEdP
        except Exception:
            _DateEdP = QDateEdit
        self.date_from = _DateEdP()
        self.date_from.setDate(QDate.currentDate().addDays(-30))
        self.date_from.setCalendarPopup(True)
        self.date_from.setDisplayFormat("yyyy-MM-dd")
        self.date_from.setFixedWidth(150)
        self.date_from.setFixedHeight(28)
        self.date_to = _DateEdP()
        self.date_to.setDate(QDate.currentDate())
        self.date_to.setCalendarPopup(True)
        self.date_to.setDisplayFormat("yyyy-MM-dd")
        self.date_to.setFixedWidth(150)
        self.date_to.setFixedHeight(28)

        # Calendar icon leading on each date input.
        try:
            from .tabler_icons import TablerIcon as _TI_cal
            from PySide6.QtGui import QAction as _QA_cal
            from PySide6.QtWidgets import QLineEdit as _QLE_cal
            for _de in (self.date_from, self.date_to):
                _le = _de.lineEdit() if hasattr(_de, "lineEdit") else None
                if _le is not None:
                    _act = _QA_cal(
                        _TI_cal("tabler_calendar.svg").icon(color=QColor("#8B949E")),
                        "", _le,
                    )
                    _le.addAction(_act, _QLE_cal.ActionPosition.LeadingPosition)
        except Exception:
            pass

        # Auto-refresh when any filter changes — Refresh button stays as
        # a manual fallback.
        self.date_from.dateChanged.connect(lambda _d: self.refresh())
        self.date_to.dateChanged.connect(lambda _d: self.refresh())

        self.cmb_region = QComboBox()
        self.cmb_region.setMinimumWidth(160)
        self.cmb_region.setFixedHeight(28)
        self.cmb_region.addItem("All Regions", None)
        for r in sorted(self._standards.keys()):
            self.cmb_region.addItem(r, r)
        self.cmb_region.currentIndexChanged.connect(lambda _i: self.refresh())

        self.cmb_type = QComboBox()
        self.cmb_type.setFixedWidth(150)
        self.cmb_type.setFixedHeight(28)
        self.cmb_type.addItem("All Types", None)
        all_types: set = set()
        for reg_data in self._standards.values():
            if isinstance(reg_data, dict):
                for sub in reg_data.values():
                    if isinstance(sub, dict):
                        all_types.update(sub.keys())
        for t in sorted(all_types):
            self.cmb_type.addItem(t, t)

        self.cmb_type.currentIndexChanged.connect(lambda _i: self.refresh())

        self.cmb_source = QComboBox()
        self.cmb_source.setFixedWidth(150)
        self.cmb_source.setFixedHeight(28)
        for label, val in [("REG + OT", "both"), ("REG only", "reg"), ("OT only", "ot")]:
            self.cmb_source.addItem(label, val)
        self.cmb_source.currentIndexChanged.connect(lambda _i: self.refresh())

        # Reset button — restores every filter to its default value.
        # Auto-refresh handles re-rendering on change.
        btn_refresh = QPushButton("  Reset")
        btn_refresh.setCursor(Qt.PointingHandCursor)
        btn_refresh.setMinimumWidth(110)
        btn_refresh.setFixedHeight(28)
        try:
            from .tabler_icons import TablerIcon as _TI_rf
            from PySide6.QtCore import QSize as _QSrf
            btn_refresh.setIcon(_TI_rf("tabler_refresh.svg").icon(color=QColor("#C9D1D9")))
            btn_refresh.setIconSize(_QSrf(14, 14))
        except Exception:
            pass
        btn_refresh.setStyleSheet(
            "QPushButton { background: transparent; border: 1px solid #30363D;"
            "  color: #C9D1D9; border-radius: 6px; padding: 4px 14px;"
            "  font-size: 11px; font-weight: 700; }"
            "QPushButton:hover { background: rgba(255,255,255,0.05); }"
        )
        btn_refresh.clicked.connect(self._reset_filters)

        def _lbl(text, w=48):
            l = QLabel(text)
            l.setStyleSheet(
                "color: #C9D1D9; font-size: 11px; font-weight: 600;"
                " background: transparent;"
            )
            l.setMinimumWidth(w)
            l.setAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            )
            return l

        # Row 0: From / To / Region.
        grid.addWidget(_lbl("From"),   0, 0)
        grid.addWidget(self.date_from, 0, 1)
        grid.addWidget(_lbl("To"),     0, 2)
        grid.addWidget(self.date_to,   0, 3)
        grid.addWidget(_lbl("Region"), 0, 4)
        grid.addWidget(self.cmb_region, 0, 5)

        # Row 1: Type / Source / Reset (below All Regions).
        grid.addWidget(_lbl("Type"),    1, 0)
        grid.addWidget(self.cmb_type,   1, 1)
        grid.addWidget(_lbl("Source"),  1, 2)
        grid.addWidget(self.cmb_source, 1, 3)
        grid.addWidget(btn_refresh,     1, 5, Qt.AlignmentFlag.AlignLeft)

        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        grid.setColumnStretch(5, 1)
        body.addLayout(grid, 1)

        # Vertical divider between the grid and the Quick column.
        _div = QFrame()
        _div.setFixedWidth(1)
        _div.setStyleSheet("background: #21262D; border: none;")
        _div.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        body.addWidget(_div)

        # ── Quick column on the right (vertically centered) ──
        quick_col = QVBoxLayout(); quick_col.setSpacing(2)
        quick_col.setContentsMargins(0, 0, 0, 0)
        quick_col.addStretch(1)
        quick_lbl = QLabel("Quick")
        quick_lbl.setStyleSheet(
            "color: #8B949E; font-size: 10px; font-weight: 700;"
            " letter-spacing: 0.5px; background: transparent;"
        )
        quick_col.addWidget(quick_lbl)
        quick_btns = QHBoxLayout(); quick_btns.setSpacing(6)

        def _quick_btn(text):
            b = QPushButton(text)
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedHeight(28)
            b.setMinimumWidth(78)
            b.setCheckable(True)
            b.setStyleSheet(
                "QPushButton { background: transparent; border: 1px solid #30363D;"
                "  color: #C9D1D9; border-radius: 6px; padding: 4px 12px;"
                "  font-size: 11px; font-weight: 700; }"
                "QPushButton:hover { background: rgba(255,255,255,0.05); }"
                "QPushButton:checked { background: #1e63e4;"
                "  border: 1px solid #1e63e4; color: white; }"
            )
            return b

        btn_today = _quick_btn("Today")
        btn_week  = _quick_btn("This Week")
        btn_month = _quick_btn("This Month")

        def _set_today():
            today = QDate.currentDate()
            self.date_from.setDate(today)
            self.date_to.setDate(today)
            btn_today.setChecked(True)
            btn_week.setChecked(False); btn_month.setChecked(False)
            self.refresh()

        def _set_week():
            today = QDate.currentDate()
            self.date_from.setDate(today.addDays(-today.dayOfWeek() + 1))
            self.date_to.setDate(today)
            btn_week.setChecked(True)
            btn_today.setChecked(False); btn_month.setChecked(False)
            self.refresh()

        def _set_month():
            today = QDate.currentDate()
            self.date_from.setDate(QDate(today.year(), today.month(), 1))
            self.date_to.setDate(today)
            btn_month.setChecked(True)
            btn_today.setChecked(False); btn_week.setChecked(False)
            self.refresh()

        btn_today.clicked.connect(_set_today)
        btn_week.clicked.connect(_set_week)
        btn_month.clicked.connect(_set_month)

        quick_btns.addWidget(btn_today)
        quick_btns.addWidget(btn_week)
        quick_btns.addWidget(btn_month)
        # Today checked by default to match the target screenshot.
        btn_today.setChecked(True)
        quick_col.addLayout(quick_btns)
        quick_col.addStretch(1)
        body.addLayout(quick_col, 0)

        flay.addLayout(body)
        root.addWidget(fb)

        # ── KPI tiles row — 5 cards (REG / OT / Avg Eff / Total UE / Downtime) ──
        self.kpi_reg  = _PersonalKpiTile("REG Cases",  "Regular cases", "tabler_clipboard_text.svg", "#58A6FF")
        self.kpi_ot   = _PersonalKpiTile("OT Cases",   "Overtime cases", "tabler_clock.svg", "#F0883E")
        self.kpi_eff  = _PersonalKpiTile("Avg Eff %",  "Average efficiency", "tabler_percentage_30.svg", "#A371F7")
        self.kpi_ue   = _PersonalKpiTile("Total UE",   "Equivalent units", "tabler_congruent_to.svg", "#E89720")
        self.kpi_down = _PersonalKpiTile("Downtime",   "Total minutes",   "tabler_clock_off.svg",     "#F85149")

        kpi_row = QHBoxLayout(); kpi_row.setSpacing(10)
        for c in (self.kpi_reg, self.kpi_ot, self.kpi_eff, self.kpi_ue, self.kpi_down):
            kpi_row.addWidget(c, 1)
        root.addLayout(kpi_row)

        # ── Period Summary — Fluent card with header + table ───
        period_grp = QFrame()
        period_grp.setObjectName("periodCard")
        try:
            from .widgets import _icon_url as _icu_pp
            _chev_pp = _icu_pp("tabler_chevron_down.svg")
        except Exception:
            _chev_pp = ""
        period_grp.setStyleSheet(
            "#periodCard { background: #0D1117;"
            "  border: 1px solid #21262D; border-radius: 12px; }"
            "QLabel { background: transparent; color: #C9D1D9; }"
            "QComboBox { background: #161B22; border: 1px solid #30363D;"
            "  border-radius: 6px; padding: 4px 22px 4px 8px; color: #E6EDF3;"
            "  font-size: 11px; min-height: 26px; }"
            "QComboBox::drop-down { subcontrol-origin: padding;"
            "  subcontrol-position: right center; width: 22px; border: none; }"
            f"QComboBox::down-arrow {{ image: url({_chev_pp});"
            "  width: 12px; height: 12px; }"
            "QTableWidget { background: transparent;"
            "  border: none; gridline-color: transparent;"
            "  color: #E6EDF3; outline: none; font-size: 10px; }"
            "QTableWidget::item { padding: 5px 6px; border: none;"
            "  border-bottom: 1px solid #161B22; }"
            "QTableWidget::item:selected { background: rgba(56,139,253,0.14);"
            "  color: #E6EDF3; }"
            "QHeaderView { background: transparent; border: none; }"
            "QHeaderView::section { background: #161B22; color: #8B949E;"
            "  padding: 6px 6px; border: none;"
            "  border-bottom: 1px solid #21262D;"
            "  font-weight: 700; font-size: 9px; letter-spacing: 0.5px; }"
        )
        pl = QVBoxLayout(period_grp)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(0)

        # Header row: 📊 PERIOD SUMMARY + week-picker on the right.
        hdr = QHBoxLayout()
        hdr.setContentsMargins(14, 10, 14, 10)
        hdr.setSpacing(8)
        try:
            from .tabler_icons import TablerIcon as _TI_ph
            _ic = QLabel(); _ic.setFixedSize(14, 14)
            _ic.setPixmap(
                _TI_ph("tabler_chart_bar.svg").icon(color=QColor("#58A6FF")).pixmap(14, 14)
            )
            hdr.addWidget(_ic, 0, Qt.AlignmentFlag.AlignVCenter)
        except Exception:
            pass
        _h_t = QLabel("PERIOD SUMMARY")
        _h_t.setStyleSheet(
            "color: #58A6FF; font-size: 11px; font-weight: 800;"
            " letter-spacing: 0.6px;"
        )
        hdr.addWidget(_h_t)
        hdr.addStretch(1)
        _pick_lbl = QLabel("Pick week")
        _pick_lbl.setStyleSheet(
            "color: #8B949E; font-size: 10px; font-weight: 700;"
            " letter-spacing: 0.5px;"
        )
        hdr.addWidget(_pick_lbl)
        self.cmb_week = QComboBox()
        self.cmb_week.setMinimumWidth(220)
        self.cmb_week.setFixedHeight(28)
        self._populate_week_combo()
        self.cmb_week.currentIndexChanged.connect(self._on_pick_week)
        hdr.addWidget(self.cmb_week)
        pl.addLayout(hdr)

        # Divider line between header and table.
        _d = QFrame()
        _d.setFixedHeight(1)
        _d.setStyleSheet("background: #21262D; border: none;")
        pl.addWidget(_d)

        self.tbl_period = _make_table([
            "Period",
            "Avg UE (REG)",
            "Avg DT (m)",
            "Cases",
        ])
        self.tbl_period.setMinimumHeight(240)
        self.tbl_period.setMaximumHeight(320)
        self.tbl_period.setAlternatingRowColors(False)
        self.tbl_period.setShowGrid(False)
        _th = self.tbl_period.horizontalHeader()
        # Period + Cases stretch; UE + Downtime are fixed narrow. OT excluded
        # from UE — OT is tracked entirely separately.
        _th.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        _th.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        _th.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        _th.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.tbl_period.setColumnWidth(1, 104)  # UE (REG)
        self.tbl_period.setColumnWidth(2, 96)   # Downtime (m)
        _th.setStretchLastSection(False)
        _th.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        pl.addWidget(self.tbl_period)

        # ── Donuts container on the right (50/50 split with Period) ──
        self.donut_source = _DonutCard("UE by Source")
        self.donut_type   = _DonutCard("UE by Case Type")
        donuts_col = QHBoxLayout(); donuts_col.setSpacing(10)
        donuts_col.addWidget(self.donut_source, 1)
        donuts_col.addWidget(self.donut_type, 1)

        period_donuts_row = QHBoxLayout(); period_donuts_row.setSpacing(12)
        period_donuts_row.addWidget(period_grp, 1)
        donuts_wrap = QWidget(); donuts_wrap.setLayout(donuts_col)
        period_donuts_row.addWidget(donuts_wrap, 1)
        # Lock both halves to identical max height so they line up.
        _CARD_MAX_H = 460
        period_grp.setMaximumHeight(_CARD_MAX_H)
        self.donut_source.setMaximumHeight(_CARD_MAX_H)
        self.donut_type.setMaximumHeight(_CARD_MAX_H)
        donuts_wrap.setMaximumHeight(_CARD_MAX_H)
        root.addLayout(period_donuts_row)

        # ── Charts row 1 ──────────────────────────────────────────────
        self._ch1 = QHBoxLayout()
        self._ch1.setSpacing(16)
        self._bar  = StackedBarChart()
        self._bar.setMinimumSize(220, 220)
        self._line = LineChart()
        self._line.setMinimumSize(220, 220)
        self._bar_grp  = _chart_group("Daily UE — REG vs OT", self._bar)
        self._line_grp = _chart_group("Efficiency % Trend",   self._line)
        self._bar_grp.setMinimumHeight(280)
        self._line_grp.setMinimumHeight(280)

        # Day-by-day sliders for both charts (rolling 14-day window).
        from PySide6.QtWidgets import QSlider as _QSld
        slider_css = (
            "QSlider::groove:horizontal { background: #21262D;"
            "  height: 4px; border-radius: 2px; }"
            "QSlider::sub-page:horizontal { background: #1F6FEB;"
            "  border-radius: 2px; }"
            "QSlider::handle:horizontal { background: #C9D1D9;"
            "  width: 12px; height: 12px; margin: -4px 0;"
            "  border-radius: 6px; }"
            "QSlider::handle:horizontal:hover { background: #FFFFFF; }"
        )
        self._bar_slider = _QSld(Qt.Orientation.Horizontal)
        self._bar_slider.setStyleSheet(slider_css)
        self._bar_slider.setVisible(False)
        self._bar_slider.valueChanged.connect(self._apply_bar_window)
        self._bar_grp.layout().addWidget(self._bar_slider)

        self._line_slider = _QSld(Qt.Orientation.Horizontal)
        self._line_slider.setStyleSheet(slider_css)
        self._line_slider.setVisible(False)
        self._line_slider.valueChanged.connect(self._apply_line_window)
        self._line_grp.layout().addWidget(self._line_slider)

        # Full data cached for slider pagination.
        self._bar_window_size = 14
        self._bar_full: tuple[list, list, list] = ([], [], [])
        self._line_window_size = 14
        self._line_full: tuple[list, list] = ([], [])

        self._ch1.addWidget(self._bar_grp)
        self._ch1.addWidget(self._line_grp)
        root.addLayout(self._ch1)

        # ── Charts row 2 ──────────────────────────────────────────────
        self._ch2 = QHBoxLayout()
        self._ch2.setSpacing(16)
        self._pie   = PieChart(hole=0.0)
        self._pie.setMinimumSize(220, 240)
        self._donut = PieChart(hole=0.5)
        self._donut.setMinimumSize(220, 240)
        self._pie_grp   = _chart_group("UE by Region",    self._pie)
        self._donut_grp = _chart_group("UE by Case Type", self._donut)
        self._pie_grp.setMinimumHeight(340)
        self._donut_grp.setMinimumHeight(340)
        self._pie_grp.setMaximumHeight(380)
        self._donut_grp.setMaximumHeight(380)
        self._ch2.addWidget(self._pie_grp)
        self._ch2.addWidget(self._donut_grp)
        root.addLayout(self._ch2)

        # ── Insights panel (50% width) ────────────────────────────────
        self._advice_box = QFrame()
        self._advice_box.setObjectName("insightsCard")
        self._advice_box.setStyleSheet(
            "#insightsCard { background: #0D1117;"
            "  border: 1px solid #21262D; border-radius: 12px; }"
            "QLabel { background: transparent; }"
        )
        _ins_v = QVBoxLayout(self._advice_box)
        _ins_v.setContentsMargins(14, 12, 14, 12)
        _ins_v.setSpacing(8)
        _ins_hdr = QLabel("INSIGHTS & SUGGESTIONS")
        _ins_hdr.setStyleSheet(
            "color: #1F6FEB; font-size: 11px; font-weight: 800;"
            " letter-spacing: 0.5px;"
        )
        _ins_v.addWidget(_ins_hdr)
        from PySide6.QtWidgets import QScrollArea as _QSA
        self._advice_scroll = _QSA()
        self._advice_scroll.setWidgetResizable(True)
        self._advice_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._advice_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._advice_scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollBar:vertical { background: transparent; width: 8px;"
            "  margin: 0; border: none; }"
            "QScrollBar::handle:vertical { background: #30363D;"
            "  border-radius: 4px; min-height: 24px; }"
            "QScrollBar::handle:vertical:hover { background: #484F58; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {"
            "  height: 0; background: none; border: none; }"
        )
        _advice_holder = QWidget()
        _advice_holder.setStyleSheet("background: transparent;")
        self._advice_layout = QVBoxLayout(_advice_holder)
        self._advice_layout.setContentsMargins(0, 0, 4, 0)
        self._advice_layout.setSpacing(8)
        self._advice_layout.addStretch(1)
        self._advice_scroll.setWidget(_advice_holder)
        self._advice_box.setMaximumHeight(360)
        _ins_v.addWidget(self._advice_scroll, 1)

        # ── Region Breakdown card built first so it sits beside Insights ──
        self.tbl_region = _make_table(
            ["Region", "REG Cases", "OT Cases", "Total UE", "Avg Eff %"]
        )
        self.tbl_region.setMinimumHeight(180)
        region_grp = _chart_group("Region Breakdown", self.tbl_region)
        region_grp.setMaximumHeight(360)

        _ins_row = QHBoxLayout(); _ins_row.setSpacing(12)
        _ins_row.addWidget(self._advice_box, 1)
        _ins_row.addWidget(region_grp, 1)
        root.addLayout(_ins_row)

        # ── Daily Summary table (60% width) ───────────────────────────
        self._tbl_row = QHBoxLayout()
        self._tbl_row.setSpacing(16)

        self.tbl_daily = _make_table(
            ["Date", "REG", "OT", "UE (REG)", "UE (OT)", "Avg Eff %", "Downtime (m)"]
        )
        self.tbl_daily.setMinimumHeight(180)
        daily_grp = _chart_group("Daily Summary", self.tbl_daily)

        # ── "Coming soon" placeholder card (40% beside Daily Summary) ──
        coming = QFrame()
        coming.setObjectName("comingCard")
        coming.setStyleSheet(
            "#comingCard { background: #0D1117;"
            "  border: 1px dashed #30363D; border-radius: 12px; }"
            "QLabel { background: transparent; }"
        )
        cv = QVBoxLayout(coming)
        cv.setContentsMargins(18, 18, 18, 18)
        cv.setSpacing(6)
        cv.addStretch(1)
        try:
            from .tabler_icons import TablerIcon
            from PySide6.QtCore import QSize as _QSz
            icon_lbl = QLabel()
            icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            pm = TablerIcon("tabler_sparkles.svg").icon(
                color=QColor("#1F6FEB")
            ).pixmap(36, 36)
            icon_lbl.setPixmap(pm)
            cv.addWidget(icon_lbl)
        except Exception:
            pass
        t = QLabel("Coming soon")
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        t.setStyleSheet(
            "color: #C9D1D9; font-size: 14px; font-weight: 700;"
        )
        s = QLabel("New improvements and metrics on the way.")
        s.setAlignment(Qt.AlignmentFlag.AlignCenter)
        s.setStyleSheet("color: #8B949E; font-size: 11px;")
        cv.addWidget(t)
        cv.addWidget(s)
        cv.addStretch(1)
        coming.setMaximumHeight(360)

        self._tbl_row.addWidget(daily_grp, 6)
        self._tbl_row.addWidget(coming, 4)
        root.addLayout(self._tbl_row)

    # ------------------------------------------------------------------
    # Responsive layout
    # ------------------------------------------------------------------

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not hasattr(self, "_tbl_row"):   # guard: fires before _init_ui completes
            return
        two_col = event.size().width() >= 980
        direction = (
            QBoxLayout.Direction.LeftToRight
            if two_col
            else QBoxLayout.Direction.TopToBottom
        )
        for layout in (self._ch1, self._ch2, self._tbl_row):
            layout.setDirection(direction)

    # ------------------------------------------------------------------
    # Team view — placeholder. The Excel/SharePoint pipeline that used to
    # feed this view was removed entirely (per-designer file conflicts);
    # team visibility is coming back via a SharePoint List, not implemented
    # yet.
    # ------------------------------------------------------------------

    def _build_team_page(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl = QLabel(
            "Team view — próximamente vía SharePoint List.\n\n"
            "Mientras tanto, usa la vista Personal."
        )
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet("color: #8B949E; font-size: 13px;")
        v.addWidget(lbl)
        return page

    def _on_view_changed(self, view_id: int):
        # 0 = Team, 1 = Personal
        self._view_stack.setCurrentIndex(view_id)
        self._apply_view_button_styles()

    def _apply_view_button_styles(self):
        sel = (
            "QPushButton { background-color: #1757D4; color: white; "
            "border: none; border-radius: 4px; padding: 5px 14px; "
            "font-weight: 600; font-size: 12px; }"
        )
        unsel = (
            "QPushButton { background-color: transparent; color: #8B949E; "
            "border: 1px solid #30363D; border-radius: 4px; padding: 5px 14px; "
            "font-size: 12px; } "
            "QPushButton:hover { color: #E6EDF3; border-color: #5A6068; }"
        )
        self.btn_view_team.setStyleSheet(sel if self.btn_view_team.isChecked() else unsel)
        self.btn_view_personal.setStyleSheet(sel if self.btn_view_personal.isChecked() else unsel)

    # ------------------------------------------------------------------
    # Refresh (Personal view)
    # ------------------------------------------------------------------

    def _reset_filters(self):
        """Reset every filter to its default value. Auto-refresh runs
        once at the end via the last signal."""
        today = QDate.currentDate()
        # Block signals while bulk-setting so refresh() only fires once.
        for w in (self.date_from, self.date_to,
                  self.cmb_region, self.cmb_type, self.cmb_source):
            try:
                w.blockSignals(True)
            except Exception:
                pass
        try:
            self.date_from.setDate(today.addDays(-30))
            self.date_to.setDate(today)
            self.cmb_region.setCurrentIndex(0)
            self.cmb_type.setCurrentIndex(0)
            self.cmb_source.setCurrentIndex(0)
        finally:
            for w in (self.date_from, self.date_to,
                      self.cmb_region, self.cmb_type, self.cmb_source):
                try:
                    w.blockSignals(False)
                except Exception:
                    pass
        self.refresh()

    def refresh(self):
        d_from  = self.date_from.date().toString("yyyy-MM-dd")
        d_to    = self.date_to.date().toString("yyyy-MM-dd")
        region  = self.cmb_region.currentData()
        tipo    = self.cmb_type.currentData()
        source  = self.cmb_source.currentData()

        # Cache: re-running the full refresh with identical filters is the
        # common case (case_saved → refresh, plus several other signals fire
        # in quick succession). Skip the heavy chart/table rebuilds when the
        # underlying data hasn't changed since the last refresh.
        cache_key = (d_from, d_to, region, tipo, source)
        last_key = getattr(self, "_last_refresh_key", None)
        last_ts = getattr(self, "_last_refresh_ts", 0.0)
        import time as _t
        now = _t.time()
        if (last_key == cache_key) and (now - last_ts) < 1.5:
            # Same filters within 1.5 s — collapse the burst into one refresh.
            return

        reg_rows = self._query_cases("cases",    d_from, d_to, region, tipo) \
                   if source in ("both", "reg") else []
        ot_rows  = self._query_cases("ot_cases", d_from, d_to, region, tipo) \
                   if source in ("both", "ot") else []
        down_map = self._query_downtime(d_from, d_to)

        self._update_kpis(reg_rows, ot_rows, down_map)
        self._update_period_summary()
        self._update_charts(reg_rows, ot_rows)
        self._update_daily_table(reg_rows, ot_rows, down_map)
        self._update_region_table(reg_rows, ot_rows)
        self._update_advice(reg_rows, ot_rows, down_map, d_from, d_to)

        self._last_refresh_key = cache_key
        self._last_refresh_ts = now

    # ------------------------------------------------------------------
    # DB queries
    # ------------------------------------------------------------------

    def _query_cases(
        self, table: str, d_from: str, d_to: str,
        region, tipo
    ) -> list[dict]:
        params = [d_from, d_to]
        # Exclude NC (no-count-for-production) rows from every dashboard KPI.
        where  = "fecha BETWEEN ? AND ? AND COALESCE(count_production, 1) = 1"
        if region:
            where += " AND region = ?"
            params.append(region)
        if tipo:
            where += " AND tipo_caso = ?"
            params.append(tipo)
        sql = (
            f"SELECT fecha, region, tipo_caso, "
            f"COUNT(*) AS cnt_cases, "
            f"SUM(CASE WHEN efficiency BETWEEN 0 AND 1000 THEN efficiency ELSE 0 END) AS sum_eff, "
            f"SUM(CASE WHEN efficiency BETWEEN 0 AND 1000 THEN 1 ELSE 0 END) AS cnt_eff, "
            f"SUM(case_value) AS sum_cv "
            f"FROM {table} "
            f"WHERE {where} "
            f"GROUP BY fecha, region, tipo_caso "
            f"ORDER BY fecha"
        )
        units_eq = load_units_eq_data()
        result: list[dict] = []
        try:
            conn = get_connection()
            cur  = conn.cursor()
            cur.execute(sql, params)
            for fecha, reg, tipo_c, cnt, sum_eff, cnt_eff, sum_cv in cur.fetchall():
                ue = calculate_equivalent_units(
                    units_eq,
                    reg or "",
                    tipo_c or "",
                    (sum_cv or 0.0),
                    count=(cnt or 0),
                )
                result.append({
                    "fecha":    fecha,
                    "region":   reg,
                    "tipo":     tipo_c,
                    "count":    cnt or 0,
                    "sum_eff":  sum_eff or 0.0,
                    "eff_count": cnt_eff or 0,
                    "ue":       ue,
                })
            conn.close()
        except Exception as exc:
            print(f"[Dashboard] {table}: {exc}")
        return result

    def _query_downtime(self, d_from: str, d_to: str) -> dict[str, float]:
        result: dict[str, float] = {}
        try:
            conn = get_connection()
            cur  = conn.cursor()
            cur.execute(
                "SELECT fecha, SUM(duracion) FROM downtimes "
                "WHERE fecha BETWEEN ? AND ? GROUP BY fecha",
                [d_from, d_to],
            )
            for fecha, total in cur.fetchall():
                result[fecha] = float(total or 0)
            conn.close()
        except Exception as exc:
            print(f"[Dashboard] downtime: {exc}")
        return result

    # ------------------------------------------------------------------
    # KPI update
    # ------------------------------------------------------------------

    def _update_kpis(self, reg_rows, ot_rows, down_map):
        total_reg  = sum(r["count"]   for r in reg_rows)
        total_ot   = sum(r["count"]   for r in ot_rows)
        total_ue   = sum(r["ue"]      for r in reg_rows) + sum(r["ue"] for r in ot_rows)
        all_rows   = reg_rows + ot_rows
        sum_eff    = sum(r["sum_eff"] for r in all_rows)
        eff_count  = sum(r.get("eff_count", 0) for r in all_rows)
        avg_eff    = sum_eff / eff_count if eff_count else 0.0
        total_down = sum(down_map.values())

        self.kpi_reg.set_value(str(total_reg))
        self.kpi_ot.set_value(str(total_ot))
        self.kpi_ue.set_value(f"{total_ue:.2f}")
        self.kpi_eff.set_value(f"{avg_eff:.1f}%")
        h, m = divmod(int(total_down), 60)
        self.kpi_down.set_value(f"{int(total_down)}m" if h == 0 else f"{h}h {m}m")

        # Donut: UE by Source (REG / OT).
        ue_reg = sum(r["ue"] for r in reg_rows)
        ue_ot = sum(r["ue"] for r in ot_rows)
        self.donut_source.set_data(
            [("REG", ue_reg, "#1F6FEB"), ("OT", ue_ot, "#F0883E")],
            subtitle=f"% of {ue_reg + ue_ot:.2f} total UE",
        )

        # Donut: UE by Case Type (groups all rows by tipo_caso → sums UE).
        by_type: dict[str, float] = {}
        for r in (reg_rows + ot_rows):
            t = (r.get("tipo") or r.get("type") or "Other") or "Other"
            by_type[t] = by_type.get(t, 0.0) + r["ue"]
        # Palette by type — stable per name.
        palette = [
            "#1F6FEB", "#F0883E", "#3FB950", "#A371F7", "#D29922",
            "#E84393", "#00B5D8", "#7C4DFF", "#9CCC65", "#FF6B61",
        ]
        type_slices = [
            (k, v, palette[sum(ord(c) for c in k) % len(palette)])
            for k, v in sorted(by_type.items(), key=lambda x: -x[1])
        ]
        self.donut_type.set_data(
            type_slices,
            subtitle=f"% of {sum(by_type.values()):.2f} total UE",
        )

    # ------------------------------------------------------------------
    # Charts update
    # ------------------------------------------------------------------

    def _sync_chart_sliders(self):
        """Show/hide + size the bar/line sliders based on total days and
        push the current window slice into the chart widgets."""
        # Bar
        n = len(self._bar_full[0])
        w = self._bar_window_size
        if n > w:
            self._bar_slider.blockSignals(True)
            self._bar_slider.setRange(0, n - w)
            # Default: latest window.
            self._bar_slider.setValue(n - w)
            self._bar_slider.blockSignals(False)
            self._bar_slider.setVisible(True)
        else:
            self._bar_slider.setVisible(False)
        self._apply_bar_window()

        # Line
        n2 = len(self._line_full[0])
        if n2 > self._line_window_size:
            self._line_slider.blockSignals(True)
            self._line_slider.setRange(0, n2 - self._line_window_size)
            self._line_slider.setValue(n2 - self._line_window_size)
            self._line_slider.blockSignals(False)
            self._line_slider.setVisible(True)
        else:
            self._line_slider.setVisible(False)
        self._apply_line_window()

    def _apply_bar_window(self):
        labels, ue_reg, ue_ot = self._bar_full
        if not labels:
            self._bar.set_data([], [("REG", _BLUE, []), ("OT", _ORANGE, [])])
            return
        n = len(labels)
        w = min(self._bar_window_size, n)
        start = self._bar_slider.value() if self._bar_slider.isVisible() else 0
        start = max(0, min(start, max(0, n - w)))
        end = start + w
        self._bar.set_data(
            labels[start:end],
            [("REG", _BLUE, ue_reg[start:end]),
             ("OT",  _ORANGE, ue_ot[start:end])],
        )

    def _apply_line_window(self):
        labels, vals = self._line_full
        if not labels:
            self._line.set_data([], [], ref_line=100.0)
            return
        n = len(labels)
        w = min(self._line_window_size, n)
        start = self._line_slider.value() if self._line_slider.isVisible() else 0
        start = max(0, min(start, max(0, n - w)))
        end = start + w
        self._line.set_data(labels[start:end], vals[start:end], ref_line=100.0)

    def _update_charts(self, reg_rows, ot_rows):
        daily: dict[str, dict] = defaultdict(
            lambda: {"ue_reg": 0.0, "ue_ot": 0.0, "sum_eff": 0.0, "cnt_eff": 0}
        )
        for r in reg_rows:
            d = daily[r["fecha"]]
            d["ue_reg"]  += r["ue"]
            d["sum_eff"] += r["sum_eff"]
            d["cnt_eff"] += r.get("eff_count", 0)
        for r in ot_rows:
            d = daily[r["fecha"]]
            d["ue_ot"] += r["ue"]
            d["sum_eff"] += r["sum_eff"]
            d["cnt_eff"] += r.get("eff_count", 0)

        dates  = sorted(daily.keys())
        years = {d[:4] for d in dates}
        if len(years) > 1:
            short = [f"{d[2:4]}-{d[5:]}" for d in dates]   # YY-MM-DD
        else:
            short = [d[5:] for d in dates]                 # MM-DD
        ue_reg = [daily[d]["ue_reg"] for d in dates]
        ue_ot  = [daily[d]["ue_ot"]  for d in dates]
        avg_eff = [
            daily[d]["sum_eff"] / daily[d]["cnt_eff"]
            if daily[d]["cnt_eff"] else 0.0
            for d in dates
        ]

        # Cache the full series so the sliders can paginate without
        # touching the DB again. The slider is only visible when the
        # window-size threshold is exceeded.
        self._bar_full = (short, ue_reg, ue_ot)
        self._line_full = (short, avg_eff)
        self._sync_chart_sliders()

        by_region: dict[str, float] = defaultdict(float)
        by_type:   dict[str, float] = defaultdict(float)
        for r in reg_rows + ot_rows:
            by_region[r["region"] or "Unknown"] += r["ue"]
            by_type[r["tipo"]     or "Unknown"] += r["ue"]

        sr = sorted(by_region.items(), key=lambda x: -x[1])
        self._pie.set_data([x[0] for x in sr], [x[1] for x in sr])

        st = sorted(by_type.items(), key=lambda x: -x[1])
        self._donut.set_data([x[0] for x in st], [x[1] for x in st])

        total_ue = sum(v for _, v in sr)
        if hasattr(self, "_pie_grp"):
            self._pie_grp.setTitle(f"UE by Region (% of {total_ue:.2f} total UE)")
        if hasattr(self, "_donut_grp"):
            self._donut_grp.setTitle(f"UE by Case Type (% of {total_ue:.2f} total UE)")

    # ------------------------------------------------------------------
    # Advice update
    # ------------------------------------------------------------------

    def _query_period_summary(self, d_from: str, d_to: str):
        """Return (total_cases, avg_eff, total_ue) for REG cases in a date range."""
        units_eq = load_units_eq_data()
        total_cases = 0
        sum_eff = 0.0
        total_ue = 0.0
        try:
            conn = get_connection()
            cur  = conn.cursor()
            cur.execute(
                "SELECT region, tipo_caso, COUNT(*), SUM(efficiency), SUM(case_value) "
                "FROM cases WHERE fecha BETWEEN ? AND ? "
                "GROUP BY region, tipo_caso",
                [d_from, d_to],
            )
            for reg, tipo_c, cnt, s_eff, sum_cv in cur.fetchall():
                total_cases += cnt or 0
                sum_eff     += s_eff or 0.0
                total_ue    += calculate_equivalent_units(
                    units_eq,
                    reg or "",
                    tipo_c or "",
                    (sum_cv or 0.0),
                    count=(cnt or 0),
                )
            conn.close()
        except Exception:
            pass
        avg_eff = sum_eff / total_cases if total_cases else 0.0
        return total_cases, avg_eff, total_ue

    def _query_period_metrics(self, d_from: str, d_to: str) -> dict:
        """REG+OT combined metrics for a date range.

        UE breakdown:
        - cases_ue   : UE strictly from completed cases (no downtime credit).
        - dt_min     : sum of approved downtime minutes in the range.
        - ue_with_dt : cases_ue + (dt_min / DAILY_BASE_MINUTES) * DAILY_TARGET_EQ_UNITS
                       — credits the user as if downtime minutes had produced
                       at the daily target rate.
        active_days = distinct fechas with any case OR any downtime.
        """
        from tabs.utils import DAILY_BASE_MINUTES, DAILY_TARGET_EQ_UNITS
        units_eq = load_units_eq_data()
        reg_cases = 0
        ot_cases = 0
        sum_eff = 0.0
        eff_count = 0
        cases_ue = 0.0
        ue_reg = 0.0
        ue_ot = 0.0
        dt_min = 0.0
        active_days: set = set()
        reg_days: set = set()   # distinct dates with REG cases — the worked days
        try:
            conn = get_connection()
            cur = conn.cursor()
            for table, is_ot in (("cases", False), ("ot_cases", True)):
                cur.execute(
                    f"SELECT fecha, region, tipo_caso, COUNT(*), "
                    f"SUM(CASE WHEN efficiency BETWEEN 0 AND 1000 THEN efficiency ELSE 0 END), "
                    f"SUM(CASE WHEN efficiency BETWEEN 0 AND 1000 THEN 1 ELSE 0 END), "
                    f"SUM(case_value) "
                    f"FROM {table} WHERE fecha BETWEEN ? AND ? "
                    f"AND COALESCE(count_production, 1) = 1 "
                    f"GROUP BY fecha, region, tipo_caso",
                    [d_from, d_to],
                )
                for fecha, reg, tipo_c, cnt, s_eff, c_eff, sum_cv in cur.fetchall():
                    cnt = cnt or 0
                    bucket_ue = calculate_equivalent_units(
                        units_eq,
                        reg or "",
                        tipo_c or "",
                        (sum_cv or 0.0),
                        count=cnt,
                    )
                    cases_ue += bucket_ue
                    if is_ot:
                        ot_cases += cnt
                        ue_ot += bucket_ue
                    else:
                        reg_cases += cnt
                        ue_reg += bucket_ue
                    sum_eff += s_eff or 0.0
                    eff_count += c_eff or 0
                    if fecha and cnt > 0:
                        active_days.add(fecha)
                        if not is_ot:
                            reg_days.add(fecha)

            # Approved downtime in range (matches daily_performance.py filter)
            cur.execute(
                "SELECT fecha, SUM(duracion) FROM downtimes "
                "WHERE fecha BETWEEN ? AND ? "
                "AND (status = 'approved' OR status IS NULL) "
                "GROUP BY fecha",
                [d_from, d_to],
            )
            for fecha, total in cur.fetchall():
                dur = float(total or 0)
                dt_min += dur
                if fecha and dur > 0:
                    active_days.add(fecha)
            conn.close()
        except Exception as exc:
            print(f"[Dashboard] period_metrics: {exc}")

        ue_with_dt = cases_ue + (dt_min / DAILY_BASE_MINUTES) * DAILY_TARGET_EQ_UNITS
        avg_eff = sum_eff / eff_count if eff_count else 0.0
        return {
            "reg_cases": reg_cases,
            "ot_cases": ot_cases,
            "total_cases": reg_cases + ot_cases,
            "ue_no_dt": cases_ue,
            "ue_with_dt": ue_with_dt,
            "ue_reg": ue_reg,
            "ue_ot": ue_ot,
            "dt_min": dt_min,
            "downtime_min": dt_min,
            "avg_eff": avg_eff,
            "active_days": len(active_days),
            "reg_days": len(reg_days),
        }

    @staticmethod
    def _iso_week_range(year: int, week: int) -> tuple:
        """Return (monday, sunday) date objects for ISO week `week` of `year`."""
        from datetime import date as _date, timedelta as _td
        jan4 = _date(year, 1, 4)
        week1_mon = jan4 - _td(days=jan4.isoweekday() - 1)
        mon = week1_mon + _td(weeks=week - 1)
        sun = mon + _td(days=6)
        return mon, sun

    def _populate_week_combo(self):
        """Fill week dropdown with ISO weeks of current and previous year.
        Default selection = current ISO week."""
        from datetime import date as _date
        today = _date.today()
        cur_year, cur_week, _ = today.isocalendar()

        # Prior year: all 52/53 weeks. Current year: up to current week.
        items: list[tuple] = []
        for y in (cur_year - 1, cur_year):
            # Max week of a year = isocalendar of Dec 28 (always in last ISO week)
            max_w = _date(y, 12, 28).isocalendar()[1]
            last_w = cur_week if y == cur_year else max_w
            for w in range(1, last_w + 1):
                mon, sun = self._iso_week_range(y, w)
                label = f"Week {w} {y} ({mon.isoformat()} → {sun.isoformat()})"
                items.append((label, y, w))

        self.cmb_week.blockSignals(True)
        self.cmb_week.clear()
        default_idx = 0
        for i, (label, y, w) in enumerate(items):
            self.cmb_week.addItem(label, (y, w))
            if y == cur_year and w == cur_week:
                default_idx = i
        self.cmb_week.setCurrentIndex(default_idx)
        self.cmb_week.blockSignals(False)

    def _on_pick_week(self, _idx: int):
        """Refresh just the Period Summary so the Selected Week row updates."""
        try:
            self._update_period_summary()
        except Exception as exc:
            print(f"[Dashboard] week pick refresh failed: {exc}")

    @staticmethod
    def _period_ranges() -> list[tuple]:
        """Return [(label, d_from, d_to)] for the standard period buckets.
        Anchored on today's local date."""
        from datetime import date as _date
        today = _date.today()

        # Week: Monday → today (ISO weekday Mon=0 in date.weekday())
        week_from = today.fromordinal(today.toordinal() - today.weekday())

        # Month: 1st → today
        month_from = today.replace(day=1)

        # Quarter: first day of current 3-month quarter → today
        q_start_month = ((today.month - 1) // 3) * 3 + 1
        q_from = today.replace(month=q_start_month, day=1)

        # Year-to-date
        ytd_from = today.replace(month=1, day=1)

        iso = lambda d: d.isoformat()
        return [
            ("Today",        iso(today),      iso(today)),
            ("This Week",    iso(week_from),  iso(today)),
            ("This Month",   iso(month_from), iso(today)),
            ("This Quarter", iso(q_from),     iso(today)),
            ("YTD",          iso(ytd_from),   iso(today)),
        ]

    def _update_period_summary(self):
        ranges = list(self._period_ranges())

        # Append the dropdown-selected ISO week as the last row.
        picked = self.cmb_week.currentData() if hasattr(self, "cmb_week") else None
        if picked:
            y, w = picked
            mon, sun = self._iso_week_range(y, w)
            ranges.append((f"Week {w} {y}", mon.isoformat(), sun.isoformat()))

        # New table shape: Period | UE (REG) | Downtime (m) | Cases
        # Each numeric column is an AVERAGE per active workday — matches the
        # weekly reports the team receives so the user can validate that
        # local data lines up with the shared dashboard.
        self.tbl_period.setRowCount(len(ranges))
        self.tbl_period.verticalHeader().setDefaultSectionSize(30)
        for row, (label, d_from, d_to) in enumerate(ranges):
            m = self._query_period_metrics(d_from, d_to)
            reg_ue = m.get("ue_reg")
            if reg_ue is None:
                reg_ue = m.get("ue_no_dt", 0.0)
            downtime_min = m.get("downtime_min", 0)
            active = max(1, int(m.get("active_days", 0) or 0))
            # UE average: divide by worked days = days with REG cases only
            # (Mon–Fri; Saturday is an OT-only day and must not dilute REG).
            reg_worked = max(1, int(m.get("reg_days", 0) or 0))
            avg_reg_ue = reg_ue / reg_worked
            avg_downtime = downtime_min / active
            cases_label = f"{m['total_cases']} ({m['reg_cases']}R/{m['ot_cases']}OT)"
            _fill_row(self.tbl_period, row, [
                label,
                f"{avg_reg_ue:.2f}",
                f"{int(round(avg_downtime))}",
                cases_label,
            ])

    def _query_top_doctor(self, d_from: str, d_to: str):
        """Return (doctor_name, count) for the doctor with most REG cases, or None."""
        try:
            conn = get_connection()
            cur  = conn.cursor()
            cur.execute(
                "SELECT doctor, COUNT(*) AS cnt FROM cases "
                "WHERE fecha BETWEEN ? AND ? AND doctor != '' "
                "GROUP BY doctor ORDER BY cnt DESC LIMIT 1",
                [d_from, d_to],
            )
            row = cur.fetchone()
            conn.close()
            return (row[0], row[1]) if row else None
        except Exception:
            return None

    def _query_non_counting(self, d_from: str, d_to: str) -> int:
        """Return count of REG cases with count_production = 0 in range."""
        try:
            conn = get_connection()
            cur  = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM cases "
                "WHERE fecha BETWEEN ? AND ? AND count_production = 0",
                [d_from, d_to],
            )
            row = cur.fetchone()
            conn.close()
            return row[0] if row else 0
        except Exception:
            return 0

    def _update_advice(self, reg_rows, ot_rows, down_map, d_from: str, d_to: str):
        # Clear previous items but keep the trailing stretch alive.
        while self._advice_layout.count():
            item = self._advice_layout.takeAt(0)
            if item is None:
                break
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._advice_layout.addStretch(1)

        advice: list[tuple[str, str]] = []

        total_reg = sum(r["count"] for r in reg_rows)
        total_ot  = sum(r["count"] for r in ot_rows)
        total_all = total_reg + total_ot

        # Per-row efficiency checks — consolidated to avoid visual noise
        high_eff_rows = []   # (avg, loc)
        low_eff_rows  = []   # (avg, loc)
        for r in reg_rows:
            eff_n = r.get("eff_count", 0)
            if eff_n > 0:
                avg = r["sum_eff"] / eff_n
                loc = f"{r['fecha']} | {r['region']} | {r['tipo']}"
                if avg > 300:
                    high_eff_rows.append((avg, loc))
                elif avg < 20 and eff_n >= 3:
                    low_eff_rows.append((avg, loc))

        if len(high_eff_rows) == 1:
            avg, loc = high_eff_rows[0]
            advice.append(("warning",
                f"Avg efficiency {avg:.0f}% on {loc}. Verify std_time in Standards."))
        elif len(high_eff_rows) > 1:
            worst_avg, worst_loc = max(high_eff_rows, key=lambda x: x[0])
            advice.append(("warning",
                f"{len(high_eff_rows)} combos with unusually high efficiency (>300%). "
                f"Worst: {worst_avg:.0f}% on {worst_loc}. Verify std_time in Standards."))

        if len(low_eff_rows) == 1:
            avg, loc = low_eff_rows[0]
            advice.append(("warning",
                f"Low avg efficiency {avg:.0f}% on {loc}. Review these cases."))
        elif len(low_eff_rows) > 1:
            advice.append(("warning",
                f"{len(low_eff_rows)} combos with low efficiency (<20%). Review these cases."))

        # OT ratio
        if total_all > 0:
            ot_ratio = total_ot / total_all
            if ot_ratio > 0.5:
                advice.append(("warning",
                    f"OT cases are {ot_ratio*100:.0f}% of work ({total_ot}/{total_all}). "
                    "Review workload balance."))
            elif total_ot > 0 and ot_ratio <= 0.15:
                advice.append(("good",
                    f"Healthy OT ratio — only {ot_ratio*100:.0f}% ({total_ot}) overtime cases."))
            elif total_ot == 0:
                advice.append(("info", "No OT cases in this period."))

        # Downtime with no cases logged
        all_dates = {r["fecha"] for r in reg_rows + ot_rows}
        for fecha, mins in down_map.items():
            if fecha not in all_dates and mins > 0:
                advice.append(("info",
                    f"{fecha}: {mins:.0f} min downtime logged but no cases registered."))

        # Top region
        by_region: dict[str, float] = defaultdict(float)
        for r in reg_rows + ot_rows:
            by_region[r["region"] or "Unknown"] += r["ue"]
        if by_region:
            top_r, top_ue = max(by_region.items(), key=lambda x: x[1])
            advice.append(("good", f"Top region: {top_r} — {top_ue:.2f} UE."))

        # Top case type
        by_type: dict[str, float] = defaultdict(float)
        for r in reg_rows + ot_rows:
            by_type[r["tipo"] or "Unknown"] += r["ue"]
        if by_type:
            top_t, top_t_ue = max(by_type.items(), key=lambda x: x[1])
            advice.append(("tip",
                f"Most productive type: {top_t} ({top_t_ue:.2f} UE). "
                "Consider prioritising on high-load days."))

        # ── NEW: Trend vs previous equal-length period ─────────────────
        try:
            from datetime import date as _date, timedelta as _td
            dt_from = _date.fromisoformat(d_from)
            dt_to   = _date.fromisoformat(d_to)
            span    = (dt_to - dt_from).days + 1
            prev_to   = dt_from - _td(days=1)
            prev_from = prev_to  - _td(days=span - 1)
            _, cur_avg,  cur_ue  = self._query_period_summary(d_from, d_to)
            _, prev_avg, prev_ue = self._query_period_summary(
                prev_from.isoformat(), prev_to.isoformat()
            )
            if prev_avg > 0:
                delta_eff = cur_avg - prev_avg
                delta_ue  = cur_ue  - prev_ue
                icon = "good" if delta_eff >= 0 else "warning"
                sign_e = "+" if delta_eff >= 0 else ""
                sign_u = "+" if delta_ue  >= 0 else ""
                advice.append((icon,
                    f"vs previous {span}-day period: Avg Eff {sign_e}{delta_eff:.1f}% "
                    f"({prev_avg:.1f}% → {cur_avg:.1f}%), "
                    f"UE {sign_u}{delta_ue:.2f} ({prev_ue:.2f} → {cur_ue:.2f})."))
        except Exception:
            pass

        # ── NEW: Inactive days (no cases, no downtime) ─────────────────
        try:
            from datetime import date as _date, timedelta as _td
            dt_from = _date.fromisoformat(d_from)
            dt_to   = _date.fromisoformat(d_to)
            active  = {r["fecha"] for r in reg_rows + ot_rows} | set(down_map.keys())
            inactive = []
            cur_d = dt_from
            while cur_d <= dt_to:
                if cur_d.isoformat() not in active:
                    inactive.append(cur_d.isoformat())
                cur_d += _td(days=1)
            if inactive:
                if len(inactive) <= 3:
                    advice.append(("info",
                        f"No activity on: {', '.join(inactive)}."))
                else:
                    advice.append(("info",
                        f"{len(inactive)} days with no cases or downtime "
                        f"({inactive[0]} … {inactive[-1]})."))
        except Exception:
            pass

        # ── NEW: Top doctor ────────────────────────────────────────────
        top_doc = self._query_top_doctor(d_from, d_to)
        if top_doc:
            doc_name, doc_cnt = top_doc
            advice.append(("tip",
                f"Most active doctor: {doc_name} ({doc_cnt} REG cases)."))

        # ── NEW: Non-counting cases ────────────────────────────────────
        nc_count = self._query_non_counting(d_from, d_to)
        if nc_count > 0 and total_reg > 0:
            nc_pct = nc_count / total_reg * 100
            icon = "warning" if nc_pct >= 20 else "info"
            advice.append((icon,
                f"{nc_count} REG case{'s' if nc_count > 1 else ''} "
                f"({nc_pct:.1f}%) marked as not counting for production."))

        # ── NEW: Consecutive days below 100% efficiency ────────────────
        try:
            daily_eff: dict[str, float] = defaultdict(
                lambda: {"s": 0.0, "n": 0}
            )
            for r in reg_rows:
                daily_eff[r["fecha"]]["s"] += r["sum_eff"]
                daily_eff[r["fecha"]]["n"] += r["count"]
            eff_by_day = {
                d: v["s"] / v["n"]
                for d, v in daily_eff.items()
                if v["n"] > 0
            }
            sorted_days = sorted(eff_by_day.keys())
            streak, max_streak, streak_start = 0, 0, None
            ms_start = None
            for d in sorted_days:
                if eff_by_day[d] < 100:
                    if streak == 0:
                        streak_start = d
                    streak += 1
                    if streak > max_streak:
                        max_streak = streak
                        ms_start   = streak_start
                else:
                    streak = 0
            if max_streak >= 3:
                advice.append(("warning",
                    f"{max_streak} consecutive days below 100% efficiency "
                    f"(starting {ms_start}). Check workload or standards."))
        except Exception:
            pass

        if total_all == 0:
            advice.append(("info", "No cases found for the selected filters and date range."))

        def _insert(item):
            # Insert above the trailing stretch.
            self._advice_layout.insertWidget(
                self._advice_layout.count() - 1, item,
            )

        for kind, text in advice:
            _insert(_AdviceItem(kind, text))

        if not advice:
            _insert(_AdviceItem("good", "Everything looks normal for the selected period."))

    # ------------------------------------------------------------------
    # Daily table update
    # ------------------------------------------------------------------

    def _update_daily_table(self, reg_rows, ot_rows, down_map):
        daily: dict[str, dict] = defaultdict(
            lambda: {"reg": 0, "ot": 0, "ue_reg": 0.0, "ue_ot": 0.0,
                     "sum_eff": 0.0, "eff_count": 0, "down": 0.0}
        )
        for r in reg_rows:
            d = daily[r["fecha"]]
            d["reg"]     += r["count"]
            d["ue_reg"]  += r["ue"]
            d["sum_eff"] += r["sum_eff"]
            d["eff_count"] += r.get("eff_count", 0)
        for r in ot_rows:
            d = daily[r["fecha"]]
            d["ot"]    += r["count"]
            d["ue_ot"] += r["ue"]
            d["sum_eff"] += r["sum_eff"]
            d["eff_count"] += r.get("eff_count", 0)
        for fecha, mins in down_map.items():
            daily[fecha]["down"] = mins

        dates = sorted(daily.keys())
        self.tbl_daily.setRowCount(len(dates))
        for i, fecha in enumerate(dates):
            d   = daily[fecha]
            avg = d["sum_eff"] / d["eff_count"] if d["eff_count"] else 0.0
            _fill_row(self.tbl_daily, i, [
                fecha,
                str(d["reg"]),
                str(d["ot"]),
                f"{d['ue_reg']:.2f}",
                f"{d['ue_ot']:.2f}",
                f"{avg:.1f}%",
                f"{d['down']:.0f}",
            ])

    # ------------------------------------------------------------------
    # Region table update
    # ------------------------------------------------------------------

    def _update_region_table(self, reg_rows, ot_rows):
        by_region: dict[str, dict] = defaultdict(
            lambda: {"reg": 0, "ot": 0, "ue": 0.0, "sum_eff": 0.0, "eff_count": 0}
        )
        for r in reg_rows:
            d = by_region[r["region"] or "Unknown"]
            d["reg"]     += r["count"]
            d["ue"]      += r["ue"]
            d["sum_eff"] += r["sum_eff"]
            d["eff_count"] += r.get("eff_count", 0)
        for r in ot_rows:
            d = by_region[r["region"] or "Unknown"]
            d["ot"] += r["count"]
            d["ue"] += r["ue"]
            d["sum_eff"] += r["sum_eff"]
            d["eff_count"] += r.get("eff_count", 0)

        regions = sorted(by_region.keys())
        self.tbl_region.setRowCount(len(regions))
        for i, region in enumerate(regions):
            d   = by_region[region]
            avg = d["sum_eff"] / d["eff_count"] if d["eff_count"] else 0.0
            _fill_row(self.tbl_region, i, [
                region,
                str(d["reg"]),
                str(d["ot"]),
                f"{d['ue']:.2f}",
                f"{avg:.1f}%",
            ])

    # ------------------------------------------------------------------
    # Theme hook — swap chart palette, restyle cards, force repaint
    # ------------------------------------------------------------------

    def update_font_sizes(self, _new_size: int = 0):
        """Refresh widgets that build QFont objects with hard-coded point sizes
        and force charts to repaint with the new global scale."""
        for card in self.findChildren(_Card):
            try:
                card.update_font_sizes()
            except Exception:
                pass
        for child in self.findChildren(QWidget):
            try:
                child.update()
            except Exception:
                pass

    def update_theme_labels(self, is_light: bool):
        if is_light:
            self.title_label.setStyleSheet("font-size: 16px; font-weight: 700; color: #111; letter-spacing: 0.5px;")
        else:
            self.title_label.setStyleSheet("font-size: 16px; font-weight: 700; color: #E6EDF3; letter-spacing: 0.5px;")

        # Mutate module-level chart colors so paintEvent picks them up next frame
        _apply_chart_palette(is_light)

        # Re-theme KPI cards (title color depends on theme)
        for card in self.findChildren(_Card):
            try:
                card.apply_theme(is_light)
            except Exception:
                pass

        # Force a repaint of every chart child so the new palette renders now
        for child in self.findChildren(QWidget):
            try:
                child.update()
            except Exception:
                pass
