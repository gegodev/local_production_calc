from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QPropertyAnimation, QEasingCurve, Property, Signal
from PySide6.QtGui import QPainter, QColor, QPen


class ToggleSwitch(QWidget):
    toggled = Signal(bool)

    # Visual constants — bumped to a chunkier 56x28 pill.
    _W = 50
    _H = 25
    _CIRCLE = 19
    _OFF_X = 3
    _ON_X = _W - _CIRCLE - _OFF_X  # mirror padding on the right

    def __init__(self, parent=None, checked=True, color_on=None, color_off=None):
        super().__init__(parent)
        self._checked = checked
        self._circle_position = self._ON_X if checked else self._OFF_X
        self._color_on  = color_on  or QColor(76, 175, 80)   # green default
        self._color_off = color_off or QColor(158, 158, 158)  # gray default
        self.setFixedSize(self._W, self._H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        # Animation
        self._animation = QPropertyAnimation(self, b"circle_position", self)
        self._animation.setDuration(150)
        self._animation.setEasingCurve(QEasingCurve.Type.InOutCubic)

    def apply_palette(self, is_light: bool):
        """Force repaint so the off-state track adapts to the active theme."""
        self.update()
    
    def get_circle_position(self):
        return self._circle_position
    
    def set_circle_position(self, pos):
        self._circle_position = pos
        self.update()
    
    circle_position = Property(float, get_circle_position, set_circle_position)
    
    def isChecked(self):
        return self._checked
    
    def setChecked(self, checked):
        if self._checked != checked:
            self._checked = checked
            self._animation.setStartValue(self._circle_position)
            self._animation.setEndValue(self._ON_X if checked else self._OFF_X)
            self._animation.start()
            self.toggled.emit(checked)
    
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setChecked(not self._checked)
    
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Background
        bg_color = self._color_on if self._checked else self._color_off
        
        painter.setBrush(bg_color)
        painter.setPen(Qt.PenStyle.NoPen)
        r = self._H // 2
        painter.drawRoundedRect(0, 0, self._W, self._H, r, r)

        # Circle
        painter.setBrush(QColor(255, 255, 255))
        circle_y = (self._H - self._CIRCLE) // 2
        painter.drawEllipse(int(self._circle_position), circle_y,
                            self._CIRCLE, self._CIRCLE)
