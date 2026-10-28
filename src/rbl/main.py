"""
Entry point for the Right Beam Line DAQ app and the Capture Process.

Usage:
    python -m rbl.main                    # launch PySide6 GUI application
    python -m rbl.main --capture-process  # launch the Qt-free Capture Process child

WHY THIS EXISTS
---------------
ADR 0008 decision 2 establishes that the Capture Process is RBL.exe relaunched in
a capture mode, rather than a separate executable, to avoid Windows Defender machine-
learning quarantine risks on the lab PC. When launched with ``--capture-process``,
this entry point immediately dispatches to ``rbl.capture.process`` without importing
Qt (PySide6) or any GUI modules, preserving the strict isolation required by ADR 0008
decision 3.
"""
from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> None:
    if argv is None:
        argv = sys.argv[1:]

    if "--capture-process" in argv:
        from rbl.capture.process import main as capture_main

        filtered_argv = [arg for arg in argv if arg != "--capture-process"]
        rc = capture_main(filtered_argv)
        raise SystemExit(rc)

    from rbl.gui.app import main as app_main

    app_main()


if __name__ == "__main__":
    main()
