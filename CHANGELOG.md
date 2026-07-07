# Changelog

## 1.0.0-beta - 2026-07-08

- 将主界面升级为标准 macOS App 窗口，支持 Dock、系统红黄绿窗口控制、最小化，以及关闭主窗口后继续在菜单栏运行。
- 保留菜单栏状态项，并增强 Runtime 浮窗：新增设置入口，支持打开主窗口、打开设置和退出。
- `Command + U` 调整为显示/隐藏主窗口；窗口最小化时会恢复并唤到前台。
- 菜单栏浮窗支持在其他全屏 App 的当前 Space 中展示。
- 新增设置窗口，集中管理语言、外观、主窗口置顶和关闭行为；语言、主题和 PRO 状态不再常驻主窗口顶部。
- 恢复主窗口 Liquid Glass 材质和半透明质感，并优化标题栏工具区、窗口圆角、顶部间距和按钮尺寸。
- 新增 Codex 与 Claude Code 彩色 Runtime 图标资源，统一主窗口、菜单栏浮窗和 Runtime 切换控件的视觉。
- 更新 README 截图、安装说明和源码构建示例。

## 0.4.0 - 2026-07-07

- Added a multi-runtime usage architecture with Codex and Claude Code providers.
- Added Claude Code local transcript parsing for tokens, trends, projects, tool usage, Skill usage, and tasks.
- Added a menu bar runtime popover with Codex and Claude Code summary cards and total tokens today.
- Added a top-level Codex / Claude Code switch in the main widget.
- Added runtime-aware `--dump-json` output with `schemaVersion: 2`, `aggregate`, `runtimes[]`, and legacy Codex compatibility fields.
- Added local statusLine snapshot support for Claude Code active quota, with missing/stale diagnostics.

## 0.3.0 - 2026-07-04

- Reworked the lower dashboard into three tabs: today's task board, usage trend, and project board.
- Added a six-month daily token heatmap with local `token_count` event aggregation, fixed week-by-week matrix layout, percentile-based purple intensity levels, and per-day hover tooltips.
- Added a last-7-day line chart with total, daily average, and previous-period comparison.
- Added project usage rankings for the last 7 days and all time, with thread counts, recent activity, and detailed/approximate source labels.
- Added tool usage TOP10 with call counts, categories, and session-share token/value estimates.
- Added Skill usage TOP20 analytics based on local Skill load events.
- Added local analytics JSON output for trend, project, and tool data in `--dump-json`.
- Added foreground pin mode while keeping `Command + U` as a temporary foreground toggle.
- Fixed heatmap month labels so each month starts on the week column containing that month's first day.
- Documented the v0.3.0 product requirements in `docs/PRD-v0.3.0.md`.

## 0.2.0 - 2026-07-01

- Introduced the new Apple-inspired visual system with refined light and dark palettes, elevated surfaces, consistent control styling, and updated token colors.
- Added system, light, and dark appearance modes with a persistent top-level mode switch.
- Added detailed token parsing from local Codex `token_count` session events, including uncached input, cached input, output, and monthly API-equivalent value estimates.
- Redesigned the value progress card around Plus, Pro100, Pro200, and full monthly quota milestones.
- Simplified the quota area by moving reset times under the dual ring and removing redundant 5-hour and 7-day progress rows.
- Increased the widget height so task board rows have more room to render cleanly.
- Added explicit Intel Mac and Apple Silicon DMG packaging targets and documented x86_64 release artifacts.

## 0.1.4

- Added Chinese and English UI text support.
- Default language now follows the system time zone: Chinese for China/Hong Kong/Macau/Taiwan time zones, English otherwise.
- Added a top bar `中 | EN` language switch that persists the manual selection.

## 0.1.3

- Added the app icon to the widget header.
- Moved account status into a right-side pill next to the plan badge.
- Updated the README screenshot for the new header layout.

## 0.1.2

- Added local desktop widget UI for Codex quota, token usage, trend, and task board.
- Added `Command + U` foreground/desktop layer toggle.
- Added DMG packaging, checksum generation, signing hooks, and notarization helper.
- Added local data source probe command.
