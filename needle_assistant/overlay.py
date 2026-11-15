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
from PyQt6.QtGui import (
    QAction,
    QColor,
    QCursor,
    QFont,
    QGuiApplication,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PyQt6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .catalog import badge_text, describe_call
from .config import AppConfig
from .models import ExecutionResult, ToolCall
from .theme import ACCENT, ACCENT_2, MUTED, accent_color, app_icon
from .waveform import PulseDot, WaveformWidget
from . import windows_api

log = logging.getLogger("needle.overlay")

OVERLAY_HEIGHT = 270
OVERLAY_MARGIN = 24


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
        radius = 24.0

        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)

        # Gemini-inspired deep cosmic glass gradient
        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        gradient.setColorAt(0.0, QColor(24, 28, 42, 248))
        gradient.setColorAt(0.5, QColor(16, 20, 32, 244))
        gradient.setColorAt(1.0, QColor(10, 12, 22, 242))
        painter.fillPath(path, gradient)

        # Soft ambient Gemini accent bloom (cyan-blue to purple) in lower area
        glow = QRadialGradient(rect.center().x(), rect.bottom() + 20, rect.width() * 0.65)
        glow.setColorAt(0.0, QColor(56, 189, 248, 30))
        glow.setColorAt(0.5, QColor(139, 124, 255, 22))
        glow.setColorAt(1.0, QColor(139, 124, 255, 0))
        painter.fillPath(path, glow)

        # Top glass highlight
        painter.setPen(QPen(QColor(255, 255, 255, 45), 1.0))
        painter.drawLine(
            int(rect.left() + radius),
            int(rect.top() + 2),
            int(rect.right() - radius),
            int(rect.top() + 2),
        )

        # Outer subtle glowing border
        painter.setPen(QPen(QColor(255, 255, 255, 30), 1.2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, radius, radius)
        painter.end()


class OverlayWindow(QWidget):
    dismissed = pyqtSignal()
    hidden = pyqtSignal()
    settings_requested = pyqtSignal()
    voice_triggered = pyqtSignal()
    reset_perch_requested = pyqtSignal()
    quit_requested = pyqtSignal()
    toggle_enabled_requested = pyqtSignal()
    command_submitted = pyqtSignal(str)

    def __init__(self, config: AppConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self.enabled = getattr(config, "enabled", True)
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
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(8)

        # Header row
        header = QHBoxLayout()
        header.setSpacing(10)
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

        self.status_pill = QLabel("100% OFFLINE", self.card)
        self.status_pill.setObjectName("OfflinePill")
        self.status_pill.setStyleSheet(
            "QLabel#OfflinePill { background-color: rgba(30, 41, 59, 180); color: #94A3B8; "
            "border: 1px solid rgba(255, 255, 255, 20); border-radius: 9px; padding: 2px 10px; "
            "font-size: 10px; font-weight: 600; }"
        )
        header.addWidget(self.status_pill, 0, Qt.AlignmentFlag.AlignVCenter)
        header.addStretch(1)

        self.mic_btn = QToolButton(self.card)
        self.mic_btn.setText("🎙️")
        self.mic_btn.setToolTip("Start listening")
        self.mic_btn.setStyleSheet(
            "QToolButton { background: transparent; border: none; font-size: 14px; padding: 2px; } "
            "QToolButton:hover { background-color: rgba(255, 255, 255, 20); border-radius: 6px; }"
        )
        self.mic_btn.clicked.connect(self.voice_triggered.emit)
        header.addWidget(self.mic_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        self.settings_btn = QToolButton(self.card)
        self.settings_btn.setText("⚙️")
        self.settings_btn.setToolTip("Settings")
        self.settings_btn.setStyleSheet(
            "QToolButton { background: transparent; border: none; font-size: 14px; padding: 2px; } "
            "QToolButton:hover { background-color: rgba(255, 255, 255, 20); border-radius: 6px; }"
        )
        self.settings_btn.clicked.connect(self.settings_requested.emit)
        header.addWidget(self.settings_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        self.close_btn = QToolButton(self.card)
        self.close_btn.setText("✕")
        self.close_btn.setToolTip("Dismiss")
        self.close_btn.setStyleSheet(
            "QToolButton { background: transparent; border: none; color: #858B9F; font-size: 13px; padding: 2px; } "
            "QToolButton:hover { color: #FFFFFF; background-color: rgba(255, 255, 255, 20); border-radius: 6px; }"
        )
        self.close_btn.clicked.connect(self.dismissed.emit)
        header.addWidget(self.close_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(header)

        # Google Gemini colorful glowing dynamic audio waveform
        self.waveform = WaveformWidget(self.card)
        self.waveform.set_accent(self._accent)
        layout.addWidget(self.waveform)

        # Live Transcript display
        self.transcript = QLabel("Say a command or type below…", self.card)
        self.transcript.setObjectName("Transcript")
        self.transcript.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.transcript.setWordWrap(True)
        t_font = QFont("Segoe UI", 13)
        t_font.setWeight(QFont.Weight.Medium)
        self.transcript.setFont(t_font)
        self.transcript.setStyleSheet("color: #858B9F; min-height: 28px;")
        layout.addWidget(self.transcript)

        # Gemini-style Text Command Input Bar (Type command fallback if mic is poor)
        input_row = QHBoxLayout()
        input_row.setSpacing(6)
        self.input_edit = QLineEdit(self.card)
        self.input_edit.setPlaceholderText("Type a command (e.g. open Chrome, take a screenshot, move cursor)...")
        self.input_edit.setStyleSheet(
            "QLineEdit { background-color: rgba(15, 23, 42, 180); color: #F1F5F9; "
            "border: 1px solid rgba(255, 255, 255, 24); border-radius: 12px; padding: 6px 14px; "
            "font-size: 12px; font-family: 'Segoe UI'; } "
            "QLineEdit:focus { border: 1px solid #38BDF8; background-color: rgba(15, 23, 42, 230); }"
        )
        self.input_edit.returnPressed.connect(self._on_text_submitted)
        input_row.addWidget(self.input_edit, 1)

        self.send_btn = QPushButton("➔", self.card)
        self.send_btn.setToolTip("Execute typed command")
        self.send_btn.setStyleSheet(
            "QPushButton { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #38BDF8, stop:1 #8B7CFF); "
            "color: #FFFFFF; font-weight: bold; font-size: 13px; border: none; border-radius: 12px; "
            "min-width: 34px; max-width: 34px; min-height: 28px; max-height: 28px; } "
            "QPushButton:hover { background: #60A5FA; } QPushButton:pressed { background: #2563EB; }"
        )
        self.send_btn.clicked.connect(self._on_text_submitted)
        input_row.addWidget(self.send_btn, 0)
        layout.addLayout(input_row)

        # Footer row: Action badge + Hint
        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.badge = QLabel("", self.card)
        self.badge.setStyleSheet(
            "background-color: rgba(56, 189, 248, 30); color: #38BDF8; border: 1px solid rgba(56, 189, 248, 60); "
            "border-radius: 8px; padding: 2px 8px; font-size: 11px; font-weight: 600;"
        )
        self.badge.hide()
        footer.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignVCenter)

        footer.addStretch(1)
        self.hint = QLabel("Esc to dismiss", self.card)
        self.hint.setStyleSheet("color: #64748B; font-size: 10px;")
        footer.addWidget(self.hint, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(footer)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide_animated)

        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._tick_thinking)

    def _make_shadow(self) -> QGraphicsDropShadowEffect:
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(36.0)
        shadow.setColor(QColor(0, 0, 0, 160))
        shadow.setOffset(0.0, 10.0)
        return shadow

    def _on_text_submitted(self) -> None:
        text = self.input_edit.text().strip()
        if not text:
            return
        self.input_edit.clear()
        self.command_submitted.emit(text)

    # ------------------------------------------------------------------ state
    @property
    def state(self) -> str:
        return self._state

    @property
    def busy(self) -> bool:
        return self._state in {"thinking", "executing"}

    def reset_perch(self) -> None:
        self.show_animated()

    def set_accent(self, accent: str) -> None:
        self._accent = accent
        self.card.set_accent(accent)
        self.dot.set_accent(accent)
        self.waveform.set_accent(accent)

    def set_level(self, level: float) -> None:
        self.waveform.set_level(level)

    # ------------------------------------------------------------------ public views
    def show_welcome(self) -> None:
        self._state = "welcome"
        self._status_timer.stop()
        self.transcript.setText("Ready when you are. Say 'Hey Needle' or press Ctrl+Space.")
        self.transcript.setStyleSheet("color: #F1F5F9;")
        self.status_pill.setText("READY")
        self.status_pill.setStyleSheet(
            "background-color: rgba(34, 197, 94, 30); color: #4ADE80; "
            "border: 1px solid rgba(74, 222, 128, 50); border-radius: 9px; padding: 2px 10px; font-size: 10px; font-weight: 600;"
        )
        self.badge.hide()
        self.waveform.set_active(False)
        self.waveform.set_thinking(False)
        self.dot.set_active(False)
        self.show_animated()

    def show_listening(self, source: str = "hotkey") -> None:
        self._state = "listening"
        self._source = source
        self._status_timer.stop()
        self._hide_timer.stop()
        self.transcript.setText("Listening…")
        self.transcript.setStyleSheet("color: #94A3B8;")
        self.status_pill.setText("LISTENING")
        self.status_pill.setStyleSheet(
            "background-color: rgba(56, 189, 248, 30); color: #38BDF8; "
            "border: 1px solid rgba(56, 189, 248, 60); border-radius: 9px; padding: 2px 10px; font-size: 10px; font-weight: 600;"
        )
        self.badge.hide()
        self.waveform.set_active(True)
        self.waveform.set_thinking(False)
        self.dot.set_active(True)
        self.show_animated()

    def set_partial(self, text: str) -> None:
        if self._state not in {"listening", "thinking"}:
            return
        text = (text or "").strip()
        if text:
            self.transcript.setText(text)
            self.transcript.setStyleSheet("color: #F8FAFC;")
        else:
            self.transcript.setText("Listening…")
            self.transcript.setStyleSheet("color: #94A3B8;")

    def set_thinking(self, text: str = "") -> None:
        self._state = "thinking"
        self._thinking_started = time.monotonic()
        self._hide_timer.stop()
        if text:
            self.transcript.setText(text)
            self.transcript.setStyleSheet("color: #F8FAFC;")
        self.status_pill.setText("THINKING")
        self.status_pill.setStyleSheet(
            "background-color: rgba(139, 124, 255, 30); color: #A78BFA; "
            "border: 1px solid rgba(139, 124, 255, 60); border-radius: 9px; padding: 2px 10px; font-size: 10px; font-weight: 600;"
        )
        self.waveform.set_active(True)
        self.waveform.set_thinking(True)
        self.dot.set_active(True)
        self._status_timer.start(500)
        self.show_animated()

    def _tick_thinking(self) -> None:
        if self._state != "thinking":
            self._status_timer.stop()
            return
        elapsed = time.monotonic() - self._thinking_started
        if elapsed > 1.2:
            self.status_pill.setText("ROUTING WITH NEEDLE 3")

    def show_tool(self, call: ToolCall, hide: bool = False) -> None:
        self._state = "executing"
        self._status_timer.stop()
        self.status_pill.setText("EXECUTING")
        self.status_pill.setStyleSheet(
            "background-color: rgba(245, 158, 11, 30); color: #FBBF24; "
            "border: 1px solid rgba(245, 158, 11, 60); border-radius: 9px; padding: 2px 10px; font-size: 10px; font-weight: 600;"
        )
        desc = describe_call(call)
        self.transcript.setText(desc)
        self.transcript.setStyleSheet("color: #F1F5F9;")
        badge = badge_text(call)
        if badge:
            self.badge.setText(badge)
            self.badge.show()
        if hide:
            self.hide_animated()

    def show_result(self, result: ExecutionResult, call: ToolCall | None = None, source: str = "needle") -> None:
        self._status_timer.stop()
        success = getattr(result, "success", False)
        self._state = "done" if success else "error"
        status_text = "DONE" if success else "FAILED"
        bg_color = "rgba(34, 197, 94, 30)" if success else "rgba(239, 68, 68, 30)"
        txt_color = "#4ADE80" if success else "#F87171"
        self.status_pill.setText(status_text)
        self.status_pill.setStyleSheet(
            f"background-color: {bg_color}; color: {txt_color}; "
            f"border: 1px solid {txt_color}; border-radius: 9px; padding: 2px 10px; font-size: 10px; font-weight: 600;"
        )
        msg = getattr(result, "message", "Done") if success else getattr(result, "message", "Couldn't do that")
        self.transcript.setText(msg)
        self.transcript.setStyleSheet("color: #F8FAFC;")
        if call is not None:
            badge = badge_text(call)
            if badge:
                self.badge.setText(f"{badge} · {source}")
                self.badge.show()
        self.waveform.set_active(False)
        self.waveform.set_thinking(False)
        self.dot.set_active(False)
        self.show_animated()

    def show_error(self, message: str) -> None:
        self._state = "error"
        self._status_timer.stop()
        self._hide_timer.stop()
        self.status_pill.setText("ERROR")
        self.status_pill.setStyleSheet(
            "background-color: rgba(239, 68, 68, 30); color: #F87171; "
            "border: 1px solid rgba(239, 68, 68, 60); border-radius: 9px; padding: 2px 10px; font-size: 10px; font-weight: 600;"
        )
        self.transcript.setText(message or "Something went wrong")
        self.transcript.setStyleSheet("color: #FCA5A5;")
        self.badge.hide()
        self.waveform.set_active(False)
        self.waveform.set_thinking(False)
        self.dot.set_active(False)
        self.show_animated()

    # ------------------------------------------------------------------ animation & positioning
    def show_animated(self) -> None:
        self._hiding = False
        self._stop_animations()
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        width = max(560, min(int(self.config.overlay_width), available.width() - 40))
        height = OVERLAY_HEIGHT
        self.setFixedSize(width, height)
        x = available.x() + (available.width() - width) // 2
        target_y = available.y() + available.height() - height - 20
        start_y = target_y + 45

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
        pos_anim.setDuration(240)
        pos_anim.setStartValue(QPoint(x, start_y))
        pos_anim.setEndValue(QPoint(x, target_y))
        pos_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        opacity_anim = QPropertyAnimation(self, b"windowOpacity", self)
        opacity_anim.setDuration(200)
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
                log.debug("Acrylic effects unavailable.", exc_info=True)

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
        self.hidden.emit()

    def hide_animated(self) -> None:
        self._hide_timer.stop()
        self._status_timer.stop()
        if not self.isVisible() or self._hiding:
            return
        self._hiding = True
        self._stop_animations()
        current = self.pos()
        pos_anim = QPropertyAnimation(self, b"pos", self)
        pos_anim.setDuration(180)
        pos_anim.setStartValue(current)
        pos_anim.setEndValue(QPoint(current.x(), current.y() + 35))
        pos_anim.setEasingCurve(QEasingCurve.Type.InCubic)

        opacity_anim = QPropertyAnimation(self, b"windowOpacity", self)
        opacity_anim.setDuration(160)
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

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background-color: rgba(15, 23, 42, 240); color: #F1F5F9; "
            "border: 1px solid rgba(255, 255, 255, 30); border-radius: 10px; padding: 6px; } "
            "QMenu::item { padding: 8px 18px; border-radius: 6px; } "
            "QMenu::item:selected { background-color: rgba(56, 189, 248, 0.35); }"
        )
        menu.addAction("⚙️ Settings & Microphones", self.settings_requested.emit)
        menu.addAction("🎙️ Listen for Voice Command", self.voice_triggered.emit)
        menu.addSeparator()
        menu.addAction("✕ Close", self.dismissed.emit)
        menu.exec(event.globalPos())
        event.accept()
