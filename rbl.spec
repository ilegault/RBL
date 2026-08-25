# rbl.spec — PyInstaller spec for the RBL PySide6 desktop application
#
# Build:
#   cd C:\Users\IGLeg\PycharmProjects\RBL
#   .venv\Scripts\activate
#   pyinstaller rbl.spec --clean
#
# Output: dist\RBL\RBL.exe  (one-folder distribution)
# Distribute the entire dist\RBL\ folder to the target machine.

import os
from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

# ---------------------------------------------------------------------------
# Key paths
# ---------------------------------------------------------------------------
ROOT     = os.path.abspath(SPECPATH)          # C:\...\RBL
RBL_PKG  = os.path.join(ROOT, "rbl")          # C:\...\RBL\rbl
RBL_GUI  = os.path.join(RBL_PKG, "gui")       # C:\...\RBL\rbl\gui

# ---------------------------------------------------------------------------
# Data files only — PyInstaller's built-in hooks handle binaries/submodules
# for PySide6, scipy, matplotlib automatically via the dependency graph.
# We just need to ensure data files (fonts, styles, .pyi stubs) are copied.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# vendor\ installers — SHIPPED BESIDE THE APP, NOT INSIDE IT.
#
# These used to be added as datas, which buries them in dist\RBL\_internal\
# vendor\ — i.e. INSIDE the PyInstaller archive.  That is the same shape that
# got this app quarantined once already with ffmpeg (see the note below), and
# it is worse here, because vendor\ now holds:
#
#   LabJackBasic_*.exe   18 MB third-party driver installer
#   ni-visa_*_online.exe  9 MB ONLINE installer - a stub that downloads and
#                         runs more code from the internet at first launch
#
# What a scanner sees statically is then: an unsigned executable that carries
# other executables inside it, one of which is a network downloader, plus code
# (driver.py: ShellExecuteW "runas") that launches them ELEVATED.  Read
# without context that is the textbook description of a dropper, and no amount
# of it being true and well-intentioned changes what the heuristic matches.
#
# Nothing is lost by moving them out.  driver._candidate_dirs() looks in
# exe_dir\vendor\ BEFORE sys._MEIPASS\vendor\, so installers sitting next to
# RBL.exe are found first and one-click driver install behaves identically.
# build.bat copies vendor\ into dist\RBL\ after PyInstaller finishes, and the
# USB deploy picks it up with the rest of the folder.
#
# This is a real reduction in what the app IS - it no longer contains other
# programs - not a way of hiding what it does.  If IT still objects, the right
# answer is to drop vendor\ entirely and have them install the LabJack and
# NI-VISA drivers once, system-wide; the app already degrades gracefully.
# ---------------------------------------------------------------------------
all_datas = (
    collect_data_files("matplotlib")   # fonts, style sheets, matplotlibrc
    + collect_data_files("scipy")      # .pyi stubs, cython data
)

# ---------------------------------------------------------------------------
# ffmpeg — DELIBERATELY NOT BUNDLED.
#
# A 100 MB unsigned static ffmpeg.exe, bundled inside an unsigned PyInstaller
# app and spawned as a hidden subprocess (CREATE_NO_WINDOW), trips antivirus
# heuristics.  Lab IT quarantined the app because of it.  So the lab build
# ships no ffmpeg and keeps .avi only — video_transcoder.find_ffmpeg() returns
# None, the transcode queue is never started, and the UI says
# "ffmpeg not found — keeping .avi only".  This path is supported by design.
#
# MP4s for sharing are produced offline instead:  converter\avi2mp4.py on a
# personal machine.  ffmpeg.exe lives in converter\ and is never seen here.
#
# If IT ever installs ffmpeg system-wide, find_ffmpeg() already falls back to
# shutil.which("ffmpeg") and transcoding turns itself back on — no rebuild.
#
# To deliberately re-bundle it (NOT for lab deployment), put ffmpeg.exe back in
# tools\ and build with  set RBL_BUNDLE_FFMPEG=1
# ---------------------------------------------------------------------------
_ffmpeg = os.path.join(ROOT, "tools", "ffmpeg.exe")
_bundle_ffmpeg = os.environ.get("RBL_BUNDLE_FFMPEG") == "1"
all_binaries = (
    [(_ffmpeg, ".")] if (_bundle_ffmpeg and os.path.isfile(_ffmpeg)) else []
)
if all_binaries:
    print("*** WARNING: bundling ffmpeg.exe — antivirus will likely flag this "
          "build.  Do not deploy to the lab machine. ***")

# ---------------------------------------------------------------------------
# Hidden imports — ONLY what's actually used in this codebase:
#   main.py        → numpy, yaml, json, csv, matplotlib.use("Agg")
#   current_tab.py → matplotlib.use("QtAgg"), FigureCanvasQTAgg
#   optimizer.py   → scipy.optimize.differential_evolution
#   app.py, tabs   → PySide6.QtWidgets/Core/Gui
#
# Do NOT use collect_submodules() here — it pulls in hundreds of test files
# and unused backends (gtk, wx, tkinter, macosx, sphinx...) that bloat the
# bundle massively.
#
# Do NOT use collect_all("PySide6") — it triggers hook-PySide6.QtQml which
# crashes on Python 3.14 + PyInstaller 6.x.
# ---------------------------------------------------------------------------
all_hiddenimports = [
    # PySide6 — only modules actually imported in source
    "PySide6.QtWidgets",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtPrintSupport",      # required by matplotlib toolbar
    "PySide6.QtSvg",               # SVG icon support
    "PySide6.QtOpenGL",            # needed by QtOpenGLWidgets
    "PySide6.QtOpenGLWidgets",     # needed by matplotlib qtagg canvas

    # Matplotlib — only the backends this app calls matplotlib.use() with
    "matplotlib.backends.backend_qtagg",   # current_tab: matplotlib.use("QtAgg")
    "matplotlib.backends.backend_agg",     # main.py:     matplotlib.use("Agg")
    "matplotlib.backends.backend_qt",      # shared Qt backend base
    "mpl_toolkits.mplot3d",                # needed for 3D axes (viz.py)

    # scipy — only what optimizer.py actually imports
    "scipy.optimize",
    "scipy.optimize._differentialevolution",
    "scipy.linalg.cython_blas",    # often needed at runtime by scipy internals
    "scipy.linalg.cython_lapack",

    # Hardware drivers (gracefully absent at runtime if not installed)
    "labjack",
    "labjack.ljm",

    # yaml — main.py uses yaml.safe_load for --config mode
    "yaml",

    # opencv-python — USB camera capture in camera_source.py
    # The import is guarded (try/except ImportError) so the app still runs
    # without a camera if cv2 is absent, but include it in the bundle when
    # it is installed.
    "cv2",

    # pyserial — RS-232 transport for XGS-600, VGC083, TDS 2012
    "serial",
    "serial.tools",
    "serial.tools.list_ports",

    # scipy curve_fit — used by profile_fwhm.gaussian_fwhm_fit (optional path)
    "scipy.optimize._minpack_py",   # curve_fit implementation
]

# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
a = Analysis(
    # Entry point
    [os.path.join(RBL_PKG, "main.py")],

    # pathex tells PyInstaller where to look for imports:
    #   ROOT      → finds the "rbl" package  (from rbl.gui.app import ...)
    #   RBL_PKG   → finds top-level modules inside rbl/ (when main.py path-inserts itself)
    #   RBL_GUI   → finds viz, motor_tab, current_tab  (app.py path-inserts rbl/gui/)
    pathex=[ROOT, RBL_PKG, RBL_GUI],

    binaries=all_binaries,
    datas=all_datas,
    hiddenimports=all_hiddenimports,

    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],

    excludes=[
        # UI toolkits not used
        "tkinter", "_tkinter",
        "PyQt5", "PyQt6", "PySide2",
        "wx",

        # Unused PySide6 submodules whose hooks crash or bloat the bundle
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.QtQuickWidgets",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebEngineQuick",
        "PySide6.Qt3DCore",
        "PySide6.Qt3DRender",
        "PySide6.Qt3DInput",
        "PySide6.Qt3DLogic",
        "PySide6.Qt3DAnimation",
        "PySide6.Qt3DExtras",
        "PySide6.QtLocation",
        "PySide6.QtGraphs",
        "PySide6.QtBluetooth",
        "PySide6.QtNfc",
        "PySide6.scripts.deploy_lib",
        "PySide6.QtMultimedia",
        "PySide6.QtMultimediaWidgets",
        "PySide6.QtSql",

        # Matplotlib backends not used by this app
        "matplotlib.backends.backend_gtk3agg",
        "matplotlib.backends.backend_gtk3cairo",
        "matplotlib.backends.backend_gtk4agg",
        "matplotlib.backends.backend_gtk4cairo",
        "matplotlib.backends.backend_tkagg",
        "matplotlib.backends.backend_tkcairo",
        "matplotlib.backends.backend_wxagg",
        "matplotlib.backends.backend_wxcairo",
        "matplotlib.backends.backend_macosx",
        "matplotlib.backends.backend_nbagg",
        "matplotlib.backends.backend_webagg",
        "matplotlib.sphinxext",
        "matplotlib.testing",

        # Large packages not used by this app
        "pandas",
        "pyarrow",
        "IPython",
        "jupyter",
        "notebook",
        "streamlit",
        "tornado",
        "pytest",
        "sphinx",
        "docutils",

    ],

    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# ---------------------------------------------------------------------------
# PYZ
# ---------------------------------------------------------------------------
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ---------------------------------------------------------------------------
# EXE
# console=False → no terminal window behind the GUI
# console=True  → shows log output (helpful during first-deploy testing)
# ---------------------------------------------------------------------------
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="RBL",
    # ---- VERSION RESOURCE: OFF BY DEFAULT, AND THAT IS DELIBERATE ----------
    #
    # This is the ONLY change in this session that alters the structure of the
    # .exe itself. Everything else was Python source, which adds no imports,
    # no DLLs and no new strings, and therefore cannot move an ML score.
    #
    # It was added on the reasoning that "unsigned AND anonymous" scores worse
    # than "unsigned but clearly identified". That reasoning is not free: an
    # unsigned binary that CLAIMS an organisational identity is also the exact
    # shape of metadata-spoofing malware, and which way a model reads it is an
    # empirical question, not a deducible one. It was asserted, not tested.
    #
    # So it defaults OFF -- matching the build that was passing -- and is a
    # one-variable A/B you can run yourself:
    #
    #     pyinstaller rbl.spec --clean                      -> no resource
    #     set RBL_VERSION_RESOURCE=1 && pyinstaller rbl.spec --clean  -> with
    #
    # Scan both. Whichever passes, keep, and write the answer down here.
    # ------------------------------------------------------------------------
    version=("version_info.txt"
             if os.environ.get("RBL_VERSION_RESOURCE") == "1" else None),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,     # flip to True if you want a console log window
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# ---------------------------------------------------------------------------
# COLLECT — one-folder distribution
# dist\RBL\RBL.exe is the single entry point; the folder must stay together
# ---------------------------------------------------------------------------
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="RBL",
)
