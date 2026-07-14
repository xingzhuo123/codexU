from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from codexu_win.i18n import tr
from codexu_win.settings import AppPreferences


class SettingsDialog(QDialog):
    preferences_saved = Signal(object)

    def __init__(
        self,
        preferences: AppPreferences | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._initial = preferences or AppPreferences()
        self.language = self._initial.language
        self.setWindowTitle(tr("settings", self.language))
        self.setObjectName("settingsRoot")
        self.setModal(False)
        self.setMinimumSize(500, 410)
        self.resize(520, 450)
        self._build()
        self.set_preferences(self._initial)

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 16)
        root.setSpacing(14)

        heading = QLabel(tr("settings", self.language))
        heading_font = QFont()
        heading_font.setPointSizeF(15)
        heading_font.setWeight(QFont.Weight.DemiBold)
        heading.setFont(heading_font)
        root.addWidget(heading)

        general = QFrame()
        general.setObjectName("settingsSection")
        general_layout = QVBoxLayout(general)
        general_layout.setContentsMargins(14, 12, 14, 12)
        general_layout.setSpacing(12)

        self.language_combo = QComboBox()
        self.language_combo.addItem("中文", "zh")
        self.language_combo.addItem("English", "en")
        general_layout.addLayout(
            self._setting_row(tr("language", self.language), self.language_combo)
        )

        self.theme_combo = QComboBox()
        self.theme_combo.addItem(tr("theme_system", self.language), "system")
        self.theme_combo.addItem(tr("theme_light", self.language), "light")
        self.theme_combo.addItem(tr("theme_dark", self.language), "dark")
        general_layout.addLayout(self._setting_row(tr("theme", self.language), self.theme_combo))

        root.addWidget(general)

        behavior = QFrame()
        behavior.setObjectName("settingsSection")
        behavior_layout = QVBoxLayout(behavior)
        behavior_layout.setContentsMargins(14, 12, 14, 12)
        behavior_layout.setSpacing(10)
        self.minimize_to_tray = QCheckBox(tr("minimize_to_tray", self.language))
        self.start_minimized = QCheckBox(tr("start_minimized", self.language))
        behavior_layout.addWidget(self.minimize_to_tray)
        behavior_layout.addWidget(self.start_minimized)
        root.addWidget(behavior)

        privacy = QLabel(tr("privacy", self.language))
        privacy.setProperty("role", "secondary")
        privacy.setWordWrap(True)
        privacy.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(privacy)
        root.addStretch()

        actions = QHBoxLayout()
        actions.addStretch()
        cancel = QPushButton(tr("cancel", self.language))
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        save = QPushButton(tr("save", self.language))
        save.setDefault(True)
        save.clicked.connect(self._save)
        actions.addWidget(save)
        root.addLayout(actions)

    def _setting_row(self, title: str, control: QWidget) -> QHBoxLayout:
        layout = QHBoxLayout()
        label = QLabel(title)
        label.setMinimumWidth(150)
        layout.addWidget(label)
        layout.addStretch()
        control.setMinimumWidth(200)
        layout.addWidget(control)
        return layout

    def set_preferences(self, preferences: AppPreferences) -> None:
        self._select_data(self.language_combo, preferences.language)
        self._select_data(self.theme_combo, preferences.theme)
        self.minimize_to_tray.setChecked(preferences.minimize_to_tray)
        self.start_minimized.setChecked(preferences.start_minimized)

    def preferences(self) -> AppPreferences:
        return AppPreferences(
            language=str(self.language_combo.currentData()),
            theme=str(self.theme_combo.currentData()),
            minimize_to_tray=self.minimize_to_tray.isChecked(),
            start_minimized=self.start_minimized.isChecked(),
            show_full_paths=False,
            selected_runtime=self._initial.selected_runtime,
        )

    def _save(self) -> None:
        value = self.preferences()
        self.preferences_saved.emit(value)
        self.accept()

    @staticmethod
    def _select_data(combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(index if index >= 0 else 0)
