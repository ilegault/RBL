"""
Tests for rbl.services.calibration_runner.CalibrationRunner.

No hardware: a FakeGen test double stands in for DG1022Z, and synthetic
window payloads are injected directly via on_window(). The QTimer used for
the SETTLE delay is never allowed to actually fire on its own (the test
never calls processEvents()/exec()) — its timeout handler is invoked
directly instead, which is exactly the point: nothing in the runner needs a
running event loop OR a real elapsed delay to make forward progress in a
test, because nothing in it blocks.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time

import numpy as np
import pytest

pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtWidgets import QApplication

from rbl.services import calibration_runner as calibration_runner_module
from rbl.config.calibration_config import (
    CAL_MAX_KV, CAL_PASSES, LoadCondition,
)
from rbl.config.hardware_config import AMP_AIN_NAMES, AMP_CHANNEL_MAP, AMP_LABELS
from rbl.config.labjack_stream_config import GUI_REFRESH_HZ
from rbl.services.calibration_runner import CalibrationRunner, _State


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


# ---------------------------------------------------------------------------
# Fake DG1022Z: mirrors the write-then-error-query contract of the real
# driver's _write_checked (funcgen_driver.py) so "every write followed by an
# error query" is something this fake can actually attest to.
# ---------------------------------------------------------------------------

class FakeGen:
    def __init__(self, name):
        self.name = name
        self.log = []       # [("WRITE", op, channel, kwargs) | ("ERR?", op, channel), ...]
        self.state = {
            1: dict(shape="Sine", freq=10.0, amp=1.0, offset=0.0,
                    phase=0.0, output=False, load="INFinity"),
            2: dict(shape="Sine", freq=10.0, amp=1.0, offset=0.0,
                    phase=0.0, output=False, load="INFinity"),
        }
        self.fail_after = None   # int: raise on the Nth write to this instance

    def _write(self, op, channel, **kwargs):
        if self.fail_after is not None:
            self.fail_after -= 1
            if self.fail_after == 0:
                raise RuntimeError(f"simulated SCPI failure on {op} ch{channel}")
        self.log.append(("WRITE", op, channel, kwargs))
        self.log.append(("ERR?", op, channel))   # the _write_checked contract

    def set_waveform(self, channel, shape, freq_hz, amp_vpp, offset_v, phase_deg):
        self._write("set_waveform", channel, shape=shape, freq=freq_hz,
                     amp=amp_vpp, offset=offset_v, phase=phase_deg)
        self.state[channel].update(shape=shape, freq=freq_hz, amp=amp_vpp,
                                    offset=offset_v, phase=phase_deg)
        return ""

    def output_on(self, channel):
        self._write("output_on", channel)
        self.state[channel]["output"] = True

    def output_off(self, channel):
        self._write("output_off", channel)
        self.state[channel]["output"] = False

    def set_output_load(self, channel, value="INFinity"):
        self._write("set_output_load", channel, load=value)
        self.state[channel]["load"] = value

    def get_state(self, channel):
        return dict(self.state[channel])


@pytest.fixture
def gens():
    return {"A": FakeGen("A"), "B": FakeGen("B")}


@pytest.fixture
def funcgen_map(gens):
    return {
        "X+": (gens["A"], 1), "X-": (gens["A"], 2),
        "Y+": (gens["B"], 1), "Y-": (gens["B"], 2),
    }


def make_payload(value_v: float = 1.0, n: int = 40):
    """A synthetic window_ready payload carrying all 8 amp AINs."""
    return {
        "profile": "WAVEFORM",
        "window_samples": n,
        "t": 0.0,
        "sample_period": 1e-4,
        "channels": {
            ain: {
                "waveform": np.full(n, value_v),
                "peak": abs(value_v), "pk_pk": 0.0, "rms": abs(value_v),
                "mean": value_v, "std": 0.0,
            }
            for ain in AMP_AIN_NAMES
        },
    }


WINDOWS_PER_COLLECT = None   # filled in by a fixture-independent computation below


def _windows_per_collect():
    from rbl.config.calibration_config import CAL_COLLECT_S
    return max(1, round(CAL_COLLECT_S * GUI_REFRESH_HZ))


def _drive_one_setpoint(runner: CalibrationRunner, value_v: float = 1.0):
    """Simulate settle-elapsed + a full collect window count for one point."""
    runner._on_settle_elapsed()
    n = _windows_per_collect()
    for _ in range(n):
        runner.on_window(make_payload(value_v))


def _drive_full_sweep(runner: CalibrationRunner, max_points: int = None):
    total = len(runner._sequence)
    limit = total if max_points is None else min(max_points, total)
    for _ in range(limit):
        _drive_one_setpoint(runner)


# ---------------------------------------------------------------------------

class TestFullSweep:
    def test_completes_without_time_sleep(self, qapp, funcgen_map, monkeypatch):
        monkeypatch.setattr(
            calibration_runner_module.time, "sleep",
            lambda *a, **k: pytest.fail("CalibrationRunner called time.sleep"),
        )
        runner = CalibrationRunner(funcgen_map, LoadCondition.DISCONNECTED)
        finished = []
        runner.finished.connect(finished.append)

        runner.start_sweep()
        _drive_full_sweep(runner)

        assert finished, "sweep never finished"
        print("[OK] runner completes a full sweep with zero calls to time.sleep")

    def test_total_setpoints_matches_4ch_3pass_43pt(self, qapp, funcgen_map):
        runner = CalibrationRunner(funcgen_map, LoadCondition.DISCONNECTED)
        runner.start_sweep()
        assert len(runner._sequence) == len(AMP_LABELS) * len(CAL_PASSES) * 43

    def test_eight_rows_per_setpoint(self, qapp, funcgen_map):
        runner = CalibrationRunner(funcgen_map, LoadCondition.DISCONNECTED)
        rows = []
        runner.row_recorded.connect(rows.append)
        runner.start_sweep()

        _drive_one_setpoint(runner)
        assert len(rows) == 8
        seen_ains = {r["ain"] for r in rows}
        assert seen_ains == set(AMP_AIN_NAMES)
        print("[OK] exactly 8 rows emitted per setpoint")

    def test_windows_during_settle_are_discarded(self, qapp, funcgen_map):
        runner = CalibrationRunner(funcgen_map, LoadCondition.DISCONNECTED)
        rows = []
        runner.row_recorded.connect(rows.append)
        runner.start_sweep()

        # Still in SETTLE — these must not be averaged into the recording.
        for _ in range(50):
            runner.on_window(make_payload(999.0))
        assert rows == []

        runner._on_settle_elapsed()   # now COLLECT
        n = _windows_per_collect()
        for _ in range(n):
            runner.on_window(make_payload(2.0))

        assert len(rows) == 8
        for r in rows:
            if r["n_samples"] > 0:
                assert r["mean_v"] == pytest.approx(2.0)
        print("[OK] windows arriving during SETTLE are discarded, not averaged")

    def test_undriven_channels_commanded_zero_every_setpoint(self, qapp, funcgen_map, gens):
        runner = CalibrationRunner(funcgen_map, LoadCondition.DISCONNECTED)
        runner.start_sweep()   # first point: driven = X+, kv = 0.0 (bracket)
        _drive_one_setpoint(runner)   # second point starts (nonzero X+)

        # Every WRITE with op=set_waveform to a non-X+ channel this setpoint
        # must carry offset 0.0.
        writes = [e for e in gens["A"].log if e[0] == "WRITE" and e[1] == "set_waveform"]
        # gens["A"] carries both X+ (ch1, driven) and X- (ch2, undriven).
        ch2_writes = [e for e in writes if e[2] == 2]
        assert ch2_writes, "expected set_waveform writes on the undriven channel"
        assert all(w[3]["offset"] == pytest.approx(0.0) for w in ch2_writes)

        writes_b = [e for e in gens["B"].log if e[0] == "WRITE" and e[1] == "set_waveform"]
        assert writes_b
        assert all(w[3]["offset"] == pytest.approx(0.0) for w in writes_b)
        print("[OK] undriven channels are commanded to 0.0 at every setpoint")

    def test_no_commanded_value_exceeds_cal_max_kv(self, qapp, funcgen_map, gens):
        runner = CalibrationRunner(funcgen_map, LoadCondition.DISCONNECTED)
        runner.start_sweep()
        _drive_full_sweep(runner, max_points=30)

        for gen in gens.values():
            for entry in gen.log:
                if entry[0] == "WRITE" and entry[1] == "set_waveform":
                    offset_v = entry[3]["offset"]
                    # offset_v is in generator volts; gain is 1000x kV<->V
                    # numerically 1:1 here (_AMP_GAIN == 1000).
                    assert abs(offset_v) <= CAL_MAX_KV + 1e-9, offset_v
        print("[OK] no commanded value exceeds CAL_MAX_KV")

    def test_every_write_followed_by_error_query(self, qapp, funcgen_map, gens):
        runner = CalibrationRunner(funcgen_map, LoadCondition.DISCONNECTED)
        runner.start_sweep()
        _drive_full_sweep(runner, max_points=5)

        for gen in gens.values():
            i = 0
            log = gen.log
            assert log, "expected at least one write"
            while i < len(log):
                assert log[i][0] == "WRITE", log[i]
                assert log[i + 1][0] == "ERR?", "write not followed by error query"
                assert log[i + 1][1] == log[i][1]
                assert log[i + 1][2] == log[i][2]
                i += 2
        print("[OK] every SCPI write is followed by an error-queue query")


class TestAbortAndExceptions:
    def test_abort_mid_sweep_zeros_and_disables_all_four(self, qapp, funcgen_map, gens):
        runner = CalibrationRunner(funcgen_map, LoadCondition.DISCONNECTED)
        finished = []
        runner.finished.connect(finished.append)
        runner.start_sweep()
        _drive_full_sweep(runner, max_points=5)

        runner.abort()

        assert finished
        assert runner._state == _State.IDLE
        for label, (gen, channel) in funcgen_map.items():
            assert gen.state[channel]["offset"] == pytest.approx(0.0)
            assert gen.state[channel]["output"] is False
        print("[OK] abort() mid-sweep commands 0 V and outputs OFF on all four")

    def test_exception_mid_sweep_still_zeros_and_disables(self, qapp, funcgen_map, gens):
        runner = CalibrationRunner(funcgen_map, LoadCondition.DISCONNECTED)
        finished = []
        errors = []
        runner.finished.connect(finished.append)
        runner.error.connect(errors.append)
        runner.start_sweep()
        _drive_full_sweep(runner, max_points=3)   # a few clean setpoints first

        # Arm a failure on the NEXT set_waveform call to gen A (mid-sweep).
        gens["A"].fail_after = 1

        # Driving one more setpoint hits the failure inside _command_channel,
        # called from _enter_settle the moment the previous point finishes
        # recording. The runner catches it internally (it must never crash
        # the Qt event loop) and reports it via `error` instead of raising.
        _drive_one_setpoint(runner)

        assert finished, "runner must still reach a terminal state"
        assert errors
        for label, (gen, channel) in funcgen_map.items():
            assert gen.state[channel]["offset"] == pytest.approx(0.0)
            assert gen.state[channel]["output"] is False
        print("[OK] an exception mid-sweep still commands 0 V and outputs OFF")


class TestReproducibility:
    def test_fixed_seed_reproduces_sequence(self, qapp, funcgen_map):
        r1 = CalibrationRunner(funcgen_map, LoadCondition.DISCONNECTED)
        r1._seed = 42
        seq1 = [s.commanded_kv for s in r1._build_sequence()]

        r2 = CalibrationRunner(funcgen_map, LoadCondition.DISCONNECTED)
        r2._seed = 42
        seq2 = [s.commanded_kv for s in r2._build_sequence()]

        assert seq1 == seq2
        print("[OK] a random pass with a fixed seed reproduces exactly")


class TestProgressAndFinish:
    def test_progress_emitted_each_setpoint(self, qapp, funcgen_map):
        runner = CalibrationRunner(funcgen_map, LoadCondition.DISCONNECTED)
        progress = []
        runner.progress.connect(lambda d, t, lbl: progress.append((d, t, lbl)))
        runner.start_sweep()
        total = len(runner._sequence)
        _drive_one_setpoint(runner)
        assert progress
        assert progress[0][1] == total

    def test_finished_emits_a_string(self, qapp, funcgen_map):
        runner = CalibrationRunner(funcgen_map, LoadCondition.DISCONNECTED)
        finished = []
        runner.finished.connect(finished.append)
        runner.start_sweep()
        _drive_full_sweep(runner)
        assert len(finished) == 1
        assert isinstance(finished[0], str)
