# 09: Period and dwell edits queue instead of landing mid-insertion

**Blocked by:** None (can start immediately)

**Status:** done

**Read first:** `src/rbl/services/sampling_cycle.py` in full - `set_period`,
`set_dwell`, `arm`, `tick`, and the `CycleState` values. The module docstring and
`set_period`'s docstring already state the rule this ticket makes visible: a change
takes effect at the next period boundary and never resets a countdown in progress.

**What to build:** Queued changes. An operator edits the period or the dwell while the
cycle is armed; the scheduler accepts the value, keeps using the old one until the next
boundary, and can be asked what is queued. Today `set_period` assigns immediately and
the tab has no way to show the operator that their number is not in force yet, which
is the difference between a setting that looks ignored and one that is explained.

**The pure-object requirement is not negotiable:** this scheduler takes every
timestamp as a parameter and calls no clock. An eight-hour cycle with a real clock
inside it cannot be tested, and a cycle never tested across a fault will fail silently
across one. That is why every test below runs in microseconds.

- [x] `set_period(period_s)` and `set_dwell(dwell_s)` store the value as pending
      instead of assigning it. Read-only properties `pending_period_s` and
      `pending_dwell_s` return the queued value, or `None` when nothing is queued. A
      test asserts `period_s` is unchanged immediately after `set_period`, and that
      `pending_period_s` holds the new value.
- [x] `tick(t)` applies a pending period when it crosses a period boundary, and clears
      the pending value. A test arms at `t=0` with period 300, calls `set_period(60)`
      at `t=10`, ticks to `t=299` asserting no insertion, ticks to `t=300` asserting
      exactly one `CycleInsert`, then asserts `period_s == 60.0`,
      `pending_period_s is None`, and that the following insertion falls at `t=360`.
- [x] `tick(t)` applies a pending dwell when it begins an insertion, never during one.
      A test arms with dwell 3.0, ticks into an insertion, calls `set_dwell(10.0)`
      mid-dwell, and asserts the in-progress insertion still retracts 3.0 s after it
      began; then asserts the next insertion lasts 10.0 s.
- [x] A pending value set while the scheduler is `DISARMED` is applied by the next
      `arm(t)`. A test disarms, calls `set_period(60)`, arms at `t=1000`, and asserts
      the first insertion is scheduled at `t=1060`, and `pending_period_s is None`.
- [x] The scheduler still imports no PySide6 and calls no clock. A test asserts the
      module source contains no `time.time`, no `time.monotonic` and no `PySide6`.
      `tests/test_sampling_cycle.py` passes unchanged except for additions.

**Tests may fake:** nothing. Every test calls `arm`, `tick`, `set_period` and
`set_dwell` directly with chosen timestamps.

**Out of scope:** validating the values (ticket 02 owns the rules), the GUI that shows
what is pending (ticket 15), and the saved boundary (ticket 10).

## Comments

2026-10-05: set_period/set_dwell now queue; pending_period_s / pending_dwell_s expose
the queue. Period applies at a boundary in tick (insertion, skip, or the retract that
schedules the next insertion, which keeps the existing mid-dwell period test passing
unchanged) and in arm; dwell applies when an insertion begins and in arm. Tests:
`TestPendingPeriodAndDwell` in tests/test_sampling_cycle.py cover each criterion.
Note: the faraday_cup_tab still calls set_period/set_dwell, so its displayed period
now lags until the boundary; ticket 15 owns showing the pending value.
