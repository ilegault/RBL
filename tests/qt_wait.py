"""Shared Qt wait helper: spin the event loop until a condition holds."""
from __future__ import annotations

import time
from typing import Callable

from PySide6.QtWidgets import QApplication


def wait_until(predicate: Callable[[], bool], timeout_s: float = 30.0) -> bool:
    app = QApplication.instance()
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        if app is not None:
            app.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    if app is not None:
        app.processEvents()
    return predicate()
