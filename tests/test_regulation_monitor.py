"""
Tests for rbl.services.regulation_monitor.RegulationMonitor.
"""
import pytest

pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtWidgets import QApplication

from rbl.services.regulation_monitor import RegulationMonitor


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class FakeRamp:
    def __init__(self):
        self.ramping = set()

    def is_ramping(self, label):
        return label in self.ramping


class TestDebounce:
    def test_single_bad_window_does_not_fire(self, qapp):
        monitor = RegulationMonitor(debounce_windows=2)
        faults = []
        monitor.fault_detected.connect(lambda *a: faults.append(a))
        monitor.evaluate("X+", 0.01, 3.0, 0.1, 20.0)
        assert not faults

    def test_two_consecutive_bad_windows_confirm(self, qapp):
        monitor = RegulationMonitor(debounce_windows=2)
        faults = []
        monitor.fault_detected.connect(lambda *a: faults.append(a))
        monitor.evaluate("X+", 0.01, 3.0, 0.1, 20.0)
        monitor.evaluate("X+", 0.01, 3.0, 0.1, 20.0)
        assert faults and faults[0][:2] == ("X+", "amp_off")

    def test_does_not_refire_every_window_once_confirmed(self, qapp):
        monitor = RegulationMonitor(debounce_windows=2)
        faults = []
        monitor.fault_detected.connect(lambda *a: faults.append(a))
        for _ in range(5):
            monitor.evaluate("X+", 0.01, 3.0, 0.1, 20.0)
        assert len(faults) == 1

    def test_a_healthy_window_resets_the_debounce_count(self, qapp):
        monitor = RegulationMonitor(debounce_windows=2)
        faults = []
        monitor.fault_detected.connect(lambda *a: faults.append(a))
        monitor.evaluate("X+", 0.01, 3.0, 0.1, 20.0)   # 1 bad
        monitor.evaluate("X+", 2.94, 3.0, 4.0, 20.0)    # ratio ~0.98 — healthy, resets count
        monitor.evaluate("X+", 0.01, 3.0, 0.1, 20.0)   # 1 bad again
        assert not faults


class TestRampCoupling:
    def test_ramping_channel_never_fires(self, qapp):
        ramp = FakeRamp()
        ramp.ramping.add("Y+")
        monitor = RegulationMonitor(ramp_engine=ramp, debounce_windows=2)
        faults = []
        monitor.fault_detected.connect(lambda *a: faults.append(a))
        for _ in range(10):
            monitor.evaluate("Y+", 0.5, 3.0, 19.9, 20.0)
        assert not faults

    def test_post_ramp_blank_windows_absorb_settling(self, qapp):
        ramp = FakeRamp()
        ramp.ramping.add("Y+")
        monitor = RegulationMonitor(ramp_engine=ramp, debounce_windows=2,
                                     post_ramp_blank_windows=2)
        faults = []
        monitor.fault_detected.connect(lambda *a: faults.append(a))
        monitor.evaluate("Y+", 0.5, 3.0, 19.9, 20.0)   # while ramping
        ramp.ramping.discard("Y+")
        monitor.evaluate("Y+", 0.5, 3.0, 19.9, 20.0)   # blank window 1
        monitor.evaluate("Y+", 0.5, 3.0, 19.9, 20.0)   # blank window 2
        assert not faults

    def test_fires_after_blank_window_budget_spent(self, qapp):
        ramp = FakeRamp()
        ramp.ramping.add("Y+")
        monitor = RegulationMonitor(ramp_engine=ramp, debounce_windows=2,
                                     post_ramp_blank_windows=1)
        faults = []
        monitor.fault_detected.connect(lambda *a: faults.append(a))
        monitor.evaluate("Y+", 0.5, 3.0, 19.9, 20.0)   # while ramping
        ramp.ramping.discard("Y+")
        monitor.evaluate("Y+", 0.5, 3.0, 19.9, 20.0)   # blank window
        monitor.evaluate("Y+", 0.5, 3.0, 19.9, 20.0)   # debounce count 1
        monitor.evaluate("Y+", 0.5, 3.0, 19.9, 20.0)   # debounce count 2 -> fires
        assert faults and faults[0][0] == "Y+"

    def test_no_ramp_engine_never_suspends(self, qapp):
        monitor = RegulationMonitor(ramp_engine=None, debounce_windows=1)
        faults = []
        monitor.fault_detected.connect(lambda *a: faults.append(a))
        monitor.evaluate("X+", 0.01, 3.0, 0.1, 20.0)
        assert faults


class TestReset:
    def test_reset_one_label_clears_only_that_label(self, qapp):
        monitor = RegulationMonitor(debounce_windows=5)
        monitor.evaluate("X+", 0.01, 3.0, 0.1, 20.0)
        monitor.evaluate("Y+", 0.01, 3.0, 0.1, 20.0)
        monitor.reset("X+")
        assert "X+" not in monitor._consec
        assert "Y+" in monitor._consec

    def test_reset_all_clears_everything(self, qapp):
        monitor = RegulationMonitor(debounce_windows=5)
        monitor.evaluate("X+", 0.01, 3.0, 0.1, 20.0)
        monitor.evaluate("Y+", 0.01, 3.0, 0.1, 20.0)
        monitor.reset()
        assert not monitor._consec


class TestIdleAndOk:
    def test_idle_below_arm_threshold_never_fires(self, qapp):
        monitor = RegulationMonitor(debounce_windows=1, arm_threshold_kv=0.1)
        faults = []
        monitor.fault_detected.connect(lambda *a: faults.append(a))
        state = monitor.evaluate("X+", 0.0, 0.0, 0.0, 20.0)
        assert state == "idle"
        assert not faults

    def test_healthy_window_returns_ok(self, qapp):
        monitor = RegulationMonitor(debounce_windows=1)
        state = monitor.evaluate("X+", 2.0, 2.0, 4.0, 20.0)
        assert state == "ok"
