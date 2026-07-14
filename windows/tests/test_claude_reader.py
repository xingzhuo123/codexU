from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from codexu_win.data.claude_reader import ClaudeReader
from codexu_win.models import SourceQuality, TaskColumnKind
from codexu_win.paths import RuntimePaths


FIXTURE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "claude-code-session.jsonl"
NOW = datetime(2026, 7, 7, 8, 0, tzinfo=timezone.utc)


def _paths(tmp_path: Path) -> RuntimePaths:
    home = tmp_path / "home"
    local = tmp_path / "local"
    claude_root = home / ".claude"
    return RuntimePaths(
        home=home,
        codex_root=home / ".codex",
        claude_root=claude_root,
        cache_root=local / "codexU" / "Cache",
        settings_root=local / "codexU",
    )


def _write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(record, separators=(",", ":")) + "\n" for record in records),
        encoding="utf-8",
    )


def _usage_record(
    *,
    session_id: str = "session-1",
    message_id: str = "message-1",
    cwd: str = r"C:\work\sample-project",
    tool_name: str = "Read",
    attribution_skill: str | None = None,
) -> dict[str, object]:
    record: dict[str, object] = {
        "type": "assistant",
        "sessionId": session_id,
        "timestamp": "2026-07-07T07:30:00Z",
        "cwd": cwd,
        "message": {
            "id": message_id,
            "model": "claude-sonnet-4",
            "usage": {
                "input_tokens": 100,
                "cache_creation_input_tokens": 20,
                "cache_read_input_tokens": 30,
                "output_tokens": 50,
            },
            "content": [
                {
                    "type": "tool_use",
                    "name": tool_name,
                    "input": {"private": "must never reach the cache"},
                }
            ],
        },
        "body": "must never reach the cache",
    }
    if attribution_skill is not None:
        record["attributionSkill"] = attribution_skill
    return record


def test_existing_fixture_and_status_snapshot(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    transcript = paths.claude_root / "projects" / "C--fixture-project" / "session.jsonl"
    transcript.parent.mkdir(parents=True)
    shutil.copyfile(FIXTURE, transcript)

    status = paths.cache_root / "claude-code" / "statusline-snapshot.json"
    status.parent.mkdir(parents=True)
    status.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "capturedAt": NOW.isoformat(),
                "rateLimits": {
                    "fiveHour": {
                        "usedPercentage": 25,
                        "resetsAt": "2026-07-07T10:00:00Z",
                    },
                    "sevenDay": {
                        "usedPercentage": 40,
                        "resetsAt": "2026-07-14T07:00:00Z",
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    snapshot = ClaudeReader(paths).load(now=NOW)

    assert snapshot.quality is SourceQuality.DETAILED
    assert snapshot.detailed is not None
    assert snapshot.detailed.lifetime.tokens.visible_total_tokens == 1_900
    assert snapshot.detailed.token_event_count == 1
    assert len(snapshot.daily_usage) == 180
    assert snapshot.tools[0].name == "Read"
    assert snapshot.tools[0].call_count == 1
    assert snapshot.tools[0].estimated_tokens is None
    assert snapshot.primary is not None
    assert snapshot.primary.remaining_percent == 75
    assert snapshot.secondary is not None
    assert snapshot.secondary.remaining_percent == 60

    cache_path = paths.cache_root / "claude-code" / "session-usage-v1.json"
    cache_text = cache_path.read_text(encoding="utf-8")
    assert '"cwd"' not in cache_text
    assert '"body"' not in cache_text
    assert '"input"' not in cache_text

    cached_snapshot = ClaudeReader(paths).load(now=NOW)
    assert cached_snapshot.lifetime_tokens == 1_900
    assert [(tool.name, tool.call_count) for tool in cached_snapshot.tools] == [("Read", 1)]

    transcript.unlink()
    without_transcript = ClaudeReader(paths).load(now=NOW)
    assert without_transcript.detailed is None
    assert without_transcript.lifetime_tokens is None
    assert json.loads(cache_path.read_text(encoding="utf-8"))["entries"] == {}


def test_duplicate_session_message_is_counted_once(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    record = _usage_record(attribution_skill="review-skill")
    other_session = _usage_record(
        session_id="session-2",
        message_id="message-1",
        attribution_skill="review-skill",
    )
    _write_jsonl(
        paths.claude_root / "projects" / "C--work-sample" / "session.jsonl",
        [record, record, other_session],
    )

    snapshot = ClaudeReader(paths).load(now=NOW)

    assert snapshot.detailed is not None
    assert snapshot.detailed.lifetime.tokens.visible_total_tokens == 400
    assert snapshot.detailed.token_event_count == 2
    assert snapshot.thread_count == 2
    assert [(tool.name, tool.call_count) for tool in snapshot.tools] == [("Read", 2)]
    assert [(skill.display_name, skill.load_count) for skill in snapshot.skills] == [
        ("review-skill", 2)
    ]


def test_windows_project_name_is_preserved_without_exporting_path(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    private_path = r"C:\Users\Sensitive Person\source\demo-project"
    _write_jsonl(
        paths.claude_root / "projects" / "C--encoded-private-path" / "session.jsonl",
        [_usage_record(cwd=private_path)],
    )

    snapshot = ClaudeReader(paths).load(now=NOW)

    assert len(snapshot.all_projects) == 1
    project = snapshot.all_projects[0]
    assert project.display_name == "demo-project"
    assert project.project_id.startswith("claude-project-")
    assert "\\" not in project.project_id
    assert "Sensitive Person" not in json.dumps(snapshot.safe_dict())

    cache_text = (
        paths.cache_root / "claude-code" / "session-usage-v1.json"
    ).read_text(encoding="utf-8")
    assert private_path not in cache_text
    assert "Sensitive Person" not in cache_text


def test_tasks_only_expose_allowed_metadata(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    task_root = paths.claude_root / "tasks" / "private-session"
    task_root.mkdir(parents=True)
    (task_root / "one.json").write_text(
        json.dumps(
            {
                "status": "in_progress",
                "subject": "Review the local changes",
                "updatedAt": "2026-07-07T07:00:00Z",
                "description": "private body",
                "toolArguments": {"secret": True},
            }
        ),
        encoding="utf-8",
    )
    (task_root / "two.json").write_text(
        json.dumps(
            {
                "status": "scheduled",
                "title": "Run tests",
                "updated_at": "2026-07-07T06:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    (task_root / "ignored.lock").write_text("private", encoding="utf-8")
    (task_root / ".highwatermark").write_text("private", encoding="utf-8")

    snapshot = ClaudeReader(paths).load(now=NOW)

    assert snapshot.task_board is not None
    assert snapshot.task_board.total_count == 2
    active = snapshot.task_board.columns[TaskColumnKind.ACTIVE][0]
    scheduled = snapshot.task_board.columns[TaskColumnKind.SCHEDULED][0]
    assert active.title == "Review the local changes"
    assert active.detail == "Claude Code task"
    assert active.tokens is None
    assert active.task_id.startswith("claude-task-")
    assert "private-session" not in active.task_id
    assert scheduled.title == "Run tests"


def test_missing_usage_and_quota_remain_none(tmp_path: Path) -> None:
    paths = _paths(tmp_path)

    snapshot = ClaudeReader(paths).load(now=NOW)

    assert snapshot.primary is None
    assert snapshot.secondary is None
    assert snapshot.detailed is None
    assert snapshot.today_tokens is None
    assert snapshot.seven_day_tokens is None
    assert snapshot.lifetime_tokens is None
    assert snapshot.thread_count is None
    assert snapshot.quality is SourceQuality.UNAVAILABLE


def test_stats_fallback_does_not_invent_missing_totals(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.claude_root.mkdir(parents=True)
    (paths.claude_root / "stats-cache.json").write_text(
        json.dumps(
            {
                "todayTokens": 123,
                "updatedAt": "2026-07-07T07:00:00Z",
                "unrelatedPrivateField": "ignored",
            }
        ),
        encoding="utf-8",
    )

    snapshot = ClaudeReader(paths).load(now=NOW)

    assert snapshot.quality is SourceQuality.APPROXIMATE
    assert snapshot.today_tokens == 123
    assert snapshot.seven_day_tokens is None
    assert snapshot.lifetime_tokens is None
    assert snapshot.detailed is None
