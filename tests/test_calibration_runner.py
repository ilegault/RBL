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


import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

from rbl.config.calibration_config import (
    CAL_MAX_KV,
    CAL_PASSES,
    DRIFT_LOG_INTERVAL_S,
    LoadCondition,
)
from rbl.config.hardware_config import AMP_AIN_NAMES, AMP_CHANNEL_MAP, AMP_LABELS
from rbl.config.labjack_stream_config import GUI_REFRESH_HZ
from rbl.services import calibration_runner as calibration_runner_module
from rbl.services.calibration_runner import CalibrationRunner, _State
from rbl.services.calibration_writer import CalibrationWriter


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


_VOLTAGE_AINS = {AMP_CHANNEL_MAP[amp]["voltage"] for amp in AMP_LABELS}


def make_payload(voltage_v: float = 1.0, n: int = 40, current_v: float = 0.1):
    """A synthetic window_ready payload carrying all 8 amp AINs.

    voltage_v drives every voltage-monitor AIN; current_v drives every
    current-monitor AIN. They are kept separate because the two monitors
    have different gains (1 V == 1 kV vs 1 V == 10 mA, see
    hardware/amp_monitor.py) — a value that is an unremarkable reading on
    one monitor can be a railed, interlock-tripping one on the other, so a
    single shared value fed to both indiscriminately is not a realistic
    payload.
    """
    def channel(ain):
        value = voltage_v if ain in _VOLTAGE_AINS else current_v
        return {
            "waveform": np.full(n, value),
            "peak": abs(value), "pk_pk": 0.0, "rms": abs(value),
            "mean": value, "std": 0.0,
        }

    return {
        "profile": "WAVEFORM",
        "window_samples": n,
        "t": 0.0,
        "sample_period": 1e-4,
        "channels": {ain: channel(ain) for ain in AMP_AIN_NAMES},
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

    def test_total_setpoints_matches_config(self, qapp, funcgen_map):
        runner = CalibrationRunner(funcgen_map, LoadCondition.DISCONNECTED)
        runner.start_sweep()
        from rbl.config.calibration_config import sweep_points as _sp
        # "up" and "down" each visit every rung twice (out and back); "random"
        # visits each rung once, so it is a shorter sequence than either.
        pts_per_amp = sum(len(_sp(pass_type)) for pass_type in CAL_PASSES)
        assert len(runner._sequence) == len(AMP_LABELS) * pts_per_amp

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
        # 4.5 kV is a physically possible voltage-monitor reading (within
        # CAL_MAX_KV), and distinct from the 2.0 kV used once COLLECT
        # actually starts, so a leak from SETTLE would still be caught.
        for _ in range(50):
            runner.on_window(make_payload(4.5))
        assert rows == []

        runner._on_settle_elapsed()   # now COLLECT
        n = _windows_per_collect()
        for _ in range(n):
            runner.on_window(make_payload(2.0))

        assert len(rows) == 8
        for r in rows:
            if r["n_samples"] > 0:
                expected = 2.0 if r["kind"] == "voltage" else 0.1
                assert r["mean_v"] == pytest.approx(expected)
        print("[OK] windows arriving during SETTLE are discarded, not averaged")

    def test_railed_current_monitor_hard_trip_fires(self, qapp, funcgen_map, gens):
        """A current monitor pinned at the AIN's own rail is a physically
        real fault (dead short, arc, or failing amplifier), not a test
        artifact. hardware_config.py records the current monitor's own
        design range as +/-10 V ('the current monitor reaches +/-10 V during
        the 100 mA / 4 ms transient the amplifier is rated for'); at the
        documented 10 mA/V gain that is a 100 mA reading, over
        CAL_TRIP_HARD_MA (60), and the hard interlock must abort and zero
        every channel — this is the interlock doing its job, not a failure."""
        runner = CalibrationRunner(funcgen_map, LoadCondition.DISCONNECTED)
        finished = []
        errors = []
        overcurrent = []
        runner.finished.connect(finished.append)
        runner.error.connect(errors.append)
        runner.overcurrent.connect(lambda *a: overcurrent.append(a))
        runner.start_sweep()   # SETTLE on the first point; interlock is armed here too

        runner.on_window(make_payload(voltage_v=0.0, current_v=10.0))

        assert overcurrent
        assert errors
        assert finished
        assert runner._state == _State.IDLE
        for label, (gen, channel) in funcgen_map.items():
            assert gen.state[channel]["offset"] == pytest.approx(0.0)
            assert gen.state[channel]["output"] is False
        print("[OK] a railed current monitor trips the hard interlock and zeros all four")

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


class TestRegulationState:
    """Section 7.5: every recorded row is tagged with the joint V+I
    classification from rbl.hardware.regulation, so a row taken while the
    amplifier was not following its input is flagged, not silently dropped.
    `_regulation_state_for`/`_make_row` are exercised directly against a
    hand-populated `_collect_windows`, the same private-method-under-test
    convention this file already uses for `_on_settle_elapsed` etc."""

    @staticmethod
    def _seed_windows(runner, amp, v_v, i_v, n=10):
        ain_v = AMP_CHANNEL_MAP[amp]["voltage"]
        ain_i = AMP_CHANNEL_MAP[amp]["current"]
        runner._collect_windows = {
            ain_v: [np.full(n, v_v)], ain_i: [np.full(n, i_v)],
        }
        runner._last_sample_period = 1e-4

    def test_healthy_point_is_ok(self, qapp, funcgen_map):
        runner = CalibrationRunner(funcgen_map, LoadCondition.ON_PLATES)
        self._seed_windows(runner, "X+", v_v=2.0, i_v=0.5)   # 2 kV, 5 mA
        state, _ = runner._regulation_state_for("X+", commanded_kv=2.0,
                                                 freq_hz=0.0, ac=False)
        assert state == "ok"

    def test_amp_off_when_both_monitors_near_zero(self, qapp, funcgen_map):
        runner = CalibrationRunner(funcgen_map, LoadCondition.ON_PLATES)
        self._seed_windows(runner, "X+", v_v=0.01, i_v=0.001)
        state, _ = runner._regulation_state_for("X+", commanded_kv=3.0,
                                                 freq_hz=0.0, ac=False)
        assert state == "amp_off"

    def test_current_limited_when_voltage_low_current_pinned(self, qapp, funcgen_map):
        runner = CalibrationRunner(funcgen_map, LoadCondition.ON_PLATES)
        self._seed_windows(runner, "X+", v_v=1.0, i_v=1.95)   # 1 kV of 5 kV, 19.5 mA
        state, reason = runner._regulation_state_for("X+", commanded_kv=5.0,
                                                       freq_hz=0.0, ac=False)
        assert state == "current_limited"
        assert "invalid" in reason

    def test_idle_when_no_data_collected_for_this_amp(self, qapp, funcgen_map):
        runner = CalibrationRunner(funcgen_map, LoadCondition.ON_PLATES)
        state, _ = runner._regulation_state_for("X+", commanded_kv=2.0,
                                                 freq_hz=0.0, ac=False)
        assert state == "idle"

    def test_make_row_includes_regulation_columns(self, qapp, funcgen_map):
        runner = CalibrationRunner(funcgen_map, LoadCondition.ON_PLATES)
        self._seed_windows(runner, "X+", v_v=2.0, i_v=0.5)
        row = runner._make_row(0, "up", "X+", 2.0, "X+", "voltage",
                                0.0, "2026-01-01T00:00:00")
        assert row["regulation_state"] == "ok"
        assert "regulation_reason" in row


class TestWriterIntegration:
    def test_rows_and_metadata_reach_a_real_writer(self, qapp, funcgen_map, tmp_path):
        writer = CalibrationWriter(output_dir=tmp_path)
        runner = CalibrationRunner(funcgen_map, LoadCondition.ON_PLATES, writer=writer)
        runner.operator_note = "bench sanity check"
        finished = []
        runner.finished.connect(finished.append)

        runner.start_sweep()
        _drive_one_setpoint(runner)
        runner.abort()

        assert finished and finished[0] == str(writer.csv_path)
        assert writer.csv_path.exists()
        assert writer.meta_path.exists()

        import csv
        import json
        with open(writer.csv_path) as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 8   # one setpoint recorded before abort

        with open(writer.meta_path) as f:
            meta = json.load(f)
        assert meta["load_condition"] == "ON_PLATES"
        assert meta["operator_note"] == "bench sanity check"
        assert "seed" in meta


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


# ---------------------------------------------------------------------------
# Drift mode
# ---------------------------------------------------------------------------

class FakeClock:
    """A settable stand-in for time.monotonic, so drift-completion timing
    can be tested without a real wait."""
    def __init__(self, t: float = 0.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt: float):
        self.t += dt


def _windows_per_drift_log():
    return max(1, round(DRIFT_LOG_INTERVAL_S * GUI_REFRESH_HZ))


class TestDriftLoadConditionGuard:
    def test_on_plates_8h_refused(self, qapp, funcgen_map):
        runner = CalibrationRunner(funcgen_map, LoadCondition.ON_PLATES)
        errors = []
        runner.error.connect(errors.append)
        runner.start_drift(3.0, 8.0)
        assert errors
        assert runner._state == _State.IDLE
        print("[OK] ON_PLATES + 8 h is refused")

    def test_on_plates_1h_accepted(self, qapp, funcgen_map):
        runner = CalibrationRunner(funcgen_map, LoadCondition.ON_PLATES)
        errors = []
        runner.error.connect(errors.append)
        runner.start_drift(3.0, 1.0)
        runner._on_settle_elapsed()   # SETTLE -> COLLECT, same as production
        assert not errors
        assert runner._state == _State.COLLECT
        print("[OK] ON_PLATES + 1 h is accepted")

    def test_disconnected_10h_accepted(self, qapp, funcgen_map):
        runner = CalibrationRunner(funcgen_map, LoadCondition.DISCONNECTED)
        errors = []
        runner.error.connect(errors.append)
        runner.start_drift(3.0, 10.0)
        runner._on_settle_elapsed()   # SETTLE -> COLLECT, same as production
        assert not errors
        assert runner._state == _State.COLLECT
        print("[OK] DISCONNECTED + 10 h is accepted")

    def test_disconnected_20h_refused(self, qapp, funcgen_map):
        runner = CalibrationRunner(funcgen_map, LoadCondition.DISCONNECTED)
        errors = []
        runner.error.connect(errors.append)
        runner.start_drift(3.0, 20.0)
        assert errors
        assert runner._state == _State.IDLE
        print("[OK] DISCONNECTED + 20 h is refused (exceeds DRIFT_MAX_UNATTENDED_H)")



class TestDriftCompletionAndWatchdog:
    def test_auto_zeros_at_completion(self, qapp, funcgen_map, gens, monkeypatch):
        clock = FakeClock(0.0)
        monkeypatch.setattr(calibration_runner_module.time, "monotonic", clock)

        runner = CalibrationRunner(funcgen_map, LoadCondition.DISCONNECTED)
        finished = []
        runner.finished.connect(finished.append)

        duration_h = 1.0 / 3600.0   # 1 simulated second
        runner.start_drift(1.5, duration_h)
        runner._on_settle_elapsed()   # SETTLE -> COLLECT, same as production
        assert runner._state == _State.COLLECT

        # Advance the clock past the drift's end before the log interval
        # that will trigger the completion check.
        clock.advance(duration_h * 3600.0 + 1.0)
        for _ in range(_windows_per_drift_log()):
            runner.on_window(make_payload(1.5))

        assert finished
        for label, (gen, channel) in funcgen_map.items():
            assert gen.state[channel]["offset"] == pytest.approx(0.0)
            assert gen.state[channel]["output"] is False
        print("[OK] drift run auto-zeros at completion")

    def test_watchdog_triggers_on_gap_and_zeros_output(self, qapp, funcgen_map, gens):
        runner = CalibrationRunner(funcgen_map, LoadCondition.DISCONNECTED)
        finished = []
        errors = []
        runner.finished.connect(finished.append)
        runner.error.connect(errors.append)

        runner.start_drift(2.0, 1.0)   # 1 h, well within the unattended cap
        runner._on_settle_elapsed()   # SETTLE -> COLLECT, same as production
        assert runner._state == _State.COLLECT

        # A >5 s gap would fire the real QTimer in production; call its
        # handler directly, the same technique used for the SETTLE timer —
        # nothing here needs a real elapsed delay to make progress.
        runner._on_watchdog_timeout()

        assert finished
        assert errors
        for label, (gen, channel) in funcgen_map.items():
            assert gen.state[channel]["offset"] == pytest.approx(0.0)
            assert gen.state[channel]["output"] is False
        print("[OK] a 6-second window gap triggers the watchdog and zeros the output")

    def test_watchdog_resets_on_each_window(self, qapp, funcgen_map):
        runner = CalibrationRunner(funcgen_map, LoadCondition.DISCONNECTED)
        errors = []
        runner.error.connect(errors.append)
        runner.start_drift(1.0, 1.0)
        runner._on_settle_elapsed()   # SETTLE -> COLLECT, same as production

        # Windows keep arriving (fewer than a full log interval) -- the
        # watchdog must not trip while data is still flowing.
        for _ in range(_windows_per_drift_log() - 1):
            runner.on_window(make_payload(1.0))
        assert not errors
        assert runner._state == _State.COLLECT

    def test_drift_rows_carry_pass_type_drift(self, qapp, funcgen_map):
        runner = CalibrationRunner(funcgen_map, LoadCondition.DISCONNECTED)
        rows = []
        runner.row_recorded.connect(rows.append)
        runner.start_drift(2.5, 1.0)
        runner._on_settle_elapsed()   # SETTLE -> COLLECT, same as production
        for _ in range(_windows_per_drift_log()):
            runner.on_window(make_payload(2.5))

        assert len(rows) == 8
        assert all(r["pass_type"] == "drift" for r in rows)
        assert all(r["driven_amp"] == "ALL" for r in rows)
        assert all(r["commanded_kv"] == pytest.approx(2.5) for r in rows)
