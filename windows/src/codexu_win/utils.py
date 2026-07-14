from __future__ import annotations

import hashlib
import math
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, Iterable


def parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 10_000_000_000:
            seconds /= 1000.0
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc).astimezone()
        except (OSError, OverflowError, ValueError):
            return None
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if re.fullmatch(r"-?\d+(?:\.\d+)?", raw):
            return parse_datetime(float(raw))
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone()
    return None


def start_of_local_day(moment: datetime | None = None) -> datetime:
    current = moment.astimezone() if moment else datetime.now().astimezone()
    return current.replace(hour=0, minute=0, second=0, microsecond=0)


def day_key(moment: datetime | date) -> str:
    if isinstance(moment, datetime):
        moment = moment.astimezone().date() if moment.tzinfo else moment.date()
    return moment.isoformat()


def normalize_windows_path(raw: str) -> str:
    if not raw:
        return ""
    expanded = os.path.expandvars(os.path.expanduser(raw.strip()))
    if expanded.startswith("\\\\?\\UNC\\"):
        expanded = "\\\\" + expanded[8:]
    elif expanded.startswith("\\\\?\\"):
        expanded = expanded[4:]
    return os.path.normcase(os.path.normpath(expanded))


def project_display_name(raw: str, fallback: str = "未归类") -> str:
    normalized = normalize_windows_path(raw)
    if not normalized or normalized in (".", os.path.sep):
        return fallback
    if "\\" in normalized or re.match(r"^[a-zA-Z]:", normalized):
        name = PureWindowsPath(normalized).name
    else:
        name = Path(normalized).name
    return name or fallback


def stable_private_id(namespace: str, raw: str) -> str:
    digest = hashlib.blake2b(
        normalize_windows_path(raw).encode("utf-8", errors="replace"),
        digest_size=10,
        person=namespace.encode("ascii", errors="ignore")[:16],
    ).hexdigest()
    return f"{namespace}-{digest}"


def safe_title(value: str | None, fallback: str = "未命名") -> str:
    if not value:
        return fallback
    single_line = re.sub(r"\s+", " ", value).strip()
    if not single_line:
        return fallback
    return single_line if len(single_line) <= 64 else single_line[:61] + "..."


def format_tokens(value: int | None) -> str:
    if value is None:
        return "--"
    magnitude = abs(value)
    if magnitude >= 1_000_000_000:
        return f"{value / 1_000_000_000:.1f}B"
    if magnitude >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if magnitude >= 1_000:
        return f"{value / 1_000:.1f}K"
    return f"{value:,}"


def format_currency(value: float | None) -> str:
    if value is None:
        return "--"
    if value >= 1000:
        return f"${value:,.0f}"
    if value >= 100:
        return f"${value:,.1f}"
    return f"${value:,.2f}"


def relative_time(moment: datetime | None, language: str = "zh") -> str:
    if moment is None:
        return "--"
    current = datetime.now().astimezone()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=current.tzinfo)
    seconds = max(0, int((current - moment.astimezone()).total_seconds()))
    if language == "en":
        if seconds < 60:
            return "just now"
        if seconds < 3600:
            return f"{seconds // 60}m ago"
        if seconds < 86400:
            return f"{seconds // 3600}h ago"
        return f"{seconds // 86400}d ago"
    if seconds < 60:
        return "刚刚"
    if seconds < 3600:
        return f"{seconds // 60} 分钟前"
    if seconds < 86400:
        return f"{seconds // 3600} 小时前"
    return f"{seconds // 86400} 天前"


def heatmap_thresholds(values: Iterable[int]) -> list[int]:
    sorted_values = sorted(value for value in values if value > 0)
    if not sorted_values:
        return [1, 2, 3, 4]
    if len(sorted_values) < 5:
        maximum = max(sorted_values)
        return [max(1, math.ceil(maximum * ratio)) for ratio in (0.25, 0.5, 0.75, 1.0)]

    def percentile(fraction: float) -> int:
        index = min(len(sorted_values) - 1, max(0, math.ceil(len(sorted_values) * fraction) - 1))
        return sorted_values[index]

    result = [percentile(value) for value in (0.25, 0.5, 0.75, 0.9)]
    for index in range(1, len(result)):
        result[index] = max(result[index], result[index - 1] + 1)
    return result


def tool_category(name: str) -> str:
    normalized = name.lower()
    if any(part in normalized for part in ("exec", "shell", "terminal", "stdin", "bash")):
        return "terminal"
    if any(part in normalized for part in ("patch", "edit", "write")):
        return "edit"
    if any(part in normalized for part in ("web", "browser", "page", "click", "fetch", "screenshot")):
        return "browser"
    if any(part in normalized for part in ("image", "figma", "visual")):
        return "visual"
    if any(part in normalized for part in ("docs", "read", "grep", "glob", "library", "resource", "mcp")):
        return "docs"
    if any(part in normalized for part in ("plan", "goal", "task", "agent", "todo")):
        return "planning"
    return "tool"
