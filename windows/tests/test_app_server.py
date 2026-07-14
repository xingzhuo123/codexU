from __future__ import annotations

import sys
import textwrap

from codexu_win.data.app_server import CodexAppServerClient, _resolve_codex_command


def _server_command(body: str) -> list[str]:
    return [sys.executable, "-u", "-c", textwrap.dedent(body)]


def test_app_server_parses_out_of_order_successful_endpoints() -> None:
    command = _server_command(
        r"""
        import json
        import sys

        initialize = json.loads(sys.stdin.readline())
        assert initialize["method"] == "initialize"
        assert initialize["params"]["capabilities"]["experimentalApi"] is True
        print(json.dumps({"id": 1, "result": {"platformFamily": "windows"}}), flush=True)

        initialized = json.loads(sys.stdin.readline())
        assert initialized == {"method": "initialized"}
        requests = [json.loads(sys.stdin.readline()) for _ in range(3)]
        assert {item["method"] for item in requests} == {
            "account/read", "account/rateLimits/read", "account/usage/read"
        }

        print(json.dumps({"id": 4, "result": {"summary": {"lifetimeTokens": "321"}}}), flush=True)
        print(json.dumps({
            "id": 3,
            "result": {
                "rateLimitsByLimitId": {
                    "codex": {
                        "primary": {"usedPercent": 25, "windowDurationMins": 300, "resetsAt": 1900000000},
                        "secondary": {"usedPercent": 60, "windowDurationMins": 10080, "resetsAt": 1900001000},
                        "credits": {"balance": "12.50"}
                    }
                }
            }
        }), flush=True)
        print(json.dumps({
            "id": 2,
            "result": {
                "account": {"type": "chatgpt", "planType": "plus", "email": "private@example.test"},
                "requiresOpenaiAuth": False
            }
        }), flush=True)
        """
    )

    result = CodexAppServerClient(command).read_snapshot(timeout_seconds=2)

    assert result.account is not None
    assert result.account.account_type == "chatgpt"
    assert result.account.plan_type == "plus"
    assert result.account.email_present is True
    assert result.primary is not None
    assert result.primary.remaining_percent == 75
    assert result.primary.window_minutes == 300
    assert result.primary.resets_at is not None
    assert result.secondary is not None
    assert result.secondary.remaining_percent == 40
    assert result.credits_balance == "12.50"
    assert result.cloud_lifetime_tokens == 321
    assert result.diagnostics == []


def test_app_server_keeps_successes_when_one_endpoint_fails_and_another_times_out() -> None:
    command = _server_command(
        r"""
        import json
        import sys
        import time

        json.loads(sys.stdin.readline())
        print(json.dumps({"id": 1, "result": {}}), flush=True)
        json.loads(sys.stdin.readline())
        [json.loads(sys.stdin.readline()) for _ in range(3)]

        print(json.dumps({"id": 2, "result": {"account": {"type": "apiKey"}}}), flush=True)
        print(json.dumps({
            "id": 3,
            "error": {
                "code": -32600,
                "message": "ChatGPT required; private@example.test C:\\private\\prompt tool-arguments"
            }
        }), flush=True)
        time.sleep(2)
        """
    )

    result = CodexAppServerClient(command).read_snapshot(timeout_seconds=0.25)

    assert result.account is not None
    assert result.account.account_type == "apiKey"
    assert result.primary is None
    assert result.cloud_lifetime_tokens is None
    diagnostics = "\n".join(result.diagnostics)
    assert "account/rateLimits/read is unavailable for this account type" in diagnostics
    assert "account/usage/read timed out" in diagnostics
    assert "private@example.test" not in diagnostics
    assert "C:\\private" not in diagnostics
    assert "prompt" not in diagnostics
    assert "tool-arguments" not in diagnostics


def test_windows_cli_shim_is_preferred_over_windowsapps_executable(monkeypatch) -> None:
    paths = {
        "codex.cmd": r"C:\Users\test\AppData\Roaming\npm\codex.cmd",
        "codex.exe": r"C:\Program Files\WindowsApps\OpenAI.Codex\codex.exe",
        "codex": None,
    }
    monkeypatch.delenv("CODEXU_CODEX_EXECUTABLE", raising=False)
    monkeypatch.setattr(
        "codexu_win.data.app_server.shutil.which",
        lambda name: paths.get(name),
    )
    monkeypatch.setattr(
        "codexu_win.data.app_server.Path.is_file",
        lambda _path: True,
    )

    command = _resolve_codex_command()

    assert command is not None
    assert command[0].lower().endswith("cmd.exe")
    assert "codex.cmd" in command[-1]
