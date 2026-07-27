"""
Entry point for the Right Beam Line DAQ app.

Usage:
    python -m rbl.main   # launch PySide6 GUI
"""


def main():
    from rbl.gui.app import main as app_main
    app_main()


if __name__ == "__main__":
    main()
