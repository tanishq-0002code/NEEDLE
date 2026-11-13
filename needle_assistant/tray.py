from __future__ import annotations

import logging

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon

log = logging.getLogger("needle.tray")


class TrayIcon(QObject):
    show_requested = pyqtSignal()
    type_requested = pyqtSignal()
    settings_requested = pyqtSignal()
    open_folder_requested = pyqtSignal()
    wake_toggled = pyqtSignal(bool)
    quit_requested = pyqtSignal()

    def __init__(self, icon: QIcon, wake_enabled: bool, hotkey: str) -> None:
        super().__init__()
        self._hotkey = hotkey.upper()
        self.tray = QSystemTrayIcon(icon)
        self.tray.setToolTip(f"Needle Desktop — {self._hotkey}")

        menu = QMenu()
        self.show_action = QAction("Show assistant", menu)
        self.show_action.triggered.connect(self.show_requested.emit)
        menu.addAction(self.show_action)

        self.type_action = QAction("Type a command…", menu)
        self.type_action.triggered.connect(self.type_requested.emit)
        menu.addAction(self.type_action)
        menu.addSeparator()

        self.wake_action = QAction("Listen for wake word", menu)
        self.wake_action.setCheckable(True)
        self.wake_action.setChecked(wake_enabled)
        self.wake_action.toggled.connect(self.wake_toggled.emit)
        menu.addAction(self.wake_action)
        menu.addSeparator()

        self.settings_action = QAction("Settings…", menu)
        self.settings_action.triggered.connect(self.settings_requested.emit)
        menu.addAction(self.settings_action)

        self.folder_action = QAction("Open data folder", menu)
        self.folder_action.triggered.connect(self.open_folder_requested.emit)
        menu.addAction(self.folder_action)
        menu.addSeparator()

        self.quit_action = QAction("Quit Needle Desktop", menu)
        self.quit_action.triggered.connect(self.quit_requested.emit)
        menu.addAction(self.quit_action)

        self._menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_activated)
        self.tray.show()

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in {
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        }:
            self.show_requested.emit()

    def set_wake_checked(self, enabled: bool) -> None:
        self.wake_action.blockSignals(True)
        self.wake_action.setChecked(bool(enabled))
        self.wake_action.blockSignals(False)

    def set_hotkey(self, hotkey: str) -> None:
        self._hotkey = hotkey.upper()
        self.show_action.setText(f"Show assistant ({self._hotkey})")

    def set_status(self, text: str) -> None:
        self.tray.setToolTip(text)

    def show_message(self, title: str, message: str,
                     icon: QSystemTrayIcon.MessageIcon = QSystemTrayIcon.MessageIcon.Information,
                     milliseconds: int = 4000) -> None:
        try:
            self.tray.showMessage(title, message, icon, milliseconds)
        except Exception:
            log.debug("Tray notification failed.", exc_info=True)

    def hide(self) -> None:
        self.tray.hide()
