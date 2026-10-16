"""
tests/test_session_cup_log.py
A session opens the cup log and offers to continue the previous dose (ticket 32, spec C6).

Real MainWindow, real SessionRecorder, real CupLog and CupSessionWriter files on disk.
Only the two operator questions (`_ask_continue_dose`, `_confirm_stop_automatic`) and the
camera (absent) are stand-ins.
"""
import os
import time
from datetime import datetime
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from rbl.services.cup_log import ContinueChoice, CupLogKind
from rbl.services.cup_session_writer import CupSessionWriter


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(tmp_path, qapp, monkeypatch):
    import rbl.config.paths as paths
    import rbl.services.cup_log as cup_log_mod
    import rbl.services.session_recorder as sr
    from rbl.gui.app import MainWindow

    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")
    # Pin the recorder's folder explicitly: several tests in test_session_recorder.py
    # assign `_logs_dir` without restoring it, and it would otherwise leak into this
    # file when both run in one xdist worker.
    (tmp_path / "logs").mkdir(exist_ok=True)
    monkeypatch.setattr(sr, "_logs_dir", lambda: str(tmp_path / "logs"))
    monkeypatch.setattr(cup_log_mod, "FARADAY_CUP_DIR", tmp_path / "cup_tests")
    w = MainWindow()
    yield w
    if w.session_recorder.is_recording():
        w.session_recorder.stop(force=True)
    w.close()


def _earlier_session(logs: Path, name: str, charge_c: float = 3e-6) -> Path:
    folder = logs / name
    w = CupSessionWriter(session_id="cup", output_dir=folder)
    w.write_insertion_summary(
        run_id=1,
        commanded_timestamp=1_700_000_000.0,
        confirmed_timestamp=1_700_000_010.0,
        dwell=10.0,
        sample_count=5,
        mean_current_a=2e-9,
        std_current_a=1e-11,
        beam_on_seconds=10.0,
        charge=charge_c,
        origin="automatic",
        counted_in_dose=True,
        total_beam_on_s=10.0,
    )
    w.close()
    return folder / "cup.csv"


def _answers(win, monkeypatch, choice):
    calls = []

    def _ask(totals):
        calls.append(totals)
        return choice

    monkeypatch.setattr(win, "_ask_continue_dose", _ask)
    return calls


def test_session_start_opens_cup_csv_and_closes_test_log_untouched(win, monkeypatch):
    _answers(win, monkeypatch, None)
    test_writer = win.cup_log.open_test(datetime(2026, 1, 2, 3, 4, 5))
    test_path = Path(test_writer.csv_path)
    before = test_path.read_bytes()

    assert win.session_recorder.start()

    folder = Path(win.cup_log.path).parent
    assert win.cup_log.kind is CupLogKind.SESSION
    assert Path(win.cup_log.path).name == "cup.csv"
    assert (folder / "events.csv").is_file()  # the same folder the recorder just made
    assert (folder / "cup.csv").is_file()
    assert test_path.read_bytes() == before


def test_session_stop_closes_cup_log(win, monkeypatch):
    _answers(win, monkeypatch, None)
    win.session_recorder.start()
    assert win.cup_log.path is not None
    win.session_recorder.stop()
    assert win.cup_log.path is None
    assert not win.session_recorder.is_recording()


def test_continue_seeds_tab_and_writes_header(win, monkeypatch, tmp_path):
    prev = _earlier_session(tmp_path / "logs", "session_20200101_000000")
    calls = _answers(win, monkeypatch, ContinueChoice(True, True))

    win.session_recorder.start()

    assert len(calls) == 1
    assert calls[0].total_charge_c == pytest.approx(3e-6)
    assert Path(calls[0].source_path) == prev
    assert win.faraday_cup_tab.lbl_running_q.text() == "3.000000e-06 C"
    header = Path(win.cup_log.path).read_text(encoding="utf-8")
    assert f"# dose continued from: {prev}" in header
    assert "beam_on_during_gap=" in header


def test_declining_continue_starts_at_zero(win, monkeypatch, tmp_path):
    _earlier_session(tmp_path / "logs", "session_20200101_000000")
    calls = _answers(win, monkeypatch, None)
    win.session_recorder.start()
    assert len(calls) == 1
    assert win.faraday_cup_tab.lbl_running_q.text() == "0.000000e+00 C"
    assert "dose continued from" not in Path(win.cup_log.path).read_text(encoding="utf-8")


def test_no_earlier_counted_session_does_not_ask(win, monkeypatch, tmp_path):
    # An earlier session with no counted row has nothing to continue from.
    folder = tmp_path / "logs" / "session_20200101_000000"
    CupSessionWriter(session_id="cup", output_dir=folder).close()
    calls = _answers(win, monkeypatch, ContinueChoice(True, True))
    win.session_recorder.start()
    assert calls == []


def _run_automatic(win):
    win.faraday_cup_tab.cycle.arm(t=time.time())
    assert win.faraday_cup_tab.automatic_insertion_running()


def test_stop_declined_keeps_session_and_cup_log(win, monkeypatch):
    _answers(win, monkeypatch, None)
    win.session_recorder.start()
    _run_automatic(win)
    monkeypatch.setattr(win.faraday_cup_tab, "_confirm_stop_automatic", lambda: False)

    win.session_recorder.stop()

    assert win.session_recorder.is_recording()
    assert win.cup_log.path is not None
    assert win.faraday_cup_tab.automatic_insertion_running()


def test_stop_confirmed_stops_session_and_closes_cup_log(win, monkeypatch):
    _answers(win, monkeypatch, None)
    win.session_recorder.start()
    _run_automatic(win)
    monkeypatch.setattr(win.faraday_cup_tab, "_confirm_stop_automatic", lambda: True)

    win.session_recorder.stop()

    assert not win.session_recorder.is_recording()
    assert win.cup_log.path is None
    assert not win.faraday_cup_tab.automatic_insertion_running()


def test_start_declined_while_test_log_runs_automatic_insertion(win, monkeypatch):
    _answers(win, monkeypatch, None)
    win.cup_log.open_test(datetime(2026, 1, 2, 3, 4, 5))
    _run_automatic(win)
    monkeypatch.setattr(win.faraday_cup_tab, "_confirm_stop_automatic", lambda: False)

    assert win.session_recorder.start() is False

    assert not win.session_recorder.is_recording()
    assert win.cup_log.kind is CupLogKind.TEST
    assert win.faraday_cup_tab.automatic_insertion_running()


def test_start_confirmed_while_test_log_runs_automatic_insertion(win, monkeypatch):
    _answers(win, monkeypatch, None)
    win.cup_log.open_test(datetime(2026, 1, 2, 3, 4, 5))
    _run_automatic(win)
    monkeypatch.setattr(win.faraday_cup_tab, "_confirm_stop_automatic", lambda: True)

    assert win.session_recorder.start() is True
    assert win.cup_log.kind is CupLogKind.SESSION
    assert not win.faraday_cup_tab.automatic_insertion_running()


def test_disk_auto_stop_skips_both_guards(tmp_path, qapp, monkeypatch):
    """Drive the real segment-close path with no free disk; no guard may be consulted."""
    import rbl.services.session_recorder as sr
    from tests.test_session_recorder import _make_open_camera
    from tests.test_video_recorder import _fake_frame, _patch_cv2

    _patch_cv2(monkeypatch, [])
    monkeypatch.setattr(sr, "_logs_dir", lambda: str(tmp_path))
    cam = _make_open_camera()
    rec = sr.SessionRecorder(lambda: {}, cam)
    rec.set_record_fps(2)
    rec.set_segment_seconds(1)
    guard_calls = []
    rec.set_start_guard(lambda: guard_calls.append("start") or True)
    rec.set_stop_guard(lambda: guard_calls.append("stop") or False)
    assert rec.start()
    assert rec.start_video()
    guard_calls.clear()
    monkeypatch.setattr(sr.shutil, "disk_usage", lambda _p: type("U", (), {"free": 0})())

    for i in range(6):  # a frame two seconds after the last closes a one-second segment
        cam.frame_ready.emit(_fake_frame(), time.perf_counter() + 2.0 * i)

    assert not rec.is_recording()
    assert guard_calls == []


def test_stop_force_skips_the_stop_guard_but_plain_stop_honours_it(tmp_path, qapp, monkeypatch):
    import rbl.services.session_recorder as sr
    from tests.test_session_recorder import _make_open_camera

    monkeypatch.setattr(sr, "_logs_dir", lambda: str(tmp_path))
    rec = sr.SessionRecorder(lambda: {}, _make_open_camera())
    rec.set_stop_guard(lambda: False)
    assert rec.start()
    rec.stop()
    assert rec.is_recording()
    rec.stop(force=True)
    assert not rec.is_recording()


@pytest.mark.parametrize("beam_on", [False, True])
def test_continue_dose_gap_answer_decides_the_next_hold_interval(
    qapp, tmp_path, monkeypatch, beam_on
):
    """Seed the tab as a session would, then run one insertion and read the cup file.

    The earlier session's last withdrawal is at t=100 and 'now' is t=700. With the beam
    off, that 600 s lies inside the next insertion's hold interval and is excluded; with
    the beam on it counts. The two files differ by exactly the gap, 600 s.
    """
    from rbl.hardware.cup_status import CupPosition
    from rbl.hardware.dose_model import DoseTotals
    from tests.test_cup_stop_restart import Bench

    totals = DoseTotals(
        total_charge_c=3e-6, total_beam_on_s=10.0, insertion_count=1,
        last_out_t=100.0, last_current_a=2e-9, source_path="earlier/cup.csv",
    )
    results = {}
    for flag in (beam_on, not beam_on):
        b = Bench(qapp, tmp_path / str(flag), monkeypatch)
        try:
            b.state(700.0, CupPosition.OUT, CupPosition.OUT)
            b.tab.continue_dose(totals, flag)
            b.start_at(700.0)
            b.insertion(1000.0, 1003.0)
            results[flag] = b.rows("insertion_summary")[-1]
        finally:
            b.close()
    held, excluded = results[True], results[False]
    gap_s = float(held["beam_on_seconds"]) - float(excluded["beam_on_seconds"])
    assert gap_s == pytest.approx(600.0)
    assert float(excluded["charge"]) > 3e-6  # the seeded total is carried either way
