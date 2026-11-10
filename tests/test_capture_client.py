"""CaptureClient against the real fake-camera Capture Process (Seam B)."""
from __future__ import annotations

import csv
import os
import time

import pytest
from PySide6.QtWidgets import QApplication

from rbl.config.capture_config import CAPTURE_SHUTDOWN_WAIT_S
from rbl.hardware.cup_status import CupPosition
from rbl.services.capture_client import CaptureClient
from rbl.snapshots import CupActuationState
from tests.qt_wait import wait_until


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def client(qapp):
    c = CaptureClient()
    yield c
    c.shutdown()


class Spy:
    def __init__(self, c: CaptureClient) -> None:
        self.hello: list = []
        self.opened: list = []
        self.status: list = []
        self.preview: list = []
        self.errors: list = []
        self.exits: list = []
        self.replies: list = []
        c.hello.connect(self.hello.append)
        c.opened.connect(self.opened.append)
        c.status.connect(self.status.append)
        c.preview.connect(lambda img, f: self.preview.append((img, f)))
        c.error.connect(self.errors.append)
        c.exited.connect(self.exits.append)
        c.reply.connect(lambda i, m: self.replies.append((i, m)))


def test_hello_open_status_preview(client):
    spy = Spy(client)
    client.start("fake")
    assert wait_until(lambda: spy.hello)
    client.open()
    assert wait_until(lambda: spy.opened)
    assert spy.opened[0]["serial"] == "FAKE0001"
    assert wait_until(lambda: spy.status and spy.preview)
    img, fields = spy.preview[0]
    assert not img.isNull()
    assert (img.width(), img.height()) == (fields["width"], fields["height"])
    assert "jpeg_b64" not in fields
    assert client.is_open()


def test_heartbeat_keeps_the_child_alive(client):
    spy = Spy(client)
    client.start("fake", heartbeat_timeout_s=2)
    assert wait_until(lambda: spy.hello)
    wait_until(lambda: False, timeout_s=5)
    assert client.is_running()
    assert not spy.exits


def test_cup_position_is_sent_only_on_change(client, tmp_path):
    spy = Spy(client)
    client.start("fake")
    assert wait_until(lambda: spy.hello)
    client.open()
    assert wait_until(lambda: spy.opened)
    client.apply(roi={"x": 0, "y": 0, "width": 64, "height": 32})
    assert wait_until(lambda: len(spy.replies) >= 2)
    client.start_recording(str(tmp_path), "2026-01-01T00:00:00+00:00", 0, 0)
    assert wait_until(lambda: any(
        i for i, m in spy.replies if m.get("type") == "ok" and i > 2) or spy.errors)

    def state(**kw):
        return CupActuationState(connected=True, **kw)

    results = []
    for s in (state(confirmed=CupPosition.IN),) * 3:
        results.append(client.on_cup_actuation_state(s))
        wait_until(lambda: False, timeout_s=0.7)
    results.append(client.on_cup_actuation_state(state(confirmed=CupPosition.OUT)))
    wait_until(lambda: False, timeout_s=0.7)
    results.append(client.on_cup_actuation_state(
        state(confirmed=CupPosition.OUT, stale=True)))
    wait_until(lambda: False, timeout_s=0.7)
    client.stop_recording()
    assert wait_until(lambda: len(list(tmp_path.rglob("frames.csv"))) > 0)
    wait_until(lambda: False, timeout_s=1.0)

    assert [r is not None for r in results] == [True, False, False, True, True]
    log = next(tmp_path.rglob("frames.csv"))
    with open(log, newline="") as fh:
        # The frame log opens with "#" comment lines (spec: Frame log); DictReader
        # would otherwise take the first comment as the header row.
        body = (line for line in fh if not line.startswith("#"))
        col = [row["cup_position"] for row in csv.DictReader(body)]
    collapsed = [v for i, v in enumerate(col) if i == 0 or v != col[i - 1]]
    assert collapsed[-3:] == ["IN", "OUT", "UNKNOWN"]
    it = iter(["UNKNOWN", "IN", "OUT", "UNKNOWN"])
    assert all(any(v == w for w in it) for v in collapsed)


def test_shutdown_and_unexpected_exit(qapp):
    c1 = CaptureClient()
    spy1 = Spy(c1)
    c1.start("fake")
    assert wait_until(lambda: spy1.hello)
    t0 = time.monotonic()
    c1.shutdown()
    assert time.monotonic() - t0 < CAPTURE_SHUTDOWN_WAIT_S + 2
    wait_until(lambda: spy1.exits)
    assert spy1.exits == [0]
    assert spy1.errors == []

    c2 = CaptureClient()
    spy2 = Spy(c2)
    try:
        c2.start("fake")
        assert wait_until(lambda: spy2.hello)
        import signal
        os.kill(spy2.hello[0]["pid"], signal.SIGTERM)
        assert wait_until(lambda: spy2.exits)
        assert wait_until(lambda: any("exited unexpectedly" in e for e in spy2.errors))
    finally:
        c2.shutdown()


def test_missing_dll_launches_nothing(client, monkeypatch, tmp_path):
    missing = tmp_path / "nope" / "ASICamera2.dll"
    monkeypatch.setenv("RBL_ZWO_SDK_DLL", str(missing))
    spy = Spy(client)
    client.start("zwo")
    assert spy.errors == [f"ZWO SDK library not found: {missing}"]
    assert not client.is_running()
