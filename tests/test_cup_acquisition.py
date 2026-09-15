"""
test_cup_acquisition.py
Unit tests for the Faraday cup acquisition state machine and detector.

WHY THIS EXISTS
---------------
ADR 0002 decision 6: the acquisition state machine and detector are pure Python
objects with no Qt and no clock dependencies. All timestamps are passed as
explicit floats. This allows exact, deterministic verification of debounce,
release, hysteresis, force start/stop, and disconnection handling without any
reliance on sleep() calls.
"""
import pytest

from rbl.config.cup_config import (
    CUP_ARM_THRESHOLD_A,
    CUP_RELEASE_THRESHOLD_A,
)
from rbl.services.cup_acquisition import (
    CupAcquisitionStateMachine,
    CupDetector,
    RunClosed,
    RunOpened,
)


class TestCupDetector:
    """Direct tests for pure CupDetector."""

    def test_threshold_validation(self):
        """Release threshold must be strictly less than arm threshold."""
        with pytest.raises(ValueError, match="strictly less"):
            CupDetector(arm_threshold=0.5e-6, release_threshold=0.5e-6)

        with pytest.raises(ValueError, match="strictly less"):
            CupDetector(arm_threshold=0.5e-6, release_threshold=0.6e-6)

        # Valid constructor
        det = CupDetector(arm_threshold=0.5e-6, release_threshold=0.25e-6)
        assert det.arm_threshold == 0.5e-6
        assert det.release_threshold == 0.25e-6

    def test_initial_state(self):
        det = CupDetector()
        assert not det.cup_in_beam
        assert not det.arm_pending
        assert not det.release_pending

    def test_spike_shorter_than_debounce_does_not_arm(self):
        """A spike above arm threshold shorter than debounce does not arm."""
        det = CupDetector(arm_debounce_s=1.0, arm_threshold=0.5e-6)
        # Below threshold
        assert not det.update(current=0.1e-6, t=0.0)
        assert not det.arm_pending

        # Spike starts
        assert not det.update(current=1.0e-6, t=1.0)
        assert det.arm_pending
        assert not det.cup_in_beam

        # Spike mid-way
        assert not det.update(current=1.2e-6, t=1.5)
        assert det.arm_pending
        assert not det.cup_in_beam

        # Spike drops below threshold at t=1.8 (only 0.8s elapsed)
        assert not det.update(current=0.1e-6, t=1.8)
        assert not det.arm_pending
        assert not det.cup_in_beam

        # Remains below threshold
        assert not det.update(current=0.1e-6, t=3.0)
        assert not det.cup_in_beam

    def test_sustained_current_arms_detector(self):
        """Current sustained above arm threshold for >= arm_debounce_s arms detector."""
        det = CupDetector(arm_debounce_s=1.0, arm_threshold=0.5e-6)
        assert not det.update(current=1.0e-6, t=10.0)
        assert det.arm_pending

        # At t=10.9 (0.9s elapsed, < 1.0s)
        assert not det.update(current=1.0e-6, t=10.9)
        assert not det.cup_in_beam

        # At t=11.0 (1.0s elapsed >= 1.0s)
        assert det.update(current=1.0e-6, t=11.0)
        assert det.cup_in_beam
        assert not det.arm_pending

    def test_hysteresis_dwelling_between_thresholds(self):
        """Current dwelling between arm and release thresholds maintains in_beam state."""
        det = CupDetector(
            arm_threshold=0.5e-6,
            release_threshold=0.25e-6,
            arm_debounce_s=1.0,
            release_interval_s=3.0,
        )
        # Arm detector
        det.update(current=1.0e-6, t=0.0)
        det.update(current=1.0e-6, t=1.0)
        assert det.cup_in_beam

        # Drop current to 0.35 µA (between 0.25 µA and 0.5 µA)
        det.update(current=0.35e-6, t=2.0)
        assert det.cup_in_beam
        assert not det.release_pending

        det.update(current=0.35e-6, t=10.0)
        assert det.cup_in_beam
        assert not det.release_pending

    def test_withdrawal_shorter_than_release_interval_does_not_release(self):
        """A temporary current drop below release threshold for < release interval
        does not release.
        """
        det = CupDetector(
            arm_threshold=0.5e-6,
            release_threshold=0.25e-6,
            arm_debounce_s=1.0,
            release_interval_s=3.0,
        )
        # Arm
        det.update(current=1.0e-6, t=0.0)
        det.update(current=1.0e-6, t=1.0)
        assert det.cup_in_beam

        # Drop below release threshold at t=5.0
        det.update(current=0.1e-6, t=5.0)
        assert det.cup_in_beam
        assert det.release_pending

        # Still below at t=7.0 (2.0s elapsed < 3.0s)
        det.update(current=0.1e-6, t=7.0)
        assert det.cup_in_beam
        assert det.release_pending

        # Recovers to beam current at t=7.5
        det.update(current=1.0e-6, t=7.5)
        assert det.cup_in_beam
        assert not det.release_pending

        # Remains in beam at t=12.0
        det.update(current=1.0e-6, t=12.0)
        assert det.cup_in_beam

    def test_sustained_withdrawal_releases_detector(self):
        """Current sustained below release threshold for >= release_interval releases detector."""
        det = CupDetector(
            arm_threshold=0.5e-6,
            release_threshold=0.25e-6,
            arm_debounce_s=1.0,
            release_interval_s=3.0,
        )
        # Arm
        det.update(current=1.0e-6, t=0.0)
        det.update(current=1.0e-6, t=1.0)
        assert det.cup_in_beam

        # Drop below release threshold at t=5.0
        det.update(current=0.0, t=5.0)
        det.update(current=0.0, t=7.9)
        assert det.cup_in_beam

        # At t=8.0 (3.0s elapsed >= 3.0s)
        assert not det.update(current=0.0, t=8.0)
        assert not det.cup_in_beam
        assert not det.release_pending

    def test_over_range_treated_as_beam_present(self):
        """Over-range reading is treated as high beam current."""
        det = CupDetector(arm_debounce_s=1.0, arm_threshold=0.5e-6)
        det.update(current=None, t=0.0, over_range=True)
        assert det.arm_pending
        assert not det.cup_in_beam

        det.update(current=None, t=1.0, over_range=True)
        assert det.cup_in_beam

    def test_reset(self):
        det = CupDetector()
        det.update(current=1.0e-6, t=0.0)
        det.update(current=1.0e-6, t=1.0)
        assert det.cup_in_beam

        det.reset()
        assert not det.cup_in_beam
        assert not det.arm_pending
        assert not det.release_pending


class TestCupAcquisitionStateMachine:
    """Direct tests for CupAcquisitionStateMachine."""

    def test_clean_insertion_and_withdrawal(self):
        """Test full automatic run lifecycle across insertion and withdrawal."""
        sm = CupAcquisitionStateMachine()
        assert not sm.is_acquiring
        assert sm.current_run_id is None

        # t=0: idle baseline
        evt = sm.update(current=0.0, t=0.0)
        assert evt is None
        assert not sm.is_acquiring

        # t=1.0: insertion starts (current rises to 1.0 µA > 0.5 µA)
        evt = sm.update(current=1.0e-6, t=1.0)
        assert evt is None
        assert not sm.is_acquiring

        # t=1.5: debounce mid-way
        evt = sm.update(current=1.0e-6, t=1.5)
        assert evt is None
        assert not sm.is_acquiring

        # t=2.0: debounce completed (1.0s elapsed) -> Run 1 begins!
        evt = sm.update(current=1.0e-6, t=2.0)
        assert isinstance(evt, RunOpened)
        assert evt.run_id == 1
        assert evt.t == 2.0
        assert evt.arm_threshold == CUP_ARM_THRESHOLD_A
        assert evt.release_threshold == CUP_RELEASE_THRESHOLD_A
        assert not evt.forced
        assert sm.is_acquiring
        assert sm.current_run_id == 1
        assert sm.active_run is not None
        assert sm.active_run.start_time == 2.0

        # t=3.0 .. 10.0: ongoing run
        evt = sm.update(current=1.2e-6, t=5.0)
        assert evt is None
        assert sm.is_acquiring

        # t=10.0: cup withdrawn (current drops to 0.0 A < 0.25 µA)
        evt = sm.update(current=0.0, t=10.0)
        assert evt is None
        assert sm.is_acquiring  # still acquiring during release interval

        # t=12.0: 2.0s into release interval
        evt = sm.update(current=0.0, t=12.0)
        assert evt is None
        assert sm.is_acquiring

        # t=13.0: release interval completed (3.0s elapsed) -> Run 1 closes!
        evt = sm.update(current=0.0, t=13.0)
        assert isinstance(evt, RunClosed)
        assert evt.run_id == 1
        assert evt.t == 13.0
        assert evt.reason == "released"
        assert not sm.is_acquiring
        assert sm.current_run_id is None

    def test_consecutive_runs_increment_run_id(self):
        """Subsequent insertions produce incrementing run IDs (1, 2, ...)."""
        sm = CupAcquisitionStateMachine()

        # Run 1
        sm.update(current=1.0e-6, t=0.0)
        evt1 = sm.update(current=1.0e-6, t=1.0)
        assert isinstance(evt1, RunOpened)
        assert evt1.run_id == 1

        sm.update(current=0.0, t=5.0)
        evt1_close = sm.update(current=0.0, t=8.0)
        assert isinstance(evt1_close, RunClosed)
        assert evt1_close.run_id == 1

        # Run 2
        sm.update(current=2.0e-6, t=10.0)
        evt2 = sm.update(current=2.0e-6, t=11.0)
        assert isinstance(evt2, RunOpened)
        assert evt2.run_id == 2
        assert sm.current_run_id == 2

        sm.update(current=0.0, t=20.0)
        evt2_close = sm.update(current=0.0, t=23.0)
        assert isinstance(evt2_close, RunClosed)
        assert evt2_close.run_id == 2
        assert not sm.is_acquiring

    def test_force_start_below_threshold(self):
        """Force start opens a run immediately even when current is zero."""
        sm = CupAcquisitionStateMachine()
        assert not sm.is_acquiring

        evt = sm.force_start(t=100.0)
        assert isinstance(evt, RunOpened)
        assert evt.run_id == 1
        assert evt.t == 100.0
        assert evt.forced
        assert sm.is_acquiring
        assert sm.current_run_id == 1

        # Calling force_start again while acquiring is a no-op
        assert sm.force_start(t=105.0) is None

        # Samples at current = 0.0 do not close a forced run automatically
        evt = sm.update(current=0.0, t=110.0)
        assert evt is None
        assert sm.is_acquiring

    def test_force_stop_above_threshold(self):
        """Force stop closes an active run immediately, suppressing immediate auto-reopen."""
        sm = CupAcquisitionStateMachine()

        # Auto-start Run 1
        sm.update(current=1.0e-6, t=0.0)
        sm.update(current=1.0e-6, t=1.0)
        assert sm.is_acquiring

        # Force stop at t=5.0 while current is still high
        evt = sm.force_stop(t=5.0)
        assert isinstance(evt, RunClosed)
        assert evt.run_id == 1
        assert evt.t == 5.0
        assert evt.reason == "forced_stop"
        assert not sm.is_acquiring

        # Calling force_stop again when not acquiring is a no-op
        assert sm.force_stop(t=6.0) is None

        # Subsequent updates while current is still high do NOT immediately reopen a run
        evt = sm.update(current=1.0e-6, t=7.0)
        assert evt is None
        assert not sm.is_acquiring

        # Once cup is withdrawn and release interval elapses, suppression is cleared
        sm.update(current=0.0, t=10.0)
        sm.update(current=0.0, t=13.0)
        assert not sm.cup_in_beam

        # Next insertion starts Run 2 cleanly
        sm.update(current=1.0e-6, t=20.0)
        evt2 = sm.update(current=1.0e-6, t=21.0)
        assert isinstance(evt2, RunOpened)
        assert evt2.run_id == 2
        assert sm.is_acquiring

    def test_disconnection_mid_run_closes_run(self):
        """A disconnection mid-run closes the run immediately."""
        sm = CupAcquisitionStateMachine()

        # Start Run 1
        sm.update(current=1.0e-6, t=0.0)
        sm.update(current=1.0e-6, t=1.0)
        assert sm.is_acquiring

        # Disconnect at t=5.0
        evt = sm.disconnect(t=5.0)
        assert isinstance(evt, RunClosed)
        assert evt.run_id == 1
        assert evt.t == 5.0
        assert evt.reason == "disconnected"
        assert not sm.is_acquiring
        assert sm.current_run_id is None

        # Disconnecting while already idle returns None
        assert sm.disconnect(t=6.0) is None

    def test_update_with_connected_false_closes_run(self):
        """Calling update with connected=False closes the run."""
        sm = CupAcquisitionStateMachine()
        sm.force_start(t=1.0)
        assert sm.is_acquiring

        evt = sm.update(current=1.0e-6, t=2.0, connected=False)
        assert isinstance(evt, RunClosed)
        assert evt.run_id == 1
        assert evt.reason == "disconnected"
        assert not sm.is_acquiring
