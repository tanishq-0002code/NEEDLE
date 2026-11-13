from __future__ import annotations

import math
import time

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QWidget

from .theme import ACCENT, ACCENT_2


class WaveformWidget(QWidget):
    """A lightweight glowing audio wave; no blur effects or shaders."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(68)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._accent = QColor(ACCENT)
        self._accent2 = QColor(ACCENT_2)
        self._active = False
        self._thinking = False
        self._level = 0.0
        self._display_level = 0.0
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)

    def set_accent(self, value: str) -> None:
        color = QColor(value)
        if color.isValid():
            self._accent = color
            self._accent2 = QColor.fromHsv((color.hsvHue() + 48) % 360, 185, 245)

    def set_active(self, active: bool) -> None:
        self._active = bool(active)
        if not active:
            self._thinking = False
        self.update()

    def set_thinking(self, thinking: bool) -> None:
        self._thinking = bool(thinking)
        self.update()

    def set_level(self, value: float) -> None:
        self._level = max(0.0, min(1.0, float(value)))

    def _tick(self) -> None:
        self._phase += 0.13
        target = self._level
        if self._display_level < target:
            self._display_level = min(target, self._display_level + 0.16)
        else:
            self._display_level *= 0.90
        self._level *= 0.93
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        width = max(1, self.width())
        height = max(1, self.height())
        center = height / 2.0

        # A faint baseline gives the card a grounded, Gemini-like feel.
        painter.setPen(QPen(QColor(255, 255, 255, 16), 1.0))
        painter.drawLine(0, int(center), width, int(center))

        amplitude = 4.0 + self._display_level * 27.0
        if not self._active and not self._thinking:
            amplitude = 2.0

        points: list[QPointF] = []
        samples = max(48, width // 4)
        for index in range(samples + 1):
            x = width * index / samples
            normalized = index / samples
            envelope = math.sin(math.pi * normalized) ** 0.65
            if self._thinking:
                wave = math.sin(normalized * 8.0 * math.pi + self._phase * 0.9) * 0.20
                wave += math.sin(normalized * 3.0 * math.pi - self._phase * 0.5) * 0.12
            else:
                wave = math.sin(normalized * 5.2 * math.pi + self._phase) * 0.55
                wave += math.sin(normalized * 11.0 * math.pi - self._phase * 1.7) * 0.20
                wave += math.sin(normalized * 2.0 * math.pi + self._phase * 0.4) * 0.25
            y = center + wave * amplitude * envelope
            points.append(QPointF(x, y))

        path = QPainterPath()
        path.moveTo(points[0])
        for point in points[1:]:
            path.lineTo(point)

        gradient = QLinearGradient(0, 0, width, 0)
        gradient.setColorAt(0.0, self._accent)
        gradient.setColorAt(0.55, self._accent2)
        gradient.setColorAt(1.0, self._accent)

        # Layered strokes make a soft glow without a QGraphicsBlurEffect.
        for stroke_width, alpha in ((16.0, 16), (9.0, 32), (4.5, 75), (2.2, 235)):
            color = gradient
            pen = QPen(color, stroke_width)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            # QPen does not expose alpha directly for gradients; compose a
            # translucent copy of the gradient via a solid overlay if needed.
            if alpha < 235:
                painter.setOpacity(alpha / 255.0)
            else:
                painter.setOpacity(1.0)
            painter.setPen(pen)
            painter.drawPath(path)
        painter.setOpacity(1.0)

        if self._thinking:
            # A bright pulse travels across the wave while Needle is routing.
            travel = (self._phase * 0.22) % 1.0
            x = width * travel
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(self._accent2.red(), self._accent2.green(), self._accent2.blue(), 180))
            painter.drawEllipse(QRectF(x - 3.0, center - 3.0, 6.0, 6.0))
        painter.end()


class PulseDot(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(14, 14)
        self._accent = QColor(ACCENT)
        self._active = False
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(40)

    def set_accent(self, value: str) -> None:
        color = QColor(value)
        if color.isValid():
            self._accent = color

    def set_active(self, active: bool) -> None:
        self._active = bool(active)

    def _tick(self) -> None:
        self._phase += 0.11
        if self._active:
            self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        center = QPointF(self.width() / 2, self.height() / 2)
        if self._active:
            pulse = 0.5 + 0.5 * math.sin(self._phase)
            painter.setPen(Qt.PenStyle.NoPen)
            for radius, alpha in ((7.0, 20), (5.5, 35), (4.0, 55)):
                color = QColor(self._accent)
                color.setAlpha(int(alpha + pulse * 20))
                painter.setBrush(color)
                painter.drawEllipse(center, radius + pulse * 1.2, radius + pulse * 1.2)
            color = QColor(self._accent)
            color.setAlpha(245)
            painter.setBrush(color)
            painter.drawEllipse(center, 2.6, 2.6)
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(100, 105, 125))
            painter.drawEllipse(center, 2.2, 2.2)
        painter.end()
