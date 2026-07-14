from __future__ import annotations

import argparse
import json
import os
import queue
import sys
import threading
from dataclasses import replace
from typing import Sequence

from PySide6.QtCore import (
    QEvent,
    QObject,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from codexu_win import __version__
from codexu_win.demo import make_demo_bundle
from codexu_win.models import RuntimeKind, SnapshotBundle
from codexu_win.service import RuntimeService
from codexu_win.settings import AppPreferences, SettingsStore
from codexu_win.ui import MainWindow, SettingsDialog, TrayController, apply_theme


def _load_runtime_bundle(
    service: RuntimeService,
    output: queue.Queue[tuple[str, object]],
) -> None:
    """Run local I/O without touching Qt objects from the worker thread."""

    try:
        output.put(("result", service.load()))
    except Exception as error:
        output.put(("error", type(error).__name__))
    finally:
        output.put(("finished", None))


class _SingleInstance(QObject):
    activate_requested = Signal()

    def __init__(self, name: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.name = name
        self.server: QLocalServer | None = None

    def claim(self) -> bool:
        if self._notify_existing(200):
            return False

        self.server = QLocalServer(self)
        if self.server.listen(self.name):
            self.server.newConnection.connect(self._accept_connection)
            return True

        # Another instance may have won the listen race after our first probe.
        if self._notify_existing(500):
            return False

        # Only remove an endpoint after both connection attempts failed.
        QLocalServer.removeServer(self.name)
        self.server = QLocalServer(self)
        if not self.server.listen(self.name):
            return False
        self.server.newConnection.connect(self._accept_connection)
        return True

    def _notify_existing(self, timeout_ms: int) -> bool:
        socket = QLocalSocket(self)
        socket.connectToServer(self.name)
        if socket.waitForConnected(timeout_ms):
            socket.write(b"show")
            socket.flush()
            socket.waitForBytesWritten(timeout_ms)
            socket.disconnectFromServer()
            return True
        socket.abort()
        return False

    def _accept_connection(self) -> None:
        if self.server is None:
            return
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            socket.readyRead.connect(lambda current=socket: self._read_socket(current))
            if socket.bytesAvailable():
                self._read_socket(socket)

    def _read_socket(self, socket: QLocalSocket) -> None:
        if b"show" in bytes(socket.readAll()):
            self.activate_requested.emit()
        socket.disconnectFromServer()
        socket.deleteLater()


class ApplicationController(QObject):
    def __init__(
        self,
        app: QApplication,
        preferences: AppPreferences,
        settings_store: SettingsStore,
        *,
        demo: bool = False,
        disable_tray: bool = False,
    ) -> None:
        super().__init__(app)
        self.app = app
        self.preferences = preferences
        self.settings_store = settings_store
        self.demo = demo
        self.disable_tray = disable_tray
        self.service = RuntimeService()
        self.window: MainWindow | None = None
        self.tray: TrayController | None = None
        self.settings_dialog: SettingsDialog | None = None
        self.bundle: SnapshotBundle | None = None
        self._refreshing = False
        self._pending_refresh = False
        self._quitting = False
        self._refresh_thread: threading.Thread | None = None
        self._result_queue: queue.Queue[tuple[str, object]] = queue.Queue()

        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(5 * 60 * 1000)
        self.refresh_timer.timeout.connect(self.refresh)
        self.refresh_timer.start()

        self.result_timer = QTimer(self)
        self.result_timer.setInterval(50)
        self.result_timer.timeout.connect(self._poll_results)
        self.result_timer.start()

        self._create_ui()
        if self.demo:
            self._apply_bundle(make_demo_bundle())
        else:
            QTimer.singleShot(0, self.refresh)

    def _create_ui(self) -> None:
        apply_theme(self.app, self.preferences.theme)
        window = MainWindow(self.preferences)
        window.installEventFilter(self)
        window.refresh_requested.connect(self.refresh)
        window.settings_requested.connect(self.open_settings)
        window.runtime_changed.connect(self._runtime_changed)
        self.window = window

        if not self.disable_tray:
            tray = TrayController(self.preferences.language)
            tray.open.connect(self.show_main)
            tray.activated.connect(self.show_main)
            tray.refresh.connect(self.refresh)
            tray.settings.connect(self.open_settings)
            tray.quit.connect(self.quit)
            self.tray = tray

        if self.bundle is not None:
            self.window.apply_bundle(self.bundle)
            if self.tray is not None:
                self.tray.update_bundle(self.bundle, self.window.runtime)

    def start(self, minimized: bool = False) -> None:
        if self.window is None:
            return
        can_hide = self.tray is not None and self.tray.available
        if (minimized or self.preferences.start_minimized) and can_hide:
            return
        self.show_main()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt API
        if watched is self.window and event.type() == QEvent.Type.Close and not self._quitting:
            can_hide = self.tray is not None and self.tray.available
            if self.preferences.minimize_to_tray and can_hide:
                event.ignore()
                self.window.hide()
                return True
            self.quit()
            return True
        return super().eventFilter(watched, event)

    @Slot()
    def show_main(self) -> None:
        if self.window is None:
            return
        if self.window.isMinimized():
            self.window.showNormal()
        else:
            self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    @Slot()
    def refresh(self) -> None:
        if self._quitting:
            return
        if self.demo:
            self._apply_bundle(make_demo_bundle())
            return
        if self._refreshing:
            self._pending_refresh = True
            return
        self._refreshing = True
        if self.window is not None:
            self.window.set_loading(True)
        self._refresh_thread = threading.Thread(
            target=_load_runtime_bundle,
            args=(self.service, self._result_queue),
            name="codexu-local-refresh",
            daemon=True,
        )
        self._refresh_thread.start()

    @Slot()
    def _poll_results(self) -> None:
        while True:
            try:
                kind, payload = self._result_queue.get_nowait()
            except queue.Empty:
                return
            if self._quitting:
                continue
            if kind == "result" and isinstance(payload, SnapshotBundle):
                self._apply_bundle(payload)
            elif kind == "error":
                self._refresh_error(str(payload))
            elif kind == "finished":
                self._refresh_finished()

    @Slot(object)
    def _apply_bundle(self, bundle: SnapshotBundle) -> None:
        if self._quitting:
            return
        self.bundle = bundle
        if self.window is not None:
            self.window.apply_bundle(bundle)
        if self.tray is not None and self.window is not None:
            self.tray.update_bundle(bundle, self.window.runtime)

    @Slot(str)
    def _refresh_error(self, category: str) -> None:
        if self.window is not None:
            self.window.statusBar().showMessage(f"Local refresh failed ({category})", 5000)

    @Slot()
    def _refresh_finished(self) -> None:
        if self._quitting:
            return
        self._refreshing = False
        self._refresh_thread = None
        if self.window is not None:
            self.window.set_loading(False)
        if self._pending_refresh:
            self._pending_refresh = False
            QTimer.singleShot(0, self.refresh)

    @Slot(object)
    def _runtime_changed(self, runtime: RuntimeKind) -> None:
        self.preferences.selected_runtime = runtime.value
        self.settings_store.save(self.preferences)
        if self.tray is not None and self.bundle is not None:
            self.tray.update_bundle(self.bundle, runtime)

    @Slot()
    def open_settings(self) -> None:
        if self.settings_dialog is not None:
            self.settings_dialog.show()
            self.settings_dialog.raise_()
            self.settings_dialog.activateWindow()
            return
        if self.window is not None:
            self.preferences.selected_runtime = self.window.runtime.value
        dialog = SettingsDialog(replace(self.preferences), self.window)
        dialog.preferences_saved.connect(self._save_preferences)
        dialog.finished.connect(self._settings_closed)
        self.settings_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    @Slot(int)
    def _settings_closed(self, _result: int) -> None:
        if self.settings_dialog is not None:
            self.settings_dialog.deleteLater()
            self.settings_dialog = None

    @Slot(object)
    def _save_preferences(self, preferences: AppPreferences) -> None:
        if self.window is not None:
            preferences.selected_runtime = self.window.runtime.value
        language_changed = preferences.language != self.preferences.language
        self.preferences = preferences
        self.settings_store.save(preferences)
        apply_theme(self.app, preferences.theme)
        if language_changed:
            QTimer.singleShot(0, self._recreate_ui)
        elif self.window is not None:
            self.window.preferences = preferences
            self.window.update()

    def _recreate_ui(self) -> None:
        old_window = self.window
        old_tray = self.tray
        if old_window is not None:
            old_window.removeEventFilter(self)
            old_window.hide()
        if old_tray is not None:
            old_tray.set_visible(False)
            old_tray.panel.hide()
        self.window = None
        self.tray = None
        self._create_ui()
        self.show_main()
        if old_window is not None:
            old_window.deleteLater()
        if old_tray is not None:
            old_tray.deleteLater()

    @Slot()
    def quit(self) -> None:
        self._quitting = True
        self.refresh_timer.stop()
        self.result_timer.stop()
        self._pending_refresh = False
        if self.tray is not None:
            self.tray.set_visible(False)
        if self.window is not None:
            self.window.removeEventFilter(self)
            self.window.close()
        self.app.quit()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="codexU for Windows")
    parser.add_argument("--demo", action="store_true", help="use deterministic sample data")
    parser.add_argument("--dump-json", action="store_true", help="print aggregate-only JSON")
    parser.add_argument("--minimized", action="store_true", help="start hidden in the tray")
    parser.add_argument("--no-tray", action="store_true", help="disable the Windows tray icon")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(list(argv) if argv is not None else None)
    if arguments.dump_json:
        bundle = make_demo_bundle() if arguments.demo else RuntimeService().load()
        print(json.dumps(bundle.safe_dict(), ensure_ascii=False, indent=2))
        return 0

    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    app = QApplication(sys.argv[:1])
    app.setApplicationName("codexU")
    app.setApplicationDisplayName("codexU")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("codexU")
    app.setQuitOnLastWindowClosed(False)

    single = _SingleInstance("codexU-Windows-local")
    if not single.claim():
        return 0

    settings = SettingsStore()
    preferences = settings.load()
    controller = ApplicationController(
        app,
        preferences,
        settings,
        demo=arguments.demo,
        disable_tray=arguments.no_tray,
    )
    single.activate_requested.connect(controller.show_main)
    controller.start(minimized=arguments.minimized)
    return app.exec()
