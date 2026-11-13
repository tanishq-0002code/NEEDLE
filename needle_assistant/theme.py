from __future__ import annotations

from PyQt6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication

from .paths import icon_path

ACCENT = "#8B7CFF"
ACCENT_2 = "#4EE8DC"
TEXT = "#F2F3FA"
MUTED = "#9AA0B4"
SURFACE = "#11131A"
CARD = "#171A24"
BORDER = "#2A2E3D"


def accent_color(value: str) -> QColor:
    color = QColor(value)
    return color if color.isValid() else QColor(ACCENT)


def apply_app_style(app: QApplication, accent: str = ACCENT) -> None:
    app.setStyle("Fusion")
    font = QFont("Segoe UI Variable Text", 10)
    if not QFontDatabase.families() or font.family() not in QFontDatabase.families():
        font = QFont("Segoe UI", 10)
    app.setFont(font)
    app.setStyleSheet(f"""
        QWidget {{
            color: {TEXT};
        }}
        QDialog, QMainWindow {{
            background: {SURFACE};
        }}
        QLabel#Title {{
            font-family: "Segoe UI Variable Display", "Segoe UI";
            font-size: 26px;
            font-weight: 700;
            color: {TEXT};
        }}
        QLabel#Subtitle {{
            color: {MUTED};
            font-size: 12px;
        }}
        QLabel#SectionTitle {{
            font-size: 13px;
            font-weight: 600;
            color: {TEXT};
        }}
        QLabel#Muted {{
            color: {MUTED};
            font-size: 11px;
        }}
        QLabel#StatusGood {{
            color: {ACCENT_2};
            font-weight: 600;
        }}
        QLabel#StatusBad {{
            color: #FF8E9B;
            font-weight: 600;
        }}
        QFrame#Card {{
            background: {CARD};
            border: 1px solid {BORDER};
            border-radius: 16px;
        }}
        QLineEdit, QComboBox, QSpinBox, QKeySequenceEdit {{
            background: #0D0F16;
            border: 1px solid {BORDER};
            border-radius: 9px;
            padding: 8px 10px;
            selection-background-color: {accent};
            min-height: 18px;
        }}
        QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QKeySequenceEdit:focus {{
            border: 1px solid {accent};
        }}
        QComboBox::drop-down {{
            border: none;
            width: 24px;
        }}
        QComboBox QAbstractItemView {{
            background: {CARD};
            border: 1px solid {BORDER};
            selection-background-color: {accent};
            outline: none;
        }}
        QCheckBox {{
            spacing: 9px;
            color: {TEXT};
        }}
        QCheckBox::indicator {{
            width: 18px;
            height: 18px;
            border-radius: 5px;
            border: 1px solid #3A3F52;
            background: #0D0F16;
        }}
        QCheckBox::indicator:checked {{
            background: {accent};
            border: 1px solid {accent};
        }}
        QPushButton {{
            background: #1B1F2B;
            border: 1px solid {BORDER};
            border-radius: 9px;
            padding: 9px 15px;
            color: {TEXT};
            font-weight: 500;
        }}
        QPushButton:hover {{
            background: #232838;
            border-color: #3A4055;
        }}
        QPushButton:pressed {{
            background: #151925;
        }}
        QPushButton#Accent {{
            background: {accent};
            border: 1px solid {accent};
            color: #0B0C12;
            font-weight: 700;
        }}
        QPushButton#Accent:hover {{
            background: #A296FF;
        }}
        QPushButton#Flat {{
            background: transparent;
            border: none;
            color: {MUTED};
            padding: 6px 8px;
        }}
        QPushButton#Flat:hover {{
            color: {TEXT};
        }}
        QToolButton {{
            background: transparent;
            border: none;
            color: {MUTED};
            font-size: 15px;
            padding: 4px;
        }}
        QToolButton:hover {{
            color: {TEXT};
        }}
        QScrollArea {{
            border: none;
            background: transparent;
        }}
        QScrollBar:vertical {{
            background: transparent;
            width: 8px;
            margin: 2px;
        }}
        QScrollBar::handle:vertical {{
            background: #33394B;
            border-radius: 4px;
            min-height: 32px;
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0px;
        }}
    """)


def _fallback_pixmap(size: int = 128) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(24, 26, 38))
    painter.setPen(QColor(60, 66, 90))
    painter.drawRoundedRect(4, 4, size - 8, size - 8, size // 4, size // 4)
    painter.setPen(QColor(245, 246, 255))
    font = QFont("Segoe UI", int(size * 0.5), QFont.Weight.Bold)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), 0x84, "N")
    painter.end()
    return pixmap


def app_icon(accent: str = ACCENT) -> QIcon:
    path = icon_path()
    if path.exists():
        icon = QIcon(str(path))
        if not icon.isNull():
            return icon
    return QIcon(_fallback_pixmap())
