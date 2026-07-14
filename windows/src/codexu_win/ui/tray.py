from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QCursor,
    QFocusEvent,
    QIcon,
    QKeyEvent,
    QLinearGradient,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QStyle,
    QSystemTrayIcon,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from codexu_win.i18n import tr
from codexu_win.models import RuntimeKind, RuntimeSnapshot, SnapshotBundle
from codexu_win.paths import asset_path
from codexu_win.ui.theme import current_tokens
from codexu_win.ui.widgets import SurfaceFrame, make_runtime_logo, runtime_summary_lines
from codexu_win.utils import format_tokens


class _QuickRuntimeCard(SurfaceFrame):
    def __init__(self, runtime: RuntimeKind, language: str, parent: QWidget | None = None) -> None:
        super().__init__("elevatedCard", parent)
        self.runtime = runtime
        self.language = language
        self.setFixedHeight(112)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(11, 9, 11, 9)
        layout.setSpacing(6)
        header = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(make_runtime_logo(runtime, 22))
        logo.setFixedSize(24, 24)
        header.addWidget(logo)
        title = QLabel(tr("codex" if runtime == RuntimeKind.CODEX else "claude", language))
        title.setStyleSheet("font-weight: 600; background: transparent;")
        header.addWidget(title)
        header.addStretch()
        self.status = QLabel("--")
        self.status.setProperty("role", "secondary")
        header.addWidget(self.status)
        layout.addLayout(header)

        metrics = QHBoxLayout()
        metrics.setSpacing(12)
        self.primary = self._metric(metrics, "5h")
        self.secondary = self._metric(metrics, "7d")
        self.today = self._metric(metrics, tr("today", language))
        layout.addLayout(metrics)

        self.source = QLabel(tr("empty", language))
        self.source.setProperty("role", "tertiary")
        layout.addWidget(self.source)

    def _metric(self, layout: QHBoxLayout, title: str) -> QLabel:
        column = QVBoxLayout()
        column.setSpacing(1)
        name = QLabel(title)
        name.setProperty("role", "secondary")
        column.addWidget(name)
        value = QLabel("--")
        value.setStyleSheet("font-size: 14px; font-weight: 700; background: transparent;")
        value.setMinimumWidth(66)
        column.addWidget(value)
        layout.addLayout(column, 1)
        return value

    def set_snapshot(self, snapshot: RuntimeSnapshot | None, selected: bool) -> None:
        primary, secondary, today, source = runtime_summary_lines(snapshot, self.language)
        self.primary.setText(primary)
        self.secondary.setText(secondary)
        self.today.setText(today)
        self.source.setText(source)
        if snapshot and snapshot.account:
            self.status.setText(snapshot.account.plan_type or snapshot.account.account_type)
        else:
            self.status.setText(tr("empty", self.language))
        self.setProperty("selected", selected)
        self.style().unpolish(self)
        self.style().polish(self)


class QuickPanel(SurfaceFrame):
    open_requested = Signal()
    refresh_requested = Signal()
    settings_requested = Signal()
    quit_requested = Signal()

    def __init__(self, language: str = "zh", parent: QWidget | None = None) -> None:
        super().__init__("quickPanel", parent)
        self.language = language
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedWidth(380)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(9)

        header = QHBoxLayout()
        app_icon = QLabel()
        icon_path = asset_path("codexU-icon.png")
        if icon_path.exists():
            app_icon.setPixmap(
                QPixmap(str(icon_path)).scaled(
                    26,
                    26,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        app_icon.setFixedSize(28, 28)
        header.addWidget(app_icon)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        app_name = QLabel("codexU")
        app_name.setStyleSheet("font-weight: 600; background: transparent;")
        titles.addWidget(app_name)
        self.updated = QLabel(f"{tr('refresh_time', language)} --")
        self.updated.setProperty("role", "secondary")
        titles.addWidget(self.updated)
        header.addLayout(titles)
        header.addStretch()
        refresh = QToolButton()
        refresh.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        refresh.setIconSize(QSize(15, 15))
        refresh.setToolTip(tr("refresh", language))
        refresh.setAccessibleName(tr("refresh", language))
        refresh.setFixedSize(30, 30)
        refresh.clicked.connect(self.refresh_requested)
        header.addWidget(refresh)
        layout.addLayout(header)

        self.cards: dict[RuntimeKind, _QuickRuntimeCard] = {}
        for runtime in RuntimeKind:
            card = _QuickRuntimeCard(runtime, language)
            self.cards[runtime] = card
            layout.addWidget(card)

        total = SurfaceFrame("elevatedCard")
        total_layout = QHBoxLayout(total)
        total_layout.setContentsMargins(11, 8, 11, 8)
        label = QLabel(f"{tr('today', language)} token")
        label.setProperty("role", "secondary")
        total_layout.addWidget(label)
        total_layout.addStretch()
        self.total_tokens = QLabel("--")
        self.total_tokens.setStyleSheet("font-size: 14px; font-weight: 700; background: transparent;")
        total_layout.addWidget(self.total_tokens)
        layout.addWidget(total)

        actions = QHBoxLayout()
        actions.setSpacing(7)
        for title, signal in (
            (tr("open", language), self.open_requested),
            (tr("settings", language), self.settings_requested),
            (tr("quit", language), self.quit_requested),
        ):
            button = QPushButton(title)
            button.setMinimumHeight(32)
            button.clicked.connect(signal)
            actions.addWidget(button, 1)
        layout.addLayout(actions)

    def update_bundle(self, bundle: SnapshotBundle | None, selected: RuntimeKind) -> None:
        snapshots = bundle.snapshots if bundle else {}
        values: list[int] = []
        refreshed: list[datetime] = []
        for runtime, card in self.cards.items():
            snapshot = snapshots.get(runtime)
            card.set_snapshot(snapshot, runtime == selected)
            if snapshot is not None:
                refreshed.append(snapshot.refreshed_at)
                if snapshot.today_tokens is not None:
                    values.append(snapshot.today_tokens)
        self.total_tokens.setText(format_tokens(sum(values)) if values else "--")
        if refreshed:
            latest = max(refreshed)
            if latest.tzinfo:
                latest = latest.astimezone()
            self.updated.setText(f"{tr('refresh_time', self.language)} {latest.strftime('%H:%M')}")
        else:
            self.updated.setText(f"{tr('refresh_time', self.language)} --")
        self.adjustSize()

    def focusOutEvent(self, event: QFocusEvent) -> None:  # noqa: N802 - Qt API
        super().focusOutEvent(event)
        self.hide()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt API
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            event.accept()
            return
        super().keyPressEvent(event)


class TrayController(QWidget):
    activated = Signal()
    open = Signal()
    refresh = Signal()
    settings = Signal()
    quit = Signal()

    def __init__(self, language: str = "zh", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        self.language = language
        self._bundle: SnapshotBundle | None = None
        self._runtime = RuntimeKind.CODEX
        self.panel = QuickPanel(language)
        self.panel.open_requested.connect(self._open)
        self.panel.refresh_requested.connect(self.refresh)
        self.panel.settings_requested.connect(self.settings)
        self.panel.quit_requested.connect(self.quit)

        self.tray = QSystemTrayIcon(self)
        icon_path = asset_path("codexU.ico")
        if icon_path.exists():
            self.tray.setIcon(QIcon(str(icon_path)))
        self.menu = QMenu()
        self._add_menu_action(tr("open", language), self.open, QStyle.StandardPixmap.SP_DialogOpenButton)
        self._add_menu_action(tr("refresh", language), self.refresh, QStyle.StandardPixmap.SP_BrowserReload)
        self.menu.addSeparator()
        self._add_menu_action(tr("settings", language), self.settings, QStyle.StandardPixmap.SP_ComputerIcon)
        self.menu.addSeparator()
        self._add_menu_action(tr("quit", language), self.quit, QStyle.StandardPixmap.SP_DialogCloseButton)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._tray_activated)
        # Calling show() before Explorer's tray is ready lets Qt register the
        # icon automatically when the tray becomes available later.
        self.tray.show()

    @property
    def available(self) -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable() and self.tray.isVisible()

    def _add_menu_action(self, title: str, signal, icon: QStyle.StandardPixmap) -> QAction:
        action = QAction(self.style().standardIcon(icon), title, self.menu)
        action.triggered.connect(signal)
        self.menu.addAction(action)
        return action

    def update_bundle(self, bundle: SnapshotBundle, runtime: RuntimeKind | str) -> None:
        self._bundle = bundle
        self._runtime = runtime if isinstance(runtime, RuntimeKind) else RuntimeKind(runtime)
        self.panel.update_bundle(bundle, self._runtime)
        snapshot = bundle.snapshots.get(self._runtime)
        self.tray.setIcon(self._quota_icon(snapshot))
        self.tray.setToolTip(self._tooltip(snapshot))

    def set_visible(self, visible: bool) -> None:
        self.tray.setVisible(visible and self.available)

    def toggle_panel(self) -> None:
        if self.panel.isVisible():
            self.panel.hide()
            return
        self.panel.update_bundle(self._bundle, self._runtime)
        self.panel.adjustSize()
        self._position_panel()
        self.panel.show()
        self.panel.raise_()
        self.panel.activateWindow()
        self.panel.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def _open(self) -> None:
        self.panel.hide()
        self.open.emit()

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.toggle_panel()
        elif reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.activated.emit()
            self._open()

    def _position_panel(self) -> None:
        geometry = self.tray.geometry()
        app = QApplication.instance()
        if app is None:
            return
        screen = QApplication.screenAt(geometry.center()) if geometry.isValid() else None
        if screen is None:
            screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        panel_size = self.panel.sizeHint().expandedTo(self.panel.minimumSizeHint())
        width = 380
        height = panel_size.height()
        if geometry.isValid():
            x = geometry.center().x() - width // 2
            above = geometry.top() - height - 8
            below = geometry.bottom() + 8
            y = above if above >= available.top() else below
        else:
            x = available.right() - width - 12
            y = available.bottom() - height - 12
        x = max(available.left() + 8, min(x, available.right() - width - 8))
        y = max(available.top() + 8, min(y, available.bottom() - height - 8))
        self.panel.move(QPoint(x, y))

    def _tooltip(self, snapshot: RuntimeSnapshot | None) -> str:
        if snapshot is None:
            return f"codexU · {tr('empty', self.language)}"
        primary, secondary, today, _source = runtime_summary_lines(snapshot, self.language)
        name = tr("codex" if snapshot.runtime == RuntimeKind.CODEX else "claude", self.language)
        refreshed = snapshot.refreshed_at.astimezone().strftime("%H:%M") if snapshot.refreshed_at.tzinfo else snapshot.refreshed_at.strftime("%H:%M")
        return f"codexU · {name}\n5h {primary} · 7d {secondary}\n{tr('today', self.language)} {today} · {refreshed}"

    def _quota_icon(self, snapshot: RuntimeSnapshot | None) -> QIcon:
        if snapshot is None or (snapshot.primary is None and snapshot.secondary is None):
            path = asset_path("codexU.ico")
            return QIcon(str(path)) if path.exists() else QIcon()
        icon = QIcon()
        for size in (16, 20, 24, 32, 48, 64):
            pixmap = QPixmap(size, size)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            self._paint_tray_ring(painter, snapshot, size)
            painter.end()
            icon.addPixmap(pixmap)
        return icon

    def _paint_tray_ring(self, painter: QPainter, snapshot: RuntimeSnapshot, size: int) -> None:
        t = current_tokens(self)
        outer_width = max(2.0, size * 0.13)
        inner_width = max(1.7, size * 0.105)
        outer = QRectF(outer_width / 2 + 1, outer_width / 2 + 1, size - outer_width - 2, size - outer_width - 2)
        gap = max(2.3, size * 0.16)
        inner = outer.adjusted(gap, gap, -gap, -gap)
        for rect, width, window, start, end in (
            (outer, outer_width, snapshot.primary, t.brand_light, t.brand),
            (inner, inner_width, snapshot.secondary, t.highlight, t.secondary_brand),
        ):
            track = QColor(t.neutral)
            track.setAlpha(95)
            painter.setPen(QPen(track, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawArc(rect, 0, 360 * 16)
            if window is None or window.remaining_percent <= 0:
                continue
            gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
            gradient.setColorAt(0, start)
            gradient.setColorAt(1, end)
            painter.setPen(QPen(QBrush(gradient), width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawArc(rect, 90 * 16, -round(360 * 16 * window.remaining_percent / 100))
