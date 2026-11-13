from __future__ import annotations

import math
import random
import re
import time
from typing import Callable

from PyQt6.QtCore import (
    QEasingCurve,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QAction,
    QCursor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PyQt6.QtWidgets import QApplication, QMenu, QWidget




CANVAS = 130
ORB = 92
ORB_CENTER = QPointF(65, 78)


class OrbWindow(QWidget):
    dismissed = pyqtSignal()
    hidden = pyqtSignal()
    settings_requested = pyqtSignal()
    voice_triggered = pyqtSignal()
    reset_perch_requested = pyqtSignal()
    quit_requested = pyqtSignal()
    toggle_enabled_requested = pyqtSignal()

    def __init__(self, config=None, parent=None):
        super().__init__(parent)
        self.config = config
        self.enabled = True
        self._state = "idle"
        self._source = ""
        self._text = ""
        self._muted = True
        self._level = 0.0
        self._resting_pos = None
        self._drag_offset = None
        self._dragging = False
        self._hiding = False
        self._animations: list[QPropertyAnimation] = []
        self._group = None
        self._time = 0.0
        self._bob_phase = 0.0
        self._blink_start = 0.0
        self._last_blink = time.monotonic()
        self._blink = 0.0
        self._pupil = QPointF(0.0, 0.0)
        self._tilt = 0.0
        self._hop = 0.0
        self._tap = 0.0

        self.setWindowTitle("Needle Desktop")
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
        self.setFixedSize(CANVAS, CANVAS)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

        screen = QApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            self._resting_pos = QPoint(
                avail.x() + (avail.width() - CANVAS) // 2,
                avail.y() + avail.height() - CANVAS - 24,
            )
        else:
            self._resting_pos = QPoint(100, 100)
        self.move(self._resting_pos)

        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._tick)
        self._anim_timer.start(16)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide_animated)

        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._tick_status)
        self._thinking_started = 0.0

    # ------------------------------------------------------------------ state
    @property
    def state(self) -> str:
        return self._state

    def _set_state(self, state: str) -> None:
        self._state = state
        if state == "thinking":
            self._thinking_started = time.monotonic()
            self._status_timer.start(500)
        else:
            self._status_timer.stop()

    # ------------------------------------------------------------------ helpers
    def _clamp(self, value: float, low: float, high: float) -> float:
        return max(low, min(high, value))

    def _bob_y(self) -> float:
        if self._state == "executing":
            return math.sin(self._time * 9.0) * 8.0 + self._hop
        if self._state == "happy":
            return -6.0 + self._hop
        return math.sin(self._time * 1.6) * 5.0 + self._hop

    # ------------------------------------------------------------------ public API
    def show_listening(self, source: str = "hotkey") -> None:
        self._source = source
        self._set_state("listening")
        self._text = "Say a command…"
        self._muted = True
        self._hide_timer.stop()
        self.show_animated()

    def show_welcome(self) -> None:
        self._set_state("welcome")
        self._text = "Ready when you are"
        self._muted = False
        self.show_animated()
        self.hide_after(4200)

    def set_partial(self, text: str) -> None:
        if self._state != "listening":
            return
        text = (text or "").strip()
        self._text = text if text else "Say a command…"
        self._muted = not bool(text)

    def set_level(self, level: float) -> None:
        self._level = self._clamp(float(level), 0.0, 1.0)

    def show_error(self, message: str) -> None:
        self._set_state("error")
        self._text = message or "Something went wrong"
        self._muted = False
        self.show_animated()

    def show_tool(self, call, hide: bool = True) -> None:
        self._set_state("executing")
        try:
            from needle_assistant.catalog import describe_call

            self._text = describe_call(call)
        except Exception:
            self._text = "Working…"
        self._muted = False
        target = self._target_for_call(call)
        self.fly_to(target)

    def show_result(self, result, call=None, route_source: str = "needle") -> None:
        if getattr(result, "success", False):
            self._set_state("happy")
            self._text = getattr(result, "message", "Done")
            self._hop = -8.0
            QTimer.singleShot(260, lambda: setattr(self, "_hop", 0.0))
        else:
            self._set_state("error")
            self._text = getattr(result, "message", "Couldn't do that")
        self._muted = False
        self.show_animated()
        self.hide_after(1200 if getattr(result, "success", False) else 2800)

    def hide_after(self, milliseconds: int) -> None:
        self._hide_timer.stop()
        self._hide_timer.start(max(100, int(milliseconds)))

    def hide_immediately(self) -> None:
        self._hide_timer.stop()
        self._status_timer.stop()
        self._set_state("idle")
        self._hiding = False
        self.hide()
        self.hidden.emit()

    def hide_animated(self) -> None:
        if not self.isVisible() or self._hiding:
            return
        self._hiding = True
        anim = QPropertyAnimation(self, b"windowOpacity", self)
        anim.setDuration(180)
        anim.setStartValue(self.windowOpacity())
        anim.setEndValue(0.0)
        anim.finished.connect(self._after_hide)
        anim.start()
        self._animations.append(anim)

    def _after_hide(self) -> None:
        self._hiding = False
        self.hide()
        self._group = None
        self.hidden.emit()

    def show_animated(self) -> None:
        self._hiding = False
        self._stop_animations()
        if not self.isVisible():
            self.setWindowOpacity(0.0)
            self.move(self._resting_pos or self.pos())
            self.show()
            self.raise_()
            anim = QPropertyAnimation(self, b"windowOpacity", self)
            anim.setDuration(220)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.start()
            self._animations.append(anim)
        else:
            self.move(self._resting_pos or self.pos())
            self.raise_()
            self.setWindowOpacity(1.0)

    def reset_perch(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            self._resting_pos = QPoint(
                avail.x() + avail.width() - CANVAS - 24,
                avail.y() + avail.height() - CANVAS - 24,
            )
        else:
            self._resting_pos = QPoint(100, 100)
        self.move(self._resting_pos)
        self.show_animated()

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background-color: rgba(15, 23, 42, 230); color: #F2F3FA; "
            "border: 1px solid rgba(255, 255, 255, 30); border-radius: 10px; padding: 6px; }"
            "QMenu::item { padding: 8px 18px; border-radius: 6px; }"
            "QMenu::item:selected { background-color: rgba(59, 130, 246, 0.35); }"
        )
        menu.addAction("⚙️ Settings & Control Center", self.settings_requested.emit)
        menu.addAction("🎙️ Trigger Voice / Wake Word", self.voice_triggered.emit)
        toggle_label = "🔴 Disable Needle" if self.enabled else "🟢 Enable Needle"
        menu.addAction(toggle_label, self.toggle_enabled_requested.emit)
        menu.addAction("📍 Reset to Default Perch", lambda: (self.reset_perch(), self.reset_perch_requested.emit()))
        menu.addSeparator()
        menu.addAction("❌ Quit Needle", self.quit_requested.emit)
        menu.exec(event.globalPos())
        event.accept()

    def contains_global_point(self, x: int, y: int) -> bool:
        if not self.isVisible():
            return False
        return self.frameGeometry().contains(QPoint(int(x), int(y)))

    def fly_to(self, target: QPoint, on_arrive: Callable | None = None) -> None:
        self._set_state("executing")
        self._tilt = 0.0
        anim = QPropertyAnimation(self, b"pos", self)
        anim.setDuration(520)
        anim.setStartValue(self.pos())
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        def arrived():
            # physical tap bounce
            bounce = QPropertyAnimation(self, b"pos", self)
            bounce.setDuration(160)
            bounce.setStartValue(self.pos())
            bounce.setEndValue(self.pos() + QPoint(0, 16))
            bounce.setEasingCurve(QEasingCurve.Type.OutBounce)
            bounce.finished.connect(lambda: self._return_home(on_arrive))
            bounce.start()
            self._animations.append(bounce)

        anim.finished.connect(arrived)
        anim.start()
        self._animations.append(anim)

    def _return_home(self, on_arrive: Callable | None = None) -> None:
        anim = QPropertyAnimation(self, b"pos", self)
        anim.setDuration(480)
        anim.setStartValue(self.pos())
        anim.setEndValue(self._resting_pos or self.pos())
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        def done():
            self._set_state("happy")
            self._text = "Done!"
            self._hop = -8.0
            QTimer.singleShot(260, lambda: setattr(self, "_hop", 0.0))
            if on_arrive:
                try:
                    on_arrive()
                except Exception:
                    pass
            QTimer.singleShot(900, lambda: self._set_state("idle"))

        anim.finished.connect(done)
        anim.start()
        self._animations.append(anim)

    def _target_for_call(self, call) -> QPoint:
        screen = QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen is not None else QRectF(0, 0, 1920, 1080).toRect()
        cx = avail.x() + (avail.width() - CANVAS) // 2
        cy = avail.y() + (avail.height() - CANVAS) // 2
        try:
            name = getattr(call, "name", "")
            args = getattr(call, "arguments", {}) or {}
            if name == "click_element":
                target = str(args.get("target", ""))
                m = re.search(r"(\d+)\s*,\s*(\d+)", target)
                if m:
                    x = int(m.group(1)) - CANVAS // 2
                    y = int(m.group(2)) - CANVAS // 2
                    x = self._clamp(x, avail.x(), avail.x() + avail.width() - CANVAS)
                    y = self._clamp(y, avail.y(), avail.y() + avail.height() - CANVAS)
                    return QPoint(int(x), int(y))
        except Exception:
            pass
        return QPoint(int(cx), int(cy))

    def _stop_animations(self) -> None:
        for anim in list(self._animations):
            try:
                anim.stop()
            except Exception:
                pass
        self._animations.clear()
        if self._group is not None:
            try:
                self._group.stop()
            except Exception:
                pass
            self._group = None

    # ------------------------------------------------------------------ tick
    def _tick(self) -> None:
        self._time += 0.016
        now = time.monotonic()
        # blink logic
        if self._state in ("idle", "listening", "welcome", "happy"):
            if self._blink_start == 0.0 and (now - self._last_blink) >= 3.5 + random.random() * 0.8:
                self._blink_start = now
                self._last_blink = now
            if self._blink_start:
                t = now - self._blink_start
                if t < 0.10:
                    self._blink = t / 0.10
                elif t < 0.20:
                    self._blink = 1.0 - (t - 0.10) / 0.10
                else:
                    self._blink = 0.0
                    self._blink_start = 0.0
        else:
            self._blink = 0.0
            self._blink_start = 0.0

        # pupil tracking
        try:
            center = self.mapToGlobal(QPoint(CANVAS // 2, CANVAS // 2))
            mouse = QCursor.pos()
            dx = (mouse.x() - center.x()) / 45.0
            dy = (mouse.y() - center.y()) / 45.0
            self._pupil = QPointF(
                self._clamp(dx * 4.0, -4.0, 4.0),
                self._clamp(dy * 4.0, -4.0, 4.0),
            )
        except Exception:
            self._pupil = QPointF(0.0, 0.0)

        if self._state == "executing":
            self._tilt = math.sin(self._time * 7.0) * 6.0
        else:
            self._tilt = 0.0

        self.update()

    def _tick_status(self) -> None:
        if self._state != "thinking":
            self._status_timer.stop()
            return

    # ------------------------------------------------------------------ paint
    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        center = QPointF(ORB_CENTER.x(), ORB_CENTER.y() + self._bob_y())
        radius = ORB / 2.0

        # outer glow / halo
        if self._state == "listening":
            halo = QRadialGradient(center, radius + 26)
            halo.setColorAt(0.0, QColor(59, 130, 246, 120))
            halo.setColorAt(0.6, QColor(59, 130, 246, 60))
            halo.setColorAt(1.0, QColor(59, 130, 246, 0))
            p.setBrush(halo)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(center, radius + 26, radius + 26)

        if self._state == "executing":
            glow = QRadialGradient(center, radius + 18)
            glow.setColorAt(0.0, QColor(96, 165, 250, 90))
            glow.setColorAt(1.0, QColor(96, 165, 250, 0))
            p.setBrush(glow)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(center, radius + 18, radius + 18)

        # subtle shadow
        shadow = QRadialGradient(center.x(), center.y() + 6, radius + 8)
        shadow.setColorAt(0.0, QColor(0, 0, 0, 70))
        shadow.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setBrush(shadow)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(center.x(), center.y() + 8), radius + 6, radius + 6)

        # sphere
        p.save()
        p.translate(center)
        p.rotate(self._tilt)
        grad = QRadialGradient(QPointF(-16, -18), radius * 1.4)
        grad.setColorAt(0.0, QColor("#CBE5FF"))
        grad.setColorAt(0.35, QColor("#7CB9FF"))
        grad.setColorAt(0.70, QColor("#4389F5"))
        grad.setColorAt(1.0, QColor("#2563EB"))
        p.setBrush(grad)
        p.setPen(QPen(QColor(255, 255, 255, 50), 1.0))
        p.drawEllipse(QPointF(0, 0), radius, radius)

        # glossy highlight
        hi = QRadialGradient(QPointF(-14, -20), 26)
        hi.setColorAt(0.0, QColor(255, 255, 255, 150))
        hi.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setBrush(hi)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(-12, -18), 22, 16)

        # inner rim
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 35), 1.0))
        p.drawEllipse(QPointF(0, 0), radius - 1, radius - 1)

        # eyes
        if self._state == "happy":
            # curved arches
            pen = QPen(QColor("#F4F9FF"), 3.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawArc(QRectF(-22, -12, 16, 12), 30 * 16, 120 * 16)
            p.drawArc(QRectF(6, -12, 16, 12), 30 * 16, 120 * 16)
        elif self._state == "thinking":
            # asymmetric squint
            self._draw_eye(p, QPointF(-16, -4), 11, 12, self._pupil, squint=0.55)
            self._draw_eye(p, QPointF(16, -4), 11, 22, self._pupil, squint=1.0)
        else:
            if self._state == "listening":
                ew, eh = 13, 23
            else:
                ew, eh = 11, 20
            # blink
            eh = max(2.0, eh * (1.0 - self._blink * 0.92))
            self._draw_eye(p, QPointF(-16, -4), ew, eh, self._pupil)
            self._draw_eye(p, QPointF(16, -4), ew, eh, self._pupil)

        p.restore()

        # speech pill
        if self._state in ("listening", "thinking", "error", "welcome", "happy") and self._text:
            self._draw_pill(p, self._text, self._muted)

        p.end()

    def _draw_eye(self, p: QPainter, center: QPointF, w: float, h: float,
                  pupil: QPointF, squint: float = 1.0) -> None:
        h = max(2.0, h * squint)
        rect = QRectF(center.x() - w / 2.0, center.y() - h / 2.0, w, h)
        p.setBrush(QBrush(QColor("#F4F9FF")))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(rect, 6.0, 6.0)
        # pupil
        if h > 4:
            pr = 3.0
            px = center.x() + pupil.x() * 0.6
            py = center.y() + pupil.y() * 0.6
            p.setBrush(QBrush(QColor("#0F172A")))
            p.drawEllipse(QPointF(px, py), pr, pr)
            # highlight
            p.setBrush(QBrush(QColor(255, 255, 255, 180)))
            p.drawEllipse(QPointF(px - 1.2, py - 1.2), 1.0, 1.0)

    def _draw_pill(self, p: QPainter, text: str, muted: bool) -> None:
        font = QFont("Segoe UI", 9)
        font.setWeight(QFont.Weight.Medium)
        p.setFont(font)
        fm = p.fontMetrics()
        display = text if len(text) <= 46 else text[:43] + "…"
        tw = fm.horizontalAdvance(display) + 24
        th = 24
        x = (CANVAS - tw) / 2.0
        y = 2.0
        rect = QRectF(x, y, tw, th)
        # glass background
        p.setBrush(QBrush(QColor(15, 23, 42, 200)))
        p.setPen(QPen(QColor(255, 255, 255, 30), 1.0))
        p.drawRoundedRect(rect, 12.0, 12.0)
        # text
        color = QColor("#858B9F") if muted else QColor("#F2F3FA")
        p.setPen(color)
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, display)

    # ------------------------------------------------------------------ input
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
        elif event.button() == Qt.MouseButton.RightButton:
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._dragging:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if self._dragging:
            self._dragging = False
            self._resting_pos = self.pos()
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self.hidden.emit()