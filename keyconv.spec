# -*- mode: python ; coding: utf-8 -*-
# keyconv.spec — بناء ملف واحد KeyConv.exe (الواجهة + الخدمة + الأيقونة).
#
#   python -m PyInstaller --noconfirm --clean keyconv.spec
#
# الناتج: dist\KeyConv.exe

from PyInstaller.utils.hooks import collect_submodules

hiddenimports = collect_submodules("pynput")

a = Analysis(
    ["keyconv.py"],
    pathex=[],
    binaries=[],
    datas=[("assets/keyconv.ico", "assets")],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="KeyConv",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/keyconv.ico",
    version="version_info.txt",
)
