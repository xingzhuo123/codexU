from __future__ import annotations

import math
from datetime import datetime, timedelta

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
    SnapshotBundle,
    SourceQuality,
    TaskBoard,
    TaskColumnKind,
    TaskItem,
    TokenBreakdown,
    ToolUsage,
)


def _priced(total: int, input_ratio: float = 0.82, cached_ratio: float = 0.7) -> PricedUsage:
    input_tokens = int(total * input_ratio)
    output_tokens = max(0, total - input_tokens)
    cached_tokens = int(input_tokens * cached_ratio)
    tokens = TokenBreakdown(
        input_tokens=input_tokens,
        cached_input_tokens=cached_tokens,
        output_tokens=output_tokens,
        reasoning_output_tokens=int(output_tokens * 0.42),
        total_tokens=total,
    )
    estimated = (
        tokens.uncached_input_tokens * 5
        + tokens.billable_cached_input_tokens * 0.5
        + tokens.output_tokens * 30
    ) / 1_000_000
    return PricedUsage(tokens=tokens, estimated_cost_usd=estimated)


def make_demo_bundle(now: datetime | None = None) -> SnapshotBundle:
    current = now.astimezone() if now else datetime.now().astimezone()
    day_start = current.replace(hour=0, minute=0, second=0, microsecond=0)
    daily: list[DailyUsage] = []
    for offset in range(179, -1, -1):
        day = day_start - timedelta(days=offset)
        wave = (math.sin(offset / 6.3) + 1.15) * 4_100_000
        weekly = 0.42 if day.weekday() >= 5 else 1.0
        pulse = 7_500_000 if offset % 23 == 0 else 0
        total = int(max(0, wave * weekly + pulse - 1_800_000))
        usage = _priced(total)
        daily.append(
            DailyUsage(
                day=day.date().isoformat(),
                tokens=total,
                estimated_cost_usd=usage.estimated_cost_usd,
                input_tokens=usage.tokens.input_tokens,
                cached_input_tokens=usage.tokens.cached_input_tokens,
                output_tokens=usage.tokens.output_tokens,
            )
        )

    seven_total = sum(item.tokens for item in daily[-7:])
    previous_total = sum(item.tokens for item in daily[-14:-7])
    month_items = [item for item in daily if item.day.startswith(current.strftime("%Y-%m"))]
    month_total = sum(item.tokens for item in month_items)
    lifetime_total = sum(item.tokens for item in daily)
    detailed = DetailedUsage(
        today=_priced(daily[-1].tokens),
        seven_day=_priced(seven_total),
        previous_seven_day=_priced(previous_total),
        month=_priced(month_total),
        lifetime=_priced(lifetime_total),
        parsed_file_count=42,
        token_event_count=1280,
    )

    projects = [
        ProjectUsage("demo-1", "codexU Windows", 82_400_000, 76.24, 18, current - timedelta(minutes=4), SourceQuality.DETAILED),
        ProjectUsage("demo-2", "Research Notes", 43_100_000, 31.70, 9, current - timedelta(hours=2), SourceQuality.DETAILED),
        ProjectUsage("demo-3", "Automation Lab", 28_700_000, 22.15, 7, current - timedelta(days=1), SourceQuality.DETAILED),
        ProjectUsage("demo-4", "Data Toolkit", 14_900_000, 10.92, 4, current - timedelta(days=3), SourceQuality.DETAILED),
        ProjectUsage("demo-5", "Desktop Client", 8_300_000, 6.12, 3, current - timedelta(days=5), SourceQuality.DETAILED),
    ]
    tools = [
        ToolUsage("shell_command", "terminal", 642, 38, 62_100_000, 48.2),
        ToolUsage("apply_patch", "edit", 214, 31, 21_700_000, 16.4),
        ToolUsage("web.run", "browser", 98, 14, 9_600_000, 7.8),
        ToolUsage("view_image", "visual", 44, 8, 3_100_000, 2.2),
        ToolUsage("update_plan", "planning", 31, 12, 1_800_000, 1.4),
    ]
    skills = [
        SkillUsage("skill-1", "github", "OpenAI curated", 24, 9, 950, current - timedelta(minutes=8)),
        SkillUsage("skill-2", "computer-use", "OpenAI bundled", 18, 7, 2_480, current - timedelta(hours=1)),
        SkillUsage("skill-3", "playwright", "Local", 11, 5, 1_720, current - timedelta(days=1)),
        SkillUsage("skill-4", "documents", "OpenAI bundled", 8, 4, 2_150, current - timedelta(days=2)),
    ]
    board = TaskBoard(refreshed_at=current)
    board.columns[TaskColumnKind.ACTIVE] = [
        TaskItem("task-1", "COD-A142", "移植 Windows 数据读取", "codexU Windows · 12.4M", "Active", current - timedelta(minutes=3), 12_400_000, TaskColumnKind.ACTIVE),
        TaskItem("task-2", "COD-B083", "验证系统托盘与快捷面板", "Desktop Client · 4.8M", "Active", current - timedelta(minutes=18), 4_800_000, TaskColumnKind.ACTIVE),
    ]
    board.columns[TaskColumnKind.PENDING] = [
        TaskItem("task-3", "COD-C219", "整理发布说明", "codexU Windows", "Idle", current - timedelta(hours=3), 920_000, TaskColumnKind.PENDING),
    ]
    board.columns[TaskColumnKind.SCHEDULED] = [
        TaskItem("task-4", "AUTO-09", "每日用量快照", "CRON · 每天 09:00", "Cron", current - timedelta(days=1), None, TaskColumnKind.SCHEDULED),
    ]
    board.columns[TaskColumnKind.DONE] = [
        TaskItem("task-5", "COD-D511", "确认 SQLite 字段兼容", "Data Toolkit · 2.1M", "Done", current - timedelta(minutes=42), 2_100_000, TaskColumnKind.DONE),
    ]

    codex = RuntimeSnapshot(
        runtime=RuntimeKind.CODEX,
        refreshed_at=current,
        account=AccountInfo("chatgpt", "Pro", True),
        primary=RateWindow(used_percent=36, window_minutes=300, resets_at=current + timedelta(hours=2, minutes=14)),
        secondary=RateWindow(used_percent=58, window_minutes=10_080, resets_at=current + timedelta(days=3, hours=8)),
        cloud_lifetime_tokens=lifetime_total,
        detailed=detailed,
        approximate_lifetime_tokens=lifetime_total,
        approximate_today_tokens=daily[-1].tokens,
        approximate_seven_day_tokens=seven_total,
        thread_count=42,
        last_active_at=current - timedelta(minutes=3),
        daily_usage=daily,
        recent_projects=projects[:4],
        all_projects=projects,
        tools=tools,
        skills=skills,
        task_board=board,
        quality=SourceQuality.DETAILED,
    )

    claude_detail = DetailedUsage(
        today=_priced(5_200_000, 0.75, 0.45),
        seven_day=_priced(31_800_000, 0.75, 0.45),
        previous_seven_day=_priced(28_500_000, 0.75, 0.45),
        month=_priced(84_600_000, 0.75, 0.45),
        lifetime=_priced(321_900_000, 0.75, 0.45),
        parsed_file_count=16,
        token_event_count=188,
    )
    claude = RuntimeSnapshot(
        runtime=RuntimeKind.CLAUDE,
        refreshed_at=current,
        account=AccountInfo("local", "Claude Code", False),
        detailed=claude_detail,
        thread_count=16,
        last_active_at=current - timedelta(hours=1),
        daily_usage=daily,
        recent_projects=projects[1:],
        all_projects=projects[1:],
        tools=tools[1:],
        skills=skills[1:],
        quality=SourceQuality.DETAILED,
        diagnostics=["额度需要 Claude Code statusline 快照"],
    )
    return SnapshotBundle({RuntimeKind.CODEX: codex, RuntimeKind.CLAUDE: claude})
