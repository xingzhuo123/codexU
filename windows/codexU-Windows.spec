# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path


root = Path.cwd()
src = root / "src"
assets = root / "assets"

a = Analysis(
    [str(src / "codexu_win" / "__main__.py")],
    pathex=[str(src)],
    binaries=[],
    datas=[(str(assets), "assets")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtMultimedia", "PySide6.QtWebEngineCore"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="codexU",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(assets / "codexU.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="codexU-Windows",
)
