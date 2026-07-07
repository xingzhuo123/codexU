# codexU v1.0.0-beta

这是 codexU 从“桌面常驻小组件”升级为标准 macOS App 的 beta 版本。主窗口现在是正常的 macOS 窗口，支持 Dock、系统窗口控制、最小化、关闭后继续在菜单栏运行，并保留菜单栏状态项和快捷键唤起能力。

## 主要更新

- 主界面升级为标准 macOS 窗口，不再默认常驻桌面底层。
- 保留菜单栏状态项，点击后展示 Codex / Claude Code Runtime 浮窗。
- 菜单栏浮窗新增设置入口，并提供打开主窗口、打开设置和退出应用。
- 支持在其他全屏 App 的当前 Space 中打开菜单栏浮窗。
- `Command + U` 现在用于显示或隐藏主窗口；窗口最小化时会恢复并唤到前台。
- 新增设置窗口，集中管理语言、外观、主窗口置顶和关闭主窗口后的运行行为。
- 主窗口恢复 Liquid Glass 材质和半透明质感，并优化标题栏工具区、窗口圆角、顶部间距和按钮尺寸。
- 语言切换、主题切换从主窗口顶部移入设置；PRO 状态不再常驻顶栏。
- 新增 Codex 与 Claude Code 彩色 Runtime 图标资源。
- 更新 README 截图、安装说明、构建说明和 CHANGELOG。

## 安装包

- Apple Silicon: `codexU-1.0.0-beta-mac-arm64.dmg`
- Intel: `codexU-1.0.0-beta-mac-x86_64.dmg`

## 校验

- `make build`
- `make release-all`
- `git diff --check`

SHA-256:

```text
b8dd63ec8d357880dabc1f0c3c7605d8c4f6e824198b5c073f4e2696f621a863  codexU-1.0.0-beta-mac-arm64.dmg
b9eef9aac350fcaf67a0ff03b5e3bf7442c9fd18503d4a0285b24e16625c035f  codexU-1.0.0-beta-mac-x86_64.dmg
```
