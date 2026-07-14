from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QIcon,
    QLinearGradient,
    QMouseEvent,
    QPaintEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QResizeEvent,
)
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStyle,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from codexu_win.i18n import tr
from codexu_win.models import (
    DailyUsage,
    PricedUsage,
    ProjectUsage,
    RateWindow,
    RuntimeKind,
    RuntimeSnapshot,
    SkillUsage,
    SourceQuality,
    TaskBoard,
    TaskColumnKind,
    TaskItem,
    ToolUsage,
)
from codexu_win.paths import asset_path
from codexu_win.ui.theme import ThemeTokens, current_tokens
from codexu_win.utils import format_currency, format_tokens, heatmap_thresholds, relative_time


def _runtime(value: RuntimeKind | str) -> RuntimeKind:
    if isinstance(value, RuntimeKind):
        return value
    try:
        return RuntimeKind(value)
    except ValueError:
        return RuntimeKind.CODEX


def _clear_layout(layout: QVBoxLayout | QHBoxLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()
        child = item.layout()
        if child is not None:
            _clear_layout(child)  # type: ignore[arg-type]


def _font(size: float, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    result = QFont()
    result.setPointSizeF(size)
    result.setWeight(weight)
    return result


def _remaining(window: RateWindow | None) -> float | None:
    return window.remaining_percent if window is not None else None


def _reset_text(window: RateWindow | None, language: str) -> str:
    if window is None or window.resets_at is None:
        return "--"
    moment = window.resets_at.astimezone() if window.resets_at.tzinfo else window.resets_at
    if language == "en":
        return moment.strftime("%b %d %H:%M")
    return moment.strftime("%m/%d %H:%M")


class ElidedLabel(QLabel):
    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full_text = text
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802 - Qt API
        self._full_text = text
        self.setToolTip(text if text and text != "--" else "")
        self._update_elision()

    def full_text(self) -> str:
        return self._full_text

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._update_elision()

    def _update_elision(self) -> None:
        width = max(0, self.contentsRect().width())
        shown = self.fontMetrics().elidedText(
            self._full_text,
            Qt.TextElideMode.ElideRight,
            width,
        )
        QLabel.setText(self, shown)


class SurfaceFrame(QFrame):
    def __init__(self, role: str = "card", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName(role)
        self.setFrameShape(QFrame.Shape.NoFrame)


class EmptyState(QWidget):
    def __init__(self, title: str, detail: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(112)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 18, 12, 18)
        layout.setSpacing(5)
        layout.addStretch()
        icon = QLabel()
        pixmap = self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogInfoView).pixmap(18, 18)
        icon.setPixmap(pixmap)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(icon)
        title_label = QLabel(title)
        title_label.setProperty("role", "secondary")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setFont(_font(9.0, QFont.Weight.DemiBold))
        layout.addWidget(title_label)
        if detail:
            detail_label = QLabel(detail)
            detail_label.setProperty("role", "tertiary")
            detail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            detail_label.setWordWrap(True)
            detail_label.setFont(_font(8.0))
            layout.addWidget(detail_label)
        layout.addStretch()


class RuntimeSegmentedControl(SurfaceFrame):
    runtime_changed = Signal(object)

    def __init__(
        self,
        runtime: RuntimeKind = RuntimeKind.CODEX,
        language: str = "zh",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("segmentedControl", parent)
        self.language = language
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[RuntimeKind, QPushButton] = {}
        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)
        for kind, key, icon_name in (
            (RuntimeKind.CODEX, "codex", "codex-color.png"),
            (RuntimeKind.CLAUDE, "claude", "claudecode-color.png"),
        ):
            button = QPushButton(tr(key, language))
            button.setProperty("role", "segment")
            button.setCheckable(True)
            button.setMinimumWidth(86 if kind == RuntimeKind.CODEX else 116)
            icon_path = asset_path(icon_name)
            if icon_path.exists():
                button.setIcon(QIcon(str(icon_path)))
                button.setIconSize(QSize(16, 16))
            button.clicked.connect(lambda _checked=False, value=kind: self._select(value, True))
            self._group.addButton(button)
            self._buttons[kind] = button
            layout.addWidget(button)
        self.set_runtime(runtime)

    @property
    def runtime(self) -> RuntimeKind:
        for kind, button in self._buttons.items():
            if button.isChecked():
                return kind
        return RuntimeKind.CODEX

    def set_runtime(self, runtime: RuntimeKind | str) -> None:
        self._select(_runtime(runtime), False)

    def _select(self, runtime: RuntimeKind, emit: bool) -> None:
        self._buttons[runtime].setChecked(True)
        if emit:
            self.runtime_changed.emit(runtime)


class HeaderBar(SurfaceFrame):
    runtime_changed = Signal(object)
    refresh_requested = Signal()
    settings_requested = Signal()

    def __init__(self, runtime: RuntimeKind, language: str, parent: QWidget | None = None) -> None:
        super().__init__("toolbar", parent)
        self.setFixedHeight(48)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 7, 16, 7)
        layout.setSpacing(10)

        subtitle = QLabel(tr("app_subtitle", language))
        subtitle.setProperty("role", "secondary")
        subtitle.setFont(_font(9.0, QFont.Weight.DemiBold))
        layout.addWidget(subtitle)
        layout.addStretch()

        self.runtime_control = RuntimeSegmentedControl(runtime, language)
        self.runtime_control.runtime_changed.connect(self.runtime_changed)
        layout.addWidget(self.runtime_control)

        self.refresh_button = self._tool_button(
            QStyle.StandardPixmap.SP_BrowserReload,
            tr("refresh", language),
        )
        self.refresh_button.clicked.connect(self.refresh_requested)
        layout.addWidget(self.refresh_button)

        self.settings_button = self._tool_button(
            QStyle.StandardPixmap.SP_ComputerIcon,
            tr("settings", language),
        )
        self.settings_button.clicked.connect(self.settings_requested)
        layout.addWidget(self.settings_button)

    def _tool_button(self, icon: QStyle.StandardPixmap, tooltip: str) -> QToolButton:
        button = QToolButton()
        button.setIcon(self.style().standardIcon(icon))
        button.setIconSize(QSize(16, 16))
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        button.setFixedSize(32, 32)
        return button

    def set_loading(self, loading: bool) -> None:
        self.refresh_button.setDisabled(loading)


class DualQuotaRing(QWidget):
    def __init__(self, language: str = "zh", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.language = language
        self.primary: RateWindow | None = None
        self.secondary: RateWindow | None = None
        self.setMinimumSize(156, 156)
        self.setMaximumSize(176, 176)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(164, 164)

    def set_windows(self, primary: RateWindow | None, secondary: RateWindow | None) -> None:
        self.primary = primary
        self.secondary = secondary
        primary_text = "--" if primary is None else f"{primary.remaining_percent:.0f}%"
        secondary_text = "--" if secondary is None else f"{secondary.remaining_percent:.0f}%"
        self.setToolTip(
            f"5h {tr('remaining', self.language)} {primary_text} · {tr('resets', self.language)} {_reset_text(primary, self.language)}\n"
            f"7d {tr('remaining', self.language)} {secondary_text} · {tr('resets', self.language)} {_reset_text(secondary, self.language)}"
        )
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt API
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = current_tokens(self)
        side = min(self.width(), self.height()) - 12
        origin_x = (self.width() - side) / 2
        origin_y = (self.height() - side) / 2
        outer = QRectF(origin_x + 7, origin_y + 7, side - 14, side - 14)
        inner = outer.adjusted(18, 18, -18, -18)
        self._draw_ring(painter, outer, _remaining(self.primary), t.brand_light, t.brand, t)
        self._draw_ring(painter, inner, _remaining(self.secondary), t.highlight, t.secondary_brand, t)

        center_x = self.width() / 2
        painter.setFont(_font(9.0, QFont.Weight.DemiBold))
        primary_value = "--" if self.primary is None else f"{self.primary.remaining_percent:.0f}%"
        secondary_value = "--" if self.secondary is None else f"{self.secondary.remaining_percent:.0f}%"
        self._draw_quota_text(painter, center_x, self.height() / 2 - 17, "5h", primary_value, t.brand, t)
        self._draw_quota_text(
            painter,
            center_x,
            self.height() / 2 + 7,
            "7d",
            secondary_value,
            t.secondary_brand,
            t,
        )
        painter.setPen(t.secondary_text)
        painter.setFont(_font(8.0, QFont.Weight.DemiBold))
        painter.drawText(
            QRectF(center_x - 42, self.height() / 2 + 25, 84, 18),
            Qt.AlignmentFlag.AlignCenter,
            tr("remaining", self.language),
        )

    def _draw_ring(
        self,
        painter: QPainter,
        rect: QRectF,
        percent: float | None,
        start_color: QColor,
        end_color: QColor,
        t: ThemeTokens,
    ) -> None:
        track_pen = QPen(t.track, 10, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(track_pen)
        painter.drawArc(rect, 0, 360 * 16)
        if percent is None:
            return
        fraction = max(0.0, min(100.0, percent)) / 100.0
        if fraction <= 0:
            return
        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        gradient.setColorAt(0.0, start_color)
        gradient.setColorAt(1.0, end_color)
        progress_pen = QPen(QBrush(gradient), 10, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(progress_pen)
        painter.drawArc(rect, 90 * 16, -int(360 * 16 * fraction))

    def _draw_quota_text(
        self,
        painter: QPainter,
        center_x: float,
        y: float,
        label: str,
        value: str,
        accent: QColor,
        t: ThemeTokens,
    ) -> None:
        painter.setFont(_font(8.5, QFont.Weight.Bold))
        painter.setPen(accent)
        painter.drawText(QRectF(center_x - 38, y, 26, 20), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, label)
        painter.setFont(_font(13.0, QFont.Weight.Bold))
        painter.setPen(t.text)
        painter.drawText(QRectF(center_x - 8, y, 52, 20), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, value)


class TokenStackBar(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._values: tuple[int, int, int] | None = None
        self.setFixedHeight(8)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_values(self, values: tuple[int, int, int] | None) -> None:
        self._values = values
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt API
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = current_tokens(self)
        rect = QRectF(0, 1, self.width(), max(1, self.height() - 2))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(t.track)
        painter.drawRoundedRect(rect, 3, 3)
        if self._values is None:
            return
        values = tuple(max(0, value) for value in self._values)
        total = sum(values)
        if total <= 0:
            return
        path = QPainterPath()
        path.addRoundedRect(rect, 3, 3)
        painter.save()
        painter.setClipPath(path)
        x = rect.left()
        colors = (t.info, t.secondary_brand, t.warning)
        for index, value in enumerate(values):
            if value <= 0:
                continue
            width = rect.width() * value / total
            if index == len(values) - 1:
                width = rect.right() - x + 1
            painter.fillRect(QRectF(x, rect.top(), max(0.0, width), rect.height()), colors[index])
            x += width
        painter.restore()


class LegendDot(QWidget):
    def __init__(self, color_role: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.color_role = color_role
        self.setFixedSize(9, 9)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt API
        del event
        t = current_tokens(self)
        color = {
            "info": t.info,
            "secondary": t.secondary_brand,
            "warning": t.warning,
        }[self.color_role]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(QRectF(1, 1, 7, 7))


class TokenMetricCard(SurfaceFrame):
    def __init__(self, title: str, language: str = "zh", parent: QWidget | None = None) -> None:
        super().__init__("card", parent)
        self.setMinimumHeight(154)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(11, 10, 11, 10)
        layout.setSpacing(6)

        header = QHBoxLayout()
        self.title_label = QLabel(title)
        self.title_label.setProperty("role", "cardTitle")
        self.title_label.setFont(_font(9.0, QFont.Weight.DemiBold))
        header.addWidget(self.title_label)
        header.addStretch()
        self.cost_label = QLabel("--")
        self.cost_label.setProperty("role", "secondary")
        self.cost_label.setFont(_font(8.5, QFont.Weight.DemiBold))
        header.addWidget(self.cost_label)
        layout.addLayout(header)

        self.value_label = QLabel("--")
        self.value_label.setProperty("role", "metric")
        self.value_label.setFont(_font(17.0, QFont.Weight.Bold))
        self.value_label.setMinimumWidth(96)
        layout.addWidget(self.value_label)

        self.bar = TokenStackBar()
        layout.addWidget(self.bar)
        self.legend_labels: list[QLabel] = []
        for color, label in (
            ("info", tr("uncached", language)),
            ("secondary", tr("cached", language)),
            ("warning", tr("output", language)),
        ):
            row = QHBoxLayout()
            row.setSpacing(5)
            dot = LegendDot(color)
            row.addWidget(dot)
            name = QLabel(label)
            name.setProperty("role", "secondary")
            name.setFont(_font(7.8))
            row.addWidget(name)
            row.addStretch()
            value = QLabel("--")
            value.setProperty("role", "secondary")
            value.setFont(_font(7.8, QFont.Weight.DemiBold))
            value.setMinimumWidth(42)
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            row.addWidget(value)
            self.legend_labels.append(value)
            layout.addLayout(row)

    def set_usage(self, usage: PricedUsage | None, fallback_total: int | None) -> None:
        if usage is None:
            self.value_label.setText(format_tokens(fallback_total))
            self.cost_label.setText("--")
            self.bar.set_values(None)
            for label in self.legend_labels:
                label.setText("--")
            return
        tokens = usage.tokens
        total = tokens.visible_total_tokens
        self.value_label.setText(format_tokens(total))
        self.cost_label.setText(format_currency(usage.estimated_cost_usd))
        values = (
            tokens.uncached_input_tokens,
            tokens.billable_cached_input_tokens,
            max(0, tokens.output_tokens),
        )
        self.bar.set_values(values)
        for label, value in zip(self.legend_labels, values, strict=True):
            label.setText(format_tokens(value))


class ApiValueCard(SurfaceFrame):
    def __init__(self, language: str, parent: QWidget | None = None) -> None:
        super().__init__("card", parent)
        self.language = language
        self.setFixedHeight(92)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(6)
        header = QHBoxLayout()
        title = QLabel(tr("value_progress", language))
        title.setProperty("role", "cardTitle")
        title.setFont(_font(9.0, QFont.Weight.DemiBold))
        header.addWidget(title)
        header.addStretch()
        self.value = QLabel("--")
        self.value.setFont(_font(14.0, QFont.Weight.Bold))
        header.addWidget(self.value)
        self.estimate = QLabel(tr("estimated", language))
        self.estimate.setProperty("role", "secondary")
        self.estimate.setFont(_font(8.0, QFont.Weight.DemiBold))
        header.addWidget(self.estimate)
        layout.addLayout(header)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)
        footer = QHBoxLayout()
        for label in ("Plus", "Pro 100", "Pro 200"):
            item = QLabel(label)
            item.setProperty("role", "secondary")
            item.setFont(_font(7.5))
            footer.addWidget(item)
        footer.addStretch()
        self.cap = QLabel("$200+")
        self.cap.setProperty("role", "secondary")
        self.cap.setFont(_font(7.5))
        footer.addWidget(self.cap)
        layout.addLayout(footer)

    def set_value(self, value: float | None) -> None:
        self.value.setText(format_currency(value))
        if value is None:
            self.progress.setValue(0)
            self.progress.setProperty("missing", True)
            self.cap.setText("$200+")
        else:
            cap = max(200.0, value * 1.05)
            self.progress.setValue(round(min(1.0, max(0.0, value) / cap) * 1000))
            self.progress.setProperty("missing", False)
            self.cap.setText(f"{format_currency(cap)}+")


class OverviewPanel(SurfaceFrame):
    def __init__(self, language: str = "zh", parent: QWidget | None = None) -> None:
        super().__init__("section", parent)
        self.language = language
        # Three fixed token cards plus the value card require a stable 292 px.
        # Let the page scroll vertically instead of compressing and clipping rows.
        self.setFixedHeight(292)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(22)

        quota_block = QWidget()
        quota_block.setFixedWidth(170)
        quota_layout = QVBoxLayout(quota_block)
        quota_layout.setContentsMargins(0, 0, 0, 0)
        quota_layout.setSpacing(4)
        self.ring = DualQuotaRing(language)
        quota_layout.addWidget(self.ring, 0, Qt.AlignmentFlag.AlignHCenter)
        self.primary_reset = QLabel("5h  ·  --")
        self.secondary_reset = QLabel("7d  ·  --")
        for label in (self.primary_reset, self.secondary_reset):
            label.setProperty("role", "secondary")
            label.setFont(_font(8.0, QFont.Weight.DemiBold))
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            quota_layout.addWidget(label)
        layout.addWidget(quota_block)

        right = QVBoxLayout()
        right.setSpacing(10)
        metrics = QHBoxLayout()
        metrics.setSpacing(10)
        self.today = TokenMetricCard(tr("today", language), language)
        self.seven = TokenMetricCard(tr("seven_day", language), language)
        self.lifetime = TokenMetricCard(tr("lifetime", language), language)
        for card in (self.today, self.seven, self.lifetime):
            metrics.addWidget(card, 1)
        right.addLayout(metrics)
        self.api_value = ApiValueCard(language)
        right.addWidget(self.api_value)
        layout.addLayout(right, 1)

    def set_snapshot(self, snapshot: RuntimeSnapshot | None) -> None:
        if snapshot is None:
            self.ring.set_windows(None, None)
            self.primary_reset.setText("5h  ·  --")
            self.secondary_reset.setText("7d  ·  --")
            self.today.set_usage(None, None)
            self.seven.set_usage(None, None)
            self.lifetime.set_usage(None, None)
            self.api_value.set_value(None)
            return
        self.ring.set_windows(snapshot.primary, snapshot.secondary)
        self.primary_reset.setText(f"5h  {tr('resets', self.language)}  {_reset_text(snapshot.primary, self.language)}")
        self.secondary_reset.setText(f"7d  {tr('resets', self.language)}  {_reset_text(snapshot.secondary, self.language)}")
        detailed = snapshot.detailed
        self.today.set_usage(detailed.today if detailed else None, snapshot.approximate_today_tokens)
        self.seven.set_usage(detailed.seven_day if detailed else None, snapshot.approximate_seven_day_tokens)
        self.lifetime.set_usage(detailed.lifetime if detailed else None, snapshot.approximate_lifetime_tokens)
        self.api_value.set_value(detailed.month.estimated_cost_usd if detailed else None)


class TaskIssueWidget(SurfaceFrame):
    def __init__(self, item: TaskItem, language: str, parent: QWidget | None = None) -> None:
        super().__init__("listRow", parent)
        self.setMinimumHeight(88)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(9, 8, 9, 8)
        layout.setSpacing(3)
        first = QHBoxLayout()
        code = QLabel(item.code or item.task_id)
        code.setProperty("role", "secondary")
        code.setFont(_font(8.0, QFont.Weight.DemiBold))
        first.addWidget(code)
        first.addStretch()
        updated = QLabel(relative_time(item.updated_at, language))
        updated.setProperty("role", "tertiary")
        updated.setFont(_font(7.5))
        first.addWidget(updated)
        layout.addLayout(first)
        title = ElidedLabel(item.title or "--")
        title.setFont(_font(8.7, QFont.Weight.DemiBold))
        layout.addWidget(title)
        detail = ElidedLabel(item.detail or "--")
        detail.setProperty("role", "secondary")
        detail.setFont(_font(7.8))
        layout.addWidget(detail)
        footer = QHBoxLayout()
        chip = QLabel(item.chip or "--")
        chip.setProperty("role", "secondary")
        chip.setFont(_font(7.5, QFont.Weight.DemiBold))
        footer.addWidget(chip)
        footer.addStretch()
        tokens = QLabel(format_tokens(item.tokens))
        tokens.setProperty("role", "secondary")
        tokens.setFont(_font(7.5, QFont.Weight.DemiBold))
        footer.addWidget(tokens)
        layout.addLayout(footer)


class TaskColumnWidget(SurfaceFrame):
    def __init__(self, kind: TaskColumnKind, language: str, parent: QWidget | None = None) -> None:
        super().__init__("card", parent)
        self.kind = kind
        self.language = language
        self.setMinimumWidth(168)
        self.setMinimumHeight(300)
        self.layout_root = QVBoxLayout(self)
        self.layout_root.setContentsMargins(9, 9, 9, 9)
        self.layout_root.setSpacing(7)
        header = QHBoxLayout()
        key = {
            TaskColumnKind.ACTIVE: "active",
            TaskColumnKind.PENDING: "pending",
            TaskColumnKind.SCHEDULED: "scheduled",
            TaskColumnKind.DONE: "done",
        }[kind]
        title = QLabel(tr(key, language))
        title.setFont(_font(8.8, QFont.Weight.DemiBold))
        header.addWidget(title)
        self.count = QLabel("0")
        self.count.setProperty("role", "secondary")
        self.count.setFont(_font(8.0, QFont.Weight.DemiBold))
        header.addWidget(self.count)
        header.addStretch()
        self.layout_root.addLayout(header)
        self.items_layout = QVBoxLayout()
        self.items_layout.setSpacing(7)
        self.layout_root.addLayout(self.items_layout)
        self.layout_root.addStretch()

    def set_items(self, items: Iterable[TaskItem]) -> None:
        values = list(items)
        self.count.setText(str(len(values)))
        _clear_layout(self.items_layout)
        if not values:
            self.items_layout.addWidget(EmptyState(tr("empty", self.language)))
            return
        for item in values[:4]:
            self.items_layout.addWidget(TaskIssueWidget(item, self.language))
        if len(values) > 4:
            more = QLabel(f"+ {len(values) - 4}")
            more.setProperty("role", "secondary")
            more.setFont(_font(8.0, QFont.Weight.DemiBold))
            self.items_layout.addWidget(more)


class TaskBoardPanel(QWidget):
    def __init__(self, language: str = "zh", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.language = language
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.columns: dict[TaskColumnKind, TaskColumnWidget] = {}
        for kind in TaskColumnKind:
            column = TaskColumnWidget(kind, language)
            self.columns[kind] = column
            layout.addWidget(column, 1)

    def set_board(self, board: TaskBoard | None) -> None:
        for kind, column in self.columns.items():
            column.set_items(board.columns.get(kind, []) if board else [])


@dataclass(slots=True)
class _HeatCell:
    rect: QRectF
    day: date
    value: int | None


class HeatmapWidget(QWidget):
    def __init__(self, language: str = "zh", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.language = language
        self._usage: list[DailyUsage] = []
        self._cells: list[_HeatCell] = []
        self.setMouseTracking(True)
        self.setMinimumSize(390, 158)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(440, 170)

    def set_usage(self, usage: Iterable[DailyUsage]) -> None:
        self._usage = sorted(list(usage), key=lambda item: item.day)
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt API
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = current_tokens(self)
        self._cells = []
        if not self._usage:
            self._draw_empty(painter, tr("no_usage", self.language), t)
            return
        parsed: dict[date, int] = {}
        for item in self._usage:
            try:
                parsed[date.fromisoformat(item.day)] = max(0, item.tokens)
            except ValueError:
                continue
        if not parsed:
            self._draw_empty(painter, tr("no_usage", self.language), t)
            return
        end = max(parsed)
        grid_start = end - timedelta(days=end.weekday() + 25 * 7)
        columns = 26
        rows = 7
        left = 27.0
        top = 25.0
        right = 8.0
        gap = 3.0
        available = max(0.0, self.width() - left - right - gap * (columns - 1))
        cell = max(7.0, min(11.0, available / columns))
        grid_width = columns * cell + (columns - 1) * gap
        x0 = left + max(0.0, (self.width() - left - right - grid_width) / 2)
        thresholds = heatmap_thresholds(parsed.values())
        painter.setFont(_font(7.0))
        painter.setPen(t.tertiary_text)
        weekdays = ["一", "二", "三", "四", "五", "六", "日"] if self.language != "en" else ["M", "T", "W", "T", "F", "S", "S"]
        for row, label in enumerate(weekdays):
            y = top + row * (cell + gap)
            painter.drawText(QRectF(0, y - 1, 21, cell + 2), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, label)
        last_month = -1
        for column in range(columns):
            first_day = grid_start + timedelta(days=column * 7)
            if first_day.month != last_month:
                last_month = first_day.month
                label = first_day.strftime("%b") if self.language == "en" else f"{first_day.month}月"
                painter.drawText(QRectF(x0 + column * (cell + gap), 2, 38, 17), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)
            for row in range(rows):
                day = first_day + timedelta(days=row)
                rect = QRectF(x0 + column * (cell + gap), top + row * (cell + gap), cell, cell)
                if day > end:
                    continue
                value = parsed.get(day)
                if value is None:
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.setPen(QPen(t.stroke, 1, Qt.PenStyle.DotLine))
                    painter.drawRoundedRect(rect, 2, 2)
                else:
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(self._heat_color(value, thresholds, t))
                    painter.drawRoundedRect(rect, 2, 2)
                self._cells.append(_HeatCell(rect, day, value))
        legend_y = top + rows * (cell + gap) + 7
        painter.setFont(_font(7.0))
        painter.setPen(t.tertiary_text)
        painter.drawText(QRectF(x0, legend_y, 28, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, tr("less", self.language))
        lx = x0 + 30
        legend_values = (0, thresholds[0], thresholds[1], thresholds[2], thresholds[3])
        for value in legend_values:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self._heat_color(value, thresholds, t))
            painter.drawRoundedRect(QRectF(lx, legend_y + 2, 10, 10), 2, 2)
            lx += 14
        painter.setPen(t.tertiary_text)
        painter.drawText(QRectF(lx + 1, legend_y, 30, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, tr("more", self.language))

    def _heat_color(self, value: int, thresholds: list[int], t: ThemeTokens) -> QColor:
        if value <= 0:
            return t.track
        level = 1
        for threshold in thresholds:
            if value >= threshold:
                level += 1
        alpha = (72, 112, 166, 220, 245)[min(4, level - 1)]
        color = QColor(t.secondary_brand)
        color.setAlpha(alpha)
        return color

    def _draw_empty(self, painter: QPainter, text: str, t: ThemeTokens) -> None:
        painter.setPen(t.secondary_text)
        painter.setFont(_font(9.0, QFont.Weight.DemiBold))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, text)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        point = event.position()
        for cell in self._cells:
            if cell.rect.contains(point):
                value = tr("empty", self.language) if cell.value is None else f"{format_tokens(cell.value)} token"
                QToolTip.showText(event.globalPosition().toPoint(), f"{cell.day.isoformat()}\n{value}", self)
                return
        QToolTip.hideText()
        super().mouseMoveEvent(event)


@dataclass(slots=True)
class _ChartPoint:
    point: QPoint
    usage: DailyUsage


class SevenDayChart(QWidget):
    def __init__(self, language: str = "zh", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.language = language
        self._usage: list[DailyUsage] = []
        self._points: list[_ChartPoint] = []
        self.setMouseTracking(True)
        self.setMinimumSize(260, 158)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(320, 170)

    def set_usage(self, usage: Iterable[DailyUsage]) -> None:
        self._usage = sorted(list(usage), key=lambda item: item.day)[-7:]
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt API
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = current_tokens(self)
        self._points = []
        if not self._usage:
            painter.setPen(t.secondary_text)
            painter.setFont(_font(9.0, QFont.Weight.DemiBold))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, tr("no_usage", self.language))
            return
        plot = QRectF(16, 12, max(1, self.width() - 32), max(1, self.height() - 43))
        painter.setPen(QPen(t.stroke, 1))
        for index in range(4):
            y = plot.top() + plot.height() * index / 3
            painter.drawLine(QPoint(round(plot.left()), round(y)), QPoint(round(plot.right()), round(y)))
        maximum = max((item.tokens for item in self._usage), default=0)
        maximum = max(1, maximum)
        count = len(self._usage)
        points: list[QPoint] = []
        for index, item in enumerate(self._usage):
            x = plot.left() if count == 1 else plot.left() + plot.width() * index / (count - 1)
            y = plot.bottom() - plot.height() * max(0, item.tokens) / maximum
            point = QPoint(round(x), round(y))
            points.append(point)
            self._points.append(_ChartPoint(point, item))
        if len(points) > 1:
            path = QPainterPath()
            path.moveTo(points[0])
            for point in points[1:]:
                path.lineTo(point)
            painter.setPen(QPen(t.secondary_brand, 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.drawPath(path)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(t.secondary_brand)
        for point in points:
            painter.drawEllipse(point, 4, 4)
        painter.setFont(_font(7.0))
        painter.setPen(t.tertiary_text)
        for index, item in enumerate(self._usage):
            point = points[index]
            try:
                parsed = date.fromisoformat(item.day)
                label = parsed.strftime("%a") if self.language == "en" else "一二三四五六日"[parsed.weekday()]
            except ValueError:
                label = item.day[-2:]
            painter.drawText(QRectF(point.x() - 18, plot.bottom() + 8, 36, 16), Qt.AlignmentFlag.AlignCenter, label)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        position = event.position().toPoint()
        for item in self._points:
            if (item.point - position).manhattanLength() <= 10:
                QToolTip.showText(
                    event.globalPosition().toPoint(),
                    f"{item.usage.day}\n{format_tokens(item.usage.tokens)} token\n{tr('estimated', self.language)} {format_currency(item.usage.estimated_cost_usd)}",
                    self,
                )
                return
        QToolTip.hideText()
        super().mouseMoveEvent(event)


class UsagePanel(QWidget):
    def __init__(self, language: str = "zh", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.language = language
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        heat_card = SurfaceFrame("card")
        heat_layout = QVBoxLayout(heat_card)
        heat_layout.setContentsMargins(10, 9, 10, 9)
        heat_layout.setSpacing(5)
        heat_header = QLabel(tr("last_six_months", language))
        heat_header.setProperty("role", "cardTitle")
        heat_header.setFont(_font(9.0, QFont.Weight.DemiBold))
        heat_layout.addWidget(heat_header)
        self.heatmap = HeatmapWidget(language)
        heat_layout.addWidget(self.heatmap)
        layout.addWidget(heat_card, 55)

        line_card = SurfaceFrame("card")
        line_layout = QVBoxLayout(line_card)
        line_layout.setContentsMargins(10, 9, 10, 9)
        line_layout.setSpacing(5)
        line_header = QHBoxLayout()
        title = QLabel(tr("last_seven_summary", language))
        title.setProperty("role", "cardTitle")
        title.setFont(_font(9.0, QFont.Weight.DemiBold))
        line_header.addWidget(title)
        line_header.addStretch()
        self.change = QLabel("--")
        self.change.setProperty("role", "secondary")
        self.change.setFont(_font(8.0, QFont.Weight.DemiBold))
        line_header.addWidget(self.change)
        line_layout.addLayout(line_header)
        self.line_chart = SevenDayChart(language)
        line_layout.addWidget(self.line_chart)
        footer = QHBoxLayout()
        self.total = QLabel("--")
        self.total.setFont(_font(12.0, QFont.Weight.Bold))
        footer.addWidget(self.total)
        footer.addStretch()
        self.average = QLabel("--")
        self.average.setProperty("role", "secondary")
        self.average.setFont(_font(8.0, QFont.Weight.DemiBold))
        footer.addWidget(self.average)
        line_layout.addLayout(footer)
        layout.addWidget(line_card, 45)

    def set_snapshot(self, snapshot: RuntimeSnapshot | None) -> None:
        daily = snapshot.daily_usage if snapshot else []
        self.heatmap.set_usage(daily)
        self.line_chart.set_usage(daily)
        recent = daily[-7:]
        total = sum(max(0, item.tokens) for item in recent)
        self.total.setText(format_tokens(total) if daily else "--")
        self.average.setText(
            f"{tr('daily_average', self.language)} {format_tokens(round(total / len(recent)))}"
            if recent
            else "--"
        )
        if snapshot and snapshot.detailed:
            previous = snapshot.detailed.previous_seven_day.tokens.visible_total_tokens
            current = snapshot.detailed.seven_day.tokens.visible_total_tokens
            if previous > 0:
                change = (current - previous) / previous * 100
                self.change.setText(f"{change:+.0f}%")
            elif current == 0:
                self.change.setText("0%")
            else:
                self.change.setText("--")
        else:
            self.change.setText("--")


class RelativeListRow(SurfaceFrame):
    def __init__(
        self,
        title: str,
        subtitle: str,
        primary: str,
        secondary: str,
        fraction: float,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("listRow", parent)
        self.setMinimumHeight(66)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(9, 7, 9, 7)
        layout.setSpacing(3)
        top = QHBoxLayout()
        labels = QVBoxLayout()
        labels.setSpacing(1)
        name = ElidedLabel(title)
        name.setFont(_font(8.8, QFont.Weight.DemiBold))
        labels.addWidget(name)
        detail = ElidedLabel(subtitle)
        detail.setProperty("role", "secondary")
        detail.setFont(_font(7.6))
        labels.addWidget(detail)
        top.addLayout(labels, 1)
        values = QVBoxLayout()
        values.setSpacing(1)
        value = QLabel(primary)
        value.setFont(_font(9.0, QFont.Weight.Bold))
        value.setAlignment(Qt.AlignmentFlag.AlignRight)
        values.addWidget(value)
        supporting = QLabel(secondary)
        supporting.setProperty("role", "secondary")
        supporting.setFont(_font(7.4, QFont.Weight.DemiBold))
        supporting.setAlignment(Qt.AlignmentFlag.AlignRight)
        values.addWidget(supporting)
        top.addLayout(values)
        layout.addLayout(top)
        progress = QProgressBar()
        progress.setRange(0, 1000)
        progress.setValue(round(max(0.0, min(1.0, fraction)) * 1000))
        progress.setTextVisible(False)
        layout.addWidget(progress)


class DynamicListCard(SurfaceFrame):
    def __init__(self, title: str, language: str, parent: QWidget | None = None) -> None:
        super().__init__("card", parent)
        self.language = language
        self.setMinimumHeight(280)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 9, 10, 9)
        layout.setSpacing(7)
        header = QLabel(title)
        header.setProperty("role", "cardTitle")
        header.setFont(_font(9.0, QFont.Weight.DemiBold))
        layout.addWidget(header)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(6)
        layout.addLayout(self.rows)
        layout.addStretch()

    def clear(self) -> None:
        _clear_layout(self.rows)

    def show_empty(self, detail: str | None = None) -> None:
        self.clear()
        self.rows.addWidget(EmptyState(tr("empty", self.language), detail or ""))


class ProjectsPanel(QWidget):
    def __init__(self, language: str = "zh", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.language = language
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.ranking = DynamicListCard(tr("projects", language), language)
        self.activity = DynamicListCard(tr("last_active", language), language)
        layout.addWidget(self.ranking, 1)
        layout.addWidget(self.activity, 1)

    def set_snapshot(self, snapshot: RuntimeSnapshot | None) -> None:
        projects = snapshot.recent_projects if snapshot else []
        all_projects = snapshot.all_projects if snapshot else []
        self.ranking.clear()
        if not all_projects:
            self.ranking.show_empty()
        else:
            maximum = max(1, max(project.tokens for project in all_projects))
            for project in all_projects[:6]:
                self.ranking.rows.addWidget(self._project_row(project, maximum))
        self.activity.clear()
        if not projects:
            self.activity.show_empty()
        else:
            maximum = max(1, max(project.tokens for project in projects))
            for project in projects[:6]:
                self.activity.rows.addWidget(self._project_row(project, maximum))

    def _project_row(self, project: ProjectUsage, maximum: int) -> RelativeListRow:
        quality = tr("detailed", self.language) if project.quality == SourceQuality.DETAILED else tr("approximate", self.language)
        subtitle = f"{project.session_count} {tr('sessions', self.language)} · {relative_time(project.last_active_at, self.language)} · {quality}"
        return RelativeListRow(
            project.display_name,
            subtitle,
            format_tokens(project.tokens),
            f"{tr('estimated', self.language)} {format_currency(project.estimated_cost_usd)}",
            project.tokens / maximum,
        )


class ToolsSkillsPanel(QWidget):
    def __init__(self, language: str = "zh", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.language = language
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.skills = DynamicListCard(tr("skills_top", language), language)
        self.tools = DynamicListCard(tr("tools_top", language), language)
        layout.addWidget(self.skills, 1)
        layout.addWidget(self.tools, 1)

    def set_snapshot(self, snapshot: RuntimeSnapshot | None) -> None:
        self._set_skills(snapshot.skills if snapshot else [])
        self._set_tools(snapshot.tools if snapshot else [])

    def _set_skills(self, skills: list[SkillUsage]) -> None:
        self.skills.clear()
        if not skills:
            self.skills.show_empty()
            return
        maximum = max(1, max(item.load_count for item in skills))
        for item in skills[:8]:
            secondary = "--" if item.static_token_estimate is None else f"Skill.md {format_tokens(item.static_token_estimate)}"
            subtitle = f"{item.source_label} · {item.session_count} {tr('sessions', self.language)} · {relative_time(item.last_loaded_at, self.language)}"
            self.skills.rows.addWidget(
                RelativeListRow(
                    item.display_name,
                    subtitle,
                    f"{item.load_count} {tr('loads', self.language)}",
                    secondary,
                    item.load_count / maximum,
                )
            )

    def _set_tools(self, tools: list[ToolUsage]) -> None:
        self.tools.clear()
        if not tools:
            self.tools.show_empty()
            return
        maximum = max(1, max(item.call_count for item in tools))
        for item in tools[:8]:
            estimated = "--" if item.estimated_tokens is None else f"{tr('estimated', self.language)} {format_tokens(item.estimated_tokens)}"
            subtitle = f"{item.category} · {item.session_count} {tr('sessions', self.language)}"
            self.tools.rows.addWidget(
                RelativeListRow(
                    item.name,
                    subtitle,
                    f"{item.call_count} {tr('calls', self.language)}",
                    estimated,
                    item.call_count / maximum,
                )
            )


def runtime_summary_lines(snapshot: RuntimeSnapshot | None, language: str) -> tuple[str, str, str, str]:
    if snapshot is None:
        return "--", "--", "--", tr("no_usage", language)
    primary = "--" if snapshot.primary is None else f"{snapshot.primary.remaining_percent:.0f}%"
    secondary = "--" if snapshot.secondary is None else f"{snapshot.secondary.remaining_percent:.0f}%"
    today = format_tokens(snapshot.today_tokens)
    if snapshot.quality == SourceQuality.DETAILED:
        source = tr("detailed", language)
    elif snapshot.quality == SourceQuality.APPROXIMATE:
        source = tr("approximate", language)
    else:
        source = tr("empty", language)
    return primary, secondary, today, source


def make_runtime_logo(runtime: RuntimeKind, size: int = 24) -> QPixmap:
    filename = "codex-color.png" if runtime == RuntimeKind.CODEX else "claudecode-color.png"
    path = asset_path(filename)
    if path.exists():
        return QPixmap(str(path)).scaled(
            size,
            size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    return QPixmap()
