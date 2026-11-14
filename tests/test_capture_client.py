"""CaptureClient against the real fake-camera Capture Process (Seam B)."""
from __future__ import annotations

import ast
import csv
import logging
import os
import pathlib
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
    assert not client.is_ready()
    client.start("fake")
    assert wait_until(lambda: spy.hello)
    assert client.is_ready()
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


def test_missing_dll_launches_nothing(client, monkeypatch, tmp_path, caplog):
    missing = tmp_path / "nope" / "ASICamera2.dll"
    monkeypatch.setenv("RBL_ZWO_SDK_DLL", str(missing))
    spy = Spy(client)
    expected_text = (
        f"ZWO SDK library not found: {missing}. "
        "Copy ASICamera2.dll there or set RBL_ZWO_SDK_DLL."
    )
    with caplog.at_level(logging.ERROR, logger="rbl.capture"):
        client.start("zwo")
    assert spy.errors == [expected_text]
    assert not client.is_running()
    records = [
        r for r in caplog.records
        if r.name == "rbl.capture" and r.levelno == logging.ERROR
    ]
    assert any(r.getMessage() == expected_text for r in records)


def test_fatal_before_hello_is_reported_verbatim(client):
    spy = Spy(client)
    client.start("fake", extra_args=["--fake-fatal", "zwoasi.init failed: bad DLL"])
    assert wait_until(lambda: spy.exits)
    assert spy.errors == ["zwoasi.init failed: bad DLL"]
    assert spy.hello == []


def test_empty_camera_list_ends_the_attempt(client):
    spy = Spy(client)
    client.start("fake", extra_args=["--fake-cameras-empty"])
    assert wait_until(lambda: spy.exits)
    assert spy.errors == ["No ZWO camera detected on USB"]
    assert spy.hello == []
    assert spy.exits == [0]


def test_refusals_are_logged(client, tmp_path, caplog):
    spy = Spy(client)
    client.start("fake")
    assert wait_until(lambda: spy.hello)
    with caplog.at_level(logging.ERROR, logger="rbl.capture"):
        client.take_darks(str(tmp_path), 1)
        assert wait_until(lambda: spy.errors)
    error_text = spy.errors[-1]
    records = [
        r for r in caplog.records
        if r.name == "rbl.capture" and r.levelno == logging.ERROR
    ]
    assert any(r.getMessage() == error_text for r in records)
    assert client.is_running()


def test_only_fail_emits_error():
    client_py = (
        pathlib.Path(__file__).resolve().parents[1]
        / "src"
        / "rbl"
        / "services"
        / "capture_client.py"
    )
    tree = ast.parse(client_py.read_text(encoding="utf-8"))

    error_emit_calls = []

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.current_function = None

        def visit_FunctionDef(self, node):
            old = self.current_function
            self.current_function = node.name
            self.generic_visit(node)
            self.current_function = old

        def visit_AsyncFunctionDef(self, node):
            old = self.current_function
            self.current_function = node.name
            self.generic_visit(node)
            self.current_function = old

        def visit_Call(self, node):
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "emit"
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "error"
                and isinstance(node.func.value.value, ast.Name)
                and node.func.value.value.id == "self"
            ):
                error_emit_calls.append((self.current_function, node))
            self.generic_visit(node)

    v = Visitor()
    v.visit(tree)
    assert len(error_emit_calls) == 1
    assert error_emit_calls[0][0] == "_fail"


def test_no_hello_times_out_and_kills_the_child(qapp):
    c = CaptureClient(hello_timeout_s=0.3)
    spy = Spy(c)
    try:
        c.start("fake", extra_args=["--fake-no-hello"])
        assert wait_until(lambda: spy.exits, timeout_s=5.0)
        assert spy.errors == ["Capture Process sent no hello within 0.3 s"]
        assert not c.is_running()
    finally:
        c.shutdown()


def test_hello_in_time_cancels_the_hello_timeout(qapp):
    c = CaptureClient(hello_timeout_s=0.5)
    spy = Spy(c)
    try:
        c.start("fake")
        assert wait_until(lambda: spy.hello)
        wait_until(lambda: False, timeout_s=1.0)
        assert c.is_running()
        assert spy.errors == []
    finally:
        c.shutdown()


def test_open_reply_timeout_warns_and_keeps_the_child(qapp, caplog):
    c = CaptureClient(open_reply_timeout_s=0.3)
    spy = Spy(c)
    timed_outs: list[bool] = []
    c.open_timed_out.connect(lambda: timed_outs.append(True))
    try:
        with caplog.at_level(logging.WARNING, logger="rbl.capture"):
            c.start("fake", extra_args=["--fake-open-delay-s", "1.5"])
            assert wait_until(lambda: spy.hello)
            c.open()
            assert wait_until(lambda: len(timed_outs) == 1, timeout_s=3.0)
            assert not spy.opened
            assert c.is_running()
            assert spy.errors == []
            records = [
                r
                for r in caplog.records
                if r.name == "rbl.capture" and r.levelno == logging.WARNING
            ]
            assert any("No reply to open" in r.getMessage() for r in records)
            assert wait_until(lambda: spy.opened, timeout_s=3.0)
            assert len(timed_outs) == 1
    finally:
        c.shutdown()


def test_open_reply_in_time_does_not_time_out(qapp):
    c = CaptureClient(open_reply_timeout_s=1.0)
    spy = Spy(c)
    timed_outs: list[bool] = []
    c.open_timed_out.connect(lambda: timed_outs.append(True))
    try:
        c.start("fake")
        assert wait_until(lambda: spy.hello)
        c.open()
        assert wait_until(lambda: spy.opened)
        wait_until(lambda: False, timeout_s=1.5)
        assert not timed_outs
    finally:
        c.shutdown()


def test_timeout_defaults_come_from_config(qapp):
    from rbl.config.capture_config import (
        CAPTURE_HELLO_TIMEOUT_S,
        CAPTURE_OPEN_REPLY_TIMEOUT_S,
    )

    c = CaptureClient()
    assert c.hello_timeout_s == CAPTURE_HELLO_TIMEOUT_S == 10.0
    assert c.open_reply_timeout_s == CAPTURE_OPEN_REPLY_TIMEOUT_S == 15.0


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("2026-11-09 20:52:01,123 INFO [rbl.capture] x", logging.INFO),
        ("2026-11-09 20:52:01,123 DEBUG [rbl.capture] x", logging.DEBUG),
        ("2026-11-09 20:52:01,123 WARNING [rbl.capture] x", logging.WARNING),
        ("2026-11-09 20:52:01,123 ERROR [rbl.capture] x", logging.ERROR),
        ("Traceback (most recent call last):", logging.WARNING),
        ('  File "x.py", line 1', logging.WARNING),
        ("", logging.WARNING),
    ],
)
def test_child_log_level(line, expected):
    from rbl.services.capture_client import child_log_level

    assert child_log_level(line) == expected


def test_drain_lines_keeps_the_partial_tail():
    from rbl.services.capture_client import drain_lines

    buf = bytearray(b"ab")
    assert drain_lines(buf) == []
    buf += b"c\nde"
    assert drain_lines(buf) == [b"abc\n"]
    assert buf == bytearray(b"de")


def test_child_stderr_keeps_its_level_and_pid(client, caplog):
    spy = Spy(client)
    with caplog.at_level(logging.WARNING, logger="rbl.capture"):
        client.start("fake", heartbeat_timeout_s=0.3)
        assert wait_until(lambda: spy.hello)
        assert wait_until(lambda: spy.exits, timeout_s=5.0)
    pid = spy.hello[0]["pid"]
    matching = [
        r
        for r in caplog.records
        if r.name == "rbl.capture"
        and r.levelno == logging.WARNING
        and r.getMessage().startswith(f"[capture pid {pid}] ")
        and "Heartbeat timed out" in r.getMessage()
    ]
    assert len(matching) == 1


def test_open_sequence_is_narrated(client, caplog):
    spy = Spy(client)
    with caplog.at_level(logging.INFO, logger="rbl.capture"):
        client.start("fake")
        assert wait_until(lambda: spy.hello)
        client.open()
        assert wait_until(lambda: spy.opened)
        client.shutdown()
        assert wait_until(lambda: spy.exits)

    info_msgs = [
        r.getMessage()
        for r in caplog.records
        if r.name == "rbl.capture" and r.levelno == logging.INFO
    ]
    it = iter(info_msgs)
    assert any(m.startswith("Starting Capture Process:") for m in it)
    assert any(m.startswith("Capture Process started, pid ") for m in it)
    assert any(
        m.startswith("Capture Process hello: protocol 1, cameras ['Fake ASI']")
        for m in it
    )
    assert any(m.startswith("Sent open (id ") for m in it)
    assert any(
        m.startswith("Open reply ok (id ") and m.endswith("Fake ASI FAKE0001")
        for m in it
    )
    assert any(
        m.startswith("Capture Process exited: code 0")
        and m.endswith("expected True")
        for m in it
    )


def test_dll_resolution_is_logged(client, monkeypatch, tmp_path, caplog):
    missing = tmp_path / "nope" / "ASICamera2.dll"
    monkeypatch.setenv("RBL_ZWO_SDK_DLL", str(missing))
    expected = f"ZWO SDK DLL: {missing} (from RBL_ZWO_SDK_DLL: True, exists: False)"
    with caplog.at_level(logging.INFO, logger="rbl.capture"):
        client.start("zwo")
    records = [
        r
        for r in caplog.records
        if r.name == "rbl.capture" and r.levelno == logging.INFO
    ]
    assert any(r.getMessage() == expected for r in records)


def test_streaming_is_not_logged_per_message(client, caplog):
    spy = Spy(client)
    with caplog.at_level(logging.DEBUG, logger="rbl.capture"):
        client.start("fake")
        assert wait_until(lambda: spy.hello)
        client.open()
        assert wait_until(lambda: spy.preview)
        non_child_records = [
            r
            for r in caplog.records
            if r.name == "rbl.capture"
            and not r.getMessage().startswith("[capture pid ")
        ]
        count_before = len(non_child_records)
        assert count_before >= 5
        wait_until(lambda: False, timeout_s=2.0)
        count_after = len([
            r
            for r in caplog.records
            if r.name == "rbl.capture"
            and not r.getMessage().startswith("[capture pid ")
        ])
        assert count_after == count_before




