from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _window(source: Any) -> dict[str, Any] | None:
    if not isinstance(source, dict):
        return None
    used = source.get("used_percentage", source.get("usedPercentage"))
    resets = source.get("resets_at", source.get("resetsAt"))
    if not isinstance(used, (int, float)):
        return None
    result: dict[str, Any] = {"used_percentage": max(0.0, min(100.0, float(used)))}
    if isinstance(resets, (int, float, str)):
        result["resets_at"] = resets
    return result


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0

    rate_limits = payload.get("rate_limits", {}) if isinstance(payload, dict) else {}
    if not isinstance(rate_limits, dict):
        return 0

    five_hour = _window(rate_limits.get("five_hour", rate_limits.get("fiveHour")))
    seven_day = _window(rate_limits.get("seven_day", rate_limits.get("sevenDay")))
    if five_hour is None and seven_day is None:
        return 0

    home = Path.home()
    local_app_data = Path(os.environ.get("LOCALAPPDATA", str(home / "AppData" / "Local")))
    cache_root = Path(os.environ.get("CODEXU_CACHE_OVERRIDE", str(local_app_data / "codexU" / "Cache")))
    target = cache_root / "claude-code" / "statusline-snapshot.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "rate_limits": {"five_hour": five_hour, "seven_day": seven_day},
    }
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(snapshot, ensure_ascii=True), encoding="utf-8")
    temporary.replace(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
