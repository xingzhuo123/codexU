from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class RuntimeKind(StrEnum):
    CODEX = "codex"
    CLAUDE = "claude"


class SourceQuality(StrEnum):
    DETAILED = "detailed"
    APPROXIMATE = "approximate"
    UNAVAILABLE = "unavailable"


class TaskColumnKind(StrEnum):
    ACTIVE = "active"
    PENDING = "pending"
    SCHEDULED = "scheduled"
    DONE = "done"


@dataclass(slots=True, frozen=True)
class RateWindow:
    used_percent: float
    window_minutes: int | None = None
    resets_at: datetime | None = None

    @property
    def remaining_percent(self) -> float:
        return max(0.0, min(100.0, 100.0 - self.used_percent))


@dataclass(slots=True, frozen=True)
class AccountInfo:
    account_type: str
    plan_type: str | None = None
    email_present: bool = False


@dataclass(slots=True)
class TokenBreakdown:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0
    total_tokens: int = 0

    @property
    def billable_cached_input_tokens(self) -> int:
        return min(max(self.cached_input_tokens, 0), max(self.input_tokens, 0))

    @property
    def uncached_input_tokens(self) -> int:
        return max(0, self.input_tokens - self.billable_cached_input_tokens)

    @property
    def visible_total_tokens(self) -> int:
        return max(self.total_tokens, self.input_tokens + self.output_tokens, 0)

    @property
    def is_zero(self) -> bool:
        return not any(
            (
                self.input_tokens,
                self.cached_input_tokens,
                self.output_tokens,
                self.reasoning_output_tokens,
                self.total_tokens,
            )
        )

    @property
    def has_negative_value(self) -> bool:
        return any(
            value < 0
            for value in (
                self.input_tokens,
                self.cached_input_tokens,
                self.output_tokens,
                self.reasoning_output_tokens,
                self.total_tokens,
            )
        )

    def add(self, other: TokenBreakdown) -> None:
        self.input_tokens += other.input_tokens
        self.cached_input_tokens += other.cached_input_tokens
        self.output_tokens += other.output_tokens
        self.reasoning_output_tokens += other.reasoning_output_tokens
        self.total_tokens += other.total_tokens

    def delta_from(self, previous: TokenBreakdown) -> TokenBreakdown:
        return TokenBreakdown(
            input_tokens=self.input_tokens - previous.input_tokens,
            cached_input_tokens=self.cached_input_tokens - previous.cached_input_tokens,
            output_tokens=self.output_tokens - previous.output_tokens,
            reasoning_output_tokens=self.reasoning_output_tokens - previous.reasoning_output_tokens,
            total_tokens=self.total_tokens - previous.total_tokens,
        )

    def copy(self) -> TokenBreakdown:
        return TokenBreakdown(
            input_tokens=self.input_tokens,
            cached_input_tokens=self.cached_input_tokens,
            output_tokens=self.output_tokens,
            reasoning_output_tokens=self.reasoning_output_tokens,
            total_tokens=self.total_tokens,
        )


@dataclass(slots=True)
class PricedUsage:
    tokens: TokenBreakdown = field(default_factory=TokenBreakdown)
    estimated_cost_usd: float = 0.0

    def add(self, tokens: TokenBreakdown, cost_usd: float = 0.0) -> None:
        self.tokens.add(tokens)
        self.estimated_cost_usd += max(cost_usd, 0.0)


@dataclass(slots=True, frozen=True)
class DailyUsage:
    day: str
    tokens: int
    estimated_cost_usd: float = 0.0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    quality: SourceQuality = SourceQuality.DETAILED


@dataclass(slots=True, frozen=True)
class ProjectUsage:
    project_id: str
    display_name: str
    tokens: int
    estimated_cost_usd: float | None
    session_count: int
    last_active_at: datetime | None
    quality: SourceQuality


@dataclass(slots=True, frozen=True)
class ToolUsage:
    name: str
    category: str
    call_count: int
    session_count: int = 0
    estimated_tokens: int | None = None
    estimated_cost_usd: float | None = None


@dataclass(slots=True, frozen=True)
class SkillUsage:
    skill_id: str
    display_name: str
    source_label: str
    load_count: int
    session_count: int
    static_token_estimate: int | None = None
    last_loaded_at: datetime | None = None


@dataclass(slots=True, frozen=True)
class TaskItem:
    task_id: str
    code: str
    title: str
    detail: str
    chip: str
    updated_at: datetime | None
    tokens: int | None
    column: TaskColumnKind


@dataclass(slots=True)
class TaskBoard:
    refreshed_at: datetime
    columns: dict[TaskColumnKind, list[TaskItem]] = field(
        default_factory=lambda: {kind: [] for kind in TaskColumnKind}
    )

    @property
    def total_count(self) -> int:
        return sum(len(items) for items in self.columns.values())


@dataclass(slots=True)
class DetailedUsage:
    today: PricedUsage = field(default_factory=PricedUsage)
    seven_day: PricedUsage = field(default_factory=PricedUsage)
    previous_seven_day: PricedUsage = field(default_factory=PricedUsage)
    month: PricedUsage = field(default_factory=PricedUsage)
    lifetime: PricedUsage = field(default_factory=PricedUsage)
    parsed_file_count: int = 0
    token_event_count: int = 0


@dataclass(slots=True)
class RuntimeSnapshot:
    runtime: RuntimeKind
    refreshed_at: datetime
    account: AccountInfo | None = None
    primary: RateWindow | None = None
    secondary: RateWindow | None = None
    credits_balance: str | None = None
    cloud_lifetime_tokens: int | None = None
    detailed: DetailedUsage | None = None
    approximate_lifetime_tokens: int | None = None
    approximate_today_tokens: int | None = None
    approximate_seven_day_tokens: int | None = None
    thread_count: int | None = None
    last_active_at: datetime | None = None
    daily_usage: list[DailyUsage] = field(default_factory=list)
    recent_projects: list[ProjectUsage] = field(default_factory=list)
    all_projects: list[ProjectUsage] = field(default_factory=list)
    tools: list[ToolUsage] = field(default_factory=list)
    skills: list[SkillUsage] = field(default_factory=list)
    task_board: TaskBoard | None = None
    quality: SourceQuality = SourceQuality.UNAVAILABLE
    diagnostics: list[str] = field(default_factory=list)

    @property
    def today_tokens(self) -> int | None:
        if self.detailed is not None:
            return self.detailed.today.tokens.visible_total_tokens
        return self.approximate_today_tokens

    @property
    def seven_day_tokens(self) -> int | None:
        if self.detailed is not None:
            return self.detailed.seven_day.tokens.visible_total_tokens
        return self.approximate_seven_day_tokens

    @property
    def lifetime_tokens(self) -> int | None:
        if self.detailed is not None:
            return self.detailed.lifetime.tokens.visible_total_tokens
        return self.approximate_lifetime_tokens

    @property
    def has_local_usage(self) -> bool:
        return self.lifetime_tokens is not None or bool(self.daily_usage)

    def safe_dict(self) -> dict[str, Any]:
        """Return aggregate-only diagnostics suitable for local probes."""

        result: dict[str, Any] = {
            "runtime": self.runtime.value,
            "refreshed_at": self.refreshed_at.isoformat(),
            "account_type": self.account.account_type if self.account else None,
            "plan_type": self.account.plan_type if self.account else None,
            "quota": {
                "primary_remaining_percent": self.primary.remaining_percent if self.primary else None,
                "secondary_remaining_percent": self.secondary.remaining_percent if self.secondary else None,
            },
            "usage": {
                "today_tokens": self.today_tokens,
                "seven_day_tokens": self.seven_day_tokens,
                "lifetime_tokens": self.lifetime_tokens,
                "thread_count": self.thread_count,
                "project_count": len(self.all_projects),
                "tool_count": len(self.tools),
                "skill_count": len(self.skills),
                "task_count": self.task_board.total_count if self.task_board else 0,
                "quality": self.quality.value,
            },
            "diagnostics": list(self.diagnostics),
        }
        if self.detailed:
            result["usage"].update(
                {
                    "month_tokens": self.detailed.month.tokens.visible_total_tokens,
                    "month_estimated_cost_usd": round(self.detailed.month.estimated_cost_usd, 4),
                    "parsed_file_count": self.detailed.parsed_file_count,
                    "token_event_count": self.detailed.token_event_count,
                }
            )
        return result


@dataclass(slots=True)
class SnapshotBundle:
    snapshots: dict[RuntimeKind, RuntimeSnapshot]

    def safe_dict(self) -> dict[str, Any]:
        return {kind.value: snapshot.safe_dict() for kind, snapshot in self.snapshots.items()}


def token_breakdown_from_dict(value: dict[str, Any]) -> TokenBreakdown:
    return TokenBreakdown(
        input_tokens=int(value.get("input_tokens", 0) or 0),
        cached_input_tokens=int(value.get("cached_input_tokens", 0) or 0),
        output_tokens=int(value.get("output_tokens", 0) or 0),
        reasoning_output_tokens=int(value.get("reasoning_output_tokens", 0) or 0),
        total_tokens=int(value.get("total_tokens", 0) or 0),
    )


def dataclass_to_dict(value: Any) -> dict[str, Any]:
    return asdict(value)
