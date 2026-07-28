import os

# Run Qt headlessly during tests so no real windows are opened. Set before any
# PySide6 import so the offscreen platform plugin is selected.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

