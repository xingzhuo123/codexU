from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QSettings


@dataclass(slots=True)
class AppPreferences:
    language: str = "zh"
    theme: str = "system"
    minimize_to_tray: bool = True
    start_minimized: bool = False
    show_full_paths: bool = False
    selected_runtime: str = "codex"


class SettingsStore:
    def __init__(self) -> None:
        self._settings = QSettings("codexU", "codexU Windows")

    def load(self) -> AppPreferences:
        return AppPreferences(
            language=str(self._settings.value("language", "zh")),
            theme=str(self._settings.value("theme", "system")),
            minimize_to_tray=self._as_bool(self._settings.value("minimize_to_tray", True)),
            start_minimized=self._as_bool(self._settings.value("start_minimized", False)),
            show_full_paths=False,
            selected_runtime=str(self._settings.value("selected_runtime", "codex")),
        )

    def save(self, preferences: AppPreferences) -> None:
        self._settings.setValue("language", preferences.language)
        self._settings.setValue("theme", preferences.theme)
        self._settings.setValue("minimize_to_tray", preferences.minimize_to_tray)
        self._settings.setValue("start_minimized", preferences.start_minimized)
        self._settings.setValue("selected_runtime", preferences.selected_runtime)
        self._settings.sync()

    @staticmethod
    def _as_bool(value: object) -> bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}
