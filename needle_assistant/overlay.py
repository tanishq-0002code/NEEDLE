from __future__ import annotations

import logging
import time

from PyQt6.QtCore import (
    QEasingCurve,
    QPoint,
    QParallelAnimationGroup,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import QColor, QCursor, QFont, QGuiApplication, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient
from PyQt6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .catalog import badge_text, describe_call
from .config import AppConfig
from .models import ExecutionResult, ToolCall
from .theme import ACCENT, ACCENT_2, MUTED, app_icon, accent_color
from .waveform import PulseDot, WaveformWidget
from . import windows_api

log = logging.getLogger("needle.overlay")

OVERLAY_HEIGHT = 300
OVERLAY_MARGIN = 30


class GlassCard(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("GlassCard")
        self._accent = QColor(ACCENT)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def set_accent(self, value: str) -> None:
        self._accent = accent_color(value)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        radius = 22.0

        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)

        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        gradient.setColorAt(0.0, QColor(28, 31, 44, 246))
        gradient.setColorAt(0.48, QColor(19, 22, 32, 242))
        gradient.setColorAt(1.0, QColor(11, 13, 20, 238))
        painter.fillPath(path, gradient)

        # A soft accent bloom in the lower-right corner.
        glow = QRadialGradient(rect.right() - 100, rect.bottom() + 40, rect.width() * 0.55)
        glow.setColorAt(0.0, QColor(self._accent.red(), self._accent.green(), self._accent.blue(), 26))
        glow.setColorAt(1.0, QColor(self._accent.red(), self._accent.green(), self._accent.blue(), 0))
        painter.fillPath(path, glow)

        # Top glass highlight and one-pixel rim.
        painter.setPen(QPen(QColor(255, 255, 255, 34), 1.0))
        painter.drawLine(int(rect.left() + radius), int(rect.top() + 2),
                         int(rect.right() - radius), int(rect.top() + 2))
        painter.setPen(QPen(QColor(255, 255, 255, 32), 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, radius, radius)
        painter.end()


class OverlayWindow(QWidget):
    dismissed = pyqtSignal()
    hidden = pyqtSignal()

    def __init__(self, config: AppConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self._accent = config.overlay_accent
        self._state = "idle"
        self._source = ""
        self._thinking_started = 0.0
        self._hiding = False
        self._effects_applied = False
        self._animations: list[QPropertyAnimation] = []
        self._group: QParallelAnimationGroup | None = None

        self.setWindowTitle("Needle Desktop")
        self.setWindowIcon(app_icon(self._accent))
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setWindowModality(Qt.WindowModality.NonModal)

        root = QVBoxLayout(self)
        root.setContentsMargins(OVERLAY_MARGIN, OVERLAY_MARGIN, OVERLAY_MARGIN, OVERLAY_MARGIN)
        root.setSpacing(0)
        self.card = GlassCard(self)
        root.addWidget(self.card)
        self.card.setGraphicsEffect(self._make_shadow())

        layout = QVBoxLayout(self.card)
        layout.setContentsMargins(26, 18, 26, 18)
        layout.setSpacing(9)

        header = QHBoxLayout()
        header.setSpacing(9)
        self.dot = PulseDot(self.card)
        self.dot.set_accent(self._accent)
        header.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignVCenter)

        self.title = QLabel("NEEDLE", self.card)
        self.title.setObjectName("OverlayTitle")
        title_font = QFont("Segoe UI Variable Display", 11)
        title_font.setWeight(QFont.Weight.Bold)
        title_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.8)
        self.title.setFont(title_font)
        self.title.setStyleSheet("color: #F2F3FA;")
        header.addWidget(self.title, 0, Qt.AlignmentFlag.AlignVCenter)

        self.local = QLabel("LOCAL · OFFLINE", self.card)
        self.local.setStyleSheet("color: #6F7488; font-size: 9px; font-weight: 600; letter-spacing: 1px;")
        header.addWidget(self.local, 0, Qt.AlignmentFlag.AlignVCenter)
        header.addStretch(1)

        self.status = QLabel("Ready", self.card)
        self.status.setStyleSheet("color: #9AA0B4; font-size: 11px; font-weight: 500;")
        header.addWidget(self.status, 0, Qt.AlignmentFlag.AlignVCenter)

        self.close_button = QToolButton(self.card)
        self.close_button.setText("✕")
        self.close_button.setFixedSize(28, 28)
        self.close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_button.setToolTip("Dismiss (Esc)")
        self.close_button.clicked.connect(self.dismissed.emit)
        header.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(header)

        self.waveform = WaveformWidget(self.card)
        self.waveform.set_accent(self._accent)
        layout.addWidget(self.waveform)

        self.transcript = QLabel("Press Ctrl+Space or say “Hey Jarvis”.", self.card)
        self.transcript.setWordWrap(True)
        self.transcript.setMinimumHeight(48)
        self.transcript.setMaximumHeight(58)
        self.transcript.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.transcript.setStyleSheet("color: #F2F3FA; font-size: 16px; font-weight: 500;")
        layout.addWidget(self.transcript)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.badge = QLabel("", self.card)
        self.badge.setVisible(False)
        self.badge.setStyleSheet(self._badge_style())
        footer.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignVCenter)
        footer.addStretch(1)
        self.hint = QLabel("Esc to dismiss  ·  Ctrl+Space", self.card)
        self.hint.setStyleSheet("color: #686D80; font-size: 9px;")
        footer.addWidget(self.hint, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(footer)

        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._tick_status)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide_animated)

        self.set_accent(self._accent)

    def _make_shadow(self) -> QGraphicsDropShadowEffect:
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(44)
        shadow.setOffset(0, 12)
        shadow.setColor(QColor(0, 0, 0, 190))
        return shadow

    def _badge_style(self) -> str:
        return (
            "QLabel {"
            f"background: rgba({accent_color(self._accent).red()}, {accent_color(self._accent).green()}, "
            f"{accent_color(self._accent).blue()}, 34);"
            f"color: {self._accent};"
            "border: 1px solid rgba(255,255,255,24);"
            "border-radius: 10px;"
            "padding: 3px 9px;"
            "font-size: 9px;"
            "font-weight: 700;"
            "letter-spacing: 0.7px;"
            "}"
        )

    def set_accent(self, value: str) -> None:
        color = accent_color(value)
        self._accent = value if color.isValid() else ACCENT
        self.dot.set_accent(self._accent)
        self.waveform.set_accent(self._accent)
        self.card.set_accent(self._accent)
        self.badge.setStyleSheet(self._badge_style())
        self.setWindowIcon(app_icon(self._accent))

    def _set_status(self, text: str, color: str = MUTED) -> None:
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: 600;")

    def _set_transcript(self, text: str, muted: bool = False) -> None:
        self.transcript.setText(text)
        color = "#858B9F" if muted else "#F2F3FA"
        self.transcript.setStyleSheet(f"color: {color}; font-size: 16px; font-weight: 500;")

    def _reset_visuals(self) -> None:
        self._status_timer.stop()
        self._hide_timer.stop()
        self.badge.hide()
        self.waveform.set_active(False)
        self.waveform.set_thinking(False)

    def show_listening(self, source: str = "hotkey") -> None:
        self._source = source
        self._state = "listening"
        self._reset_visuals()
        self.dot.set_active(True)
        self.waveform.set_active(True)
        self._set_status("Listening", ACCENT_2)
        self._set_transcript("Say a command…", muted=True)
        hint = {
            "wake": "Wake word detected",
            "hotkey": "Speak now",
            "tray": "Speak now",
            "typed": "Command",
            "welcome": "Ready when you are",
        }.get(source, "Speak now")
        self.hint.setText(f"{hint}  ·  Esc to dismiss")
        self.show_animated()

    def show_welcome(self) -> None:
        self._state = "welcome"
        self._reset_visuals()
        self.dot.set_active(True)
        self._set_status("Ready", ACCENT_2)
        self._set_transcript("Press Ctrl+Space or say “Hey Jarvis”.", muted=False)
        self.badge.setText("NEEDLE 3  ·  100% OFFLINE")
        self.badge.show()
        self.hint.setText("Esc to dismiss")
        self.show_animated()

    def set_partial(self, text: str) -> None:
        if self._state != "listening":
            return
        if text:
            self._set_transcript(text, muted=False)
            self._set_status("Listening", ACCENT_2)
        else:
            self._set_transcript("Say a command…", muted=True)

    def set_level(self, level: float) -> None:
        self.waveform.set_level(level)

    def set_thinking(self, text: str) -> None:
        self._state = "thinking"
        self._thinking_started = time.monotonic()
        self._hide_timer.stop()
        self._set_transcript(text or "Working on it…", muted=False)
        self._set_status("Thinking · local", ACCENT)
        self.badge.hide()
        self.waveform.set_active(True)
        self.waveform.set_thinking(True)
        self.hint.setText("Esc to dismiss")
        self._status_timer.start(500)
        self.show_animated()

    def _tick_status(self) -> None:
        if self._state != "thinking":
            self._status_timer.stop()
            return
        elapsed = max(0, int(time.monotonic() - self._thinking_started))
        self._set_status(f"Thinking · local · {elapsed}s", ACCENT)

    def show_tool(self, call: ToolCall, hide: bool = True) -> None:
        self._state = "executing"
        self._status_timer.stop()
        if hide:
            self.hide_immediately()
        self._hide_timer.stop()
        self._set_transcript(describe_call(call), muted=False)
        self._set_status("Executing", ACCENT_2)
        source = {
            "fast": "FAST ROUTE",
            "cache": "CACHED",
            "needle": "NEEDLE 3",
        }.get(call.source, "NEEDLE 3")
        self.badge.setText(f"{badge_text(call)}  ·  {source}")
        self.badge.show()
        self.waveform.set_active(True)
        self.waveform.set_thinking(False)
        self.dot.set_active(True)
        self.hint.setText("Working…")

    def show_result(self, result: ExecutionResult, call: ToolCall | None = None,
                    route_source: str = "needle") -> None:
        self._state = "done" if result.success else "error"
        self._status_timer.stop()
        self._hide_timer.stop()
        self._set_transcript(result.message, muted=False)
        self._set_status("Done" if result.success else "Couldn't do that",
                         ACCENT_2 if result.success else "#FF8E9B")
        if call and call.name:
            source = {
                "fast": "FAST ROUTE",
                "cache": "CACHED",
                "needle": "NEEDLE 3",
            }.get(route_source, "NEEDLE 3")
            self.badge.setText(f"{badge_text(call)}  ·  {source}")
            self.badge.show()
        self.waveform.set_active(False)
        self.waveform.set_thinking(False)
        self.dot.set_active(False)
        self.hint.setText("Dismissing…")
        self.show_animated()

    def show_error(self, message: str) -> None:
        self._state = "error"
        self._status_timer.stop()
        self._hide_timer.stop()
        self._set_transcript(message, muted=False)
        self._set_status("Error", "#FF8E9B")
        self.badge.hide()
        self.waveform.set_active(False)
        self.waveform.set_thinking(False)
        self.dot.set_active(False)
        self.hint.setText("Esc to dismiss")
        self.show_animated()

    def show_animated(self) -> None:
        self._hiding = False
        self._stop_animations()
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        width = max(520, min(int(self.config.overlay_width), available.width() - 40))
        height = OVERLAY_HEIGHT
        self.setFixedSize(width, height)
        x = available.x() + (available.width() - width) // 2
        target_y = available.y() + available.height() - height - 26
        start_y = target_y + 46
        if self.isVisible():
            self.move(x, target_y)
            self.raise_()
            self.setWindowOpacity(1.0)
            return
        self.move(x, start_y)
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        QTimer.singleShot(0, self._enable_effects)
        pos_anim = QPropertyAnimation(self, b"pos", self)
        pos_anim.setDuration(300)
        pos_anim.setStartValue(QPoint(x, start_y))
        pos_anim.setEndValue(QPoint(x, target_y))
        pos_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        opacity_anim = QPropertyAnimation(self, b"windowOpacity", self)
        opacity_anim.setDuration(220)
        opacity_anim.setStartValue(0.0)
        opacity_anim.setEndValue(1.0)
        opacity_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        group = QParallelAnimationGroup(self)
        group.addAnimation(pos_anim)
        group.addAnimation(opacity_anim)
        group.finished.connect(lambda: self._animations.clear())
        self._animations = [pos_anim, opacity_anim]
        self._group = group
        group.start()

    def _enable_effects(self) -> None:
        if self._effects_applied:
            return
        self._effects_applied = True
        if self.config.acrylic_blur:
            try:
                windows_api.enable_acrylic(int(self.winId()), 0xCC14141C)
                windows_api.enable_rounded_corners(int(self.winId()))
            except Exception:
                log.debug("Acrylic effects were not available.", exc_info=True)

    def _stop_animations(self) -> None:
        if self._group is not None:
            try:
                self._group.stop()
            except Exception:
                pass
            self._group = None
        self._animations.clear()

    def hide_immediately(self) -> None:
        self._hide_timer.stop()
        self._status_timer.stop()
        self._stop_animations()
        self._hiding = False
        self.hide()

    def hide_animated(self) -> None:
        self._hide_timer.stop()
        self._status_timer.stop()
        if not self.isVisible() or self._hiding:
            return
        self._hiding = True
        self._stop_animations()
        current = self.pos()
        pos_anim = QPropertyAnimation(self, b"pos", self)
        pos_anim.setDuration(190)
        pos_anim.setStartValue(current)
        pos_anim.setEndValue(QPoint(current.x(), current.y() + 34))
        pos_anim.setEasingCurve(QEasingCurve.Type.InCubic)
        opacity_anim = QPropertyAnimation(self, b"windowOpacity", self)
        opacity_anim.setDuration(180)
        opacity_anim.setStartValue(self.windowOpacity())
        opacity_anim.setEndValue(0.0)
        opacity_anim.setEasingCurve(QEasingCurve.Type.InCubic)
        group = QParallelAnimationGroup(self)
        group.addAnimation(pos_anim)
        group.addAnimation(opacity_anim)
        group.finished.connect(self._after_hide)
        self._group = group
        group.start()

    def _after_hide(self) -> None:
        self._hiding = False
        self.hide()
        self._group = None
        self.hidden.emit()

    def hide_after(self, milliseconds: int) -> None:
        self._hide_timer.stop()
        self._hide_timer.start(max(100, int(milliseconds)))

    def contains_global_point(self, x: int, y: int) -> bool:
        if not self.isVisible():
            return False
        return self.frameGeometry().contains(QPoint(int(x), int(y)))

    @property
    def state(self) -> str:
        return self._state

    @property
    def busy(self) -> bool:
        return self._state in {"thinking", "executing"}
