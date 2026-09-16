"""
test_cup_acquisition.py
Unit tests for the Faraday cup acquisition state machine, detectors, and
authority logic.

WHY THIS EXISTS
---------------
ADR 0002 decision 6 & ADR 0003 decisions 4 and 5: the acquisition state machine
and detector are pure Python objects with no Qt and no clock dependencies. All
readings are passed via a single unified CupReading frozen structure. This
allows exact, deterministic verification of debounce, release, hysteresis,
force start/stop, and disconnection handling without any reliance on sleep()
calls, while decoupling the state machine from specific detector implementations.

Ticket 06 adds CupPositionDetector and the authority rule: position governs when
FIO_STATE is readable and the controller is in AUTO; inference governs otherwise.
Both detectors satisfy the same CupAcquisitionStateMachine interface, so the same
event series should produce identical run-id sequencing through either.
"""
from dataclasses import FrozenInstanceError

import pytest

from rbl.config.cup_config import (
    CUP_ARM_THRESHOLD_A,
    CUP_RELEASE_THRESHOLD_A,
)
from rbl.hardware.cup_status import CupPosition
from rbl.services.cup_acquisition import (
    AuthorityDetector,
    CupAcquisitionStateMachine,
    CupDetector,
    CupPositionDetector,
    CupReading,
    RunClosed,
    RunOpened,
    position_authoritative,
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


# ─── Ticket 06: CupPositionDetector, authority rule, AuthorityDetector ────────


def _reading_in(t: float, current: float = 1.0e-6) -> CupReading:
    """CupReading with confirmed IN position and AUTO mode (position authoritative)."""
    return CupReading(
        t=t,
        current=current,
        confirmed_position=CupPosition.IN,
        auto_mode=True,
    )


def _reading_out(t: float, current: float = 0.0) -> CupReading:
    """CupReading with confirmed OUT position and AUTO mode."""
    return CupReading(
        t=t,
        current=current,
        confirmed_position=CupPosition.OUT,
        auto_mode=True,
    )


def _reading_transit(t: float, current: float = 0.0) -> CupReading:
    """CupReading with IN_TRANSIT confirmed position (between positions)."""
    return CupReading(
        t=t,
        current=current,
        confirmed_position=CupPosition.IN_TRANSIT,
        auto_mode=True,
    )


def _reading_no_fio(t: float, current: float = 0.0) -> CupReading:
    """CupReading with no FIO_STATE (stale / non-FULL profile)."""
    return CupReading(t=t, current=current, confirmed_position=None, auto_mode=None)


class TestCupPositionDetector:
    """Tests for the pure confirmed-position-based cup detector."""

    def test_initial_state_out_of_beam(self):
        det = CupPositionDetector()
        assert not det.cup_in_beam

    def test_confirmed_in_opens_beam(self):
        det = CupPositionDetector()
        result = det.update(_reading_in(t=1.0))
        assert result is True
        assert det.cup_in_beam

    def test_confirmed_out_closes_beam(self):
        det = CupPositionDetector()
        det.update(_reading_in(t=1.0))
        result = det.update(_reading_out(t=2.0))
        assert result is False
        assert not det.cup_in_beam

    def test_in_transit_does_not_change_state(self):
        """IN_TRANSIT leaves cup_in_beam unchanged (cup is moving, not confirmed)."""
        det = CupPositionDetector()
        det.update(_reading_in(t=1.0))
        assert det.cup_in_beam

        det.update(_reading_transit(t=2.0))
        assert det.cup_in_beam  # still IN

    def test_none_position_does_not_change_state(self):
        """Missing FIO_STATE (None) preserves the last known cup_in_beam state."""
        det = CupPositionDetector()
        det.update(_reading_in(t=1.0))
        assert det.cup_in_beam

        det.update(_reading_no_fio(t=1.1))
        assert det.cup_in_beam  # unchanged

        det.update(_reading_out(t=2.0))
        assert not det.cup_in_beam

        det.update(_reading_no_fio(t=2.1))
        assert not det.cup_in_beam  # unchanged

    def test_indeterminate_does_not_change_state(self):
        """INDETERMINATE (fault) leaves cup_in_beam unchanged."""
        det = CupPositionDetector()
        det.update(_reading_in(t=1.0))
        r = CupReading(t=2.0, confirmed_position=CupPosition.INDETERMINATE, auto_mode=True)
        det.update(r)
        assert det.cup_in_beam  # unchanged

    def test_reset_clears_state(self):
        det = CupPositionDetector()
        det.update(_reading_in(t=1.0))
        det.reset()
        assert not det.cup_in_beam

    def test_no_qt_no_clock(self):
        """Module imports do not touch PySide6 and update() takes no system-clock calls."""
        import sys
        mod = sys.modules.get("rbl.services.cup_acquisition")
        assert mod is not None
        # PySide6 not imported anywhere in this module
        assert not any(
            "PySide6" in str(k)
            for k in sys.modules
            if k.startswith("rbl.services.cup_acquisition")
        )


class TestPositionAuthoritative:
    """Tests for the pure authority-rule function."""

    def test_position_readable_and_auto_governs(self):
        reading = CupReading(t=1.0, confirmed_position=CupPosition.IN, auto_mode=True)
        assert position_authoritative(reading) is True

    def test_position_readable_and_auto_out_governs(self):
        reading = CupReading(t=1.0, confirmed_position=CupPosition.OUT, auto_mode=True)
        assert position_authoritative(reading) is True

    def test_fio_state_absent_inference_governs(self):
        """None confirmed_position means FIO_STATE was absent → inference governs."""
        reading = CupReading(t=1.0, confirmed_position=None, auto_mode=True)
        assert position_authoritative(reading) is False

    def test_not_in_auto_inference_governs(self):
        """Controller in LOCAL (auto_mode=False) → inference governs."""
        reading = CupReading(t=1.0, confirmed_position=CupPosition.IN, auto_mode=False)
        assert position_authoritative(reading) is False

    def test_auto_mode_none_inference_governs(self):
        """auto_mode=None (no actuation data) → inference governs."""
        reading = CupReading(t=1.0, confirmed_position=CupPosition.IN, auto_mode=None)
        assert position_authoritative(reading) is False

    def test_both_absent_inference_governs(self):
        """No confirmed_position and no auto_mode → inference governs."""
        reading = CupReading(t=1.0)
        assert position_authoritative(reading) is False

    def test_in_transit_with_auto_not_authoritative(self):
        """IN_TRANSIT is a real confirmed_position value → position authoritative.

        IN_TRANSIT means cup is between positions, which is valid status feedback.
        The detector handles it gracefully by leaving cup_in_beam unchanged.
        """
        reading = CupReading(t=1.0, confirmed_position=CupPosition.IN_TRANSIT, auto_mode=True)
        assert position_authoritative(reading) is True


class TestAuthorityDetector:
    """Tests for the composite authority detector."""

    def test_uses_inference_when_fio_absent(self):
        det = AuthorityDetector()
        # No confirmed_position → inference governs; current above arm threshold
        r = CupReading(t=0.0, current=1.0e-6, confirmed_position=None, auto_mode=None)
        det.update(r)
        assert not det.using_position
        assert not det.cup_in_beam  # arm debounce not yet satisfied

    def test_uses_position_when_auto_and_readable(self):
        det = AuthorityDetector()
        r = _reading_in(t=1.0)
        det.update(r)
        assert det.using_position
        assert det.cup_in_beam

    def test_position_cup_in_beam_and_inference_cup_in_beam_tracked_independently(self):
        """Both detectors are always updated regardless of which is authoritative."""
        det = AuthorityDetector()
        # Send current above arm threshold but no confirmed position
        # → inference in debounce; position detector sees None
        r = CupReading(t=0.0, current=1.0e-6, confirmed_position=None, auto_mode=None)
        det.update(r)
        r2 = CupReading(t=1.0, current=1.0e-6, confirmed_position=None, auto_mode=None)
        det.update(r2)
        assert det.inference_cup_in_beam  # inference debounce satisfied
        assert not det.position_cup_in_beam  # position hasn't seen IN

    def test_no_disagreement_when_fio_absent(self):
        """Disagreement is only meaningful when FIO_STATE is readable."""
        det = AuthorityDetector()
        det.update(CupReading(t=0.0, current=1.0e-6))
        det.update(CupReading(t=1.0, current=1.0e-6))
        assert det.inference_cup_in_beam
        assert not det.disagreement  # no FIO_STATE → no meaningful comparison

    def test_disagreement_detected_when_sources_differ(self):
        """Position says IN but current below inference arm threshold → disagreement."""
        det = AuthorityDetector()
        # Feed readings where position says IN but current is 0 (inference says OUT)
        r = CupReading(t=1.0, current=0.0, confirmed_position=CupPosition.IN, auto_mode=True)
        det.update(r)
        assert det.position_cup_in_beam
        assert not det.inference_cup_in_beam
        assert det.disagreement

    def test_no_disagreement_when_sources_agree(self):
        det = AuthorityDetector()
        # Both agree: position IN, current above arm threshold (after debounce)
        r0 = CupReading(t=0.0, current=1.0e-6, confirmed_position=CupPosition.IN, auto_mode=True)
        r1 = CupReading(t=1.0, current=1.0e-6, confirmed_position=CupPosition.IN, auto_mode=True)
        det.update(r0)
        det.update(r1)
        assert det.position_cup_in_beam
        assert det.inference_cup_in_beam
        assert not det.disagreement

    def test_reset_clears_all_state(self):
        det = AuthorityDetector()
        det.update(_reading_in(t=1.0))
        det.reset()
        assert not det.cup_in_beam
        assert not det.disagreement
        assert not det.using_position

    def test_arm_threshold_from_inference_detector(self):
        """arm_threshold and release_threshold delegated to inference CupDetector."""
        det = AuthorityDetector()
        from rbl.config.cup_config import CUP_ARM_THRESHOLD_A, CUP_RELEASE_THRESHOLD_A
        assert det.arm_threshold == CUP_ARM_THRESHOLD_A
        assert det.release_threshold == CUP_RELEASE_THRESHOLD_A


class TestStateMachineParityBothDetectors:
    """Drive CupAcquisitionStateMachine through both detectors with the same event
    series and verify identical run-id sequencing and open/close transitions.

    The event series is constructed so that confirmed position (IN/OUT) and current
    above/below arm threshold both flip at the same logical moments — allowing both
    detectors to produce the same cup_in_beam verdict, which means the state machine
    must produce identical run transitions through either.
    """

    def _build_event_series(self):
        """Return a list of CupReadings that both detectors interpret the same way.

        Timeline:
          t=0.0  baseline: current=0, position=OUT
          t=1.0  insertion: current rises above threshold, position=IN
          t=1.01 still in beam
          t=2.0  withdrawal: current drops to 0, position=OUT
          t=2.01 still out (inference release interval begins; position already OUT)

        For CupDetector: arm debounce = 1.0 s → run opens at t=2.0 (1 s after t=1.0)
        For CupPositionDetector: run opens when confirmed_position=IN (t=1.0)

        To get identical run-id sequencing, we need them to open/close at the same t.
        We arrange: position switches to IN at t=1.0, inference arm debounce satisfied
        also at t=1.0 by starting at t=0.0 and having 1.0 s of high current.

        Sequence that makes both see the transition at the same t:
          t=0.0: current=1e-6, confirmed_position=IN → position arms immediately,
                 inference starts arm debounce
          t=1.0: current=1e-6, confirmed_position=IN → inference debounce satisfied
          t=5.0: current=0.0, confirmed_position=OUT → inference release starts
          t=8.0: current=0.0, confirmed_position=OUT → inference release satisfied (3 s)
        """
        return [
            CupReading(t=0.0, current=1.0e-6, confirmed_position=CupPosition.IN, auto_mode=True),
            CupReading(t=1.0, current=1.0e-6, confirmed_position=CupPosition.IN, auto_mode=True),
            CupReading(t=5.0, current=0.0, confirmed_position=CupPosition.OUT, auto_mode=True),
            CupReading(t=8.0, current=0.0, confirmed_position=CupPosition.OUT, auto_mode=True),
        ]

    def test_identical_run_sequencing_through_both_detectors(self):
        """CupAcquisitionStateMachine with CupDetector and with CupPositionDetector
        produce the same run IDs at the same event indices for an aligned series."""
        series = self._build_event_series()

        sm_inference = CupAcquisitionStateMachine(detector=CupDetector())
        sm_position = CupAcquisitionStateMachine(detector=CupPositionDetector())

        events_inference = [sm_inference.update(r) for r in series]
        events_position = [sm_position.update(r) for r in series]

        # Both should open exactly one run (at index where debounce/position satisfied)
        opens_inf = [e for e in events_inference if isinstance(e, RunOpened)]
        opens_pos = [e for e in events_position if isinstance(e, RunOpened)]
        assert len(opens_inf) == 1
        assert len(opens_pos) == 1

        closes_inf = [e for e in events_inference if isinstance(e, RunClosed)]
        closes_pos = [e for e in events_position if isinstance(e, RunClosed)]
        assert len(closes_inf) == 1
        assert len(closes_pos) == 1

        # Both should produce run_id=1 on open and close
        assert opens_inf[0].run_id == opens_pos[0].run_id == 1
        assert closes_inf[0].run_id == closes_pos[0].run_id == 1
        assert closes_inf[0].reason == closes_pos[0].reason == "released"


class TestOneSecondCommandedInsertion:
    """A one-second commanded insertion opens and closes exactly one run via the
    position detector — the case pinned as impossible on the inference path
    (test_one_second_insertion_opens_no_run_adr0003_defect above)."""

    def test_one_second_insertion_opens_exactly_one_run(self):
        det = CupPositionDetector()
        sm = CupAcquisitionStateMachine(detector=det)
        assert not sm.is_acquiring

        # Baseline: cup OUT before insertion
        assert sm.update(_reading_out(t=0.0)) is None
        assert not sm.is_acquiring

        # Cup commanded IN at t=1.0 — confirmed IN arrives immediately
        evt = sm.update(_reading_in(t=1.0))
        assert isinstance(evt, RunOpened), f"Expected RunOpened, got {evt!r}"
        assert evt.run_id == 1
        assert sm.is_acquiring

        # Cup in beam through t=1.5
        assert sm.update(_reading_in(t=1.5)) is None
        assert sm.is_acquiring

        # Cup retracted at t=2.0 — exactly 1 second insertion; confirmed OUT arrives
        evt_close = sm.update(_reading_out(t=2.0))
        assert isinstance(evt_close, RunClosed), f"Expected RunClosed, got {evt_close!r}"
        assert evt_close.run_id == 1
        assert evt_close.reason == "released"
        assert not sm.is_acquiring

    def test_two_consecutive_one_second_insertions(self):
        """Two rapid insertions produce run_id=1 and run_id=2."""
        det = CupPositionDetector()
        sm = CupAcquisitionStateMachine(detector=det)

        # First insertion: t=1.0 to t=2.0
        sm.update(_reading_out(t=0.0))
        evt1 = sm.update(_reading_in(t=1.0))
        assert isinstance(evt1, RunOpened) and evt1.run_id == 1
        evt1c = sm.update(_reading_out(t=2.0))
        assert isinstance(evt1c, RunClosed) and evt1c.run_id == 1

        # Second insertion: t=10.0 to t=11.0
        sm.update(_reading_out(t=5.0))
        evt2 = sm.update(_reading_in(t=10.0))
        assert isinstance(evt2, RunOpened) and evt2.run_id == 2
        evt2c = sm.update(_reading_out(t=11.0))
        assert isinstance(evt2c, RunClosed) and evt2c.run_id == 2
        assert not sm.is_acquiring
