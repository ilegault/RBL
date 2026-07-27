"""
Entry point for the Raster Scan Analysis Tool.

Usage:
    python main.py              # launch PySide6 GUI (default)
    python main.py --validate   # run validation suite
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import argparse


def run_gui():
    from rbl.gui.app import main as app_main
    app_main()


def run_validate():
    pass


def main():
    parser = argparse.ArgumentParser(description="Raster Scan Analysis Tool")
    parser.add_argument("--validate", action="store_true", help="Run validation suite")
    args = parser.parse_args()

    if args.validate:
        import subprocess
        result = subprocess.run(
            [sys.executable, os.path.join(os.path.dirname(__file__), "config/validation.py")],
            cwd=os.path.dirname(__file__),
        )
        sys.exit(result.returncode)
    else:
        run_gui()


if __name__ == "__main__":
    main()
