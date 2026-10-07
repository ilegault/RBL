# 53: Prep: tests convert through `ma_to_monitor`, a rail tracker exists, and the 20 mA rating has one name

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/current-monitor-scale/spec.md` (Parts A and B, prep only)
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`, `docs/adr/0007-current-monitor-scale-is-2-ma-per-volt.md`

**Read first:**
- `docs/hardware/eel5000-manufacturer-notes.md`, sections 3 and 6.
- `CONTEXT.md`: "Current monitor scale" and "At the rail".
- `src/rbl/config/hardware_config.py`: the "Scale factors" and "Display / sanity limits" blocks.
- `src/rbl/hardware/amp_monitor.py`: `ma_to_monitor`, `monitor_to_ma`, `current_status`.
- `src/rbl/hardware/raster_plan.py`:
  - the `envelope_walls(load_pf=c_pf, trip_ma=CAL_AC_TRIP_MA, ...)` call;
  - the `"levels"` dict returned by `current_vs_frequency`.
- `src/rbl/gui/amp_tab.py`: the three reads of `SC.AMP_MAX_MA_DC`.
- `tests/test_load_characterizer.py`: `_run_mode_a_sine`, `_feed_rung`, `_feed_clamp_step`.
- `tests/test_dynamic_adjustment.py`: `_run_full_trial`.

## Why this ticket exists

Ticket 54 changes `CURRENT_MONITOR_MA_PER_VOLT` from 10.0 to 2.0. About a dozen test
helpers build fake current-monitor volts by dividing a current by the literal `10.0`.
If the constant changed first, every one of them would break at once and the real
change would be lost in the noise. This ticket makes those tests independent of the
scale while it is still 10.0, so 54 lands as a small, readable diff.

**Nothing the application does changes in this ticket.** `CURRENT_MONITOR_MA_PER_VOLT`
stays `10.0` here, and the scale change belongs to ticket 54 only.

## What to build

1. **`src/rbl/config/hardware_config.py`**
   - Add `CURRENT_MONITOR_RAIL_VOLTS = 9.9`. Its comment says: "a current-monitor sample
     at or beyond this magnitude is AT THE RAIL - the LabJack's +/-10 V input range is
     exhausted, so the current was at least this much and how much more is unknown
     (CONTEXT.md 'At the rail')".
   - Add `AMP_CONTINUOUS_RATING_MA = 20.0`. Its comment says: "the EEL5000's continuous
     rating; for display and planning only, never a trip level".
   - `AMP_MAX_MA_DC` no longer exists. Every reader of it uses
     `AMP_CONTINUOUS_RATING_MA` instead: `amp_monitor.current_status` and the three
     places in `amp_tab.py`.
2. **`src/rbl/hardware/raster_plan.py`**
   - The `envelope_walls(...)` call uses `trip_ma=AMP_CONTINUOUS_RATING_MA`.
   - `levels["continuous_ma"]` is `AMP_CONTINUOUS_RATING_MA`.
   - Leave `levels["burst_ma"]` as it is; ticket 55 handles it.
   - Why: ticket 54 lowers `CAL_AC_TRIP_MA` (the calibration soft-trip level) to 19 mA,
     and the planner's continuous-rating line must stay at 20 mA.
3. **`src/rbl/hardware/amp_monitor.py`**: add
   `is_at_rail(volts)`.
   - It takes a float or a numpy array and returns a bool or a bool array.
   - The result is True where `abs(v) >= CURRENT_MONITOR_RAIL_VOLTS`. NaN gives False.
   - It is pure.
4. **`src/rbl/hardware/amp_monitor.py`**: add `class RailTracker`.
   - It is pure: numpy only, no Qt, no clock, no hardware. That is what makes it
     testable with synthetic arrays.
   - `feed(volts, dt_s) -> float` returns the longest contiguous at-the-rail run, in
     seconds, among the runs that include at least one sample of this window.
   - A run still at the rail at the end of the previous `feed` continues into this
     one, and its earlier samples count.
   - Duration is sample count × `dt_s`.
   - A NaN sample or a sample below the rail ends a run.
   - `reset()` forgets any open run.
5. **Tests that fake monitor volts.** In each place below, rewrite the conversion from mA
   to current-monitor volts in place to call `ma_to_monitor(...)` instead of dividing by
   the literal `10.0`. Keep the same names and the same assertions:
   - `tests/test_load_characterizer.py`:
     - helpers `_run_mode_a_sine`, `_feed_rung` and `_feed_clamp_step`;
     - tests `test_a_completed_mode_c_run_is_a_charge_integral_ladder_result`,
       `test_recovers_known_capacitance_from_synthetic_sine` and
       `test_finds_edges_and_recovers_capacitance_ballpark`.
   - `tests/test_dynamic_adjustment.py`: `_run_full_trial`.
   - The `__main__` self-test line `raw_i = i_ma / 10.0` in
     `src/rbl/services/dynamic_adjustment.py`.
   - Leave alone the four hard-trip tests in `tests/test_load_characterizer.py` that use
     `CAL_TRIP_HARD_MA`. Ticket 54 replaces them.

Tests may fake: nothing new. The rail tracker and `is_at_rail` are exercised with real
numpy arrays.

## Guardrails

- `CURRENT_MONITOR_MA_PER_VOLT`, `CAL_AC_TRIP_MA` and `CAL_TRIP_HARD_MA` keep their
  current values in this ticket.
- No assertion, tolerance or input in an existing test is changed. Only the volts-from-mA
  conversion inside the listed helpers changes. If an existing test then fails, that is a
  real finding: escalate per `AGENTS.md` ("Fix or escalate").
- No new module-level copy of a path or constant: read `hardware_config` values through
  the module, as `amp_monitor` already does with `SC.`.

## Acceptance criteria

- [ ] `test_is_at_rail_threshold_and_nan` (new, `tests/test_amp_monitor.py`):
  - `is_at_rail(9.9)` and `is_at_rail(-9.95)` are True;
  - `is_at_rail(9.89)` and `is_at_rail(float("nan"))` are False;
  - `is_at_rail(np.array([0.0, 10.0, -10.0, np.nan]))` equals `[False, True, True, False]`.
- [ ] `test_rail_tracker_measures_a_short_run` (new): 10 samples at 10.0 V between 0 V
  samples, `dt_s=1e-4`, returns `0.001` (abs 1e-12).
- [ ] `test_rail_tracker_joins_a_run_across_windows` (new): one window ending in 30 rail
  samples, then one starting with 30 rail samples, `dt_s=1e-4`. The second `feed` returns
  `0.006` (abs 1e-12).
- [ ] `test_rail_tracker_restarts_after_one_sample_below_the_rail` (new): 30 rail
  samples, one at 9.0 V, then 30 rail samples, in one window. `feed` returns `0.003`, and
  `reset()` makes a following 1-sample rail window return `1e-4`.
- [ ] `test_continuous_rating_has_one_name` (new, `tests/test_amp_monitor.py`):
  `hardware_config.AMP_CONTINUOUS_RATING_MA == 20.0`, and
  `hasattr(hardware_config, "AMP_MAX_MA_DC")` is False.
- [ ] `test_planner_continuous_level_is_the_rating_not_the_soft_trip` (new,
  `tests/test_current_vs_frequency.py`): with `raster_plan.CAL_AC_TRIP_MA` monkeypatched
  to `19.0`, `current_vs_frequency(...)["levels"]["continuous_ma"] == 20.0`.
- [ ] The full suite passes with `CURRENT_MONITOR_MA_PER_VOLT == 10.0`, and
  `grep -nE "/ ?10\.0" tests/test_load_characterizer.py tests/test_dynamic_adjustment.py`
  finds no current-to-volts conversion (no test: refactor of test helpers, proved by the
  existing suite staying green).

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile --durations=25

## Comments
