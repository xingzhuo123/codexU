from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QStackedWidget, QSystemTrayIcon, QTabBar

from codexu_win.demo import make_demo_bundle
from codexu_win.models import RuntimeKind, SnapshotBundle
from codexu_win.settings import AppPreferences
from codexu_win.ui.main_window import MainWindow
from codexu_win.ui.settings_dialog import SettingsDialog
from codexu_win.ui.theme import apply_theme
from codexu_win.ui.tray import TrayController


@pytest.fixture(scope="module")
def app() -> QApplication:
    instance = QApplication.instance() or QApplication([])
    apply_theme(instance, "light")
    return instance


def test_main_window_applies_demo_bundle_and_switches_tabs(app: QApplication) -> None:
    window = MainWindow(AppPreferences(language="zh", theme="light"))
    bundle = make_demo_bundle()
    window.apply_bundle(bundle)
    app.processEvents()

    assert window.minimumWidth() == 820
    assert window.minimumHeight() == 640
    assert window.width() == 920
    assert window.height() == 800
    assert window.overview.minimumHeight() == 292
    assert window.overview.maximumHeight() == 292
    assert window.runtime == RuntimeKind.CODEX

    tabs = window.findChild(QTabBar, "dashboardTabs")
    stack = window.findChild(QStackedWidget, "dashboardStack")
    assert tabs is not None
    assert stack is not None
    assert tabs.count() == 4
    for index in range(4):
        window.select_tab(index)
        app.processEvents()
        assert tabs.currentIndex() == index
        assert stack.currentIndex() == index

    window.set_runtime(RuntimeKind.CLAUDE)
    app.processEvents()
    assert window.runtime == RuntimeKind.CLAUDE
    window.set_loading(True)
    assert not window.header.refresh_button.isEnabled()
    window.set_loading(False)
    assert window.header.refresh_button.isEnabled()
    window.close()


def test_main_window_handles_missing_bundle_and_real_zero(app: QApplication) -> None:
    bundle = make_demo_bundle()
    snapshot = bundle.snapshots[RuntimeKind.CODEX]
    assert snapshot.detailed is not None
    snapshot.detailed.today.tokens.input_tokens = 0
    snapshot.detailed.today.tokens.cached_input_tokens = 0
    snapshot.detailed.today.tokens.output_tokens = 0
    snapshot.detailed.today.tokens.total_tokens = 0

    window = MainWindow(AppPreferences(language="en", theme="dark"))
    window.apply_bundle(SnapshotBundle({}))
    window.apply_bundle(bundle)
    app.processEvents()
    assert window.overview.today.value_label.text() == "0"
    window.overview.api_value.set_value(1000)
    window.overview.api_value.set_value(None)
    assert window.overview.api_value.cap.text() == "$200+"
    window.close()


def test_settings_and_tray_construct_offscreen(app: QApplication) -> None:
    preferences = AppPreferences(language="en", theme="light")
    dialog = SettingsDialog(preferences)
    assert dialog.preferences().language == "en"
    assert dialog.preferences().theme == "light"

    tray = TrayController(language="en")
    bundle = make_demo_bundle()
    tray.update_bundle(bundle, RuntimeKind.CODEX)
    assert "5h" in tray.tray.toolTip()
    assert tray.panel.width() == 380
    activations: list[bool] = []
    tray.activated.connect(lambda: activations.append(True))
    tray._tray_activated(QSystemTrayIcon.ActivationReason.Trigger)
    assert activations == []
    tray._tray_activated(QSystemTrayIcon.ActivationReason.DoubleClick)
    assert activations == [True]
    tray.panel.hide()
    tray.tray.hide()
