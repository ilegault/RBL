"""
Tests for rbl.services.regulation_response.RegulationResponder — the
Section 7.4 glue between a confirmed fault, stopping the channel, asking the
operator which LED is lit, and logging the answer to trip history.

A fake dialog factory stands in for the real Qt dialog so nothing here
blocks on user input.
"""
import pytest
from PySide6.QtWidgets import QApplication, QDialog

from rbl.services.regulation_monitor import RegulationMonitor
from rbl.services.regulation_response import RegulationResponder
from rbl.services.trip_history import load_trip_history


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class FakeDialog:
    """Records construction args; .exec() returns immediately."""
    last_instance = None

    def __init__(self, label, state, reason, parent=None, answer="CURRENT LIMIT-TRIP"):
        self.label, self.state, self.reason, self.parent = label, state, reason, parent
        self._answer = answer
        FakeDialog.last_instance = self

    def exec(self):
        return 1

    def selected_reason(self):
        return self._answer


class FakeAmpDrive:
    def __init__(self):
        self.off_calls = []

    def output_off(self, label):
        self.off_calls.append(label)


class FaultyAmpDrive(FakeAmpDrive):
    def output_off(self, label):
        raise RuntimeError("simulated SCPI failure")


class TestRegulationResponder:
    def test_stops_the_channel_on_fault(self, qapp):
        monitor = RegulationMonitor(debounce_windows=1)
        amp_drive = FakeAmpDrive()
        # Held so it isn't GC'd before evaluate() fires its signal connection.
        responder = RegulationResponder(  # noqa: F841
            monitor, amp_drive, get_operating_conditions=lambda label: {},
            dialog_factory=FakeDialog)
        monitor.evaluate("X+", 0.01, 3.0, 0.1, 20.0)
        assert amp_drive.off_calls == ["X+"]

    def test_logs_operator_answer_and_conditions_to_trip_history(self, qapp, tmp_path, monkeypatch):
        import rbl.services.regulation_response as rr_module
        monkeypatch.setattr(rr_module.trip_history, "TRIP_HISTORY_PATH", tmp_path / "trip.jsonl")

        def fake_append(record, path=None):
            import json
            p = path or rr_module.trip_history.TRIP_HISTORY_PATH
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "a") as f:
                f.write(json.dumps(record, default=str) + "\n")

        monkeypatch.setattr(rr_module.trip_history, "append_trip", fake_append)

        monitor = RegulationMonitor(debounce_windows=1)
        amp_drive = FakeAmpDrive()
        conditions = {"commanded_kv": 3.0, "frequency_hz": 0.0, "chamber_pressure_torr": 1e-5}
        # Held so it isn't GC'd before evaluate() fires its signal connection.
        responder = RegulationResponder(  # noqa: F841
            monitor, amp_drive, get_operating_conditions=lambda label: conditions,
            dialog_factory=lambda *a, **k: FakeDialog(*a, answer="THERMAL LIMIT", **k))
        monitor.evaluate("X+", 0.01, 3.0, 0.1, 20.0)

        records = load_trip_history(tmp_path / "trip.jsonl")
        assert len(records) == 1
        rec = records[0]
        assert rec["label"] == "X+"
        assert rec["state"] == "amp_off"
        assert rec["operator_answer"] == "THERMAL LIMIT"
        assert rec["commanded_kv"] == 3.0
        assert rec["chamber_pressure_torr"] == 1e-5

    def test_output_off_failure_does_not_prevent_dialog_and_logging(
        self, qapp, tmp_path, monkeypatch
    ):
        import rbl.services.regulation_response as rr_module
        logged = []
        monkeypatch.setattr(rr_module.trip_history, "append_trip",
                             lambda record, path=None: logged.append(record))

        monitor = RegulationMonitor(debounce_windows=1)
        amp_drive = FaultyAmpDrive()
        # Held so it isn't GC'd before evaluate() fires its signal connection.
        responder = RegulationResponder(  # noqa: F841
            monitor, amp_drive, get_operating_conditions=lambda label: {},
            dialog_factory=FakeDialog)
        monitor.evaluate("X+", 0.01, 3.0, 0.1, 20.0)   # must not raise
        assert logged and logged[0]["label"] == "X+"

    def test_get_operating_conditions_failure_still_logs_the_core_fields(self, qapp, monkeypatch):
        import rbl.services.regulation_response as rr_module
        logged = []
        monkeypatch.setattr(rr_module.trip_history, "append_trip",
                             lambda record, path=None: logged.append(record))

        def boom(label):
            raise RuntimeError("no conditions available")

        monitor = RegulationMonitor(debounce_windows=1)
        amp_drive = FakeAmpDrive()
        # Held so it isn't GC'd before evaluate() fires its signal connection.
        responder = RegulationResponder(  # noqa: F841
            monitor, amp_drive, get_operating_conditions=boom,
            dialog_factory=FakeDialog)
        monitor.evaluate("X+", 0.01, 3.0, 0.1, 20.0)   # must not raise
        assert logged and logged[0]["label"] == "X+"


def build_observer(mode_recorder=None):
    monitor = RegulationMonitor(debounce_windows=1)
    amp_drive = FakeAmpDrive()
    responder = RegulationResponder(
        monitor, amp_drive, lambda label: {"commanded_kv": 3.0},
        dialog_factory=FakeDialog, mode="observe", spike_recorder=mode_recorder)
    return monitor, amp_drive, responder


class TestRegulationResponderObserveMode:
    """ADR 0006: in a session the application witnesses a fault, never answers it."""

    @pytest.fixture(autouse=True)
    def _trip_path(self, tmp_path, monkeypatch):
        import rbl.services.regulation_response as rr_module
        self.path = tmp_path / "trip.jsonl"
        monkeypatch.setattr(rr_module.trip_history, "TRIP_HISTORY_PATH", self.path)

    def test_current_limited_fault_never_stops_the_channel_or_opens_a_dialog(
            self, qapp, monkeypatch):
        def no_dialog(*_a, **_k):
            raise AssertionError("observe mode must never open a dialog")
        monkeypatch.setattr(QDialog, "exec", no_dialog)
        FakeDialog.last_instance = None
        monitor, amp_drive, responder = build_observer()
        warnings = []
        responder.warning.connect(warnings.append)

        monitor.evaluate("X+", 0.5, 3.0, 19.5, 20.0)     # 17 % of commanded, at the limit

        assert amp_drive.off_calls == []
        assert FakeDialog.last_instance is None
        records = load_trip_history(self.path)
        assert len(records) == 1
        assert records[0]["state"] == "current_limited"
        assert records[0]["mode"] == "observe"
        assert records[0]["commanded_kv"] == 3.0
        assert len(warnings) == 1 and "X+" in warnings[0]

    def test_amp_off_saves_the_pre_event_waveform(self, qapp):
        class Recorder:
            def __init__(self):
                self.calls = []

            def save_pre_event(self, label):
                self.calls.append(label)

        rec = Recorder()
        monitor, amp_drive, responder = build_observer(rec)
        monitor.evaluate("Y-", 0.01, 3.0, 0.1, 20.0)
        assert rec.calls == ["Y-"]
        assert amp_drive.off_calls == []

    def test_current_limited_does_not_save_a_pre_event(self, qapp):
        class Recorder:
            calls = []

            def save_pre_event(self, label):
                self.calls.append(label)

        rec = Recorder()
        monitor, _drive, responder = build_observer(rec)
        monitor.evaluate("X+", 0.5, 3.0, 19.5, 20.0)
        assert rec.calls == []

    def test_unknown_mode_is_refused(self, qapp):
        with pytest.raises(ValueError):
            RegulationResponder(RegulationMonitor(), FakeAmpDrive(),
                                lambda label: {}, mode="bogus")
