"""
cup_acquisition.py
Pure state machine and detector for Faraday cup threshold-triggered acquisition runs.

WHY THIS EXISTS
---------------
ADR 0002 records the physical reality of the Right Beam Line's Faraday cup:
the cup is inserted manually roughly every five minutes and carries no position
feedback sensor. The application must infer insertion from the measured current,
starting an acquisition run when the cup enters the beam and closing it when the
cup is withdrawn.

ADR 0002 DECISION 6: STRUCTURAL SEPARATION
-------------------------------------------
The run logic is strictly separated into two components:
1. CupDetector: computes and exposes ONE boolean value: `cup_in_beam`. Today this is
   inferred from measured current with debounce and hysteresis. When a future actuator
   or limit switch is fitted, only this detector is replaced; the acquisition state
   machine itself requires zero changes.
2. CupAcquisitionStateMachine: manages run lifecycles (run ID generation, start/stop,
   manual override via force start / force stop, and connection loss handling).

PURE OBJECT: NO QT, NO CLOCK
-----------------------------
This module contains pure Python objects only. It does NOT import PySide6/Qt and it
does NOT call the system clock (`time.time()`, `datetime.now()`). All timestamps `t`
are passed as explicit arguments. This guarantees that debounce, release, and run
lifecycles are testable deterministically and instantaneously without any `sleep()` calls.
"""
from __future__ import annotations

from dataclasses import dataclass

from rbl.config.cup_config import (
    CUP_ARM_DEBOUNCE_S,
    CUP_ARM_THRESHOLD_A,
    CUP_RELEASE_INTERVAL_S,
    CUP_RELEASE_THRESHOLD_A,
)


@dataclass(frozen=True)
class CupRunInfo:
    """Information about an active or completed acquisition run."""
    run_id: int
    start_time: float
    arm_threshold: float
    release_threshold: float
    forced: bool = False


@dataclass(frozen=True)
class RunOpened:
    """Event emitted/returned when an acquisition run begins."""
    run_id: int
    t: float
    arm_threshold: float
    release_threshold: float
    forced: bool = False


@dataclass(frozen=True)
class RunClosed:
    """Event emitted/returned when an acquisition run ends."""
    run_id: int
    t: float
    reason: str  # "released", "forced_stop", "disconnected"


class CupDetector:
    """Infers cup beam presence from current with debounce and hysteresis.

    Exposes one primary property: `cup_in_beam` (ADR 0002 Decision 6).
    """

    def __init__(
        self,
        arm_threshold: float = CUP_ARM_THRESHOLD_A,
        release_threshold: float = CUP_RELEASE_THRESHOLD_A,
        arm_debounce_s: float = CUP_ARM_DEBOUNCE_S,
        release_interval_s: float = CUP_RELEASE_INTERVAL_S,
    ) -> None:
        if release_threshold >= arm_threshold:
            raise ValueError(
                f"Release threshold ({release_threshold}) must be strictly less than "
                f"arm threshold ({arm_threshold}) for hysteresis."
            )
        self.arm_threshold = arm_threshold
        self.release_threshold = release_threshold
        self.arm_debounce_s = arm_debounce_s
        self.release_interval_s = release_interval_s

        self._in_beam: bool = False
        self._arm_start_t: float | None = None
        self._release_start_t: float | None = None

    @property
    def cup_in_beam(self) -> bool:
        """True if the cup is currently detected in the beam."""
        return self._in_beam

    @property
    def arm_pending(self) -> bool:
        """True if the current is above arm threshold but debounce has not elapsed."""
        return not self._in_beam and self._arm_start_t is not None

    @property
    def release_pending(self) -> bool:
        """True if current is below release threshold but release interval has not elapsed."""
        return self._in_beam and self._release_start_t is not None

    def update(
        self,
        current: float | None,
        t: float,
        over_range: bool = False,
    ) -> bool:
        """Update detector with a new current reading at timestamp `t`.

        Parameters:
            current: Measured current in Amperes, or None if invalid/unavailable.
            t: Monotonic timestamp in seconds.
            over_range: True if reading is over-range (indicates strong beam present).

        Returns:
            The updated `cup_in_beam` boolean.
        """
        is_above_arm = over_range or (
            current is not None and current >= self.arm_threshold
        )
        is_below_release = not over_range and (
            current is None or current < self.release_threshold
        )

        if not self._in_beam:
            if is_above_arm:
                if self._arm_start_t is None:
                    self._arm_start_t = t
                if (t - self._arm_start_t) >= self.arm_debounce_s:
                    self._in_beam = True
                    self._arm_start_t = None
                    self._release_start_t = None
            else:
                self._arm_start_t = None
        else:
            if is_below_release:
                if self._release_start_t is None:
                    self._release_start_t = t
                if (t - self._release_start_t) >= self.release_interval_s:
                    self._in_beam = False
                    self._release_start_t = None
                    self._arm_start_t = None
            else:
                self._release_start_t = None

        return self._in_beam

    def reset(self) -> None:
        """Reset detector state to initial out-of-beam baseline."""
        self._in_beam = False
        self._arm_start_t = None
        self._release_start_t = None


class CupAcquisitionStateMachine:
    """Pure state machine managing Faraday cup acquisition runs."""

    def __init__(self, detector: CupDetector | None = None) -> None:
        self.detector = detector if detector is not None else CupDetector()
        self._active_run: CupRunInfo | None = None
        self._next_run_id: int = 1
        self._forced_run: bool = False
        self._force_stopped: bool = False

    @property
    def is_acquiring(self) -> bool:
        """True if an acquisition run is currently open."""
        return self._active_run is not None

    @property
    def cup_in_beam(self) -> bool:
        """True if detector currently sees cup in beam."""
        return self.detector.cup_in_beam

    @property
    def active_run(self) -> CupRunInfo | None:
        """The currently active run info, or None if idle."""
        return self._active_run

    @property
    def current_run_id(self) -> int | None:
        """The active run ID, or None if idle."""
        return self._active_run.run_id if self._active_run else None

    def update(
        self,
        current: float | None,
        t: float,
        over_range: bool = False,
        connected: bool = True,
    ) -> RunOpened | RunClosed | None:
        """Process a reading update at timestamp `t`.

        Returns:
            RunOpened if a new run began this tick, RunClosed if an active run
            ended this tick, or None if no run transition occurred.
        """
        if not connected:
            return self.disconnect(t)

        in_beam = self.detector.update(current=current, t=t, over_range=over_range)

        # Once cup is withdrawn from beam, clear any previous force-stop suppression
        if not in_beam:
            self._force_stopped = False

        if self._active_run is None:
            # Idle: check if detector opened a run and force-stop suppression is inactive
            if in_beam and not self._force_stopped:
                run_id = self._next_run_id
                self._next_run_id += 1
                self._forced_run = False
                self._active_run = CupRunInfo(
                    run_id=run_id,
                    start_time=t,
                    arm_threshold=self.detector.arm_threshold,
                    release_threshold=self.detector.release_threshold,
                    forced=False,
                )
                return RunOpened(
                    run_id=run_id,
                    t=t,
                    arm_threshold=self.detector.arm_threshold,
                    release_threshold=self.detector.release_threshold,
                    forced=False,
                )
        else:
            # Acquiring: if not manually forced, close run when cup leaves beam
            if not self._forced_run and not in_beam:
                closed_run = self._active_run
                self._active_run = None
                return RunClosed(
                    run_id=closed_run.run_id,
                    t=t,
                    reason="released",
                )

        return None

    def force_start(self, t: float) -> RunOpened | None:
        """Force-start an acquisition run regardless of detector / current."""
        if self._active_run is not None:
            return None  # Already acquiring

        run_id = self._next_run_id
        self._next_run_id += 1
        self._forced_run = True
        self._force_stopped = False
        self._active_run = CupRunInfo(
            run_id=run_id,
            start_time=t,
            arm_threshold=self.detector.arm_threshold,
            release_threshold=self.detector.release_threshold,
            forced=True,
        )
        return RunOpened(
            run_id=run_id,
            t=t,
            arm_threshold=self.detector.arm_threshold,
            release_threshold=self.detector.release_threshold,
            forced=True,
        )

    def force_stop(self, t: float) -> RunClosed | None:
        """Force-stop an active acquisition run regardless of detector / current."""
        if self._active_run is None:
            return None  # Not acquiring

        closed_run = self._active_run
        self._active_run = None
        self._forced_run = False
        self._force_stopped = True  # Suppress immediate auto-restart if cup still in beam
        return RunClosed(
            run_id=closed_run.run_id,
            t=t,
            reason="forced_stop",
        )

    def disconnect(self, t: float) -> RunClosed | None:
        """Handle instrument disconnection, closing active run if open."""
        self.detector.reset()
        self._force_stopped = False
        if self._active_run is not None:
            closed_run = self._active_run
            self._active_run = None
            self._forced_run = False
            return RunClosed(
                run_id=closed_run.run_id,
                t=t,
                reason="disconnected",
            )
        return None

    def reset(self) -> None:
        """Reset state machine and detector completely."""
        self.detector.reset()
        self._active_run = None
        self._next_run_id = 1
        self._forced_run = False
        self._force_stopped = False
