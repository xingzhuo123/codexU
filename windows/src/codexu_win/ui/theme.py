from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication, QWidget


@dataclass(frozen=True, slots=True)
class ThemeTokens:
    dark: bool
    window: QColor
    section: QColor
    card: QColor
    elevated: QColor
    control: QColor
    control_selected: QColor
    text: QColor
    secondary_text: QColor
    tertiary_text: QColor
    stroke: QColor
    track: QColor
    brand: QColor
    brand_strong: QColor
    brand_light: QColor
    secondary_brand: QColor
    secondary_strong: QColor
    highlight: QColor
    success: QColor
    info: QColor
    warning: QColor
    danger: QColor
    neutral: QColor
    reasoning: QColor


BRAND = QColor("#2866F7")
BRAND_STRONG = QColor("#1F59ED")
BRAND_LIGHT = QColor("#7BA0FF")
SECONDARY_BRAND = QColor("#8B6DFF")
SECONDARY_STRONG = QColor("#6D45E8")
BRAND_HIGHLIGHT = QColor("#DAA3FA")
STATUS_SUCCESS = QColor("#30D158")
STATUS_INFO = QColor("#0A84FF")
STATUS_WARNING = QColor("#FF9F0A")
STATUS_DANGER = QColor("#FF453A")
STATUS_NEUTRAL = QColor("#98989D")
DATA_REASONING = QColor("#BF5AF2")


def tokens_for(dark: bool) -> ThemeTokens:
    if dark:
        return ThemeTokens(
            dark=True,
            window=QColor("#202124"),
            section=QColor("#2B2D31"),
            card=QColor("#34363B"),
            elevated=QColor("#3C3F45"),
            control=QColor("#37393F"),
            control_selected=QColor("#4A4D54"),
            text=QColor("#F2F3F5"),
            secondary_text=QColor("#B8BBC2"),
            tertiary_text=QColor("#858A94"),
            stroke=QColor("#484B52"),
            track=QColor("#4A4D54"),
            brand=BRAND,
            brand_strong=BRAND_STRONG,
            brand_light=BRAND_LIGHT,
            secondary_brand=SECONDARY_BRAND,
            secondary_strong=SECONDARY_STRONG,
            highlight=BRAND_HIGHLIGHT,
            success=STATUS_SUCCESS,
            info=STATUS_INFO,
            warning=STATUS_WARNING,
            danger=STATUS_DANGER,
            neutral=STATUS_NEUTRAL,
            reasoning=DATA_REASONING,
        )
    return ThemeTokens(
        dark=False,
        window=QColor("#F3F4F6"),
        section=QColor("#FAFAFB"),
        card=QColor("#FFFFFF"),
        elevated=QColor("#FFFFFF"),
        control=QColor("#F0F1F3"),
        control_selected=QColor("#E2E4E8"),
        text=QColor("#202124"),
        secondary_text=QColor("#61656D"),
        tertiary_text=QColor("#8B9099"),
        stroke=QColor("#DADCE1"),
        track=QColor("#E4E6EA"),
        brand=BRAND,
        brand_strong=BRAND_STRONG,
        brand_light=BRAND_LIGHT,
        secondary_brand=SECONDARY_BRAND,
        secondary_strong=SECONDARY_STRONG,
        highlight=BRAND_HIGHLIGHT,
        success=STATUS_SUCCESS,
        info=STATUS_INFO,
        warning=STATUS_WARNING,
        danger=STATUS_DANGER,
        neutral=STATUS_NEUTRAL,
        reasoning=DATA_REASONING,
    )


def system_is_dark() -> bool:
    app = QGuiApplication.instance()
    if app is None:
        return False
    scheme = app.styleHints().colorScheme()
    if scheme == Qt.ColorScheme.Dark:
        return True
    if scheme == Qt.ColorScheme.Light:
        return False
    return app.palette().color(QPalette.ColorRole.Window).lightness() < 128


def resolve_dark(theme: str) -> bool:
    if theme == "dark":
        return True
    if theme == "light":
        return False
    return system_is_dark()


def current_tokens(widget: QWidget | None = None) -> ThemeTokens:
    app = QApplication.instance()
    if app is not None:
        value = app.property("codexu_dark")
        if value is not None:
            return tokens_for(bool(value))
    if widget is not None:
        return tokens_for(widget.palette().color(QPalette.ColorRole.Window).lightness() < 128)
    return tokens_for(False)


def _rgba(color: QColor) -> str:
    return f"rgba({color.red()}, {color.green()}, {color.blue()}, {color.alpha()})"


def build_stylesheet(t: ThemeTokens) -> str:
    hover = t.control_selected.lighter(112) if t.dark else t.control_selected.darker(104)
    pressed = t.control_selected.lighter(122) if t.dark else t.control_selected.darker(110)
    return f"""
        QWidget {{
            color: {_rgba(t.text)};
            background: transparent;
            selection-background-color: {_rgba(t.brand)};
            selection-color: white;
        }}
        QMainWindow, QDialog, QWidget#windowRoot, QWidget#settingsRoot {{
            background-color: {_rgba(t.window)};
        }}
        QFrame#toolbar {{
            background-color: {_rgba(t.window)};
            border-bottom: 1px solid {_rgba(t.stroke)};
        }}
        QFrame#section, QFrame#quickPanel {{
            background-color: {_rgba(t.section)};
            border: 1px solid {_rgba(t.stroke)};
            border-radius: 14px;
        }}
        QFrame#card, QFrame#settingsSection {{
            background-color: {_rgba(t.card)};
            border: 1px solid {_rgba(t.stroke)};
            border-radius: 10px;
        }}
        QFrame#elevatedCard, QFrame#listRow {{
            background-color: {_rgba(t.elevated)};
            border: 1px solid {_rgba(t.stroke)};
            border-radius: 8px;
        }}
        QFrame#elevatedCard[selected="true"] {{
            border: 1px solid {_rgba(t.brand)};
        }}
        QLabel[role="secondary"] {{ color: {_rgba(t.secondary_text)}; }}
        QLabel[role="tertiary"] {{ color: {_rgba(t.tertiary_text)}; }}
        QLabel[role="metric"] {{ font-size: 22px; font-weight: 700; }}
        QLabel[role="cardTitle"] {{ color: {_rgba(t.secondary_text)}; font-weight: 600; }}
        QLabel[role="sectionTitle"] {{ font-size: 13px; font-weight: 600; }}
        QPushButton, QToolButton, QComboBox {{
            background-color: {_rgba(t.control)};
            border: 1px solid {_rgba(t.stroke)};
            border-radius: 8px;
            padding: 6px 10px;
        }}
        QPushButton:hover, QToolButton:hover, QComboBox:hover {{
            background-color: {_rgba(hover)};
        }}
        QPushButton:pressed, QToolButton:pressed {{
            background-color: {_rgba(pressed)};
        }}
        QPushButton:disabled, QToolButton:disabled {{
            color: {_rgba(t.tertiary_text)};
            background-color: {_rgba(t.control)};
        }}
        QPushButton[role="segment"] {{
            border: none;
            border-radius: 7px;
            background-color: transparent;
            min-height: 24px;
            padding: 3px 10px;
        }}
        QPushButton[role="segment"]:checked {{
            background-color: {_rgba(t.control_selected)};
            font-weight: 600;
        }}
        QFrame#segmentedControl {{
            background-color: {_rgba(t.control)};
            border: 1px solid {_rgba(t.stroke)};
            border-radius: 9px;
        }}
        QTabBar#dashboardTabs {{
            background-color: {_rgba(t.control)};
            border: 1px solid {_rgba(t.stroke)};
            border-radius: 9px;
        }}
        QTabBar#dashboardTabs::tab {{
            color: {_rgba(t.secondary_text)};
            background: transparent;
            border: none;
            min-width: 92px;
            min-height: 28px;
            padding: 3px 7px;
            margin: 3px;
        }}
        QTabBar#dashboardTabs::tab:selected {{
            color: {_rgba(t.text)};
            background-color: {_rgba(t.control_selected)};
            border-radius: 7px;
            font-weight: 600;
        }}
        QTabBar#dashboardTabs::tab:hover:!selected {{
            background-color: {_rgba(hover)};
            border-radius: 7px;
        }}
        QProgressBar {{
            min-height: 6px;
            max-height: 6px;
            border: none;
            border-radius: 3px;
            background-color: {_rgba(t.track)};
            text-align: center;
        }}
        QProgressBar::chunk {{
            border-radius: 3px;
            background-color: {_rgba(t.brand)};
        }}
        QScrollArea {{ border: none; background: transparent; }}
        QScrollBar:vertical {{
            width: 10px;
            margin: 2px;
            border: none;
            background: transparent;
        }}
        QScrollBar::handle:vertical {{
            min-height: 32px;
            border-radius: 4px;
            background-color: {_rgba(t.tertiary_text)};
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QCheckBox {{ spacing: 8px; }}
        QCheckBox::indicator {{ width: 17px; height: 17px; }}
        QComboBox {{ min-height: 20px; }}
        QComboBox QAbstractItemView {{
            background-color: {_rgba(t.card)};
            color: {_rgba(t.text)};
            border: 1px solid {_rgba(t.stroke)};
            selection-background-color: {_rgba(t.control_selected)};
        }}
        QToolTip {{
            color: {_rgba(t.text)};
            background-color: {_rgba(t.elevated)};
            border: 1px solid {_rgba(t.stroke)};
            padding: 5px;
        }}
        QMenu {{
            color: {_rgba(t.text)};
            background-color: {_rgba(t.card)};
            border: 1px solid {_rgba(t.stroke)};
            padding: 5px;
        }}
        QMenu::item {{ padding: 7px 28px 7px 10px; border-radius: 5px; }}
        QMenu::item:selected {{ background-color: {_rgba(t.control_selected)}; }}
        QMenu::separator {{ height: 1px; background: {_rgba(t.stroke)}; margin: 5px; }}
    """


def apply_theme(app: QApplication, theme: str = "system") -> ThemeTokens:
    dark = resolve_dark(theme)
    t = tokens_for(dark)
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, t.window)
    palette.setColor(QPalette.ColorRole.WindowText, t.text)
    palette.setColor(QPalette.ColorRole.Base, t.card)
    palette.setColor(QPalette.ColorRole.AlternateBase, t.section)
    palette.setColor(QPalette.ColorRole.ToolTipBase, t.elevated)
    palette.setColor(QPalette.ColorRole.ToolTipText, t.text)
    palette.setColor(QPalette.ColorRole.Text, t.text)
    palette.setColor(QPalette.ColorRole.Button, t.control)
    palette.setColor(QPalette.ColorRole.ButtonText, t.text)
    palette.setColor(QPalette.ColorRole.BrightText, t.danger)
    palette.setColor(QPalette.ColorRole.Highlight, t.brand)
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
    palette.setColor(QPalette.ColorRole.PlaceholderText, t.tertiary_text)
    app.setPalette(palette)
    app.setProperty("codexu_dark", dark)
    app.setStyleSheet(build_stylesheet(t))
    return t


def apply_window_backdrop(window: QWidget, dark: bool | None = None) -> None:
    """Enable the Windows 11 Mica backdrop without replacing the native frame."""

    if sys.platform != "win32" or not window.windowHandle():
        return
    try:
        hwnd = int(window.winId())
        dwmapi = ctypes.windll.dwmapi
        backdrop = ctypes.c_int(2)  # DWMSBT_MAINWINDOW
        dwmapi.DwmSetWindowAttribute(hwnd, 38, ctypes.byref(backdrop), ctypes.sizeof(backdrop))
        immersive_dark = ctypes.c_int(1 if (system_is_dark() if dark is None else dark) else 0)
        dwmapi.DwmSetWindowAttribute(
            hwnd,
            20,
            ctypes.byref(immersive_dark),
            ctypes.sizeof(immersive_dark),
        )
    except (AttributeError, OSError, ValueError):
        return
