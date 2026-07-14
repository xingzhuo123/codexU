from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from codexu_win.data.app_server import AppServerResult
from codexu_win.data.codex_reader import CodexReader
from codexu_win.models import AccountInfo, RateWindow, SourceQuality, TaskColumnKind
from codexu_win.paths import RuntimePaths


@dataclass
class _FakeAppServer:
    result: AppServerResult

    def read_snapshot(self, timeout_seconds: float = 12.0) -> AppServerResult:
        return self.result


def _runtime_paths(tmp_path: Path) -> RuntimePaths:
    home = tmp_path / "home"
    codex_root = home / ".codex"
    codex_root.mkdir(parents=True)
    return RuntimePaths(
        home=home,
        codex_root=codex_root,
        claude_root=home / ".claude",
        cache_root=tmp_path / "cache",
        settings_root=tmp_path / "settings",
    )


def _extended(path: Path) -> str:
    raw = str(path)
    return "\\\\?\\" + raw if os.name == "nt" else raw


def _write_jsonl(path: Path, events: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")


def _token_event(timestamp: datetime, **usage: int) -> dict[str, object]:
    return {
        "timestamp": timestamp.isoformat(),
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {"total_token_usage": usage},
        },
    }


def test_legacy_schema_without_recency_uses_updated_at_and_never_surfaces_sensitive_columns(
    tmp_path: Path,
) -> None:
    paths = _runtime_paths(tmp_path)
    database = paths.codex_root / "state_5.sqlite"
    now = datetime.now().astimezone().replace(microsecond=0)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE threads (
                id TEXT PRIMARY KEY,
                rollout_path TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                cwd TEXT NOT NULL,
                title TEXT NOT NULL,
                tokens_used INTEGER NOT NULL,
                archived INTEGER NOT NULL,
                archived_at INTEGER,
                model TEXT,
                preview TEXT NOT NULL,
                first_user_message TEXT NOT NULL
            )
            """
        )
        connection.executemany(
            "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    "thread-active",
                    "",
                    int((now - timedelta(hours=1)).timestamp()),
                    int((now - timedelta(minutes=20)).timestamp()),
                    r"C:\work\alpha",
                    "Visible task title",
                    100,
                    0,
                    None,
                    "gpt-5",
                    "do not expose preview-secret",
                    "do not expose prompt-secret",
                ),
                (
                    "thread-old",
                    "",
                    int((now - timedelta(days=9)).timestamp()),
                    int((now - timedelta(days=8)).timestamp()),
                    r"C:\work\beta",
                    "Old task",
                    50,
                    0,
                    None,
                    "gpt-5",
                    "old preview-secret",
                    "old prompt-secret",
                ),
            ],
        )

    reader = CodexReader(paths, _FakeAppServer(AppServerResult()))
    snapshot = reader.load(now)

    assert snapshot.quality is SourceQuality.APPROXIMATE
    assert snapshot.approximate_lifetime_tokens == 150
    assert snapshot.approximate_today_tokens == 100
    assert snapshot.approximate_seven_day_tokens == 100
    assert snapshot.thread_count == 2
    assert len(snapshot.daily_usage) == 180
    assert sum(day.tokens for day in snapshot.daily_usage) == 150
    assert snapshot.task_board is not None
    assert len(snapshot.task_board.columns[TaskColumnKind.ACTIVE]) == 1
    assert snapshot.task_board.columns[TaskColumnKind.ACTIVE][0].title == "Visible task title"
    serialized = json.dumps(snapshot.safe_dict(), ensure_ascii=False)
    assert "preview-secret" not in serialized
    assert "prompt-secret" not in serialized
    assert str(tmp_path) not in serialized


def test_detailed_sessions_compute_deltas_tools_skills_projects_tasks_and_cache(
    tmp_path: Path,
) -> None:
    paths = _runtime_paths(tmp_path)
    now = datetime.now().astimezone().replace(microsecond=0)
    workspace = tmp_path / "workspace" / "alpha"
    workspace.mkdir(parents=True)
    skill_file = paths.codex_root / "skills" / "personal" / "demo" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text("# Demo\nUse this local skill for deterministic tests.\n", encoding="utf-8")
    nonexistent_skill = paths.home / "missing" / "SKILL.md"
    rollout = paths.codex_root / "sessions" / "rollout-test.jsonl"

    events = [
        {
            "timestamp": (now - timedelta(minutes=11)).isoformat(),
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {
                        "input_tokens": "not-a-number",
                        "total_tokens": 1,
                    }
                },
            },
        },
        _token_event(
            now - timedelta(minutes=10),
            input_tokens=100,
            cached_input_tokens=20,
            output_tokens=10,
            reasoning_output_tokens=2,
            total_tokens=110,
        ),
        _token_event(
            now - timedelta(minutes=9),
            input_tokens=100,
            cached_input_tokens=20,
            output_tokens=10,
            reasoning_output_tokens=2,
            total_tokens=110,
        ),
        _token_event(
            now - timedelta(minutes=8),
            input_tokens=150,
            cached_input_tokens=30,
            output_tokens=20,
            reasoning_output_tokens=4,
            total_tokens=170,
        ),
        _token_event(
            now - timedelta(minutes=7),
            input_tokens=5,
            cached_input_tokens=1,
            output_tokens=1,
            reasoning_output_tokens=0,
            total_tokens=6,
        ),
        {
            "timestamp": (now - timedelta(minutes=6)).isoformat(),
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "arguments": json.dumps(
                    {
                        "cmd": f'Get-Content "{skill_file}"',
                        "ignored": f'Get-Content "{nonexistent_skill}"',
                    }
                ),
            },
        },
        {
            "timestamp": (now - timedelta(minutes=5)).isoformat(),
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call",
                "name": "web_fetch",
                "input": {"url": "https://example.test/no-private-data"},
            },
        },
    ]
    _write_jsonl(rollout, events)

    database = paths.codex_root / "state_5.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE threads (
                id TEXT PRIMARY KEY,
                rollout_path TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                recency_at INTEGER NOT NULL,
                cwd TEXT NOT NULL,
                title TEXT NOT NULL,
                tokens_used INTEGER NOT NULL,
                archived INTEGER NOT NULL,
                archived_at INTEGER,
                model TEXT
            )
            """
        )
        connection.executemany(
            "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    "thread-detailed",
                    _extended(rollout),
                    int((now - timedelta(hours=1)).timestamp()),
                    int((now - timedelta(minutes=5)).timestamp()),
                    int((now - timedelta(minutes=4)).timestamp()),
                    _extended(workspace),
                    "Active detailed task",
                    999,
                    0,
                    None,
                    "gpt-5",
                ),
                (
                    "thread-done",
                    "",
                    int((now - timedelta(hours=2)).timestamp()),
                    int((now - timedelta(minutes=30)).timestamp()),
                    int((now - timedelta(minutes=30)).timestamp()),
                    str(workspace),
                    "Finished task",
                    100,
                    1,
                    int((now - timedelta(minutes=25)).timestamp()),
                    "gpt-5",
                ),
            ],
        )

    app_result = AppServerResult(
        account=AccountInfo("chatgpt", "plus", email_present=True),
        primary=RateWindow(used_percent=20, window_minutes=300),
        cloud_lifetime_tokens=1234,
    )
    reader = CodexReader(paths, _FakeAppServer(app_result))
    original_parser = reader._parse_session_file
    with patch.object(reader, "_parse_session_file", wraps=original_parser) as parser:
        snapshot = reader.load(now)
        second_snapshot = reader.load(now)

    assert parser.call_count == 1
    cache_path = paths.cache_root / "codex" / "session-usage-v1.json"
    cache_text = cache_path.read_text(encoding="utf-8")
    assert str(rollout) not in cache_text
    assert str(workspace) not in cache_text
    assert str(skill_file) not in cache_text

    fresh_reader = CodexReader(paths, _FakeAppServer(app_result))
    fresh_parser = fresh_reader._parse_session_file
    with patch.object(fresh_reader, "_parse_session_file", wraps=fresh_parser) as persistent_parser:
        third_snapshot = fresh_reader.load(now)
    assert persistent_parser.call_count == 0
    assert snapshot.quality is SourceQuality.DETAILED
    assert snapshot.detailed is not None
    assert snapshot.detailed.parsed_file_count == 1
    assert snapshot.detailed.token_event_count == 4
    assert snapshot.detailed.lifetime.tokens.input_tokens == 155
    assert snapshot.detailed.lifetime.tokens.cached_input_tokens == 31
    assert snapshot.detailed.lifetime.tokens.output_tokens == 21
    assert snapshot.detailed.lifetime.tokens.total_tokens == 176
    assert snapshot.today_tokens == 176
    assert second_snapshot.today_tokens == 176
    assert third_snapshot.today_tokens == 176

    assert [(tool.name, tool.call_count, tool.session_count) for tool in snapshot.tools] == [
        ("exec_command", 1, 1),
        ("web_fetch", 1, 1),
    ]
    assert [tool.estimated_tokens for tool in snapshot.tools] == [88, 88]
    assert len(snapshot.skills) == 1
    assert snapshot.skills[0].display_name == "demo"
    assert snapshot.skills[0].source_label == "personal"
    assert snapshot.skills[0].load_count == 1
    assert snapshot.skills[0].static_token_estimate is not None
    assert str(skill_file) not in snapshot.skills[0].skill_id

    assert len(snapshot.all_projects) == 1
    assert snapshot.all_projects[0].tokens == 1099
    assert snapshot.all_projects[0].session_count == 2
    assert len(snapshot.recent_projects) == 1
    assert snapshot.recent_projects[0].tokens == 176
    assert snapshot.task_board is not None
    assert len(snapshot.task_board.columns[TaskColumnKind.ACTIVE]) == 1
    assert len(snapshot.task_board.columns[TaskColumnKind.DONE]) == 1
    assert str(tmp_path) not in json.dumps(snapshot.safe_dict(), ensure_ascii=False)
    assert all(str(tmp_path) not in diagnostic for diagnostic in snapshot.diagnostics)


def test_millisecond_only_timestamps_are_selected_from_schema(tmp_path: Path) -> None:
    paths = _runtime_paths(tmp_path)
    (paths.codex_root / "state_5.sqlite").write_bytes(b"not-a-sqlite-database")
    database = paths.codex_root / "sqlite" / "state_5.sqlite"
    database.parent.mkdir(parents=True)
    now = datetime.now().astimezone().replace(microsecond=0)
    epoch_ms = int((now - timedelta(minutes=15)).timestamp() * 1000)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE threads (
                id TEXT PRIMARY KEY,
                rollout_path TEXT NOT NULL,
                created_at_ms INTEGER NOT NULL,
                updated_at_ms INTEGER NOT NULL,
                recency_at_ms INTEGER NOT NULL,
                cwd TEXT NOT NULL,
                title TEXT NOT NULL,
                tokens_used INTEGER NOT NULL,
                archived INTEGER NOT NULL,
                model TEXT
            )
            """
        )
        connection.execute(
            "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "thread-ms",
                "",
                epoch_ms,
                epoch_ms,
                epoch_ms,
                r"C:\work\milliseconds",
                "Millisecond task",
                42,
                0,
                "gpt-5",
            ),
        )

    snapshot = CodexReader(paths, _FakeAppServer(AppServerResult())).load(now)

    assert snapshot.quality is SourceQuality.APPROXIMATE
    assert snapshot.approximate_today_tokens == 42
    assert snapshot.last_active_at is not None
    assert snapshot.task_board is not None
    assert len(snapshot.task_board.columns[TaskColumnKind.ACTIVE]) == 1
