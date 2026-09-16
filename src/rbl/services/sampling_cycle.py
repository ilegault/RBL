"""
sampling_cycle.py
Pure scheduler for the Faraday cup automated sampling cycle.

WHY THIS EXISTS
---------------
ADR 0003 Decision 2: the application now moves the Faraday cup on its own
schedule, which is a genuine increase in what a defect here can cost. That cost
is bounded by two properties this module is required to carry:

1. **No clock inside the scheduler.** Every timestamp is an argument passed
   from the caller — `arm(t)`, `tick(t)`, `notify_manual_insert(t)`, etc.
   A scheduler that calls `time.monotonic()` internally cannot be tested across
   an eight-hour cycle without sleeping for eight hours. The tests for this
   module feed explicit timestamp sequences and assert on exact insertion counts
   and times; that is only possible because no clock is hidden inside.

2. **One event per tick.** `tick(t)` returns at most one action. Callers that
   advance time in large steps must call `tick` repeatedly until it returns
   `None`. This keeps the state transitions atomic and testable.

STATES
------
- DISARMED: construction default; no insertions happen.
- WAITING:  armed; counting down to the next scheduled insertion.
- INSERTING: a scheduled insertion is in progress; dwell is counting down.

MANUAL OVERRIDES
----------------
The operator can insert or retract the cup at any time. The tab calls
`notify_manual_insert(t)` / `notify_manual_retract(t)` whenever the operator
presses Insert / Retract. These methods update the scheduler's model of whether
a manual run is open.

A "manual run" is open from the moment the operator commands cup IN until the
operator commands cup OUT. While a manual run is open:
- A period boundary that falls due is skipped (CycleSkipped returned).
- The skip is not queued; the cycle continues at the next period boundary.

If the operator retracts the cup during a scheduled dwell (scheduler state
INSERTING), the dwell is abandoned and the scheduler transitions back to WAITING
with the next insertion scheduled `period_s` seconds from the override time.

PURE OBJECT: NO QT, NO CLOCK
-----------------------------
This module contains pure Python objects only. It does NOT import PySide6/Qt
and it does NOT call the system clock (`time.time()`, `time.monotonic()`,
`datetime.now()`). All timestamps are passed as explicit arguments.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class CycleState(Enum):
    """State of the sampling cycle scheduler."""

    DISARMED = "disarmed"
    WAITING = "waiting"
    INSERTING = "inserting"


@dataclass(frozen=True)
class CycleInsert:
    """Scheduler action: command the cup IN to begin a scheduled insertion.

    t is the nominal insertion time (the period boundary, not the wall-clock
    instant the caller received the action). Using the nominal time rather than
    the actual tick time keeps insertion timestamps anchored to the schedule,
    so downstream dose arithmetic does not accumulate rounding errors.
    """

    t: float


@dataclass(frozen=True)
class CycleRetract:
    """Scheduler action: command the cup OUT — the dwell has expired.

    t is the nominal retract time (insertion time + dwell_s).
    """

    t: float


@dataclass(frozen=True)
class CycleSkipped:
    """A scheduled insertion was skipped because a manual run was open.

    insertion_due_t is when the insertion was supposed to happen. The scheduler
    advances to the next period boundary without queuing the missed insertion.
    Queuing is explicitly forbidden by the ticket: a queued insertion fires at a
    moment nobody asked for, after the operator has stopped paying attention.
    """

    insertion_due_t: float


class SamplingCycleScheduler:
    """Pure scheduler for the Faraday cup automated sampling cycle.

    Construction leaves the cycle DISARMED. Call `arm(t)` to start.

    Parameters
    ----------
    period_s:
        Seconds between the start of successive insertions. Default 300 s (5 min).
    dwell_s:
        Seconds the cup spends in the beam per insertion. Default 3 s.

    Typical call sequence
    ---------------------
    scheduler = SamplingCycleScheduler()
    scheduler.arm(t=t0)

    # On every actuation state update from the T7:
    result = scheduler.tick(state.t)
    if isinstance(result, CycleInsert):
        beamline.command_cup_in()
    elif isinstance(result, CycleRetract):
        beamline.command_cup_out()
    elif isinstance(result, CycleSkipped):
        session_writer.write_cycle_insertion_skipped(result.insertion_due_t)

    # When operator manually inserts:
    scheduler.notify_manual_insert(t)

    # When operator manually retracts:
    scheduler.notify_manual_retract(t)

    # To stop:
    retract_action = scheduler.stop(t)  # CycleRetract if cup was inserting
    """

    def __init__(
        self,
        period_s: float = 300.0,
        dwell_s: float = 3.0,
    ) -> None:
        self._period_s: float = period_s
        self._dwell_s: float = dwell_s
        self._state: CycleState = CycleState.DISARMED
        self._next_insertion_t: float = float("inf")
        self._current_insertion_t: float = float("inf")  # nominal start of active insertion
        self._dwell_end_t: float = float("inf")
        self._manual_run_open: bool = False

    # ── Read-only properties ───────────────────────────────────────────────

    @property
    def state(self) -> CycleState:
        """Current scheduler state."""
        return self._state

    @property
    def is_armed(self) -> bool:
        """True when the cycle is armed (WAITING or INSERTING)."""
        return self._state != CycleState.DISARMED

    @property
    def period_s(self) -> float:
        """Current cycle period in seconds."""
        return self._period_s

    @property
    def dwell_s(self) -> float:
        """Current dwell duration in seconds."""
        return self._dwell_s

    # ── Configuration setters ──────────────────────────────────────────────

    def set_period(self, period_s: float) -> None:
        """Update the cycle period.

        Takes effect at the next period boundary: the current countdown to the
        next insertion is NOT reset. If the cycle is WAITING, the already-
        scheduled boundary fires at the same time; the period change affects
        every subsequent insertion after that.

        WHY: "Takes effect at the next period boundary, not mid-insertion" is
        a ticket requirement. Resetting the countdown on a period change would
        cause a period-10 operator who accidentally typed a wrong value to see
        an immediate insertion, which is surprising and wastes beam time.
        """
        self._period_s = period_s

    def set_dwell(self, dwell_s: float) -> None:
        """Update the dwell duration.

        Takes effect at the next insertion. A dwell change during an active
        dwell does NOT shorten or lengthen the current insertion.
        """
        self._dwell_s = dwell_s

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def arm(self, t: float) -> None:
        """Arm the cycle. First insertion at t + period_s.

        Arming is always an explicit operator action — this method is never
        called on construction, on connect, or on restoring saved state.
        """
        self._state = CycleState.WAITING
        self._next_insertion_t = t + self._period_s
        self._current_insertion_t = float("inf")
        self._dwell_end_t = float("inf")
        self._manual_run_open = False

    def disarm(self) -> None:
        """Disarm the cycle without commanding the cup.

        The cup is left wherever it is. Callers that need a retract should
        use `stop(t)` instead.
        """
        self._state = CycleState.DISARMED
        self._next_insertion_t = float("inf")
        self._current_insertion_t = float("inf")
        self._dwell_end_t = float("inf")
        self._manual_run_open = False

    def stop(self, t: float) -> CycleRetract | None:
        """Stop the cycle. Returns CycleRetract if the cup needs to be retracted.

        If the scheduler is currently INSERTING (dwell in progress), the caller
        must command the cup OUT. The retract action carries the current time `t`
        so the caller can record the exact stop time.

        If the scheduler is WAITING or DISARMED, no retract is needed and None
        is returned.
        """
        retract = CycleRetract(t=t) if self._state == CycleState.INSERTING else None
        self.disarm()
        return retract

    # ── Manual override notifications ──────────────────────────────────────

    def notify_manual_insert(self, t: float) -> None:
        """Operator commanded cup IN manually. Manual run is now open.

        If the scheduler was INSERTING (scheduled dwell active), the manual
        override abandons the dwell immediately. The scheduler transitions back
        to WAITING with the next insertion rescheduled `period_s` seconds from
        `t`. The cup is now "owned" by the operator; the cycle waits for
        `notify_manual_retract` before the skipping logic clears.
        """
        self._manual_run_open = True
        if self._state == CycleState.INSERTING:
            # Operator took control of an active scheduled dwell.
            # Abandon the dwell; next insertion is one full period from now.
            self._state = CycleState.WAITING
            self._next_insertion_t = t + self._period_s
            self._current_insertion_t = float("inf")
            self._dwell_end_t = float("inf")

    def notify_manual_retract(self, t: float) -> None:  # noqa: ARG002
        """Operator commanded cup OUT manually. Manual run is now closed.

        The cycle continues from its current schedule; `_next_insertion_t` is
        not changed. If the period boundary falls within a few seconds of this
        call, the next insertion fires as scheduled (not skipped).
        """
        self._manual_run_open = False

    # ── Time-to-event queries ──────────────────────────────────────────────

    def time_to_next_insertion(self, t: float) -> float | None:
        """Seconds until the next scheduled insertion, or None.

        Returns None when DISARMED or INSERTING (in which case the cup is
        already in the beam and `time_to_retract` is more informative).
        """
        if self._state == CycleState.WAITING:
            return max(0.0, self._next_insertion_t - t)
        return None

    def time_to_retract(self, t: float) -> float | None:
        """Seconds until the scheduled dwell expires, or None.

        Returns None when not INSERTING.
        """
        if self._state == CycleState.INSERTING:
            return max(0.0, self._dwell_end_t - t)
        return None

    # ── Core tick ─────────────────────────────────────────────────────────

    def tick(self, t: float) -> CycleInsert | CycleRetract | CycleSkipped | None:
        """Advance the scheduler to time ``t``. Returns at most one action.

        If more than one boundary is due (e.g. after a long gap), call tick()
        repeatedly until it returns None.

        Returns
        -------
        CycleInsert   when a period boundary fires and no manual run is open.
        CycleRetract  when the dwell expires.
        CycleSkipped  when a period boundary fires but a manual run is open.
        None          when no boundary has been crossed.
        """
        if self._state == CycleState.DISARMED:
            return None

        if self._state == CycleState.WAITING:
            if t >= self._next_insertion_t:
                due_t = self._next_insertion_t
                if self._manual_run_open:
                    # Manual run is open — skip this insertion, advance schedule.
                    self._next_insertion_t += self._period_s
                    return CycleSkipped(insertion_due_t=due_t)
                else:
                    # Start the scheduled insertion.
                    self._state = CycleState.INSERTING
                    self._current_insertion_t = due_t
                    self._dwell_end_t = due_t + self._dwell_s
                    return CycleInsert(t=due_t)

        elif self._state == CycleState.INSERTING:
            if t >= self._dwell_end_t:
                retract_t = self._dwell_end_t
                # Transition back to WAITING.
                # Next insertion is one period from the start of this insertion,
                # not from the retract time. This keeps the period accurate as
                # "time between successive insertion commands" — the natural
                # meaning of "every N seconds". Using retract_t would add dwell_s
                # to every cycle, so a 300 s period with 3 s dwell would produce
                # 95 insertions per 8 hours instead of 96.
                # set_period() may have changed _period_s since we entered INSERTING;
                # this is the "next period boundary" where that change takes effect.
                self._state = CycleState.WAITING
                self._next_insertion_t = self._current_insertion_t + self._period_s
                self._current_insertion_t = float("inf")
                self._dwell_end_t = float("inf")
                return CycleRetract(t=retract_t)

        return None
