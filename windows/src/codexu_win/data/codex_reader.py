from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
import tomllib
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path, PureWindowsPath
from typing import Any, Iterable, Protocol

from codexu_win.data.app_server import AppServerResult, CodexAppServerClient
from codexu_win.models import (
    DailyUsage,
    DetailedUsage,
    PricedUsage,
    ProjectUsage,
    RuntimeKind,
    RuntimeSnapshot,
    SkillUsage,
    SourceQuality,
    TaskBoard,
    TaskColumnKind,
    TaskItem,
    TokenBreakdown,
    ToolUsage,
    token_breakdown_from_dict,
)
from codexu_win.paths import RuntimePaths
from codexu_win.utils import (
    day_key,
    format_tokens,
    normalize_windows_path,
    parse_datetime,
    project_display_name,
    safe_title,
    stable_private_id,
    start_of_local_day,
    tool_category,
)


_THREAD_COLUMN_WHITELIST = frozenset(
    {
        "id",
        "rollout_path",
        "created_at",
        "created_at_ms",
        "updated_at",
        "updated_at_ms",
        "recency_at",
        "recency_at_ms",
        "cwd",
        "title",
        "tokens_used",
        "archived",
        "archived_at",
        "model",
    }
)
_CACHE_VERSION = 1
_CACHE_FILE = "session-usage-v1.json"
_SESSION_EVENT_PATTERN = re.compile(
    br'"type"\s*:\s*"(token_count|function_call|custom_tool_call)"'
)
_WINDOWS_SKILL_PATTERN = re.compile(
    r"""
    (?P<path>
      (?:
        \\\\ \? \\ [A-Z]:[\\/]
        | \\\\ \? \\ UNC \\ [^\\/\"'\s]+ \\ [^\\/\"'\s]+ \\
        | \\\\ [^\\/\"'\s]+ \\ [^\\/\"'\s]+ \\
        | [A-Z]:[\\/]
      )
      [^\"'\r\n<>|{}]*?
      [\\/]SKILL\.md
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)
_POSIX_SKILL_PATTERN = re.compile(
    r"(?P<path>(?:~[\\/]|/)[^\"'\r\n<>|{}]*?[\\/]SKILL\.md)",
    re.IGNORECASE,
)


class _AppServerReader(Protocol):
    def read_snapshot(self, timeout_seconds: float = 12.0) -> AppServerResult: ...


@dataclass(slots=True, frozen=True)
class _ThreadRecord:
    thread_id: str
    rollout_path: str
    created_at: datetime | None
    updated_at: datetime | None
    recency_at: datetime | None
    archived_at: datetime | None
    cwd: str
    title: str
    tokens: int
    archived: bool
    model: str | None

    @property
    def last_active_at(self) -> datetime | None:
        return _max_datetime(self.recency_at, self.updated_at, self.created_at)


@dataclass(slots=True, frozen=True)
class _UsageDelta:
    timestamp: datetime
    tokens: TokenBreakdown


@dataclass(slots=True, frozen=True)
class _SkillLoad:
    skill_id: str
    display_name: str
    source_label: str
    static_token_estimate: int | None
    timestamp: datetime | None


@dataclass(slots=True)
class _SessionParseResult:
    has_token_events: bool = False
    token_event_count: int = 0
    deltas: list[_UsageDelta] = field(default_factory=list)
    tool_calls: dict[str, int] = field(default_factory=dict)
    skill_loads: list[_SkillLoad] = field(default_factory=list)


@dataclass(slots=True)
class _SessionCacheEntry:
    size: int
    mtime_ns: int
    parsed: _SessionParseResult


@dataclass(slots=True, frozen=True)
class _ModelPrice:
    input_per_million: float
    cached_input_per_million: float
    output_per_million: float


@dataclass(slots=True)
class _ProjectAccumulator:
    raw_path: str
    tokens: int = 0
    estimated_cost_usd: float = 0.0
    session_ids: set[str] = field(default_factory=set)
    last_active_at: datetime | None = None

    def add(
        self,
        session_id: str,
        tokens: int,
        timestamp: datetime | None,
        estimated_cost_usd: float = 0.0,
    ) -> None:
        self.tokens += max(tokens, 0)
        self.estimated_cost_usd += max(estimated_cost_usd, 0.0)
        self.session_ids.add(session_id)
        self.last_active_at = _max_datetime(self.last_active_at, timestamp)

    def make(self, quality: SourceQuality, include_cost: bool) -> ProjectUsage:
        identity = normalize_windows_path(self.raw_path) or "uncategorized"
        return ProjectUsage(
            project_id=stable_private_id("project", identity),
            display_name=project_display_name(self.raw_path),
            tokens=self.tokens,
            estimated_cost_usd=self.estimated_cost_usd if include_cost else None,
            session_count=max(len(self.session_ids), 1),
            last_active_at=self.last_active_at,
            quality=quality,
        )


@dataclass(slots=True)
class _ToolAccumulator:
    name: str
    call_count: int = 0
    session_ids: set[str] = field(default_factory=set)
    estimated_tokens: int = 0
    estimated_cost_usd: float = 0.0


@dataclass(slots=True)
class _SkillAccumulator:
    load: _SkillLoad
    load_count: int = 0
    session_ids: set[str] = field(default_factory=set)
    last_loaded_at: datetime | None = None


class CodexReader:
    """Build a privacy-safe Codex RuntimeSnapshot from local Windows data."""

    def __init__(
        self,
        paths: RuntimePaths | None = None,
        app_server: _AppServerReader | None = None,
    ) -> None:
        self.paths = paths or RuntimePaths.resolve()
        self.app_server = app_server or CodexAppServerClient()
        self._session_cache: dict[str, _SessionCacheEntry] = {}
        self._persistent_session_cache: dict[str, Any] | None = None
        self._current_cache_entries: dict[str, Any] = {}
        self._cache_dir = self.paths.cache_root / "codex"
        self._cache_path = self._cache_dir / _CACHE_FILE

    def load(self, now: datetime | None = None) -> RuntimeSnapshot:
        current = _local_datetime(now)
        app_server = self._read_app_server()
        snapshot = RuntimeSnapshot(
            runtime=RuntimeKind.CODEX,
            refreshed_at=current,
            account=app_server.account,
            primary=app_server.primary,
            secondary=app_server.secondary,
            credits_balance=app_server.credits_balance,
            cloud_lifetime_tokens=app_server.cloud_lifetime_tokens,
            diagnostics=list(app_server.diagnostics),
        )

        database = self._state_database()
        if database is None:
            snapshot.diagnostics.append("Codex state database was not found")
            return snapshot

        try:
            records = self._read_thread_records(database)
        except (OSError, sqlite3.Error, ValueError):
            snapshot.diagnostics.append("Codex state database could not be read")
            return snapshot

        self._apply_approximate_usage(snapshot, records, current)
        snapshot.all_projects = self._aggregate_projects(records, quality=SourceQuality.APPROXIMATE)
        recent_cutoff = start_of_local_day(current) - timedelta(days=6)
        recent_records = [record for record in records if _at_or_after(record.updated_at, recent_cutoff)]
        approximate_recent = self._aggregate_projects(
            recent_records,
            quality=SourceQuality.APPROXIMATE,
        )
        snapshot.task_board = self._build_task_board(records, current)

        parsed_sources: list[tuple[_ThreadRecord, _SessionParseResult]] = []
        unreadable_sessions = 0
        seen_rollouts: set[str] = set()
        self._current_cache_entries = {}
        for record in records:
            if record.tokens <= 0 or not record.rollout_path:
                continue
            rollout_identity = normalize_windows_path(record.rollout_path)
            if not rollout_identity or rollout_identity in seen_rollouts:
                continue
            seen_rollouts.add(rollout_identity)
            parsed = self._cached_session(record.rollout_path)
            if parsed is None:
                unreadable_sessions += 1
                continue
            parsed_sources.append((record, parsed))

        self._write_session_cache(snapshot.diagnostics)

        detailed, daily, recent_projects, tools, skills = self._aggregate_sessions(
            parsed_sources,
            current,
        )
        snapshot.tools = tools
        snapshot.skills = skills
        if detailed is not None:
            snapshot.detailed = detailed
            snapshot.daily_usage = daily
            snapshot.recent_projects = recent_projects or approximate_recent
            snapshot.quality = SourceQuality.DETAILED
        else:
            snapshot.daily_usage = self._approximate_daily_usage(records, current)
            snapshot.recent_projects = approximate_recent
            snapshot.quality = SourceQuality.APPROXIMATE
        if unreadable_sessions:
            snapshot.diagnostics.append("Some Codex session records could not be read")
        return snapshot

    def _read_app_server(self) -> AppServerResult:
        try:
            return self.app_server.read_snapshot()
        except (OSError, RuntimeError, ValueError):
            return AppServerResult(diagnostics=["Codex app-server data was unavailable"])

    def _state_database(self) -> Path | None:
        candidates = (
            self.paths.codex_root / "state_5.sqlite",
            self.paths.codex_root / "sqlite" / "state_5.sqlite",
        )
        valid: list[tuple[int, int, Path]] = []
        existing: list[Path] = []
        for priority, path in enumerate(candidates):
            if not path.is_file():
                continue
            existing.append(path)
            try:
                with closing(_readonly_connection(path)) as connection:
                    columns = _thread_columns(connection)
                    if "id" not in columns or "tokens_used" not in columns:
                        continue
                    if "updated_at" not in columns and "updated_at_ms" not in columns:
                        continue
                    updated = _epoch_expression(columns, "updated_at", "updated_at_ms")
                    latest = _as_int(
                        connection.execute(f"SELECT COALESCE(MAX({updated}), 0) FROM threads").fetchone()[0]
                    )
                    valid.append((latest, -priority, path))
            except (OSError, sqlite3.Error):
                continue
        if valid:
            return max(valid, key=lambda item: (item[0], item[1]))[2]
        return existing[0] if existing else None

    def _read_thread_records(self, database: Path) -> list[_ThreadRecord]:
        with closing(_readonly_connection(database)) as connection:
            columns = _thread_columns(connection)
            if "id" not in columns or "tokens_used" not in columns:
                raise ValueError("unsupported threads schema")
            if "updated_at" not in columns and "updated_at_ms" not in columns:
                raise ValueError("unsupported threads timestamps")

            updated = _epoch_expression(columns, "updated_at", "updated_at_ms")
            created = _epoch_expression(columns, "created_at", "created_at_ms", updated)
            recency_value = _epoch_expression(columns, "recency_at", "recency_at_ms", updated)
            recency = f"CASE WHEN ({recency_value}) > 0 THEN ({recency_value}) ELSE ({updated}) END"
            archived_at = _column_expression(columns, "archived_at", updated)
            query = f"""
                SELECT
                    "id" AS id,
                    {_column_expression(columns, "rollout_path", "''")} AS rollout_path,
                    ({created}) AS created_epoch,
                    ({updated}) AS updated_epoch,
                    ({recency}) AS recency_epoch,
                    ({archived_at}) AS archived_epoch,
                    {_column_expression(columns, "cwd", "''")} AS cwd,
                    {_column_expression(columns, "title", "''")} AS title,
                    "tokens_used" AS tokens_used,
                    {_column_expression(columns, "archived", "0")} AS archived,
                    {_column_expression(columns, "model", "NULL")} AS model
                FROM threads
            """
            rows = connection.execute(query).fetchall()

        records: list[_ThreadRecord] = []
        for row in rows:
            records.append(
                _ThreadRecord(
                    thread_id=str(row["id"] or ""),
                    rollout_path=str(row["rollout_path"] or ""),
                    created_at=parse_datetime(row["created_epoch"]),
                    updated_at=parse_datetime(row["updated_epoch"]),
                    recency_at=parse_datetime(row["recency_epoch"]),
                    archived_at=parse_datetime(row["archived_epoch"]),
                    cwd=str(row["cwd"] or ""),
                    title=str(row["title"] or ""),
                    tokens=max(_as_int(row["tokens_used"]), 0),
                    archived=bool(_as_int(row["archived"])),
                    model=str(row["model"]) if row["model"] not in (None, "") else None,
                )
            )
        return records

    def _apply_approximate_usage(
        self,
        snapshot: RuntimeSnapshot,
        records: list[_ThreadRecord],
        now: datetime,
    ) -> None:
        day_start = start_of_local_day(now)
        seven_day_start = day_start - timedelta(days=6)
        snapshot.approximate_lifetime_tokens = sum(record.tokens for record in records)
        snapshot.approximate_today_tokens = sum(
            record.tokens for record in records if _at_or_after(record.updated_at, day_start)
        )
        snapshot.approximate_seven_day_tokens = sum(
            record.tokens for record in records if _at_or_after(record.updated_at, seven_day_start)
        )
        snapshot.thread_count = len(records)
        snapshot.last_active_at = _max_datetime(*(record.last_active_at for record in records))

    def _aggregate_projects(
        self,
        records: Iterable[_ThreadRecord],
        quality: SourceQuality,
    ) -> list[ProjectUsage]:
        accumulators: dict[str, _ProjectAccumulator] = {}
        for record in records:
            if record.tokens <= 0:
                continue
            identity = normalize_windows_path(record.cwd) or "uncategorized"
            accumulator = accumulators.setdefault(identity, _ProjectAccumulator(raw_path=record.cwd))
            accumulator.add(record.thread_id, record.tokens, record.last_active_at)
        return sorted(
            (item.make(quality, include_cost=False) for item in accumulators.values()),
            key=lambda item: (-item.tokens, item.display_name.casefold()),
        )[:24]

    def _cached_session(self, raw_path: str) -> _SessionParseResult | None:
        path = _io_path(raw_path, self.paths.home)
        try:
            stat = path.stat()
        except OSError:
            return None
        cache_key = stable_private_id("session-cache", normalize_windows_path(raw_path))
        cached = self._session_cache.get(cache_key)
        if cached and cached.size == stat.st_size and cached.mtime_ns == stat.st_mtime_ns:
            self._current_cache_entries[cache_key] = _session_cache_to_json(cached)
            return cached.parsed
        persistent = self._persistent_cache().get(cache_key)
        persistent_entry = _session_cache_from_json(persistent)
        if (
            persistent_entry is not None
            and persistent_entry.size == stat.st_size
            and persistent_entry.mtime_ns == stat.st_mtime_ns
        ):
            self._session_cache[cache_key] = persistent_entry
            self._current_cache_entries[cache_key] = _session_cache_to_json(persistent_entry)
            return persistent_entry.parsed
        try:
            parsed = self._parse_session_file(path)
        except (OSError, json.JSONDecodeError, UnicodeError):
            return None
        self._session_cache[cache_key] = _SessionCacheEntry(
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            parsed=parsed,
        )
        self._current_cache_entries[cache_key] = _session_cache_to_json(self._session_cache[cache_key])
        return parsed

    def _persistent_cache(self) -> dict[str, Any]:
        if self._persistent_session_cache is not None:
            return self._persistent_session_cache
        if not self._cache_path.is_file():
            self._persistent_session_cache = {}
            return self._persistent_session_cache
        try:
            value = json.loads(self._cache_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            self._persistent_session_cache = {}
            return self._persistent_session_cache
        entries = value.get("entries") if isinstance(value, dict) else None
        if not isinstance(value, dict) or value.get("version") != _CACHE_VERSION or not isinstance(entries, dict):
            self._persistent_session_cache = {}
        else:
            self._persistent_session_cache = entries
        return self._persistent_session_cache

    def _write_session_cache(self, diagnostics: list[str]) -> None:
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
                    {"version": _CACHE_VERSION, "entries": self._current_cache_entries},
                    handle,
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self._cache_path)
            temporary_path = None
            self._persistent_session_cache = dict(self._current_cache_entries)
        except OSError:
            diagnostics.append("Codex aggregate cache could not be updated")
        finally:
            if temporary_path:
                try:
                    Path(temporary_path).unlink(missing_ok=True)
                except OSError:
                    pass

    def _parse_session_file(self, path: Path) -> _SessionParseResult:
        parsed = _SessionParseResult()
        previous = TokenBreakdown()
        tool_counts: dict[str, int] = defaultdict(int)
        with path.open("rb") as handle:
            for raw_line in handle:
                if not _SESSION_EVENT_PATTERN.search(raw_line):
                    continue
                try:
                    event = json.loads(raw_line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if not isinstance(event, dict):
                    continue
                payload = event.get("payload")
                if not isinstance(payload, dict):
                    continue
                payload_type = payload.get("type")
                timestamp = parse_datetime(event.get("timestamp"))

                if payload_type in {"function_call", "custom_tool_call"}:
                    name = _safe_tool_name(payload.get("name"))
                    if name:
                        tool_counts[name] += 1
                    parsed.skill_loads.extend(self._skill_loads(payload, timestamp))
                    continue
                if payload_type != "token_count" or timestamp is None:
                    continue
                info = payload.get("info")
                total_usage = info.get("total_token_usage") if isinstance(info, dict) else None
                if not isinstance(total_usage, dict):
                    continue
                try:
                    current = token_breakdown_from_dict(total_usage)
                except (TypeError, ValueError, OverflowError):
                    continue
                delta = current.delta_from(previous)
                if delta.has_negative_value:
                    delta = current.copy()
                previous = current.copy()
                parsed.has_token_events = True
                parsed.token_event_count += 1
                if not delta.is_zero:
                    parsed.deltas.append(_UsageDelta(timestamp=timestamp, tokens=delta))
        parsed.tool_calls = dict(tool_counts)
        return parsed

    def _skill_loads(
        self,
        payload: dict[str, Any],
        timestamp: datetime | None,
    ) -> list[_SkillLoad]:
        results: list[_SkillLoad] = []
        seen: set[str] = set()
        for key in ("arguments", "input", "cmd", "command"):
            for text in _string_values(payload.get(key)):
                for raw_path in _extract_skill_paths(text):
                    load = _audit_skill_path(raw_path, self.paths.home, timestamp)
                    if load is not None and load.skill_id not in seen:
                        seen.add(load.skill_id)
                        results.append(load)
        return results

    def _aggregate_sessions(
        self,
        sources: list[tuple[_ThreadRecord, _SessionParseResult]],
        now: datetime,
    ) -> tuple[
        DetailedUsage | None,
        list[DailyUsage],
        list[ProjectUsage],
        list[ToolUsage],
        list[SkillUsage],
    ]:
        day_start = start_of_local_day(now)
        seven_day_start = day_start - timedelta(days=6)
        previous_seven_day_start = seven_day_start - timedelta(days=7)
        month_start = day_start.replace(day=1)
        daily_start = day_start - timedelta(days=179)
        detailed = DetailedUsage()
        daily: dict[str, PricedUsage] = defaultdict(PricedUsage)
        projects: dict[str, _ProjectAccumulator] = {}
        tools: dict[str, _ToolAccumulator] = {}
        skills: dict[str, _SkillAccumulator] = {}

        for record, parsed in sources:
            if parsed.has_token_events:
                detailed.parsed_file_count += 1
                detailed.token_event_count += parsed.token_event_count
            price = _model_price(record.model)
            session_usage = PricedUsage()
            for delta in parsed.deltas:
                cost = _estimated_cost(delta.tokens, price)
                session_usage.add(delta.tokens, cost)
                detailed.lifetime.add(delta.tokens, cost)
                if delta.timestamp >= month_start:
                    detailed.month.add(delta.tokens, cost)
                if delta.timestamp >= seven_day_start:
                    detailed.seven_day.add(delta.tokens, cost)
                    project_key = normalize_windows_path(record.cwd) or "uncategorized"
                    project = projects.setdefault(project_key, _ProjectAccumulator(raw_path=record.cwd))
                    project.add(
                        record.thread_id,
                        delta.tokens.visible_total_tokens,
                        delta.timestamp,
                        cost,
                    )
                elif delta.timestamp >= previous_seven_day_start:
                    detailed.previous_seven_day.add(delta.tokens, cost)
                if delta.timestamp >= day_start:
                    detailed.today.add(delta.tokens, cost)
                if daily_start <= delta.timestamp < day_start + timedelta(days=1):
                    daily[day_key(delta.timestamp)].add(delta.tokens, cost)

            total_calls = sum(parsed.tool_calls.values())
            for name, count in parsed.tool_calls.items():
                item = tools.setdefault(name, _ToolAccumulator(name=name))
                item.call_count += count
                item.session_ids.add(record.thread_id)
                if total_calls > 0 and session_usage.tokens.visible_total_tokens > 0:
                    share = count / total_calls
                    item.estimated_tokens += round(session_usage.tokens.visible_total_tokens * share)
                    item.estimated_cost_usd += session_usage.estimated_cost_usd * share

            for load in parsed.skill_loads:
                item = skills.setdefault(load.skill_id, _SkillAccumulator(load=load))
                item.load_count += 1
                item.session_ids.add(record.thread_id)
                item.last_loaded_at = _max_datetime(
                    item.last_loaded_at,
                    load.timestamp,
                    record.updated_at,
                )

        daily_rows = _continuous_daily_usage(daily, day_start, SourceQuality.DETAILED)
        project_rows = sorted(
            (item.make(SourceQuality.DETAILED, include_cost=True) for item in projects.values()),
            key=lambda item: (-item.tokens, item.display_name.casefold()),
        )[:24]
        tool_rows = sorted(
            (
                ToolUsage(
                    name=item.name,
                    category=tool_category(item.name),
                    call_count=item.call_count,
                    session_count=len(item.session_ids),
                    estimated_tokens=item.estimated_tokens or None,
                    estimated_cost_usd=item.estimated_cost_usd or None,
                )
                for item in tools.values()
            ),
            key=lambda item: (-item.call_count, item.name.casefold()),
        )
        skill_rows = sorted(
            (
                SkillUsage(
                    skill_id=item.load.skill_id,
                    display_name=item.load.display_name,
                    source_label=item.load.source_label,
                    load_count=item.load_count,
                    session_count=len(item.session_ids),
                    static_token_estimate=item.load.static_token_estimate,
                    last_loaded_at=item.last_loaded_at,
                )
                for item in skills.values()
            ),
            key=lambda item: (-item.load_count, item.display_name.casefold()),
        )
        if detailed.parsed_file_count == 0 or detailed.token_event_count == 0:
            return None, [], project_rows, tool_rows, skill_rows
        return detailed, daily_rows, project_rows, tool_rows, skill_rows

    def _approximate_daily_usage(
        self,
        records: Iterable[_ThreadRecord],
        now: datetime,
    ) -> list[DailyUsage]:
        day_start = start_of_local_day(now)
        daily_start = day_start - timedelta(days=179)
        totals: dict[str, PricedUsage] = defaultdict(PricedUsage)
        for record in records:
            if record.updated_at is None or not (daily_start <= record.updated_at < day_start + timedelta(days=1)):
                continue
            totals[day_key(record.updated_at)].tokens.total_tokens += record.tokens
        return _continuous_daily_usage(totals, day_start, SourceQuality.APPROXIMATE)

    def _build_task_board(self, records: Iterable[_ThreadRecord], now: datetime) -> TaskBoard:
        board = TaskBoard(refreshed_at=now)
        day_start = start_of_local_day(now)
        active_cutoff = now - timedelta(hours=2)
        for record in records:
            if record.archived:
                if not _at_or_after(record.archived_at or record.updated_at, day_start):
                    continue
                column = TaskColumnKind.DONE
                updated_at = record.archived_at or record.updated_at
            else:
                updated_at = record.last_active_at
                if not _at_or_after(updated_at, day_start):
                    continue
                column = (
                    TaskColumnKind.ACTIVE
                    if _at_or_after(updated_at, active_cutoff)
                    else TaskColumnKind.PENDING
                )
            board.columns[column].append(_thread_task(record, updated_at, column))

        board.columns[TaskColumnKind.SCHEDULED].extend(self._automation_tasks())
        for items in board.columns.values():
            items.sort(
                key=lambda item: (item.updated_at is not None, item.updated_at or now),
                reverse=True,
            )
        return board

    def _automation_tasks(self) -> list[TaskItem]:
        root = self.paths.codex_root / "automations"
        if not root.is_dir():
            return []
        tasks: list[TaskItem] = []
        for path in root.glob("**/automation.toml"):
            try:
                with path.open("rb") as handle:
                    fields = tomllib.load(handle)
            except (OSError, tomllib.TOMLDecodeError):
                continue
            if str(fields.get("status", "")).upper() != "ACTIVE":
                continue
            raw_id = str(fields.get("id") or path.parent.name)
            name = safe_title(str(fields.get("name") or raw_id))
            kind = str(fields.get("kind") or "cron").lower()
            compact = "".join(character for character in raw_id if character.isalnum())[:4].upper()
            tasks.append(
                TaskItem(
                    task_id=stable_private_id("automation", raw_id),
                    code="AUTO-" + (compact or stable_private_id("auto", raw_id)[-4:].upper()),
                    title=name,
                    detail=kind.upper(),
                    chip="Wake" if kind == "heartbeat" else "Cron",
                    updated_at=parse_datetime(fields.get("updated_at")),
                    tokens=None,
                    column=TaskColumnKind.SCHEDULED,
                )
            )
        return tasks[:50]


def _readonly_connection(database: Path) -> sqlite3.Connection:
    uri = database.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=1.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA busy_timeout = 1000")
    return connection


def _thread_columns(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute("PRAGMA table_info(threads)").fetchall()
    return {str(row[1]) for row in rows if str(row[1]) in _THREAD_COLUMN_WHITELIST}


def _column_expression(columns: set[str], name: str, fallback: str) -> str:
    return f'"{name}"' if name in columns else fallback


def _epoch_expression(
    columns: set[str],
    seconds_name: str,
    milliseconds_name: str,
    fallback: str = "0",
) -> str:
    if seconds_name in columns:
        return f'CAST("{seconds_name}" AS INTEGER)'
    if milliseconds_name in columns:
        return f'CAST("{milliseconds_name}" / 1000 AS INTEGER)'
    return fallback


def _local_datetime(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now().astimezone()
    return value.astimezone() if value.tzinfo else value.astimezone()


def _at_or_after(value: datetime | None, cutoff: datetime) -> bool:
    return value is not None and value >= cutoff


def _max_datetime(*values: datetime | None) -> datetime | None:
    present = [value for value in values if value is not None]
    return max(present) if present else None


def _as_int(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return 0


def _io_path(raw_path: str, home: Path) -> Path:
    expanded = os.path.expandvars(raw_path.strip())
    if expanded == "~":
        expanded = str(home)
    elif expanded.startswith(("~\\", "~/")):
        expanded = str(home) + expanded[1:]
    return Path(expanded)


def _safe_tool_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = re.sub(r"\s+", " ", value).strip()
    return normalized[:80] if normalized else None


def _string_values(value: Any, depth: int = 0) -> Iterable[str]:
    if depth > 6:
        return
    if isinstance(value, str):
        yield value[:1_000_000]
    elif isinstance(value, dict):
        for nested in list(value.values())[:100]:
            yield from _string_values(nested, depth + 1)
    elif isinstance(value, list):
        for nested in value[:100]:
            yield from _string_values(nested, depth + 1)


def _extract_skill_paths(text: str) -> Iterable[str]:
    seen: set[str] = set()
    for pattern in (_WINDOWS_SKILL_PATTERN, _POSIX_SKILL_PATTERN):
        for match in pattern.finditer(text):
            raw = match.group("path").strip(" \t\r\n`;,.)]")
            if raw and raw not in seen:
                seen.add(raw)
                yield raw


def _audit_skill_path(
    raw_path: str,
    home: Path,
    timestamp: datetime | None,
) -> _SkillLoad | None:
    path = _io_path(raw_path, home)
    windows_path = PureWindowsPath(str(path))
    is_absolute = path.is_absolute() or windows_path.is_absolute()
    if not is_absolute or windows_path.name.casefold() != "skill.md":
        return None
    try:
        if not path.is_file():
            return None
        file_size = path.stat().st_size
    except OSError:
        return None
    if file_size > 4 * 1024 * 1024:
        static_estimate = None
    else:
        try:
            data = path.read_bytes()
        except OSError:
            return None
        text = data.decode("utf-8", errors="replace")
        static_estimate = _estimate_static_tokens(text)
    normalized = normalize_windows_path(str(path))
    return _SkillLoad(
        skill_id=stable_private_id("skill", normalized),
        display_name=windows_path.parent.name or "Skill",
        source_label=_skill_source_label(normalized),
        static_token_estimate=static_estimate,
        timestamp=timestamp,
    )


def _skill_source_label(normalized_path: str) -> str:
    comparable = normalized_path.replace("\\", "/").casefold()
    if "/.codex/skills/.system/" in comparable:
        return "system"
    if "/.codex/skills/" in comparable:
        return "personal"
    if "/.codex/plugins/cache/" in comparable:
        return "plugin"
    if "/.agents/skills/" in comparable:
        return "agents"
    return "local"


def _estimate_static_tokens(text: str) -> int:
    if not text:
        return 0
    cjk = sum(
        1
        for character in text
        if "\u3400" <= character <= "\u4dbf"
        or "\u4e00" <= character <= "\u9fff"
        or "\u3040" <= character <= "\u30ff"
        or "\uac00" <= character <= "\ud7af"
    )
    non_whitespace = sum(not character.isspace() for character in text)
    non_cjk = max(non_whitespace - cjk, 0)
    return max(1, int((non_cjk / 3.8) + cjk + 0.999999))


def _session_cache_to_json(entry: _SessionCacheEntry) -> dict[str, Any]:
    return {
        "size": entry.size,
        "mtime_ns": entry.mtime_ns,
        "has_token_events": entry.parsed.has_token_events,
        "token_event_count": entry.parsed.token_event_count,
        "deltas": [
            {
                "timestamp": delta.timestamp.isoformat(),
                "tokens": {
                    "input_tokens": delta.tokens.input_tokens,
                    "cached_input_tokens": delta.tokens.cached_input_tokens,
                    "output_tokens": delta.tokens.output_tokens,
                    "reasoning_output_tokens": delta.tokens.reasoning_output_tokens,
                    "total_tokens": delta.tokens.total_tokens,
                },
            }
            for delta in entry.parsed.deltas
        ],
        "tool_calls": dict(entry.parsed.tool_calls),
        "skill_loads": [
            {
                "skill_id": load.skill_id,
                "display_name": load.display_name,
                "source_label": load.source_label,
                "static_token_estimate": load.static_token_estimate,
                "timestamp": load.timestamp.isoformat() if load.timestamp else None,
            }
            for load in entry.parsed.skill_loads
        ],
    }


def _session_cache_from_json(value: Any) -> _SessionCacheEntry | None:
    if not isinstance(value, dict):
        return None
    size = value.get("size")
    mtime_ns = value.get("mtime_ns")
    if not isinstance(size, int) or not isinstance(mtime_ns, int):
        return None
    raw_deltas = value.get("deltas")
    raw_tools = value.get("tool_calls")
    raw_skills = value.get("skill_loads")
    if not isinstance(raw_deltas, list) or not isinstance(raw_tools, dict) or not isinstance(raw_skills, list):
        return None

    deltas: list[_UsageDelta] = []
    for raw_delta in raw_deltas:
        if not isinstance(raw_delta, dict) or not isinstance(raw_delta.get("tokens"), dict):
            return None
        timestamp = parse_datetime(raw_delta.get("timestamp"))
        if timestamp is None:
            return None
        try:
            tokens = token_breakdown_from_dict(raw_delta["tokens"])
        except (TypeError, ValueError, OverflowError):
            return None
        deltas.append(_UsageDelta(timestamp=timestamp, tokens=tokens))

    tools: dict[str, int] = {}
    for name, count in raw_tools.items():
        safe_name = _safe_tool_name(name)
        if safe_name is None or not isinstance(count, int) or count < 0:
            return None
        tools[safe_name] = count

    skills: list[_SkillLoad] = []
    for raw_skill in raw_skills:
        if not isinstance(raw_skill, dict):
            return None
        skill_id = raw_skill.get("skill_id")
        display_name = raw_skill.get("display_name")
        source_label = raw_skill.get("source_label")
        if not all(isinstance(item, str) and item for item in (skill_id, display_name, source_label)):
            return None
        estimate = raw_skill.get("static_token_estimate")
        if estimate is not None and not isinstance(estimate, int):
            return None
        skills.append(
            _SkillLoad(
                skill_id=skill_id,
                display_name=display_name[:80],
                source_label=source_label[:32],
                static_token_estimate=estimate,
                timestamp=parse_datetime(raw_skill.get("timestamp")),
            )
        )

    token_event_count = value.get("token_event_count", 0)
    if not isinstance(token_event_count, int) or token_event_count < 0:
        return None
    return _SessionCacheEntry(
        size=size,
        mtime_ns=mtime_ns,
        parsed=_SessionParseResult(
            has_token_events=bool(value.get("has_token_events")),
            token_event_count=token_event_count,
            deltas=deltas,
            tool_calls=tools,
            skill_loads=skills,
        ),
    )


def _model_price(model: str | None) -> _ModelPrice:
    normalized = (model or "").lower()
    if "gpt-5.5-pro" in normalized or "gpt-5.4-pro" in normalized:
        return _ModelPrice(30.0, 30.0, 180.0)
    if "gpt-5.5" in normalized:
        return _ModelPrice(5.0, 0.5, 30.0)
    if "gpt-5.4-mini" in normalized:
        return _ModelPrice(0.75, 0.075, 4.5)
    if "gpt-5.4-nano" in normalized:
        return _ModelPrice(0.2, 0.02, 1.25)
    if "gpt-5.4" in normalized:
        return _ModelPrice(2.5, 0.25, 15.0)
    if "gpt-5.2" in normalized:
        return _ModelPrice(1.75, 0.175, 14.0)
    if "gpt-5" in normalized:
        return _ModelPrice(1.25, 0.125, 10.0)
    return _ModelPrice(5.0, 0.5, 30.0)


def _estimated_cost(tokens: TokenBreakdown, price: _ModelPrice) -> float:
    return (
        tokens.uncached_input_tokens / 1_000_000 * price.input_per_million
        + tokens.billable_cached_input_tokens / 1_000_000 * price.cached_input_per_million
        + max(tokens.output_tokens, 0) / 1_000_000 * price.output_per_million
    )


def _continuous_daily_usage(
    values: dict[str, PricedUsage],
    day_start: datetime,
    quality: SourceQuality,
) -> list[DailyUsage]:
    rows: list[DailyUsage] = []
    for offset in range(179, -1, -1):
        date = day_start - timedelta(days=offset)
        usage = values.get(day_key(date), PricedUsage())
        rows.append(
            DailyUsage(
                day=day_key(date),
                tokens=usage.tokens.visible_total_tokens,
                estimated_cost_usd=usage.estimated_cost_usd,
                input_tokens=usage.tokens.input_tokens,
                cached_input_tokens=usage.tokens.cached_input_tokens,
                output_tokens=usage.tokens.output_tokens,
                quality=quality,
            )
        )
    return rows


def _thread_task(
    record: _ThreadRecord,
    updated_at: datetime | None,
    column: TaskColumnKind,
) -> TaskItem:
    compact = "".join(character for character in record.thread_id if character.isalnum())
    code = "COD-" + (compact[-4:].upper() if compact else stable_private_id("task", record.thread_id)[-4:].upper())
    if column is TaskColumnKind.ACTIVE:
        chip = "High" if record.tokens >= 5_000_000 else "Active"
    elif column is TaskColumnKind.PENDING:
        chip = "Medium" if record.tokens >= 2_000_000 else "Idle"
    else:
        chip = "Done"
    details = [project_display_name(record.cwd)] if record.cwd else []
    if record.tokens > 0:
        details.append(format_tokens(record.tokens))
    return TaskItem(
        task_id=stable_private_id("task", record.thread_id + column.value),
        code=code,
        title=safe_title(record.title),
        detail=" · ".join(details),
        chip=chip,
        updated_at=updated_at,
        tokens=record.tokens,
        column=column,
    )
