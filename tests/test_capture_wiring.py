"""Acceptance tests for MainWindow wiring of CaptureClient (ticket 72)."""
from __future__ import annotations

import csv
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from rbl.config.capture_config import CAPTURE_SHUTDOWN_WAIT_S
from rbl.gui.app import MainWindow
from rbl.hardware.cup_status import CupPosition
from rbl.services.capture_client import CaptureClient
from rbl.snapshots import CupActuationState
from tests.qt_wait import wait_until


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_window_has_one_client_and_launches_nothing(qapp):
    win = MainWindow()
    try:
        assert isinstance(win.capture_client, CaptureClient)
        qapp.processEvents()
        assert not win.capture_client.is_running()
        assert win.session_recorder.camera_kind() == "usb"
    finally:
        win.close()


def test_cup_state_reaches_the_client(qapp, tmp_path: Path):
    win = MainWindow()
    try:
        win.capture_client.start("fake")
        win.capture_client.open()
        assert wait_until(lambda: win.capture_client.is_open(), timeout_s=10.0)
        win.capture_client.apply(roi={"x": 0, "y": 0, "width": 64, "height": 32})
        win.capture_client.start_recording(str(tmp_path), "2026-01-01T00:00:00+00:00")

        win.beamline.cup_actuation_changed.emit(
            CupActuationState(connected=True, confirmed=CupPosition.IN)
        )

        frames_csv = tmp_path / "zwo" / "frames.csv"

        def has_in_row() -> bool:
            if not frames_csv.exists():
                return False
            with open(frames_csv, newline="", encoding="utf-8") as fh:
                body = (line for line in fh if not line.startswith("#"))
                for row in csv.DictReader(body):
                    if row.get("cup_position") == "IN":
                        return True
            return False

        assert wait_until(has_in_row, timeout_s=10.0)
    finally:
        if hasattr(win, "capture_client") and win.capture_client.is_running():
            win.capture_client.shutdown()
        win.close()


def test_close_shuts_the_child_down(qapp):
    win = MainWindow()
    exits: list[int] = []
    hellos: list[dict] = []
    try:
        win.capture_client.hello.connect(hellos.append)
        win.capture_client.exited.connect(exits.append)
        win.capture_client.start("fake")
        assert wait_until(lambda: len(hellos) > 0, timeout_s=10.0)
        win.close()
        assert wait_until(
            lambda: exits == [0] and not win.capture_client.is_running(),
            timeout_s=CAPTURE_SHUTDOWN_WAIT_S + 2,
        )
    finally:
        if hasattr(win, "capture_client") and win.capture_client.is_running():
            win.capture_client.shutdown()
        win.close()
