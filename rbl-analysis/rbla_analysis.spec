# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for RBL-Analysis — the hardware-free analysis app.

One-folder build (COLLECT). Hardware libraries are explicitly excluded so no
DAQ dependency can be pulled in transitively.
"""
from PyInstaller.utils.hooks import collect_all

datas = [('rbla/assets/*', 'rbla/assets')]
binaries = []
hiddenimports = []

# Bundle matplotlib and PySide6 data files / hidden imports (their standard
# hooks cover most of this; collect_all is belt-and-suspenders).
for _pkg in ('matplotlib', 'PySide6'):
    _d, _b, _h = collect_all(_pkg)
    datas += _d
    binaries += _b
    hiddenimports += _h

# Hardware libraries must never be bundled — this app is analysis-only.
EXCLUDES = [
    'pyvisa', 'pyvisa_py', 'usb', 'libusb_package',
    'labjack', 'labjack_ljm', 'serial',
]

a = Analysis(
    ['rbla/main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='RBL-Analysis',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='RBL-Analysis',
)
