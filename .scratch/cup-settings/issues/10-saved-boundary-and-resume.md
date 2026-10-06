# 10: A stopped cycle remembers its boundary, and re-arming can resume it

**Blocked by:** 09

**Status:** in-progress

**Read first:** `src/rbl/services/sampling_cycle.py`, `arm`, `disarm`, `stop` and the
`_next_insertion_t` field. `.scratch/cup-settings/spec.md`, "The scheduler owns pending
changes and the saved boundary". `CONTEXT.md`, the glossary entry "Saved boundary".

**What to build:** The cycle remembers when it would next have inserted, so that an
operator who stops it to change a threshold can re-arm onto the original schedule
instead of losing up to a full period of sampling. A boundary that has already passed
is never resumed, and no catch-up insertions are ever produced.

- [ ] `disarm()` and `stop(t)` both save the current `_next_insertion_t` before
      clearing the schedule, exposed as a read-only `saved_boundary_t` property
      returning `None` when there is none. A test arms at `t=0` with period 300, ticks
      to `t=10`, calls `disarm()`, and asserts `saved_boundary_t == 300.0`; a second
      test does the same with `stop(10.0)` and asserts the same value, because which
      button an operator pressed says nothing about whether they meant to come back.
- [ ] `arm(t, resume_at: float | None = None) -> str` returns exactly `"resumed"` when
      `resume_at` is not `None` and `resume_at > t`, scheduling the next insertion at
      `resume_at`; otherwise it returns exactly `"fresh"` and schedules at
      `t + period_s`. A test asserts the returned string and the resulting insertion
      time for: no `resume_at`; a `resume_at` in the future; and a `resume_at` in the
      past.
- [ ] A resumed arm produces exactly one insertion at the boundary, never a burst. A
      test arms at `t=1000` with `resume_at=1010` and period 300, ticks once to
      `t=1010`, asserts one `CycleInsert`, then ticks to `t=1011` and asserts `None`.
      A second test with `resume_at` 900 seconds in the past asserts `"fresh"` and that
      no insertion occurs before `t + period_s`.
- [ ] `saved_boundary_t` is cleared by any `arm()` and is not persisted anywhere. A
      test asserts it returns `None` after arming. Arming stays an explicit operator
      action after every application start, which is an existing requirement of
      `.scratch/cup-actuation/` ticket 09 and is unchanged.
- [ ] `stop(t)` still returns a `CycleRetract` when it is called during an insertion
      and `None` otherwise. A test asserts both, because a stop that stopped retracting
      the cup would leave it in the beam.

**Tests may fake:** nothing. Pure scheduler, chosen timestamps.

**Out of scope:** the checkbox that supplies `resume_at` and the marker rows (both
ticket 16).
