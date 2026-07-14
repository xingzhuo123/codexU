# codexU for Windows

This directory contains the Windows implementation of codexU. It is a local-first
PySide6 desktop app for viewing Codex and Claude Code quota, token usage, trends,
projects, tools, Skills, and today's tasks.

## Requirements

- Windows 10 or Windows 11, x64
- Python 3.11 or newer for source builds
- Codex CLI installed and used at least once

The packaged build does not require Python.

## Run from source

```powershell
.\windows\setup.ps1
.\windows\run.ps1
```

Use deterministic sample data for UI review:

```powershell
.\windows\run.ps1 --demo
```

Print aggregate-only diagnostics without task titles or local paths:

```powershell
.\windows\run.ps1 --dump-json
```

## Test and package

```powershell
.\windows\test.ps1
.\windows\build.ps1
```

The portable build is written to `windows/dist/codexU-Windows-portable.zip` with
a SHA-256 checksum. Build output is intentionally excluded from Git.

## Local data and privacy

codexU reads only allow-listed aggregate fields from:

- `%USERPROFILE%\.codex\state_5.sqlite`
- `%USERPROFILE%\.codex\sessions\**\*.jsonl`
- `%USERPROFILE%\.codex\automations\**\automation.toml`
- `%USERPROFILE%\.claude\projects\**\*.jsonl`
- `%USERPROFILE%\.claude\tasks\**\*.json`
- the local `codex app-server` process

It does not upload usage, sessions, prompts, responses, tool arguments, account
tokens, or local paths. Projects are exposed to the UI by display name and a
local hash rather than full path.

## Optional Claude Code quota snapshot

Claude Code transcripts provide local token history but not active 5-hour and
7-day quota windows. Source users can configure
`scripts/claude_statusline_bridge.py` as a Claude Code statusline command. The
portable ZIP includes `tools\codexU-claude-bridge.exe` for the same purpose
without requiring Python. The bridge reads statusline JSON on stdin and stores
only `used_percentage`, `resets_at`, and capture time in the codexU local cache.
It does not store the rest of the statusline payload.
