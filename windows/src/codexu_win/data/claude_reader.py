from __future__ import annotations

import json
import math
import os
import re
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from codexu_win.models import (
    AccountInfo,
    DailyUsage,
    DetailedUsage,
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
    TokenBreakdown,
    ToolUsage,
)
from codexu_win.paths import RuntimePaths
from codexu_win.utils import (
    day_key,
    parse_datetime,
    project_display_name,
    safe_title,
    stable_private_id,
    start_of_local_day,
    tool_category,
)


_CACHE_VERSION = 1
_CACHE_FILE = "session-usage-v1.json"
_STATUS_FILE = "statusline-snapshot.json"
_DAY_BUCKET_COUNT = 180
_STATUS_STALE_AFTER = timedelta(minutes=15)
_SAFE_STATUS = re.compile(r"^[a-z0-9_-]{1,32}$")


@dataclass(slots=True)
class _TranscriptEvent:
    session_key: str
    message_key: str
    occurred_at: datetime
    tokens: TokenBreakdown | None
    model: str | None
    project_id: str
    project_name: str
    tool_names: tuple[str, ...] = ()
    skill_names: tuple[str, ...] = ()


@dataclass(slots=True)
class _TranscriptSummary:
    last_active_at: datetime | None
    events: list[_TranscriptEvent] = field(default_factory=list)


@dataclass(slots=True)
class _StatsFallback:
    today_tokens: int | None = None
    seven_day_tokens: int | None = None
    lifetime_tokens: int | None = None
    updated_at: datetime | None = None

    @property
    def is_available(self) -> bool:
        return any(
            value is not None
            for value in (self.today_tokens, self.seven_day_tokens, self.lifetime_tokens)
        )


@dataclass(slots=True)
class _ProjectAccumulator:
    display_name: str
    tokens: int = 0
    estimated_cost_usd: float = 0.0
    has_cost_estimate: bool = False
    sessions: set[str] = field(default_factory=set)
    last_active_at: datetime | None = None


@dataclass(slots=True)
class _NameAccumulator:
    count: int = 0
    sessions: set[str] = field(default_factory=set)
    last_seen_at: datetime | None = None


class ClaudeReader:
    """Read aggregate-only Claude Code state from local Windows files."""

    def __init__(self, paths: RuntimePaths):
        self.paths = paths
        self._projects_root = paths.claude_root / "projects"
        self._tasks_root = paths.claude_root / "tasks"
        self._stats_path = paths.claude_root / "stats-cache.json"
        self._cache_dir = paths.cache_root / "claude-code"
        self._cache_path = self._cache_dir / _CACHE_FILE
        self._status_path = self._cache_dir / _STATUS_FILE

    def load(self, now: datetime | None = None) -> RuntimeSnapshot:
        refreshed_at = _local_moment(now)
        diagnostics: list[str] = []

        summaries = self._load_transcript_summaries(diagnostics)
        events = _deduplicate_events(summary.events for summary in summaries)
        token_events = [event for event in events if event.tokens is not None]

        detailed: DetailedUsage | None = None
        daily_usage: list[DailyUsage] = []
        projects: list[ProjectUsage] = []
        tools = _aggregate_tools(events)
        skills = _aggregate_skills(events)
        quality = SourceQuality.UNAVAILABLE

        if token_events:
            detailed, daily_usage, projects, unknown_price_count = _aggregate_token_usage(
                token_events,
                parsed_file_count=len(summaries),
                now=refreshed_at,
            )
            quality = SourceQuality.DETAILED
            if unknown_price_count:
                diagnostics.append(
                    "Some Claude Code models have no local price estimate; "
                    "token totals remain complete."
                )

        fallback = _StatsFallback()
        if detailed is None:
            fallback = self._load_stats_fallback(diagnostics)
            if fallback.is_available:
                quality = SourceQuality.APPROXIMATE

        primary, secondary = self._load_status_snapshot(refreshed_at, diagnostics)
        task_board = self._load_task_board(refreshed_at, diagnostics)

        session_keys = {event.session_key for event in events}
        last_active_at = _latest_datetime(
            [summary.last_active_at for summary in summaries]
            + [fallback.updated_at]
        )
        if detailed is None and not fallback.is_available:
            diagnostics.append("No Claude Code token usage records were found.")

        projects_by_recency = sorted(
            projects,
            key=lambda project: _datetime_order(project.last_active_at),
            reverse=True,
        )
        return RuntimeSnapshot(
            runtime=RuntimeKind.CLAUDE,
            refreshed_at=refreshed_at,
            account=AccountInfo(account_type="local", plan_type="Claude Code"),
            primary=primary,
            secondary=secondary,
            detailed=detailed,
            approximate_lifetime_tokens=fallback.lifetime_tokens,
            approximate_today_tokens=fallback.today_tokens,
            approximate_seven_day_tokens=fallback.seven_day_tokens,
            thread_count=len(session_keys) if session_keys else None,
            last_active_at=last_active_at,
            daily_usage=daily_usage,
            recent_projects=projects_by_recency[:8],
            all_projects=projects,
            tools=tools,
            skills=skills,
            task_board=task_board,
            quality=quality,
            diagnostics=diagnostics,
        )

    def _load_transcript_summaries(
        self,
        diagnostics: list[str],
    ) -> list[_TranscriptSummary]:
        cached_entries, cache_exists, cache_valid = self._read_cache()
        current_entries: dict[str, Any] = {}
        summaries: list[_TranscriptSummary] = []

        files = self._transcript_files(diagnostics)
        read_failures = 0
        malformed_lines = 0
        for path in files:
            try:
                stat = path.stat()
                relative_key = str(path.relative_to(self._projects_root))
            except (OSError, ValueError):
                read_failures += 1
                continue

            cache_key = stable_private_id("claude-file", relative_key)
            fingerprint = {
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            }
            cached_entry = cached_entries.get(cache_key)
            cached_summary = _summary_from_cache_entry(cached_entry)
            if (
                isinstance(cached_entry, dict)
                and cached_entry.get("size") == fingerprint["size"]
                and cached_entry.get("mtime_ns") == fingerprint["mtime_ns"]
                and cached_summary is not None
            ):
                summary = cached_summary
            else:
                summary, invalid_count = self._parse_transcript(
                    path,
                    cache_key=cache_key,
                    fallback_timestamp=datetime.fromtimestamp(stat.st_mtime).astimezone(),
                )
                malformed_lines += invalid_count
                if summary is None:
                    read_failures += 1
                    if cached_summary is None:
                        continue
                    summary = cached_summary
                    # Keep the old fingerprint so a later refresh retries the changed file.
                    fingerprint = {
                        "size": cached_entry.get("size"),
                        "mtime_ns": cached_entry.get("mtime_ns"),
                    }

            summaries.append(summary)
            current_entries[cache_key] = {
                **fingerprint,
                "summary": _summary_to_json(summary),
            }

        if read_failures:
            diagnostics.append(
                f"Unable to read {read_failures} Claude Code transcript file(s); "
                "partial totals are shown."
            )
        if malformed_lines:
            diagnostics.append(
                f"Skipped {malformed_lines} incomplete or malformed Claude Code transcript line(s)."
            )

        should_write = bool(current_entries) or cache_exists
        if should_write and (
            not cache_valid or current_entries != cached_entries
        ):
            self._write_cache(current_entries, diagnostics)
        return summaries

    def _transcript_files(self, diagnostics: list[str]) -> list[Path]:
        if not self._projects_root.is_dir():
            diagnostics.append("Claude Code projects directory was not found.")
            return []
        try:
            return sorted(
                path
                for path in self._projects_root.rglob("*.jsonl")
                if not path.is_symlink() and path.is_file()
            )
        except OSError:
            diagnostics.append("Claude Code projects directory could not be enumerated.")
            return []

    def _parse_transcript(
        self,
        path: Path,
        *,
        cache_key: str,
        fallback_timestamp: datetime,
    ) -> tuple[_TranscriptSummary | None, int]:
        events: list[_TranscriptEvent] = []
        latest = fallback_timestamp
        malformed_lines = 0
        fallback_session = stable_private_id("claude-session", cache_key)

        try:
            handle = path.open("r", encoding="utf-8", errors="replace")
        except OSError:
            return None, 0

        try:
            with handle:
                for line_number, raw_line in enumerate(handle, start=1):
                    if not any(
                        marker in raw_line
                        for marker in ('"usage"', '"tool_use"', "attribution")
                    ):
                        continue
                    try:
                        record = json.loads(raw_line)
                    except (json.JSONDecodeError, UnicodeError):
                        malformed_lines += 1
                        continue
                    if not isinstance(record, dict):
                        continue
                    event = _event_from_record(
                        record,
                        fallback_session=fallback_session,
                        fallback_message=f"{cache_key}:{line_number}",
                        fallback_timestamp=fallback_timestamp,
                        fallback_project_key=cache_key,
                    )
                    if event is None:
                        continue
                    events.append(event)
                    if event.occurred_at > latest:
                        latest = event.occurred_at
        except OSError:
            return None, malformed_lines

        return _TranscriptSummary(last_active_at=latest, events=events), malformed_lines

    def _read_cache(self) -> tuple[dict[str, Any], bool, bool]:
        if not self._cache_path.is_file():
            return {}, False, True
        try:
            value = json.loads(self._cache_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {}, True, False
        if not isinstance(value, dict) or value.get("version") != _CACHE_VERSION:
            return {}, True, False
        entries = value.get("entries")
        if not isinstance(entries, dict):
            return {}, True, False
        return entries, True, True

    def _write_cache(
        self,
        entries: dict[str, Any],
        diagnostics: list[str],
    ) -> None:
        temporary_path: str | None = None
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_path = tempfile.mkstemp(
                prefix=f".{_CACHE_FILE}.",
                suffix=".tmp",
                dir=self._cache_dir,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(
                    {"version": _CACHE_VERSION, "entries": entries},
                    handle,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self._cache_path)
            temporary_path = None
        except OSError:
            diagnostics.append("Claude Code aggregate cache could not be updated.")
        finally:
            if temporary_path:
                try:
                    Path(temporary_path).unlink(missing_ok=True)
                except OSError:
                    pass

    def _load_stats_fallback(self, diagnostics: list[str]) -> _StatsFallback:
        if not self._stats_path.is_file():
            return _StatsFallback()
        try:
            value = json.loads(self._stats_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            diagnostics.append("Claude Code stats cache could not be parsed.")
            return _StatsFallback()
        if not isinstance(value, dict):
            return _StatsFallback()

        result = _StatsFallback(
            today_tokens=_optional_nonnegative_int(
                _first_value(value, "todayTokens", "today_tokens")
            ),
            seven_day_tokens=_optional_nonnegative_int(
                _first_value(value, "sevenDayTokens", "seven_day_tokens")
            ),
            lifetime_tokens=_optional_nonnegative_int(
                _first_value(value, "totalTokens", "total_tokens")
            ),
            updated_at=parse_datetime(_first_value(value, "updatedAt", "updated_at")),
        )
        if result.is_available:
            diagnostics.append(
                "Claude Code stats cache is an approximate fallback; "
                "detailed splits are unavailable."
            )
        return result

    def _load_status_snapshot(
        self,
        now: datetime,
        diagnostics: list[str],
    ) -> tuple[RateWindow | None, RateWindow | None]:
        if not self._status_path.is_file():
            diagnostics.append("Claude Code quota snapshot is unavailable.")
            return None, None
        try:
            value = json.loads(self._status_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            diagnostics.append("Claude Code quota snapshot could not be parsed.")
            return None, None
        if not isinstance(value, dict):
            return None, None

        captured_at = parse_datetime(_first_value(value, "capturedAt", "captured_at"))
        if captured_at is not None and now - captured_at > _STATUS_STALE_AFTER:
            diagnostics.append("Claude Code quota snapshot is older than 15 minutes.")

        limits = _first_dict(value, "rateLimits", "rate_limits")
        if limits is None:
            return None, None
        primary = _rate_window(
            _first_dict(limits, "fiveHour", "five_hour"),
            window_minutes=300,
        )
        secondary = _rate_window(
            _first_dict(limits, "sevenDay", "seven_day"),
            window_minutes=10_080,
        )
        return primary, secondary

    def _load_task_board(
        self,
        now: datetime,
        diagnostics: list[str],
    ) -> TaskBoard | None:
        if not self._tasks_root.is_dir():
            return None
        try:
            files = sorted(
                path
                for path in self._tasks_root.rglob("*.json")
                if not path.is_symlink() and path.is_file()
            )
        except OSError:
            diagnostics.append("Claude Code tasks directory could not be enumerated.")
            return None

        columns: dict[TaskColumnKind, list[TaskItem]] = {
            kind: [] for kind in TaskColumnKind
        }
        failures = 0
        for path in files:
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                stat = path.stat()
                relative_key = str(path.relative_to(self._tasks_root))
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
                failures += 1
                continue
            if not isinstance(value, dict):
                continue

            status = _task_status(value.get("status"))
            column = _task_column(status)
            title_value = value.get("subject")
            if not isinstance(title_value, str):
                title_value = value.get("title") if isinstance(value.get("title"), str) else None
            updated_at = parse_datetime(_first_value(value, "updatedAt", "updated_at"))
            if updated_at is None:
                updated_at = datetime.fromtimestamp(stat.st_mtime).astimezone()

            columns[column].append(
                TaskItem(
                    task_id=stable_private_id("claude-task", relative_key),
                    code=status,
                    title=safe_title(title_value, "Claude Code task"),
                    detail="Claude Code task",
                    chip=status,
                    updated_at=updated_at,
                    tokens=None,
                    column=column,
                )
            )

        if failures:
            diagnostics.append(f"Skipped {failures} unreadable Claude Code task file(s).")
        if not any(columns.values()):
            return None
        for items in columns.values():
            items.sort(
                key=lambda item: _datetime_order(item.updated_at),
                reverse=True,
            )
        return TaskBoard(refreshed_at=now, columns=columns)


def load_claude_snapshot(
    paths: RuntimePaths,
    now: datetime | None = None,
) -> RuntimeSnapshot:
    return ClaudeReader(paths).load(now=now)


def _event_from_record(
    record: dict[str, Any],
    *,
    fallback_session: str,
    fallback_message: str,
    fallback_timestamp: datetime,
    fallback_project_key: str,
) -> _TranscriptEvent | None:
    message = record.get("message")
    if not isinstance(message, dict):
        message = {}

    raw_session = _first_string(record, "sessionId", "session_id")
    session_key = (
        stable_private_id("claude-session", raw_session)
        if raw_session
        else fallback_session
    )
    raw_message = (
        _first_string(message, "id")
        or _first_string(record, "uuid", "id")
    )
    message_key = stable_private_id(
        "claude-message",
        raw_message or fallback_message,
    )
    occurred_at = parse_datetime(record.get("timestamp")) or fallback_timestamp
    model = _first_string(message, "model") or _first_string(record, "model")

    raw_project = _first_string(record, "cwd", "projectPath", "project_path")
    if raw_project:
        project_id = stable_private_id("claude-project", raw_project)
        project_name = project_display_name(raw_project, "Claude Project")
    else:
        project_id = stable_private_id("claude-project", fallback_project_key)
        project_name = "Claude Project"

    tokens = _tokens_from_usage(message.get("usage"))
    tool_names = _tool_names(message.get("content"))
    skill_names: list[str] = []
    for container in (record, message):
        attributed = _first_string(
            container,
            "attributionSkill",
            "attribution_skill",
        )
        if attributed:
            skill_names.append(safe_title(attributed, "Skill"))
    skill_names.extend(name for name in tool_names if "skill" in name.casefold())
    unique_skills = tuple(dict.fromkeys(skill_names))

    if tokens is None and not tool_names and not unique_skills:
        return None
    safe_model = (safe_title(model, "") or None) if model else None
    return _TranscriptEvent(
        session_key=session_key,
        message_key=message_key,
        occurred_at=occurred_at,
        tokens=tokens,
        model=safe_model,
        project_id=project_id,
        project_name=project_name,
        tool_names=tool_names,
        skill_names=unique_skills,
    )


def _tokens_from_usage(value: Any) -> TokenBreakdown | None:
    if not isinstance(value, dict):
        return None
    input_tokens = _nonnegative_int(value.get("input_tokens"))
    cache_creation = _nonnegative_int(value.get("cache_creation_input_tokens"))
    cache_read = _nonnegative_int(value.get("cache_read_input_tokens"))
    output_tokens = _nonnegative_int(value.get("output_tokens"))
    reasoning_tokens = _nonnegative_int(value.get("reasoning_output_tokens"))
    total_value = _optional_nonnegative_int(value.get("total_tokens"))
    total_tokens = (
        total_value
        if total_value is not None
        else input_tokens + cache_creation + cache_read + output_tokens + reasoning_tokens
    )
    result = TokenBreakdown(
        input_tokens=input_tokens + cache_creation + cache_read,
        cached_input_tokens=cache_creation + cache_read,
        output_tokens=output_tokens,
        reasoning_output_tokens=reasoning_tokens,
        total_tokens=total_tokens,
    )
    return None if result.is_zero else result


def _tool_names(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    names: list[str] = []
    for item in value:
        if not isinstance(item, dict) or item.get("type") != "tool_use":
            continue
        name = item.get("name")
        if isinstance(name, str) and name.strip():
            names.append(safe_title(name, "Tool"))
    return tuple(names)


def _deduplicate_events(
    groups: Iterable[list[_TranscriptEvent]],
) -> list[_TranscriptEvent]:
    events: dict[tuple[str, str], _TranscriptEvent] = {}
    for group in groups:
        for event in group:
            key = (event.session_key, event.message_key)
            existing = events.get(key)
            events[key] = event if existing is None else _merge_duplicate_event(existing, event)
    return sorted(events.values(), key=lambda event: event.occurred_at)


def _merge_duplicate_event(
    first: _TranscriptEvent,
    second: _TranscriptEvent,
) -> _TranscriptEvent:
    first_total = first.tokens.visible_total_tokens if first.tokens else -1
    second_total = second.tokens.visible_total_tokens if second.tokens else -1
    tokens = second.tokens if second_total >= first_total else first.tokens
    tool_names = (
        second.tool_names
        if len(second.tool_names) >= len(first.tool_names)
        else first.tool_names
    )
    skill_names = tuple(dict.fromkeys((*first.skill_names, *second.skill_names)))
    later = second if second.occurred_at >= first.occurred_at else first
    return _TranscriptEvent(
        session_key=first.session_key,
        message_key=first.message_key,
        occurred_at=max(first.occurred_at, second.occurred_at),
        tokens=tokens,
        model=later.model or first.model or second.model,
        project_id=later.project_id,
        project_name=later.project_name,
        tool_names=tool_names,
        skill_names=skill_names,
    )


def _aggregate_token_usage(
    events: list[_TranscriptEvent],
    *,
    parsed_file_count: int,
    now: datetime,
) -> tuple[DetailedUsage, list[DailyUsage], list[ProjectUsage], int]:
    day_start = start_of_local_day(now)
    seven_day_start = day_start - timedelta(days=6)
    previous_seven_day_start = day_start - timedelta(days=13)
    month_start = day_start.replace(day=1)

    detailed = DetailedUsage(
        parsed_file_count=parsed_file_count,
        token_event_count=len(events),
    )
    daily: dict[str, PricedUsage] = defaultdict(PricedUsage)
    project_values: dict[str, _ProjectAccumulator] = {}
    unknown_price_count = 0

    for event in events:
        assert event.tokens is not None
        cost = _estimated_cost(event.tokens, event.model)
        if cost is None:
            unknown_price_count += 1
        detailed.lifetime.add(event.tokens, cost or 0.0)
        if event.occurred_at >= month_start:
            detailed.month.add(event.tokens, cost or 0.0)
        if event.occurred_at >= seven_day_start:
            detailed.seven_day.add(event.tokens, cost or 0.0)
        elif event.occurred_at >= previous_seven_day_start:
            detailed.previous_seven_day.add(event.tokens, cost or 0.0)
        if event.occurred_at >= day_start:
            detailed.today.add(event.tokens, cost or 0.0)

        daily[day_key(event.occurred_at)].add(event.tokens, cost or 0.0)
        project = project_values.setdefault(
            event.project_id,
            _ProjectAccumulator(display_name=event.project_name),
        )
        project.tokens += event.tokens.visible_total_tokens
        if cost is not None:
            project.estimated_cost_usd += cost
            project.has_cost_estimate = True
        project.sessions.add(event.session_key)
        project.last_active_at = _latest_datetime(
            [project.last_active_at, event.occurred_at]
        )

    first_day = day_start - timedelta(days=_DAY_BUCKET_COUNT - 1)
    daily_usage: list[DailyUsage] = []
    for offset in range(_DAY_BUCKET_COUNT):
        current = first_day + timedelta(days=offset)
        key = day_key(current)
        usage = daily.get(key, PricedUsage())
        daily_usage.append(
            DailyUsage(
                day=key,
                tokens=usage.tokens.visible_total_tokens,
                estimated_cost_usd=usage.estimated_cost_usd,
                input_tokens=usage.tokens.input_tokens,
                cached_input_tokens=usage.tokens.cached_input_tokens,
                output_tokens=usage.tokens.output_tokens,
                quality=SourceQuality.DETAILED,
            )
        )

    projects = [
        ProjectUsage(
            project_id=project_id,
            display_name=value.display_name,
            tokens=value.tokens,
            estimated_cost_usd=(
                value.estimated_cost_usd if value.has_cost_estimate else None
            ),
            session_count=len(value.sessions),
            last_active_at=value.last_active_at,
            quality=SourceQuality.DETAILED,
        )
        for project_id, value in project_values.items()
    ]
    projects.sort(
        key=lambda project: (
            project.tokens,
            _datetime_order(project.last_active_at),
        ),
        reverse=True,
    )
    return detailed, daily_usage, projects, unknown_price_count


def _aggregate_tools(events: list[_TranscriptEvent]) -> list[ToolUsage]:
    values: dict[str, _NameAccumulator] = {}
    for event in events:
        for name in event.tool_names:
            current = values.setdefault(name, _NameAccumulator())
            current.count += 1
            current.sessions.add(event.session_key)
            current.last_seen_at = _latest_datetime(
                [current.last_seen_at, event.occurred_at]
            )
    return sorted(
        (
            ToolUsage(
                name=name,
                category=tool_category(name),
                call_count=value.count,
                session_count=len(value.sessions),
                estimated_tokens=None,
                estimated_cost_usd=None,
            )
            for name, value in values.items()
        ),
        key=lambda item: (-item.call_count, item.name.casefold()),
    )


def _aggregate_skills(events: list[_TranscriptEvent]) -> list[SkillUsage]:
    values: dict[str, _NameAccumulator] = {}
    display_names: dict[str, str] = {}
    for event in events:
        for name in event.skill_names:
            key = name.casefold()
            display_names.setdefault(key, name)
            current = values.setdefault(key, _NameAccumulator())
            current.count += 1
            current.sessions.add(event.session_key)
            current.last_seen_at = _latest_datetime(
                [current.last_seen_at, event.occurred_at]
            )
    return sorted(
        (
            SkillUsage(
                skill_id=stable_private_id("claude-skill", key),
                display_name=display_names[key],
                source_label="Claude Code transcript",
                load_count=value.count,
                session_count=len(value.sessions),
                last_loaded_at=value.last_seen_at,
            )
            for key, value in values.items()
        ),
        key=lambda item: (-item.load_count, item.display_name.casefold()),
    )


def _summary_to_json(summary: _TranscriptSummary) -> dict[str, Any]:
    return {
        "last_active_at": _datetime_text(summary.last_active_at),
        "events": [
            {
                "session_key": event.session_key,
                "message_key": event.message_key,
                "occurred_at": _datetime_text(event.occurred_at),
                "tokens": _tokens_to_json(event.tokens),
                "model": event.model,
                "project_id": event.project_id,
                "project_name": event.project_name,
                "tool_names": list(event.tool_names),
                "skill_names": list(event.skill_names),
            }
            for event in summary.events
        ],
    }


def _summary_from_cache_entry(value: Any) -> _TranscriptSummary | None:
    if not isinstance(value, dict):
        return None
    summary = value.get("summary")
    if not isinstance(summary, dict) or not isinstance(summary.get("events"), list):
        return None
    events: list[_TranscriptEvent] = []
    for raw_event in summary["events"]:
        event = _event_from_cache(raw_event)
        if event is None:
            return None
        events.append(event)
    return _TranscriptSummary(
        last_active_at=parse_datetime(summary.get("last_active_at")),
        events=events,
    )


def _event_from_cache(value: Any) -> _TranscriptEvent | None:
    if not isinstance(value, dict):
        return None
    session_key = _string(value.get("session_key"))
    message_key = _string(value.get("message_key"))
    occurred_at = parse_datetime(value.get("occurred_at"))
    project_id = _string(value.get("project_id"))
    project_name = _string(value.get("project_name"))
    if not all((session_key, message_key, occurred_at, project_id, project_name)):
        return None
    tool_names = value.get("tool_names")
    skill_names = value.get("skill_names")
    if not isinstance(tool_names, list) or not isinstance(skill_names, list):
        return None
    return _TranscriptEvent(
        session_key=session_key,
        message_key=message_key,
        occurred_at=occurred_at,
        tokens=_tokens_from_cache(value.get("tokens")),
        model=_string(value.get("model")),
        project_id=project_id,
        project_name=project_display_name(project_name, "Claude Project"),
        tool_names=tuple(
            safe_title(name, "Tool") for name in tool_names if isinstance(name, str) and name
        ),
        skill_names=tuple(
            safe_title(name, "Skill") for name in skill_names if isinstance(name, str) and name
        ),
    )


def _tokens_to_json(value: TokenBreakdown | None) -> dict[str, int] | None:
    if value is None:
        return None
    return {
        "input_tokens": value.input_tokens,
        "cached_input_tokens": value.cached_input_tokens,
        "output_tokens": value.output_tokens,
        "reasoning_output_tokens": value.reasoning_output_tokens,
        "total_tokens": value.total_tokens,
    }


def _tokens_from_cache(value: Any) -> TokenBreakdown | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        return None
    tokens = TokenBreakdown(
        input_tokens=_nonnegative_int(value.get("input_tokens")),
        cached_input_tokens=_nonnegative_int(value.get("cached_input_tokens")),
        output_tokens=_nonnegative_int(value.get("output_tokens")),
        reasoning_output_tokens=_nonnegative_int(value.get("reasoning_output_tokens")),
        total_tokens=_nonnegative_int(value.get("total_tokens")),
    )
    return None if tokens.is_zero else tokens


def _rate_window(value: dict[str, Any] | None, *, window_minutes: int) -> RateWindow | None:
    if value is None:
        return None
    used = _optional_float(_first_value(value, "usedPercentage", "used_percentage"))
    if used is None:
        return None
    return RateWindow(
        used_percent=max(0.0, min(100.0, used)),
        window_minutes=window_minutes,
        resets_at=parse_datetime(_first_value(value, "resetsAt", "resets_at")),
    )


def _estimated_cost(tokens: TokenBreakdown, model: str | None) -> float | None:
    normalized = (model or "").casefold()
    if "opus" in normalized:
        input_price, cached_price, output_price = 15.0, 1.5, 75.0
    elif "sonnet" in normalized:
        input_price, cached_price, output_price = 3.0, 0.3, 15.0
    elif "haiku" in normalized:
        input_price, cached_price, output_price = 0.8, 0.08, 4.0
    else:
        return None
    return (
        tokens.uncached_input_tokens / 1_000_000 * input_price
        + tokens.billable_cached_input_tokens / 1_000_000 * cached_price
        + max(tokens.output_tokens, 0) / 1_000_000 * output_price
    )


def _task_status(value: Any) -> str:
    if not isinstance(value, str):
        return "pending"
    normalized = value.strip().casefold()
    return normalized if _SAFE_STATUS.fullmatch(normalized) else "pending"


def _task_column(status: str) -> TaskColumnKind:
    if status in {"in_progress", "active", "running"}:
        return TaskColumnKind.ACTIVE
    if status in {"completed", "done", "success"}:
        return TaskColumnKind.DONE
    if status == "scheduled":
        return TaskColumnKind.SCHEDULED
    return TaskColumnKind.PENDING


def _first_value(value: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in value and value[key] is not None:
            return value[key]
    return None


def _first_dict(value: dict[str, Any], *keys: str) -> dict[str, Any] | None:
    for key in keys:
        result = value.get(key)
        if isinstance(result, dict):
            return result
    return None


def _first_string(value: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        result = _string(value.get(key))
        if result:
            return result
    return None


def _string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    result = value.strip()
    return result or None


def _nonnegative_int(value: Any) -> int:
    result = _optional_nonnegative_int(value)
    return result if result is not None else 0


def _optional_nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if value >= 0 and value.is_integer() else None
    if isinstance(value, str) and re.fullmatch(r"\d+", value.strip()):
        return int(value)
    return None


def _optional_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        result = float(value)
    elif isinstance(value, str):
        try:
            result = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    return result if math.isfinite(result) else None


def _latest_datetime(values: Iterable[datetime | None]) -> datetime | None:
    present = [value for value in values if value is not None]
    return max(present) if present else None


def _datetime_text(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _datetime_order(value: datetime | None) -> float:
    if value is None:
        return float("-inf")
    try:
        return value.timestamp()
    except (OSError, OverflowError, ValueError):
        return float("-inf")


def _local_moment(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now().astimezone()
    if value.tzinfo is None:
        return value.astimezone()
    return value.astimezone()
