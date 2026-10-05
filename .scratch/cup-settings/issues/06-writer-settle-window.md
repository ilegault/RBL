# 06: The settle window is writer state, not a constant read twice

**Blocked by:** 05

**Status:** ready-for-agent

**Read first:** `src/rbl/services/cup_session_writer.py`, the two calls to
`compute_insertion_current(..., settle_window_s=CUP_SETTLE_WINDOW_S)` - one inside
`write_run_closed`, one in the `active_run_insertion_stats` property.
`src/rbl/hardware/dose_model.py`, `compute_insertion_current`, and the docstring
explaining what the settle window excludes and why. `cup_config.py`'s comment saying
`CUP_SETTLE_WINDOW_S = 1.0` is a placeholder pending a bench measurement.

**What to build:** A settle window the operator can change, so the samples excluded
from the post-settle mean and from the dose can be corrected without a rebuild. The
value becomes writer state with a setter, and both places that compute insertion
statistics use that state.

- [ ] `CupSessionWriter.__init__` gains a keyword parameter `settle_window_s`
      defaulting to `CUP_SETTLE_WINDOW_S`, stored as `self._settle_window_s`. A test
      constructing the writer with no arguments asserts the stored value equals the
      constant.
- [ ] `CupSessionWriter.set_settle_window(value: float)` assigns it. Both
      `compute_insertion_current` call sites pass `self._settle_window_s` instead of
      the constant. A test asserts the module no longer references
      `CUP_SETTLE_WINDOW_S` anywhere except as that default.
- [ ] A test proves the value is in use, not merely stored: feed one run a fixed set
      of samples spanning 3 seconds at 10 Hz, close it with a settle window of 1.0,
      record `post_settle_samples` and `post_settle_mean_a` from the written row; then
      repeat with an identical sample set and a settle window of 2.0, and assert the
      sample count is exactly 10 lower and the mean differs. Assert the numbers, not
      that "it changed".
- [ ] The row written by `write_insertion_summary` records the settle window that was
      applied to it. A test asserts the value appears in the row and changes when
      `set_settle_window` is called between two insertions.
- [ ] `src/rbl/hardware/dose_model.py` is not modified. `compute_insertion_current`
      keeps its signature and its `CUP_SETTLE_WINDOW_S` default; it is the caller that
      supplies the live value. That module is pure physics and is where the dose
      arithmetic can be checked against numbers worked by hand.

**Tests may fake:** the output directory, via `tmp_path`. The sample series, the
statistics and the written row must be real.

**Out of scope:** changing the default value of `CUP_SETTLE_WINDOW_S` - that is bench
ticket 13 of `.scratch/cup-actuation/` and it is unaffected by this work. Also out of
scope: the GUI field that calls `set_settle_window` (ticket 12).
