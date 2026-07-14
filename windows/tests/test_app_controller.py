from __future__ import annotations

import os
import time
import uuid
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtNetwork import QLocalServer
from PySide6.QtWidgets import QApplication

from codexu_win.app import ApplicationController, _SingleInstance
from codexu_win.demo import make_demo_bundle
from codexu_win.models import RuntimeKind
from codexu_win.settings import AppPreferences


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


class _SettingsRecorder:
    def __init__(self) -> None:
        self.saved: list[AppPreferences] = []

    def save(self, preferences: AppPreferences) -> None:
        self.saved.append(replace(preferences))


class _SlowService:
    def load(self):
        time.sleep(0.25)
        return make_demo_bundle()


def _dispose(controller: ApplicationController) -> None:
    controller._quitting = True
    controller.refresh_timer.stop()
    controller.result_timer.stop()
    if controller.window is not None:
        controller.window.removeEventFilter(controller)
        controller.window.close()
        controller.window.deleteLater()


def test_runtime_selected_while_settings_are_open_is_not_rolled_back(app: QApplication) -> None:
    recorder = _SettingsRecorder()
    controller = ApplicationController(
        app,
        AppPreferences(selected_runtime="codex"),
        recorder,  # type: ignore[arg-type]
        demo=True,
        disable_tray=True,
    )
    assert controller.window is not None
    controller.window.set_runtime(RuntimeKind.CLAUDE)

    saved = AppPreferences(selected_runtime="codex", theme="dark")
    controller._save_preferences(saved)

    assert saved.selected_runtime == "claude"
    assert recorder.saved[-1].selected_runtime == "claude"
    _dispose(controller)


def test_refresh_worker_is_daemon_and_shutdown_does_not_wait(app: QApplication) -> None:
    controller = ApplicationController(
        app,
        AppPreferences(),
        _SettingsRecorder(),  # type: ignore[arg-type]
        demo=True,
        disable_tray=True,
    )
    controller.demo = False
    controller.service = _SlowService()  # type: ignore[assignment]
    controller.refresh()
    assert controller._refresh_thread is not None
    assert controller._refresh_thread.daemon is True

    started = time.perf_counter()
    controller.quit()
    assert time.perf_counter() - started < 0.15
    controller._refresh_thread.join(timeout=1)
    assert not controller._refresh_thread.is_alive()


def test_single_instance_fails_closed_to_second_claimant(app: QApplication) -> None:
    name = f"codexu-test-{uuid.uuid4().hex}"
    first = _SingleInstance(name)
    second = _SingleInstance(name)
    try:
        assert first.claim() is True
        assert second.claim() is False
    finally:
        if first.server is not None:
            first.server.close()
        if second.server is not None:
            second.server.close()
        QLocalServer.removeServer(name)
