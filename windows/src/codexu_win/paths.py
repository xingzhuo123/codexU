from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True, frozen=True)
class RuntimePaths:
    home: Path
    codex_root: Path
    claude_root: Path
    cache_root: Path
    settings_root: Path

    @classmethod
    def resolve(cls) -> RuntimePaths:
        home = Path(os.environ.get("CODEXU_HOME_OVERRIDE", str(Path.home()))).expanduser()
        local_app_data = Path(
            os.environ.get("LOCALAPPDATA", str(home / "AppData" / "Local"))
        )
        cache_root = Path(
            os.environ.get("CODEXU_CACHE_OVERRIDE", str(local_app_data / "codexU" / "Cache"))
        )
        claude_root = Path(
            os.environ.get(
                "CODEXU_CLAUDE_ROOT_OVERRIDE",
                os.environ.get("CLAUDE_CONFIG_DIR", str(home / ".claude")),
            )
        ).expanduser()
        return cls(
            home=home,
            codex_root=home / ".codex",
            claude_root=claude_root,
            cache_root=cache_root,
            settings_root=local_app_data / "codexU",
        )


def asset_path(name: str) -> Path:
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS")) / "assets"
    else:
        base = Path(__file__).resolve().parents[2] / "assets"
    return base / name
