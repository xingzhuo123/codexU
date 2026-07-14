from __future__ import annotations

from datetime import datetime
from pathlib import Path

from codexu_win.models import RuntimeKind
from codexu_win.paths import RuntimePaths
from codexu_win.service import RuntimeService


def test_runtime_service_returns_both_unavailable_snapshots_for_empty_home(tmp_path: Path) -> None:
    paths = RuntimePaths(
        home=tmp_path,
        codex_root=tmp_path / ".codex",
        claude_root=tmp_path / ".claude",
        cache_root=tmp_path / "cache",
        settings_root=tmp_path / "settings",
    )
    bundle = RuntimeService(paths).load(datetime.now().astimezone())

    assert set(bundle.snapshots) == {RuntimeKind.CODEX, RuntimeKind.CLAUDE}
    assert bundle.snapshots[RuntimeKind.CODEX].lifetime_tokens is None
    assert bundle.snapshots[RuntimeKind.CLAUDE].lifetime_tokens is None
    assert bundle.snapshots[RuntimeKind.CODEX].diagnostics
    assert bundle.snapshots[RuntimeKind.CLAUDE].diagnostics
