"""
Tests for rbl.services.regulation_response.RegulationResponder — the
Section 7.4 glue between a confirmed fault, stopping the channel, asking the
operator which LED is lit, and logging the answer to trip history.

A fake dialog factory stands in for the real Qt dialog so nothing here
blocks on user input.
"""
import pytest
from PySide6.QtWidgets import QApplication

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
        responder = RegulationResponder(monitor, amp_drive, get_operating_conditions=lambda l: {},
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
        responder = RegulationResponder(
            monitor, amp_drive, get_operating_conditions=lambda l: conditions,
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

    def test_output_off_failure_does_not_prevent_dialog_and_logging(self, qapp, tmp_path, monkeypatch):
        import rbl.services.regulation_response as rr_module
        logged = []
        monkeypatch.setattr(rr_module.trip_history, "append_trip",
                             lambda record, path=None: logged.append(record))

        monitor = RegulationMonitor(debounce_windows=1)
        amp_drive = FaultyAmpDrive()
        responder = RegulationResponder(monitor, amp_drive, get_operating_conditions=lambda l: {},
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
        responder = RegulationResponder(monitor, amp_drive, get_operating_conditions=boom,
                                         dialog_factory=FakeDialog)
        monitor.evaluate("X+", 0.01, 3.0, 0.1, 20.0)   # must not raise
        assert logged and logged[0]["label"] == "X+"
