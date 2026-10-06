# 16: Re-arming can keep the previous schedule, and the file says which happened

**Blocked by:** 08, 10, 15

**Status:** in-progress

**Read first:** `src/rbl/gui/faraday_cup_tab.py`, `_on_cycle_arm_clicked` (note that
the Arm button doubles as Disarm and leaves the cup where it is) and
`_on_cycle_stop_clicked` (which retracts). `src/rbl/services/sampling_cycle.py` as
ticket 10 leaves it. `CONTEXT.md`, the glossary entry "Saved boundary".

**What to build:** The last piece of the stop-edit-rearm path. An operator who stops
an armed cycle to change a locked setting can re-arm onto the boundary the cycle would
have hit, instead of waiting a fresh period. The session file records both exits and
the re-arm, so a gap in the insertion series is explained rather than inferred.

- [ ] A `chk_keep_schedule` checkbox labelled `Keep previous schedule` sits beside the
      Arm button, unchecked on construction, and is enabled only while the scheduler
      reports a `saved_boundary_t` that is still in the future. Its enabled state is
      recomputed on every actuation state update, so it greys out on its own once the
      boundary passes, and its tooltip says why it is unavailable. A test asserts it
      is disabled with no saved boundary, enabled after a stop, and disabled again
      once the boundary time has passed.
- [ ] Arming passes `resume_at=self.cycle.saved_boundary_t` only when the checkbox is
      both checked and enabled, and `None` otherwise. A test asserts the insertion
      lands on the saved boundary when it is ticked and at `now + period` when it is
      not.
- [ ] Both exits write `write_cycle_disarmed` with the saved boundary: the Arm button
      acting as Disarm writes reason `"disarm"`, and Stop Cycle writes reason
      `"stop"`. A test asserts one row per exit with the right reason and the right
      boundary value.
- [ ] Arming writes `write_cycle_armed` carrying the mode string the scheduler
      returned - `"resumed"` or `"fresh"` - and the resulting next insertion time. A
      test asserts the row's mode matches what `arm` returned in both cases, so the
      file can never claim a resume that did not happen.
- [ ] One test walks the whole operator sequence and asserts the insertion series: arm
      at a known time, run past one insertion, stop, change a threshold through the
      settings group, re-arm with the checkbox ticked, and assert the next insertion
      falls on the original boundary, that exactly one insertion occurs there rather
      than a catch-up burst, and that the file contains the disarm, the
      settings-change and the re-arm rows in that order.
- [ ] Stop Cycle still retracts the cup when it is called during an insertion. A test
      asserts the retract command is issued, because a stop that stopped retracting
      would leave the cup in the beam.

**Tests may fake:** the hardware, via `tests/payloads.py`, and the output directory.
The scheduler, the checkbox state, the arming path and the written rows must be real.

**Out of scope:** persisting the saved boundary or the armed state across an
application restart - arming stays an explicit operator action after every start.
