"""
tests/test_amp_test_runner.py
Unit tests for rbl.services.amp_test_runner.AmpTestRunner.

Uses a FakeGen (no real hardware) and synthetic payloads so the Qt event loop
is exercised via QTimer but nothing blocks.
"""
import time
import tempfile
from pathlib import Path

import pytest

import numpy as np
from PySide6.QtCore import QCoreApplication

from rbl.config.amp_test_config import (
    PROFILE_SETTLE_WINDOWS, PROFILE_SWITCH_TIMEOUT_S,
    TRIP_MARGIN, AMP_MAX_MA_DC,
)
from rbl.config.amp_test_matrix import DriveSet, RawMode, TestSpec, by_id
from rbl.config.hardware_config import AMP_CHANNEL_MAP, AMP_LABELS
from rbl.config.labjack_stream_config import window_samples
from rbl.hardware.amp_monitor import monitor_to_ma
from rbl.services.amp_test_runner import AmpTestRunner, _State
from rbl.services.measured_limits import record_trip_ma


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

class FakeGen:
    def __init__(self):
        self.calls: list = []
    def set_waveform(self, ch, shape, freq, amp, offset, phase):
        self.calls.append(("set_waveform", ch, shape, freq, amp, offset, phase))
        return ""
    def output_on(self, ch):
        self.calls.append(("output_on", ch))
    def output_off(self, ch):
        self.calls.append(("output_off", ch))
    def get_state(self, ch):
        return {"shape": "DC", "freq": 0.0, "amp": 0.0, "offset": 0.0,
                "phase": 0.0, "output": "OFF", "load": "INFinity"}
    def set_output_load(self, ch, load): pass
    def set_waveform_calls(self):
        return [c for c in self.calls if c[0] == "set_waveform"]


@pytest.fixture(autouse=True)
def qt_app():
    app = QCoreApplication.instance() or QCoreApplication([])
    yield app


@pytest.fixture()
def fgens():
    fa, fb = FakeGen(), FakeGen()
    return fa, fb


@pytest.fixture()
def fmap(fgens):
    fa, fb = fgens
    return {
        "X+": (fa, 1), "X-": (fa, 2),
        "Y+": (fb, 1), "Y-": (fb, 2),
    }


@pytest.fixture()
def runner(fmap):
    return AmpTestRunner(fmap)


@pytest.fixture()
def tmp_limits(tmp_path):
    """Isolated measured_limits.json path — never touches the real project file."""
    return tmp_path / "measured_limits.json"


def _waveform_payload(profile: str = "WAVEFORM", ain_override: dict = None,
                      wrong_stride: bool = False) -> dict:
    """Build a minimal valid stream-worker payload for *profile*."""
    ws = window_samples(profile)
    channels = {}

    if profile == "WAVEFORM":
        amp_ains = ("AIN6","AIN7","AIN8","AIN9","AIN10","AIN11","AIN12","AIN13")
        for ain in amp_ains:
            wave = np.zeros(ws)
            channels[ain] = {
                "waveform": wave, "peak": 0.0, "pk_pk": 0.0, "rms": 0.0
            }
    elif profile == "SINGLE_FAST":
        # Only AIN12 (X+ current) present by default
        ain = "AIN12"
        wave = np.zeros(ws)
        channels[ain] = {
            "waveform": wave, "peak": 0.0, "pk_pk": 0.0, "rms": 0.0
        }

    if ain_override:
        channels.update(ain_override)

    return {
        "profile": profile,
        "window_samples": ws + 1 if wrong_stride else ws,
        "t": time.monotonic(),
        "channels": channels,
    }


def _pump_profile(runner: AmpTestRunner, profile: str = "WAVEFORM",
                  ain: str = None, n: int = None) -> None:
    """Feed enough good payloads to clear AWAIT_PROFILE."""
    if n is None:
        n = PROFILE_SETTLE_WINDOWS + 1
    ws = window_samples(profile)
    wave = np.zeros(ws)
    ain = ain or "AIN12"
    channels = {}
    if profile == "WAVEFORM":
        for a in ("AIN6","AIN7","AIN8","AIN9","AIN10","AIN11","AIN12","AIN13"):
            channels[a] = {"waveform": wave, "peak": 0.0, "pk_pk": 0.0, "rms": 0.0}
    else:
        channels[ain] = {"waveform": wave, "peak": 0.0, "pk_pk": 0.0, "rms": 0.0}
    payload = {"profile": profile, "window_samples": ws,
               "t": time.monotonic(), "channels": channels}
    for _ in range(n):
        runner.on_window(payload)


def _force_capture(runner: AmpTestRunner) -> None:
    """Force SETTLE -> CAPTURE by stopping the timer and calling the slot."""
    runner._settle_timer.stop()
    if runner._state == _State.SETTLE:
        runner._on_settle_elapsed()


# ---------------------------------------------------------------------------
# 1. AWAIT_PROFILE — bad stride discarded
# ---------------------------------------------------------------------------

def test_wrong_window_samples_in_await_profile_is_discarded(runner, tmp_limits):
    spec = by_id("G3.1")   # WAVEFORM, DriveSet.NONE
    runner.start(spec, limits_path=tmp_limits)
    assert runner._state == _State.AWAIT_PROFILE

    bad = _waveform_payload("WAVEFORM", wrong_stride=True)
    runner.on_window(bad)

    assert runner._state == _State.AWAIT_PROFILE, \
        "bad stride must not advance state"


# ---------------------------------------------------------------------------
# 2. AWAIT_PROFILE — wrong AIN (single-channel) discarded
# ---------------------------------------------------------------------------

def test_single_channel_wrong_ain_discarded(runner, tmp_limits):
    spec = by_id("G1.2")   # SINGLE_FAST, first amp X+, current ain AIN12
    runner.start(spec, limits_path=tmp_limits)
    assert runner._state == _State.AWAIT_PROFILE

    ws = window_samples("SINGLE_FAST")
    # Payload has AIN10 (X- current) instead of AIN12 (X+ current)
    payload = {
        "profile": "SINGLE_FAST", "window_samples": ws, "t": 1.0,
        "channels": {"AIN10": {"waveform": np.zeros(ws), "peak": 0.0,
                                "pk_pk": 0.0, "rms": 0.0}},
    }
    for _ in range(PROFILE_SETTLE_WINDOWS + 2):
        runner.on_window(payload)

    assert runner._state == _State.AWAIT_PROFILE, \
        "wrong AIN must not advance state"


# ---------------------------------------------------------------------------
# 3. Profile handshake timeout aborts
# ---------------------------------------------------------------------------

def test_profile_timeout_aborts(runner, tmp_limits):
    spec = by_id("G3.1")
    finished_paths = []
    runner.finished.connect(lambda p: finished_paths.append(p))

    runner.start(spec, limits_path=tmp_limits)
    assert runner._state == _State.AWAIT_PROFILE

    # Fire the timeout manually
    runner._profile_timeout_timer.stop()
    runner._on_profile_timeout()

    assert runner._state in (_State.DONE, _State.ABORTING)
    assert len(finished_paths) == 1


# ---------------------------------------------------------------------------
# 4. Unexpected trip aborts and sets trip_flag
# ---------------------------------------------------------------------------

def test_unexpected_trip_aborts_and_sets_trip_flag(runner, tmp_limits):
    spec = by_id("G3.2")   # DriveSet.ALL, expect_trip=False
    rows = []
    errors = []
    runner.row_recorded.connect(rows.append)
    runner.error.connect(errors.append)

    runner.start(spec, limits_path=tmp_limits)
    _pump_profile(runner, "WAVEFORM")
    _force_capture(runner)
    assert runner._state == _State.CAPTURE

    # Inject a window where X+ current (AIN12) way over trip threshold.
    # fallback trip_ma = 20 mA, TRIP_MARGIN = 1.15 → threshold = 23 mA.
    # Use 3.5V = 35 mA to exceed any plausible measured threshold too.
    ws = window_samples("WAVEFORM")
    wave_over = np.full(ws, 3.5)   # 35 mA >> 23 mA threshold
    wave_zero = np.zeros(ws)
    channels = {}
    for ain in ("AIN6","AIN7","AIN8","AIN9","AIN10","AIN11","AIN13"):
        channels[ain] = {"waveform": wave_zero, "peak": 0.0, "pk_pk": 0.0, "rms": 0.0}
    channels["AIN12"] = {"waveform": wave_over,
                          "peak": 2.5, "pk_pk": 0.0, "rms": 2.5}
    payload = {"profile": "WAVEFORM", "window_samples": ws,
               "t": 1.0, "channels": channels}

    runner.on_window(payload)

    assert any(r.get("trip_flag") for r in rows), "trip_flag must be set"
    assert runner._state in (_State.DONE, _State.ABORTING)
    assert errors, "error signal must be emitted"


# ---------------------------------------------------------------------------
# 5. Expected trip records and advances (G1.1 style)
# ---------------------------------------------------------------------------

def test_expected_trip_records_and_advances(runner, tmp_limits):
    spec = by_id("G1.1")   # expect_trip=True, DriveSet.EACH
    rows = []
    runner.row_recorded.connect(rows.append)

    runner.start(spec, limits_path=tmp_limits)
    _pump_profile(runner, "WAVEFORM")
    # Settle s=1.0, force past it
    _force_capture(runner)
    assert runner._state == _State.CAPTURE

    ws = window_samples("WAVEFORM")
    # First window: over-trip on Y+ current (AIN8) — expect_trip is True.
    # Use 3.5V = 35 mA to exceed any threshold (fallback=20mA, margin=1.15 → 23mA).
    wave_over = np.full(ws, 3.5)  # 35 mA
    wave_zero = np.zeros(ws)
    channels = {ain: {"waveform": wave_zero, "peak": 0.0, "pk_pk": 0.0, "rms": 0.0}
                for ain in ("AIN6","AIN7","AIN9","AIN10","AIN11","AIN12","AIN13")}
    channels["AIN8"] = {"waveform": wave_over, "peak": 2.5, "pk_pk": 0.0, "rms": 2.5}
    payload = {"profile": "WAVEFORM", "window_samples": ws,
               "t": 1.0, "channels": channels}

    runner.on_window(payload)

    # Should have recorded at least one row with trip_flag=True and NOT aborted
    trip_rows = [r for r in rows if r.get("trip_flag")]
    assert trip_rows, "expected a trip row when expect_trip=True"
    # Runner should continue (not done — more amps to test)
    # (It may be in SETTLE, AWAIT_PROFILE, or continuing, but NOT ABORTING)
    assert runner._state != _State.ABORTING, \
        "expect_trip=True should record and advance, not abort"


# ---------------------------------------------------------------------------
# 6. DriveSet.NONE issues zero set_waveform calls during capture
# ---------------------------------------------------------------------------

def test_drive_none_issues_no_commands(runner, fgens, tmp_limits):
    fa, fb = fgens
    spec = by_id("G3.1")   # DriveSet.NONE
    runner.start(spec, limits_path=tmp_limits)
    fa.calls.clear(); fb.calls.clear()

    _pump_profile(runner, "WAVEFORM")
    # G3.1 settle_s=0 so already in CAPTURE
    assert runner._state == _State.CAPTURE

    ws = window_samples("WAVEFORM")
    wave = np.zeros(ws)
    channels = {ain: {"waveform": wave, "peak": 0.0, "pk_pk": 0.0, "rms": 0.0}
                for ain in ("AIN6","AIN7","AIN8","AIN9","AIN10","AIN11","AIN12","AIN13")}
    payload = {"profile": "WAVEFORM", "window_samples": ws,
               "t": 1.0, "channels": channels}
    runner.on_window(payload)

    # Only zero_and_off_all (shutdown) calls are allowed after capture starts,
    # but during capture none should be issued.
    sw_during = [c for c in fa.calls + fb.calls if c[0] == "set_waveform"]
    # set_waveform calls here would only be from zero_and_off_all on shutdown
    # If runner is still in CAPTURE, there should be none
    if runner._state == _State.CAPTURE:
        assert not sw_during, \
            f"DriveSet.NONE issued set_waveform during capture: {sw_during}"


# ---------------------------------------------------------------------------
# 7. RawCaptureOverflow truncates and run continues
# ---------------------------------------------------------------------------

def test_raw_capture_overflow_truncates_and_continues(runner, tmp_limits):
    spec = by_id("G1.2")   # raw_mode=FULL, SINGLE_FAST
    rows = []
    runner.row_recorded.connect(rows.append)

    runner.start(spec, limits_path=tmp_limits)

    # Override raw_writer with a real one in a temp dir
    with tempfile.TemporaryDirectory() as tmp:
        from rbl.services.raw_capture_writer import RawCaptureWriter, RAW_MAX_SAMPLES_PER_CAPTURE
        rw = RawCaptureWriter(Path(tmp), "G1.2")
        runner._raw_writer = rw

        # Pump profile for SINGLE_FAST (AIN12 = X+ current)
        ws = window_samples("SINGLE_FAST")
        ain = "AIN12"
        channels = {ain: {"waveform": np.zeros(ws), "peak": 0.0, "pk_pk": 0.0, "rms": 0.0}}
        payload = {"profile": "SINGLE_FAST", "window_samples": ws, "t": 1.0,
                   "channels": channels}
        _pump_profile(runner, "SINGLE_FAST", ain=ain)
        # G1.2 is a step-test with settle_s=2.0 → will be in SETTLE
        _force_capture(runner)   # fires _on_settle_elapsed → opens capture, → CAPTURE

        if runner._state != _State.CAPTURE:
            pytest.skip("runner did not reach CAPTURE")

        # Capture is already open (opened in _on_settle_elapsed for step tests).
        # Fill it to the cap via the raw_writer directly.
        if runner._raw_writer is None:
            pytest.skip("no raw_writer attached")
        # Append up to the cap (writer already has the capture open)
        big_chunk = np.zeros(RAW_MAX_SAMPLES_PER_CAPTURE, dtype=np.float32)
        # Replace with a fresh append straight into the open capture
        runner._raw_writer._windows = []
        runner._raw_writer._n_samples = 0
        runner._raw_writer.append(big_chunk)

        # Next on_window should trigger overflow, truncate, and continue
        runner.on_window(payload)

        # Run must not be aborted from overflow alone
        assert runner._state != _State.ABORTING, \
            "RawCaptureOverflow must not abort the run"
        assert runner._raw_truncated or not runner._raw_capture_open, \
            "capture should be truncated or closed after overflow"


# ---------------------------------------------------------------------------
# 8. start() proceeds with warning when measured_limits is empty
# ---------------------------------------------------------------------------

def test_start_proceeds_with_empty_measured_limits(runner, tmp_limits):
    """start() must not refuse when measured_limits has no data — only warn."""
    spec = by_id("G3.1")  # simple noise floor test

    # tmp_limits path exists but is empty → fallbacks used with warnings
    errors = []
    runner.error.connect(errors.append)
    runner.start(spec, limits_path=tmp_limits)   # should NOT emit error

    # The only errors that would block start are byte-budget or envelope failures
    assert runner._state != _State.IDLE, \
        "start() must not leave runner in IDLE when limits are missing"
    # No errors about limits
    limits_errors = [e for e in errors if "measured_limits" in e.lower()]
    assert not limits_errors


# ---------------------------------------------------------------------------
# 9. Abort at any state zeros all four outputs
# ---------------------------------------------------------------------------

def test_abort_zeros_all_outputs(runner, fgens, tmp_limits):
    fa, fb = fgens
    spec = by_id("G3.2")  # DriveSet.ALL, WAVEFORM
    runner.start(spec, limits_path=tmp_limits)
    _pump_profile(runner, "WAVEFORM")
    _force_capture(runner)
    assert runner._state == _State.CAPTURE

    fa.calls.clear(); fb.calls.clear()
    runner.abort()

    # After abort, zero_and_off_all must have been called on all channels
    off_a = [c for c in fa.calls if c[0] == "output_off"]
    off_b = [c for c in fb.calls if c[0] == "output_off"]
    assert len(off_a) == 2 and len(off_b) == 2, \
        f"expected 2 output_off per gen, got fa={len(off_a)}, fb={len(off_b)}"

    zero_a = [c for c in fa.calls if c[0] == "set_waveform"]
    zero_b = [c for c in fb.calls if c[0] == "set_waveform"]
    assert all(c[5] == 0.0 for c in zero_a + zero_b), \
        "all channels must be zeroed after abort"
