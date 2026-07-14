from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QAction, QIcon, QKeySequence, QResizeEvent, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QStyle,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from codexu_win.i18n import tr
from codexu_win.models import RuntimeKind, RuntimeSnapshot, SnapshotBundle, SourceQuality
from codexu_win.paths import asset_path
from codexu_win.settings import AppPreferences
from codexu_win.ui.theme import apply_theme, apply_window_backdrop, current_tokens
from codexu_win.ui.widgets import (
    HeaderBar,
    OverviewPanel,
    ProjectsPanel,
    SurfaceFrame,
    TaskBoardPanel,
    ToolsSkillsPanel,
    UsagePanel,
)


class _CenteredScrollArea(QScrollArea):
    def __init__(self, page: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._page = page
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        wrapper = QWidget()
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addStretch()
        layout.addWidget(page, 0, Qt.AlignmentFlag.AlignTop)
        layout.addStretch()
        self.setWidget(wrapper)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._page.setFixedWidth(min(1100, max(760, self.viewport().width())))


class MainWindow(QMainWindow):
    refresh_requested = Signal()
    settings_requested = Signal()
    runtime_changed = Signal(object)

    def __init__(
        self,
        preferences: AppPreferences | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.preferences = preferences or AppPreferences()
        try:
            self._runtime = RuntimeKind(self.preferences.selected_runtime)
        except ValueError:
            self._runtime = RuntimeKind.CODEX
        self.language = self.preferences.language
        self._bundle: SnapshotBundle | None = None
        self._loading = False

        app = QApplication.instance()
        if app is not None:
            apply_theme(app, self.preferences.theme)

        self.setWindowTitle("codexU")
        icon = asset_path("codexU.ico")
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))
        self.setMinimumSize(820, 640)
        self.resize(920, 800)
        self._build()
        self._install_shortcuts()
        self.apply_bundle(SnapshotBundle({}))

    @property
    def runtime(self) -> RuntimeKind:
        return self._runtime

    def _build(self) -> None:
        root = QWidget()
        root.setObjectName("windowRoot")
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        self.setCentralWidget(root)

        self.header = HeaderBar(self._runtime, self.language)
        self.header.runtime_changed.connect(self.set_runtime)
        self.header.refresh_requested.connect(self.refresh_requested)
        self.header.settings_requested.connect(self.settings_requested)
        root_layout.addWidget(self.header)

        page = QWidget()
        page.setMinimumWidth(760)
        page.setMaximumWidth(1100)
        page.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(16, 12, 16, 12)
        page_layout.setSpacing(12)

        self.diagnostic = SurfaceFrame("card")
        diagnostic_layout = QHBoxLayout(self.diagnostic)
        diagnostic_layout.setContentsMargins(11, 8, 11, 8)
        diagnostic_layout.setSpacing(8)
        diagnostic_icon = QLabel()
        diagnostic_icon.setPixmap(
            self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxInformation).pixmap(16, 16)
        )
        diagnostic_layout.addWidget(diagnostic_icon)
        self.diagnostic_text = QLabel()
        self.diagnostic_text.setProperty("role", "secondary")
        self.diagnostic_text.setWordWrap(True)
        diagnostic_layout.addWidget(self.diagnostic_text, 1)
        self.diagnostic.hide()
        page_layout.addWidget(self.diagnostic)

        self.overview = OverviewPanel(self.language)
        page_layout.addWidget(self.overview)

        dashboard = SurfaceFrame("section")
        dashboard_layout = QVBoxLayout(dashboard)
        dashboard_layout.setContentsMargins(12, 12, 12, 12)
        dashboard_layout.setSpacing(10)
        tab_header = QHBoxLayout()
        tab_header.setSpacing(10)
        self.tabs = QTabBar()
        self.tabs.setObjectName("dashboardTabs")
        self.tabs.setDocumentMode(True)
        self.tabs.setExpanding(False)
        self.tabs.setDrawBase(False)
        self.tabs.setUsesScrollButtons(False)
        self.tabs.setAccessibleName("Dashboard tabs")
        self.tabs.addTab(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogApplyButton),
            tr("today_tasks", self.language),
        )
        self.tabs.addTab(
            self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogListView),
            tr("usage_trend", self.language),
        )
        self.tabs.addTab(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon),
            tr("projects", self.language),
        )
        self.tabs.addTab(
            self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView),
            tr("skills", self.language),
        )
        self.tabs.setIconSize(QSize(14, 14))
        tab_header.addWidget(self.tabs)
        tab_header.addStretch()
        self.summary = QLabel(tr("loading", self.language))
        self.summary.setProperty("role", "secondary")
        self.summary.setMinimumWidth(160)
        self.summary.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        tab_header.addWidget(self.summary)
        dashboard_layout.addLayout(tab_header)

        self.stack = QStackedWidget()
        self.stack.setObjectName("dashboardStack")
        self.task_panel = TaskBoardPanel(self.language)
        self.usage_panel = UsagePanel(self.language)
        self.projects_panel = ProjectsPanel(self.language)
        self.tools_panel = ToolsSkillsPanel(self.language)
        for panel in (
            self.task_panel,
            self.usage_panel,
            self.projects_panel,
            self.tools_panel,
        ):
            self.stack.addWidget(panel)
        self.tabs.currentChanged.connect(self._select_tab)
        dashboard_layout.addWidget(self.stack)
        page_layout.addWidget(dashboard)
        page_layout.addStretch()

        self.scroll = _CenteredScrollArea(page)
        root_layout.addWidget(self.scroll, 1)

        footer = QFrame()
        footer.setFixedHeight(32)
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(16, 4, 16, 6)
        footer_layout.setSpacing(8)
        self.source_note = QLabel(tr("source_note", self.language))
        self.source_note.setProperty("role", "tertiary")
        self.source_note.setMaximumWidth(620)
        footer_layout.addWidget(self.source_note)
        footer_layout.addStretch()
        self.refreshed = QLabel(tr("loading", self.language))
        self.refreshed.setProperty("role", "secondary")
        self.refreshed.setMinimumWidth(120)
        self.refreshed.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        footer_layout.addWidget(self.refreshed)
        root_layout.addWidget(footer)

    def _install_shortcuts(self) -> None:
        self._add_action("Ctrl+R", self.refresh_requested.emit)
        self._add_action("Ctrl+,", self.settings_requested.emit)
        self._add_action("Ctrl+W", self.close)
        for index in range(4):
            self._add_action(f"Ctrl+{index + 1}", lambda checked=False, value=index: self.select_tab(value))
        self._add_action("Alt+1", lambda: self.set_runtime(RuntimeKind.CODEX))
        self._add_action("Alt+2", lambda: self.set_runtime(RuntimeKind.CLAUDE))

    def _add_action(self, shortcut: str, slot) -> None:
        action = QAction(self)
        action.setShortcut(QKeySequence(shortcut))
        action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        action.triggered.connect(slot)
        self.addAction(action)

    def apply_bundle(self, bundle: SnapshotBundle) -> None:
        self._bundle = bundle
        self._apply_snapshot(self._snapshot())

    def set_loading(self, loading: bool) -> None:
        self._loading = loading
        self.header.set_loading(loading)
        if loading:
            self.refreshed.setText(tr("loading", self.language))
            self.summary.setText(tr("loading", self.language))
        else:
            self._update_footer(self._snapshot())
            self._update_summary(self._snapshot())

    def set_runtime(self, runtime: RuntimeKind | str) -> None:
        value = runtime if isinstance(runtime, RuntimeKind) else RuntimeKind(runtime)
        if value == self._runtime:
            self.header.runtime_control.set_runtime(value)
            return
        self._runtime = value
        self.preferences.selected_runtime = value.value
        self.header.runtime_control.set_runtime(value)
        self._apply_snapshot(self._snapshot())
        self.runtime_changed.emit(value)

    def select_tab(self, index: int) -> None:
        if 0 <= index < self.tabs.count():
            self.tabs.setCurrentIndex(index)

    def _select_tab(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self._update_summary(self._snapshot())

    def _snapshot(self) -> RuntimeSnapshot | None:
        if self._bundle is None:
            return None
        return self._bundle.snapshots.get(self._runtime)

    def _apply_snapshot(self, snapshot: RuntimeSnapshot | None) -> None:
        self.overview.set_snapshot(snapshot)
        self.task_panel.set_board(snapshot.task_board if snapshot else None)
        self.usage_panel.set_snapshot(snapshot)
        self.projects_panel.set_snapshot(snapshot)
        self.tools_panel.set_snapshot(snapshot)
        self._update_diagnostic(snapshot)
        if not self._loading:
            self._update_summary(snapshot)
            self._update_footer(snapshot)

    def _update_diagnostic(self, snapshot: RuntimeSnapshot | None) -> None:
        if snapshot is None:
            self.diagnostic_text.setText(tr("no_usage", self.language))
            self.diagnostic.show()
            return
        messages = list(snapshot.diagnostics)
        if snapshot.primary is None and snapshot.secondary is None:
            messages.insert(0, tr("no_quota", self.language))
        if not messages:
            self.diagnostic.hide()
            return
        self.diagnostic_text.setText(" · ".join(messages[:3]))
        self.diagnostic.show()

    def _update_summary(self, snapshot: RuntimeSnapshot | None) -> None:
        if snapshot is None:
            self.summary.setText(tr("empty", self.language))
            return
        index = self.tabs.currentIndex()
        if index == 0:
            count = snapshot.task_board.total_count if snapshot.task_board else 0
            label = f"{count} {'items' if self.language == 'en' else '事项'}"
        elif index == 1:
            active_days = sum(1 for item in snapshot.daily_usage if item.tokens > 0)
            quality = (
                tr("detailed", self.language)
                if snapshot.quality == SourceQuality.DETAILED
                else tr("approximate", self.language)
            )
            label = f"{active_days} {'active days' if self.language == 'en' else '活跃日'} · {quality}"
        elif index == 2:
            label = f"{len(snapshot.recent_projects)} / {len(snapshot.all_projects)}"
        else:
            label = f"{len(snapshot.tools)} Tools · {len(snapshot.skills)} Skills"
        self.summary.setText(label)

    def _update_footer(self, snapshot: RuntimeSnapshot | None) -> None:
        if snapshot is None:
            self.refreshed.setText(f"{tr('refresh_time', self.language)} --")
            return
        refreshed = snapshot.refreshed_at
        if refreshed.tzinfo is not None:
            refreshed = refreshed.astimezone()
        self.refreshed.setText(f"{tr('refresh_time', self.language)} {refreshed.strftime('%H:%M')}")

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        apply_window_backdrop(self, current_tokens(self).dark)
