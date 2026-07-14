from __future__ import annotations

from datetime import datetime

from codexu_win.data.claude_reader import ClaudeReader
from codexu_win.data.codex_reader import CodexReader
from codexu_win.models import RuntimeKind, RuntimeSnapshot, SnapshotBundle, SourceQuality
from codexu_win.paths import RuntimePaths


class RuntimeService:
    """Own long-lived readers so their local parse caches survive UI refreshes."""

    def __init__(self, paths: RuntimePaths | None = None) -> None:
        self.paths = paths or RuntimePaths.resolve()
        self._codex = CodexReader(self.paths)
        self._claude = ClaudeReader(self.paths)

    def load(self, now: datetime | None = None) -> SnapshotBundle:
        current = now.astimezone() if now else datetime.now().astimezone()
        snapshots: dict[RuntimeKind, RuntimeSnapshot] = {}
        for kind, reader in (
            (RuntimeKind.CODEX, self._codex),
            (RuntimeKind.CLAUDE, self._claude),
        ):
            try:
                snapshots[kind] = reader.load(current)
            except Exception as error:  # The UI must keep the other runtime available.
                snapshots[kind] = RuntimeSnapshot(
                    runtime=kind,
                    refreshed_at=current,
                    quality=SourceQuality.UNAVAILABLE,
                    diagnostics=[_safe_reader_error(kind, error)],
                )
        return SnapshotBundle(snapshots)


def _safe_reader_error(kind: RuntimeKind, error: Exception) -> str:
    category = type(error).__name__
    label = "Codex" if kind is RuntimeKind.CODEX else "Claude Code"
    return f"{label} local data reader failed ({category})"
