from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, Sequence

from codexu_win.models import AccountInfo, RateWindow
from codexu_win.utils import parse_datetime


_METHODS_BY_ID = {
    2: "account/read",
    3: "account/rateLimits/read",
    4: "account/usage/read",
}


@dataclass(slots=True)
class AppServerResult:
    account: AccountInfo | None = None
    primary: RateWindow | None = None
    secondary: RateWindow | None = None
    credits_balance: str | None = None
    cloud_lifetime_tokens: int | None = None
    diagnostics: list[str] = field(default_factory=list)


class CodexAppServerClient:
    """Read aggregate account data from the local Codex app-server."""

    def __init__(self, command: Sequence[str] | None = None) -> None:
        self._command = tuple(command) if command is not None else None

    def read_snapshot(self, timeout_seconds: float = 12.0) -> AppServerResult:
        result = AppServerResult()
        command = list(self._command or _resolve_codex_command() or ())
        if not command:
            result.diagnostics.append("Codex app-server executable was not found")
            return result

        try:
            process = _start_process(command)
        except (OSError, subprocess.SubprocessError):
            result.diagnostics.append("Codex app-server could not start")
            return result

        messages: queue.Queue[bytes | None] = queue.Queue()
        stdout_thread = threading.Thread(
            target=_pump_lines,
            args=(process.stdout, messages),
            name="codexu-app-server-stdout",
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=_drain_stream,
            args=(process.stderr,),
            name="codexu-app-server-stderr",
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()

        deadline = time.monotonic() + max(timeout_seconds, 0.05)
        pending = set(_METHODS_BY_ID)
        initialized = False
        try:
            _write_message(
                process.stdin,
                {
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "clientInfo": {
                            "name": "codexu-windows",
                            "title": "codexU Windows",
                            "version": "0.1.0",
                        },
                        "capabilities": {
                            "experimentalApi": True,
                            "optOutNotificationMethods": [],
                        },
                    },
                },
            )

            while time.monotonic() < deadline and (not initialized or pending):
                remaining = max(0.01, deadline - time.monotonic())
                try:
                    raw_line = messages.get(timeout=remaining)
                except queue.Empty:
                    break
                if raw_line is None:
                    break
                response = _decode_response(raw_line)
                if response is None:
                    continue
                response_id = _response_id(response.get("id"))
                if response_id == 1 and not initialized:
                    if isinstance(response.get("error"), dict):
                        result.diagnostics.append(_rpc_error_diagnostic("initialize", response["error"]))
                        break
                    initialized = True
                    _write_message(process.stdin, {"method": "initialized"})
                    _write_message(
                        process.stdin,
                        {"id": 2, "method": "account/read", "params": {"refreshToken": False}},
                    )
                    _write_message(process.stdin, {"id": 3, "method": "account/rateLimits/read"})
                    _write_message(process.stdin, {"id": 4, "method": "account/usage/read"})
                    continue
                if response_id not in pending:
                    continue

                pending.remove(response_id)
                method = _METHODS_BY_ID[response_id]
                error = response.get("error")
                if isinstance(error, dict):
                    result.diagnostics.append(_rpc_error_diagnostic(method, error))
                    continue
                payload = response.get("result")
                if not isinstance(payload, dict):
                    result.diagnostics.append(f"Codex {method} returned no usable data")
                    continue
                try:
                    if response_id == 2:
                        result.account = _parse_account(payload)
                    elif response_id == 3:
                        _parse_rate_limits(payload, result)
                    elif response_id == 4:
                        result.cloud_lifetime_tokens = _parse_cloud_lifetime_tokens(payload)
                except (TypeError, ValueError, OverflowError):
                    result.diagnostics.append(f"Codex {method} returned an unsupported data shape")

            if not initialized:
                if not any("initialize" in item for item in result.diagnostics):
                    result.diagnostics.append("Codex app-server initialization timed out")
            else:
                for response_id in sorted(pending):
                    result.diagnostics.append(f"Codex {_METHODS_BY_ID[response_id]} timed out")
        except (BrokenPipeError, OSError, subprocess.SubprocessError):
            result.diagnostics.append("Codex app-server connection closed unexpectedly")
        finally:
            _stop_process(process)

        return result


def _resolve_codex_command() -> list[str] | None:
    override = os.environ.get("CODEXU_CODEX_EXECUTABLE", "").strip()
    candidates: list[str] = []
    if override:
        candidates.append(override)
    # WindowsApps may expose codex.exe through PATH while denying CreateProcess.
    # The CLI shim is the reliable app-server entrypoint when both are present.
    for name in ("codex.cmd", "codex.exe", "codex"):
        resolved = shutil.which(name)
        if resolved:
            candidates.append(resolved)
    app_data = os.environ.get("APPDATA")
    if app_data:
        candidates.append(str(Path(app_data) / "npm" / "codex.cmd"))

    executable = next((item for item in candidates if Path(item).is_file()), None)
    if executable is None:
        return None
    if Path(executable).suffix.lower() in {".cmd", ".bat"}:
        command_line = subprocess.list2cmdline([executable, "app-server"])
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", command_line]
    return [executable, "app-server"]


def _start_process(command: Sequence[str]) -> subprocess.Popen[bytes]:
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    return subprocess.Popen(
        list(command),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
        creationflags=creation_flags,
    )


def _write_message(stream: BinaryIO | None, message: dict[str, Any]) -> None:
    if stream is None:
        raise BrokenPipeError
    encoded = json.dumps(message, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    stream.write(encoded + b"\n")
    stream.flush()


def _pump_lines(stream: BinaryIO | None, output: queue.Queue[bytes | None]) -> None:
    if stream is None:
        output.put(None)
        return
    try:
        for line in iter(stream.readline, b""):
            if len(line) <= 4 * 1024 * 1024:
                output.put(line)
    finally:
        output.put(None)


def _drain_stream(stream: BinaryIO | None) -> None:
    if stream is None:
        return
    try:
        while stream.read(64 * 1024):
            pass
    except OSError:
        return


def _decode_response(raw_line: bytes) -> dict[str, Any] | None:
    try:
        decoded = json.loads(raw_line)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return decoded if isinstance(decoded, dict) else None


def _response_id(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _parse_account(payload: dict[str, Any]) -> AccountInfo | None:
    account = payload.get("account")
    if not isinstance(account, dict):
        return None
    account_type = account.get("type")
    if not isinstance(account_type, str) or not account_type:
        return None
    plan_type = account.get("planType")
    return AccountInfo(
        account_type=account_type,
        plan_type=plan_type if isinstance(plan_type, str) and plan_type else None,
        email_present=account.get("email") is not None,
    )


def _parse_rate_limits(payload: dict[str, Any], result: AppServerResult) -> None:
    limits: dict[str, Any] | None = None
    by_limit_id = payload.get("rateLimitsByLimitId")
    if isinstance(by_limit_id, dict) and isinstance(by_limit_id.get("codex"), dict):
        limits = by_limit_id["codex"]
    elif isinstance(payload.get("rateLimits"), dict):
        limits = payload["rateLimits"]
    if limits is None:
        return
    result.primary = _parse_rate_window(limits.get("primary"))
    result.secondary = _parse_rate_window(limits.get("secondary"))
    credits = limits.get("credits")
    if isinstance(credits, dict):
        balance = credits.get("balance")
        if isinstance(balance, (str, int, float)) and not isinstance(balance, bool):
            result.credits_balance = str(balance)


def _parse_rate_window(value: Any) -> RateWindow | None:
    if not isinstance(value, dict):
        return None
    used = _as_float(value.get("usedPercent"))
    if used is None:
        return None
    return RateWindow(
        used_percent=used,
        window_minutes=_as_int(value.get("windowDurationMins")),
        resets_at=parse_datetime(value.get("resetsAt")),
    )


def _parse_cloud_lifetime_tokens(payload: dict[str, Any]) -> int | None:
    summary = payload.get("summary")
    if not isinstance(summary, dict):
        return None
    return _as_int(summary.get("lifetimeTokens"))


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _rpc_error_diagnostic(method: str, error: dict[str, Any]) -> str:
    code = _as_int(error.get("code"))
    message = error.get("message")
    normalized = message.lower() if isinstance(message, str) else ""
    if code == -32601:
        category = "is unsupported"
    elif code == -32602:
        category = "rejected its parameters"
    elif code == -32600 and "chatgpt" in normalized:
        category = "is unavailable for this account type"
    else:
        category = "failed"
    suffix = f" (code {code})" if code is not None else ""
    return f"Codex {method} {category}{suffix}"


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    try:
        if process.stdin is not None:
            process.stdin.close()
    except OSError:
        pass
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=0.5)
    except (OSError, subprocess.SubprocessError):
        try:
            process.kill()
            process.wait(timeout=0.5)
        except (OSError, subprocess.SubprocessError):
            pass
