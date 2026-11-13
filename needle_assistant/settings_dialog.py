from __future__ import annotations

import os
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QKeySequenceEdit,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .audio import list_input_devices
from .config import AppConfig, normalize_hotkey, normalize_wake_phrases, save_config
from .paths import app_data_dir
from .theme import accent_color
from . import windows_api


class SettingsDialog(QDialog):
    saved = pyqtSignal(object)
    test_popup = pyqtSignal()
    type_command = pyqtSignal()

    def __init__(self, config: AppConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("Needle Desktop — Settings")
        self.setMinimumSize(700, 600)
        self.resize(760, 680)
        self.setModal(False)
        self._needle_status = "Loading…"
        self._speech_status = "Loading…"
        self._mic_status = "Checking…"
        self._wake_status = "Off"
        self._build()
        self._load_values()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 22, 26, 20)
        root.setSpacing(14)

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel("Needle Desktop", self)
        title.setObjectName("Title")
        subtitle = QLabel("Private, local voice control for Windows. No cloud. No API keys.", self)
        subtitle.setObjectName("Subtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header.addLayout(title_box, 1)
        badge = QLabel("100% OFFLINE", self)
        badge.setStyleSheet(
            f"background: rgba({accent_color('#8B7CFF').red()}, {accent_color('#8B7CFF').green()}, "
            f"{accent_color('#8B7CFF').blue()}, 30); color: #A79EFF; border: 1px solid rgba(255,255,255,24);"
            "border-radius: 10px; padding: 5px 10px; font-size: 9px; font-weight: 700;"
        )
        header.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 4, 0)
        content_layout.setSpacing(12)

        content_layout.addWidget(self._build_how_to())
        content_layout.addWidget(self._build_status())
        content_layout.addWidget(self._build_controls())
        content_layout.addStretch(1)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        footer = QHBoxLayout()
        self.open_folder_button = QPushButton("Open data folder", self)
        self.open_folder_button.setObjectName("Flat")
        self.open_folder_button.clicked.connect(lambda: os.startfile(str(app_data_dir())))  # type: ignore[attr-defined]
        footer.addWidget(self.open_folder_button)
        self.type_button = QPushButton("Type a command…", self)
        self.type_button.clicked.connect(self.type_command.emit)
        footer.addWidget(self.type_button)
        self.test_button = QPushButton("Test popup", self)
        self.test_button.clicked.connect(self.test_popup.emit)
        footer.addWidget(self.test_button)
        footer.addStretch(1)
        self.cancel_button = QPushButton("Cancel", self)
        self.cancel_button.clicked.connect(self.reject)
        footer.addWidget(self.cancel_button)
        self.save_button = QPushButton("Save", self)
        self.save_button.setObjectName("Accent")
        self.save_button.clicked.connect(self._save)
        footer.addWidget(self.save_button)
        root.addLayout(footer)

    def _card(self, title: str, subtitle: str = "") -> tuple[QFrame, QVBoxLayout]:
        card = QFrame(self)
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(9)
        title_label = QLabel(title, card)
        title_label.setObjectName("SectionTitle")
        layout.addWidget(title_label)
        if subtitle:
            sub = QLabel(subtitle, card)
            sub.setObjectName("Muted")
            sub.setWordWrap(True)
            layout.addWidget(sub)
        return card, layout

    def _build_how_to(self) -> QFrame:
        card, layout = self._card(
            "How to use it",
            "Needle runs in the system tray. The popup never takes focus away from the app you are working in.",
        )
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(7)
        steps = [
            ("1", "Press Ctrl+Space anywhere, or say “Hey Jarvis”."),
            ("2", "Speak a command, for example “open Chrome” or “type hello world”."),
            ("3", "Needle 3 routes the command locally and performs the desktop action."),
            ("4", "Press Esc, click outside, or wait for the automatic dismissal."),
        ]
        for row, (number, text) in enumerate(steps):
            number_label = QLabel(number, card)
            number_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            number_label.setFixedSize(22, 22)
            number_label.setStyleSheet(
                "background: #232738; border-radius: 11px; color: #B8B1FF; font-weight: 700; font-size: 10px;"
            )
            label = QLabel(text, card)
            label.setWordWrap(True)
            label.setObjectName("Muted")
            grid.addWidget(number_label, row, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(label, row, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        return card

    def _build_status(self) -> QFrame:
        card, layout = self._card("Status", "Everything below is checked locally on this PC.")
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(8)
        self.needle_value = QLabel("Loading…", card)
        self.speech_value = QLabel("Loading…", card)
        self.mic_value = QLabel("Checking…", card)
        self.wake_value = QLabel("Off", card)
        rows = [
            ("Needle 3 router", self.needle_value),
            ("Offline speech model", self.speech_value),
            ("Microphone", self.mic_value),
            ("Wake word", self.wake_value),
        ]
        for row, (label, value) in enumerate(rows):
            key = QLabel(label, card)
            key.setObjectName("Muted")
            value.setStyleSheet("color: #D9DCEC; font-weight: 600;")
            value.setWordWrap(True)
            grid.addWidget(key, row, 0)
            grid.addWidget(value, row, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        return card

    def _build_controls(self) -> QFrame:
        card, layout = self._card("Controls", "Changes take effect as soon as you save.")
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(11)
        grid.setColumnStretch(1, 1)

        grid.addWidget(self._label("Global hotkey"), 0, 0)
        hotkey_row = QHBoxLayout()
        self.hotkey_edit = QKeySequenceEdit(card)
        if hasattr(self.hotkey_edit, "setMaximumSequenceLength"):
            self.hotkey_edit.setMaximumSequenceLength(1)
        hotkey_row.addWidget(self.hotkey_edit, 1)
        reset_hotkey = QPushButton("Reset", card)
        reset_hotkey.clicked.connect(lambda: self.hotkey_edit.setKeySequence(QKeySequence("Ctrl+Space")))
        hotkey_row.addWidget(reset_hotkey)
        grid.addLayout(hotkey_row, 0, 1)

        grid.addWidget(self._label("Wake word"), 1, 0)
        self.wake_check = QCheckBox("Listen for the wake word while the app is running", card)
        grid.addWidget(self.wake_check, 1, 1)

        grid.addWidget(self._label("Wake phrases"), 2, 0)
        self.phrases_edit = QLineEdit(card)
        self.phrases_edit.setPlaceholderText("hey jarvis, hey computer")
        grid.addWidget(self.phrases_edit, 2, 1)

        grid.addWidget(self._label("Microphone"), 3, 0)
        self.mic_combo = QComboBox(card)
        self.mic_combo.addItem("System default input", None)
        for index, label in list_input_devices():
            self.mic_combo.addItem(label, index)
        grid.addWidget(self.mic_combo, 3, 1)

        grid.addWidget(self._label("Routing"), 4, 0)
        self.fast_check = QCheckBox("Use the instant fast route for simple commands (Needle 3 handles everything else)", card)
        grid.addWidget(self.fast_check, 4, 1)

        grid.addWidget(self._label("Appearance"), 5, 0)
        self.acrylic_check = QCheckBox("Use Windows acrylic blur behind the glass popup", card)
        grid.addWidget(self.acrylic_check, 5, 1)

        grid.addWidget(self._label("Startup"), 6, 0)
        self.startup_check = QCheckBox("Start Needle Desktop with Windows", card)
        grid.addWidget(self.startup_check, 6, 1)

        grid.addWidget(self._label("First launch"), 7, 0)
        self.show_on_start_check = QCheckBox("Show a tray hint when the app starts", card)
        grid.addWidget(self.show_on_start_check, 7, 1)

        grid.addWidget(self._label("Needle output limit"), 8, 0)
        self.tokens_spin = QSpinBox(card)
        self.tokens_spin.setRange(32, 512)
        self.tokens_spin.setSingleStep(16)
        self.tokens_spin.setToolTip("Lower values finish faster. 128 is recommended on a 2-core CPU.")
        grid.addWidget(self.tokens_spin, 8, 1)
        layout.addLayout(grid)
        return card

    @staticmethod
    def _label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("Muted")
        return label

    def _load_values(self) -> None:
        config = self.config
        self.hotkey_edit.setKeySequence(QKeySequence(self._qt_hotkey(config.hotkey)))
        self.wake_check.setChecked(config.wake_word_enabled)
        self.phrases_edit.setText(", ".join(config.wake_phrases))
        index = self.mic_combo.findData(config.microphone_index)
        self.mic_combo.setCurrentIndex(index if index >= 0 else 0)
        self.fast_check.setChecked(config.fast_intent)
        self.acrylic_check.setChecked(config.acrylic_blur)
        self.startup_check.setChecked(windows_api.is_start_with_windows() or config.start_with_windows)
        self.show_on_start_check.setChecked(config.show_on_start)
        self.tokens_spin.setValue(config.max_new_tokens)
        self._update_status_labels()

    @staticmethod
    def _qt_hotkey(value: str) -> str:
        parts = []
        for part in value.split("+"):
            if part == "ctrl":
                parts.append("Ctrl")
            elif part == "alt":
                parts.append("Alt")
            elif part == "shift":
                parts.append("Shift")
            elif part == "win":
                parts.append("Meta")
            elif part == "esc":
                parts.append("Esc")
            elif part == "space":
                parts.append("Space")
            else:
                parts.append(part.upper() if len(part) == 1 else part.title())
        return "+".join(parts)

    def set_status(self, needle: str | None = None, speech: str | None = None,
                   microphone: str | None = None, wake: str | None = None) -> None:
        if needle is not None:
            self._needle_status = needle
        if speech is not None:
            self._speech_status = speech
        if microphone is not None:
            self._mic_status = microphone
        if wake is not None:
            self._wake_status = wake
        self._update_status_labels()

    def _update_status_labels(self) -> None:
        for label, text in (
            (getattr(self, "needle_value", None), self._needle_status),
            (getattr(self, "speech_value", None), self._speech_status),
            (getattr(self, "mic_value", None), self._mic_status),
            (getattr(self, "wake_value", None), self._wake_status),
        ):
            if label is None:
                continue
            label.setText(text)
            good = any(word in text.lower() for word in ("ready", "on", "default", "listening", "loaded"))
            bad = any(word in text.lower() for word in ("error", "missing", "failed", "unavailable", "off"))
            color = "#4EE8DC" if good and not bad else "#FF8E9B" if bad else "#D9DCEC"
            label.setStyleSheet(f"color: {color}; font-weight: 600;")

    def _save(self) -> None:
        sequence = self.hotkey_edit.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
        try:
            hotkey = normalize_hotkey(sequence)
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid hotkey", str(exc))
            return
        phrases = normalize_wake_phrases(self.phrases_edit.text())
        if self.wake_check.isChecked() and not phrases:
            QMessageBox.warning(self, "Wake phrases required", "Add at least one wake phrase, or turn the wake word off.")
            return
        updated = AppConfig.from_dict(self.config.to_dict())
        updated.hotkey = hotkey
        updated.wake_word_enabled = self.wake_check.isChecked()
        updated.wake_phrases = phrases or updated.wake_phrases
        updated.microphone_index = self.mic_combo.currentData()
        updated.fast_intent = self.fast_check.isChecked()
        updated.acrylic_blur = self.acrylic_check.isChecked()
        updated.start_with_windows = self.startup_check.isChecked()
        updated.show_on_start = self.show_on_start_check.isChecked()
        updated.max_new_tokens = self.tokens_spin.value()
        updated.first_run_done = True
        updated.validate()
        if not windows_api.set_start_with_windows(updated.start_with_windows):
            QMessageBox.warning(self, "Startup setting", "Windows did not accept the startup setting. Everything else was saved.")
        save_config(updated)
        self.config = updated
        self.saved.emit(updated)
        self.accept()

    def refresh_devices(self) -> None:
        current = self.mic_combo.currentData()
        self.mic_combo.blockSignals(True)
        self.mic_combo.clear()
        self.mic_combo.addItem("System default input", None)
        for index, label in list_input_devices():
            self.mic_combo.addItem(label, index)
        match = self.mic_combo.findData(current)
        self.mic_combo.setCurrentIndex(match if match >= 0 else 0)
        self.mic_combo.blockSignals(False)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._load_values()
        self.refresh_devices()
