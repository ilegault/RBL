# 57: Characterization flags the rail: Mode C edges and the clamp test

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 54

**Spec:** `.scratch/current-monitor-scale/spec.md` (Part F)
**Binding:** `docs/adr/0007-current-monitor-scale-is-2-ma-per-volt.md`, `docs/adr/0006-amplifier-faults-are-observed-not-stopped-during-a-session.md` (decision 2), `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:**
- `docs/hardware/eel5000-manufacturer-notes.md`, sections 3.3, 4 and 6, item 7.
- `src/rbl/services/load_characterizer.py`:
  - `_finish_mode_c_point`: the edge loop over `edge_idx` with its `[lo:hi]` windows, and
    the returned dict;
  - `_finish_clamp_point`;
  - the `if self._mode == Mode.CLAMP:` branch that sets `self._clamp_result`;
  - the CLAMP `values` built in the finish/write path;
  - `_ladder_rule`.
- `src/rbl/gui/load_characterization_tab.py`: the clamp status text (the lines that read
  `clamp_result` and say `not reached`).
- `src/rbl/hardware/amp_monitor.py`: `is_at_rail`.
- `tests/test_load_characterizer.py`: `_feed_rung`, `_feed_clamp_step`, `TestClampTest`,
  `TestModeCLadder`.

## What to build

With the pot fully clockwise, the amplifier clamps at 20 mA, which is also where the
monitor rails. A clamp at the rail can only be reported as "at least 20 mA". A Mode C edge
that touches the rail may have its charge under-read. Both are recorded. Neither is an
abort.

1. **Mode C rungs.** Each rung dict from `_finish_mode_c_point` gains
   `"edge_at_rail": bool`. It is True when any raw current-monitor sample inside any
   used edge's `[lo:hi]` window is at the rail (`is_at_rail` on the raw `i_wave`).
   `_ladder_rule` does not look at it.
2. **Clamp points.** Each clamp point from `_finish_clamp_point` gains `"at_rail": bool`:
   any raw current sample in that step's collected `i_wave` is at the rail.
   - When the clamp is reached, `_clamp_result` carries `"at_rail"` from that point.
   - The written result's `values` gain `"clamp_at_rail"`, which is False when the clamp
     is not reached.
3. **Clamp status text in the tab.**
   - When `clamp_result["at_rail"]` is True, the text contains
     `the current reached the monitor's rail (at least 20 mA)` in place of a bare mA
     number.
   - Otherwise the text is unchanged.

Tests may fake: the function generator and stream windows (raw volts for rail samples).

## Guardrails

- No new abort rule. The existing rules are hard trip, C change, leakage, not following,
  interlock and no_data, and their thresholds are unchanged.
- `clamp_ma` is still the current fundamental at the stopping step. Its value is not
  clamped, rescaled or replaced.

## Acceptance criteria

- [ ] **Mode C, edge at the rail.**
  `test_a_railed_edge_marks_its_rung_edge_at_rail_and_the_ladder_continues` (new,
  `TestModeCLadder`): a rung whose edges each hold 15 raw samples (30 µs at 500 kS/s) at
  10.0 V has `edge_at_rail` True, and the ladder goes on to the next rung with no
  abort rule.
- [ ] **Mode C, edge below the rail.** `test_an_edge_below_the_rail_is_not_edge_at_rail`
  (new): the same rung with edges peaking at 8.0 V has `edge_at_rail` False.
- [ ] **Clamp at the rail.** `test_a_clamp_at_the_rail_is_recorded_as_at_rail` (new,
  `TestClampTest`):
  - setup: a stopping step (ratio 0.8) whose raw current wave reaches 10.0 V;
  - result: `clamp_result["at_rail"]` is True, and the written result file's
    `values["clamp_at_rail"]` is True.
- [ ] **Clamp below the rail.** `test_a_clamp_below_the_rail_is_not_at_rail` (new):
  - setup: a stopping step whose current fundamental is 10 mA, with raw peaks near 5 V.
    This is the operator's dial-50 bench check.
  - result: `at_rail` False, and `clamp_ma` is 10.0 (abs 0.2).
- [ ] **Tab text.** `test_the_tab_says_at_least_20_ma_for_a_clamp_at_the_rail` (new,
  `tests/test_load_characterization_tab.py`): with a faked runner whose `clamp_result` has
  `at_rail` True, the status text contains `at least 20 mA`.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile --durations=25

## Comments
