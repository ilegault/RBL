"""
test_cup_acquisition.py
Unit tests for the Faraday cup acquisition state machine and detector.

WHY THIS EXISTS
---------------
ADR 0002 decision 6 & ADR 0003 decision 5: the acquisition state machine and
detector are pure Python objects with no Qt and no clock dependencies. All
readings are passed via a single unified CupReading frozen structure. This
allows exact, deterministic verification of debounce, release, hysteresis,
force start/stop, and disconnection handling without any reliance on sleep()
calls, while decoupling the state machine from specific detector implementations.
"""
from dataclasses import FrozenInstanceError

import pytest

from rbl.config.cup_config import (
    CUP_ARM_THRESHOLD_A,
    CUP_RELEASE_THRESHOLD_A,
)
from rbl.services.cup_acquisition import (
    CupAcquisitionStateMachine,
    CupDetector,
    CupReading,
    RunClosed,
    RunOpened,
)


class TestCupReading:
    """Direct tests for the unified CupReading frozen structure."""

    def test_frozen_immutability(self):
        reading = CupReading(t=1.0, current=1.0e-6)
        with pytest.raises(FrozenInstanceError):
            reading.t = 2.0  # type: ignore[misc]

    def test_default_fields(self):
        reading = CupReading(t=1.0)
        assert reading.t == 1.0
        assert reading.timestamp == 1.0
        assert reading.current is None
        assert not reading.over_range
        assert reading.connected
        assert reading.confirmed_position is None
        assert reading.auto_mode is None

    def test_timestamp_alias(self):
        r1 = CupReading(t=5.5)
        assert r1.timestamp == 5.5

        r2 = CupReading(t=0.0, timestamp=12.3)
        assert r2.t == 12.3
        assert r2.timestamp == 12.3

    def test_future_position_fields_ignored_silently_by_cup_detector(self):
        """Fields added for future position detectors are ignored by CupDetector."""
        det = CupDetector(arm_debounce_s=1.0, arm_threshold=0.5e-6)
        reading = CupReading(
            t=10.0,
            current=1.0e-6,
            confirmed_position="IN",
            auto_mode=True,
        )
        assert not det.update(reading)
        assert det.arm_pending


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
        assert not det.update(CupReading(t=0.0, current=0.1e-6))
        assert not det.arm_pending

        # Spike starts
        assert not det.update(CupReading(t=1.0, current=1.0e-6))
        assert det.arm_pending
        assert not det.cup_in_beam

        # Spike mid-way
        assert not det.update(CupReading(t=1.5, current=1.2e-6))
        assert det.arm_pending
        assert not det.cup_in_beam

        # Spike drops below threshold at t=1.8 (only 0.8s elapsed)
        assert not det.update(CupReading(t=1.8, current=0.1e-6))
        assert not det.arm_pending
        assert not det.cup_in_beam

        # Remains below threshold
        assert not det.update(CupReading(t=3.0, current=0.1e-6))
        assert not det.cup_in_beam

    def test_sustained_current_arms_detector(self):
        """Current sustained above arm threshold for >= arm_debounce_s arms detector."""
        det = CupDetector(arm_debounce_s=1.0, arm_threshold=0.5e-6)
        assert not det.update(CupReading(t=10.0, current=1.0e-6))
        assert det.arm_pending

        # At t=10.9 (0.9s elapsed, < 1.0s)
        assert not det.update(CupReading(t=10.9, current=1.0e-6))
        assert not det.cup_in_beam

        # At t=11.0 (1.0s elapsed >= 1.0s)
        assert det.update(CupReading(t=11.0, current=1.0e-6))
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
        det.update(CupReading(t=0.0, current=1.0e-6))
        det.update(CupReading(t=1.0, current=1.0e-6))
        assert det.cup_in_beam

        # Drop current to 0.35 µA (between 0.25 µA and 0.5 µA)
        det.update(CupReading(t=2.0, current=0.35e-6))
        assert det.cup_in_beam
        assert not det.release_pending

        det.update(CupReading(t=10.0, current=0.35e-6))
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
        det.update(CupReading(t=0.0, current=1.0e-6))
        det.update(CupReading(t=1.0, current=1.0e-6))
        assert det.cup_in_beam

        # Drop below release threshold at t=5.0
        det.update(CupReading(t=5.0, current=0.1e-6))
        assert det.cup_in_beam
        assert det.release_pending

        # Still below at t=7.0 (2.0s elapsed < 3.0s)
        det.update(CupReading(t=7.0, current=0.1e-6))
        assert det.cup_in_beam
        assert det.release_pending

        # Recovers to beam current at t=7.5
        det.update(CupReading(t=7.5, current=1.0e-6))
        assert det.cup_in_beam
        assert not det.release_pending

        # Remains in beam at t=12.0
        det.update(CupReading(t=12.0, current=1.0e-6))
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
        det.update(CupReading(t=0.0, current=1.0e-6))
        det.update(CupReading(t=1.0, current=1.0e-6))
        assert det.cup_in_beam

        # Drop below release threshold at t=5.0
        det.update(CupReading(t=5.0, current=0.0))
        det.update(CupReading(t=7.9, current=0.0))
        assert det.cup_in_beam

        # At t=8.0 (3.0s elapsed >= 3.0s)
        assert not det.update(CupReading(t=8.0, current=0.0))
        assert not det.cup_in_beam
        assert not det.release_pending

    def test_over_range_treated_as_beam_present(self):
        """Over-range reading is treated as high beam current."""
        det = CupDetector(arm_debounce_s=1.0, arm_threshold=0.5e-6)
        det.update(CupReading(t=0.0, current=None, over_range=True))
        assert det.arm_pending
        assert not det.cup_in_beam

        det.update(CupReading(t=1.0, current=None, over_range=True))
        assert det.cup_in_beam

    def test_reset(self):
        det = CupDetector()
        det.update(CupReading(t=0.0, current=1.0e-6))
        det.update(CupReading(t=1.0, current=1.0e-6))
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
        evt = sm.update(CupReading(t=0.0, current=0.0))
        assert evt is None
        assert not sm.is_acquiring

        # t=1.0: insertion starts (current rises to 1.0 µA > 0.5 µA)
        evt = sm.update(CupReading(t=1.0, current=1.0e-6))
        assert evt is None
        assert not sm.is_acquiring

        # t=1.5: debounce mid-way
        evt = sm.update(CupReading(t=1.5, current=1.0e-6))
        assert evt is None
        assert not sm.is_acquiring

        # t=2.0: debounce completed (1.0s elapsed) -> Run 1 begins!
        evt = sm.update(CupReading(t=2.0, current=1.0e-6))
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
        evt = sm.update(CupReading(t=5.0, current=1.2e-6))
        assert evt is None
        assert sm.is_acquiring

        # t=10.0: cup withdrawn (current drops to 0.0 A < 0.25 µA)
        evt = sm.update(CupReading(t=10.0, current=0.0))
        assert evt is None
        assert sm.is_acquiring  # still acquiring during release interval

        # t=12.0: 2.0s into release interval
        evt = sm.update(CupReading(t=12.0, current=0.0))
        assert evt is None
        assert sm.is_acquiring

        # t=13.0: release interval completed (3.0s elapsed) -> Run 1 closes!
        evt = sm.update(CupReading(t=13.0, current=0.0))
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
        sm.update(CupReading(t=0.0, current=1.0e-6))
        evt1 = sm.update(CupReading(t=1.0, current=1.0e-6))
        assert isinstance(evt1, RunOpened)
        assert evt1.run_id == 1

        sm.update(CupReading(t=5.0, current=0.0))
        evt1_close = sm.update(CupReading(t=8.0, current=0.0))
        assert isinstance(evt1_close, RunClosed)
        assert evt1_close.run_id == 1

        # Run 2
        sm.update(CupReading(t=10.0, current=2.0e-6))
        evt2 = sm.update(CupReading(t=11.0, current=2.0e-6))
        assert isinstance(evt2, RunOpened)
        assert evt2.run_id == 2
        assert sm.current_run_id == 2

        sm.update(CupReading(t=20.0, current=0.0))
        evt2_close = sm.update(CupReading(t=23.0, current=0.0))
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
        evt = sm.update(CupReading(t=110.0, current=0.0))
        assert evt is None
        assert sm.is_acquiring

    def test_force_stop_above_threshold(self):
        """Force stop closes an active run immediately, suppressing immediate auto-reopen."""
        sm = CupAcquisitionStateMachine()

        # Auto-start Run 1
        sm.update(CupReading(t=0.0, current=1.0e-6))
        sm.update(CupReading(t=1.0, current=1.0e-6))
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
        evt = sm.update(CupReading(t=7.0, current=1.0e-6))
        assert evt is None
        assert not sm.is_acquiring

        # Once cup is withdrawn and release interval elapses, suppression is cleared
        sm.update(CupReading(t=10.0, current=0.0))
        sm.update(CupReading(t=13.0, current=0.0))
        assert not sm.cup_in_beam

        # Next insertion starts Run 2 cleanly
        sm.update(CupReading(t=20.0, current=1.0e-6))
        evt2 = sm.update(CupReading(t=21.0, current=1.0e-6))
        assert isinstance(evt2, RunOpened)
        assert evt2.run_id == 2
        assert sm.is_acquiring

    def test_disconnection_mid_run_closes_run(self):
        """A disconnection mid-run closes the run immediately."""
        sm = CupAcquisitionStateMachine()

        # Start Run 1
        sm.update(CupReading(t=0.0, current=1.0e-6))
        sm.update(CupReading(t=1.0, current=1.0e-6))
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

        evt = sm.update(CupReading(t=2.0, current=1.0e-6, connected=False))
        assert isinstance(evt, RunClosed)
        assert evt.run_id == 1
        assert evt.reason == "disconnected"
        assert not sm.is_acquiring

    def test_one_second_insertion_opens_no_run_adr0003_defect(self):
        """A 1.0-second sampling insertion opens no run under current-based inference.

        ADR 0003 Decision 6 notes why this limitation is documented rather than
        fixed here: the current-based detector requires a 1.0 s debounce
        (CUP_ARM_DEBOUNCE_S). A 1.0-second insertion returns to baseline before or
        at the debounce boundary, so no run ever opens. This documents why confirmed
        position feedback (ADR 0003) is needed for short sampling insertions.
        """
        sm = CupAcquisitionStateMachine()
        assert not sm.is_acquiring

        # Baseline before insertion
        assert sm.update(CupReading(t=0.0, current=0.0)) is None

        # 1.0-second insertion begins at t=1.0 with current above arm threshold
        assert sm.update(CupReading(t=1.0, current=1.0e-6)) is None
        assert sm.update(CupReading(t=1.5, current=1.0e-6)) is None
        assert sm.update(CupReading(t=1.99, current=1.0e-6)) is None

        # At t=2.0 (1.0 s after insertion began), cup is withdrawn and current drops to baseline
        assert sm.update(CupReading(t=2.0, current=0.0)) is None
        assert sm.update(CupReading(t=3.0, current=0.0)) is None

        # Defect pinned: no run was ever opened
        assert not sm.is_acquiring
        assert sm.current_run_id is None
        assert sm.active_run is None

    def test_three_second_insertion_tail_of_baseline_adr0003_defect(self):
        """A 3.0-second insertion opens 1.0 s late and closes 3.0 s after withdrawal.

        ADR 0003 Decision 6 / Section 'Inference cannot bound a short insertion':
        Under current inference, a 3.0 s insertion (t=1.0 to t=4.0) opens at t=2.0
        (~1.0 s debounce delay) and closes at t=7.0 (~3.0 s release delay after
        withdrawal at t=4.0). This pins the 'tail-of-baseline' defect where 3.0 s
        of zero/baseline current is recorded as part of the run.
        """
        sm = CupAcquisitionStateMachine()
        assert not sm.is_acquiring

        # Baseline
        assert sm.update(CupReading(t=0.0, current=0.0)) is None

        # Insertion begins at t=1.0 (cup in beam for 3.0 s, until t=4.0)
        assert sm.update(CupReading(t=1.0, current=1.0e-6)) is None
        assert sm.update(CupReading(t=1.5, current=1.0e-6)) is None

        # Run opens ~1.0 s after insertion (at t=2.0, satisfying 1.0 s debounce)
        evt_open = sm.update(CupReading(t=2.0, current=1.0e-6))
        assert isinstance(evt_open, RunOpened)
        assert evt_open.t == 2.0
        assert sm.is_acquiring

        # Cup remains in beam until t=4.0
        assert sm.update(CupReading(t=3.0, current=1.0e-6)) is None

        # Cup withdrawn at t=4.0 (current drops to baseline 0.0)
        # Release interval is 3.0 s, so run remains open
        assert sm.update(CupReading(t=4.0, current=0.0)) is None
        assert sm.is_acquiring
        assert sm.update(CupReading(t=5.0, current=0.0)) is None
        assert sm.is_acquiring
        assert sm.update(CupReading(t=6.0, current=0.0)) is None
        assert sm.is_acquiring

        # Run closes ~3.0 s after withdrawal (at t=7.0)
        evt_close = sm.update(CupReading(t=7.0, current=0.0))
        assert isinstance(evt_close, RunClosed)
        assert evt_close.t == 7.0
        assert evt_close.reason == "released"
        assert not sm.is_acquiring
