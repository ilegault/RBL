# 55: No burst rating anywhere, and a reading at the rail shows as "at limit"

**Status:** done

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 54

**Spec:** `.scratch/current-monitor-scale/spec.md` (Parts A and D, the burst half of Part E)
**Binding:** `docs/adr/0007-current-monitor-scale-is-2-ma-per-volt.md`, `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Deletes tests:** tests/test_current_vs_frequency.py::test_levels_are_exactly_the_two_ratings, tests/test_raster_planner_tab.py::test_the_two_rating_lines_have_the_right_level_and_style, tests/test_load_characterization_tab.py::test_the_rating_zones_and_lines_are_there, tests/test_amp_monitor.py::test_current_peak_band

**Read first:**
- `docs/hardware/eel5000-manufacturer-notes.md`, sections 3.3, 3.4 and 6, items 4-5.
- `CONTEXT.md`: "Burst rating", "At the rail".
- `src/rbl/config/calibration_config.py`: `AMP_BURST_RATING_MA`.
- `src/rbl/config/hardware_config.py`: `AMP_MAX_MA_PK`.
- `src/rbl/hardware/raster_plan.py`: the `"levels"` dict in `current_vs_frequency`.
- `src/rbl/gui/raster_planner_tab.py`: `_draw_current_chart`, i.e. the two `axhline`
  calls and the `set_ylim` that uses `levels["burst_ma"]`.
- `src/rbl/gui/load_characterization_tab.py`:
  - the module docstring;
  - the constants `CONTINUOUS_RATING_MA`, `BURST_RATING_MA` and `BURST_LIMIT_S`;
  - `_redraw_spikes`.
- `src/rbl/hardware/amp_monitor.py`: `monitor_to_ma`, `current_status`, `__main__`.
- `src/rbl/gui/amp_tab.py`: `_STATUS_COLOR` and its use in `_refresh_monitors`.

## What to build

The operator's working assumption is that the four amplifiers are in the factory 20 mA DC
configuration, which has no 100 mA / 4 ms burst. The monitor also cannot read above
~20 mA. So nothing in the application may draw, warn on or classify against a burst.

1. **No burst constants.** `AMP_BURST_RATING_MA` (in `calibration_config`) and
   `AMP_MAX_MA_PK` (in `hardware_config`) no longer exist, and nothing imports them.
2. **Planner levels.** `raster_plan.current_vs_frequency(...)["levels"]` is exactly
   `{"continuous_ma": AMP_CONTINUOUS_RATING_MA}`.
3. **Planner chart** (`_draw_current_chart`):
   - one horizontal line only, the continuous rating, labelled as today;
   - the y upper limit is `levels["continuous_ma"] * 3`;
   - the docstring no longer mentions a burst line.
4. **Spike chart** (`load_characterization_tab._redraw_spikes`):
   - Draw no filled zones and no 100 mA line or label. The 4 ms vertical line goes too.
   - Keep the 20 mA continuous line and its "20 mA continuous" label, now using
     `AMP_CONTINUOUS_RATING_MA` from `hardware_config`.
   - The module constants `CONTINUOUS_RATING_MA`, `BURST_RATING_MA` and `BURST_LIMIT_S`
     no longer exist.
   - The y upper limit is `max(AMP_CONTINUOUS_RATING_MA * 3, *peaks) * 2`.
   - Rewrite the module docstring's lines about rating zones to say the continuous
     rating is drawn as a line and that a burst is not assumed, citing
     `docs/hardware/eel5000-manufacturer-notes.md` section 3.4.
5. **`monitor_to_ma`** returns NaN only for NaN input or `abs(voltage) > 11.0`, which is
   beyond the LabJack's range and therefore garbage. Update the docstring.
6. **`current_status(ma)`** returns:
   - `"over"` for NaN;
   - `"at_limit"` when
     `abs(ma) >= CURRENT_MONITOR_RAIL_VOLTS * CURRENT_MONITOR_MA_PER_VOLT`, read from
     `hardware_config` at call time;
   - `"ok"` otherwise.

   It never returns `"peak"`. Add a module constant
   `CURRENT_STATUSES = ("ok", "at_limit", "over")`. Update the docstring and the
   `__main__` self-test.
7. **`amp_tab.py`:**
   - Rename `_STATUS_COLOR` to `STATUS_COLOR`, so a test can check it without reaching
     into a private name.
   - Replace its `"peak"` key with `"at_limit": theme.WARN`, commented "monitor at its
     rail: at least 20 mA, true value unknown".
   - `voltage_status` still maps through the same dict, so keep `"ok"` and `"over"`.

Tests may fake: the measured capacitances (through `characterization_history.write_result`
into the conftest temp folder) and spike rows.

## Guardrails

- Leave the continuous-rating line's level (20 mA), and the planner's amber (10 mA) and
  red (16 mA) verdict boundaries, unchanged.
- The planner never disables, refuses or clamps a command because of a verdict. Its
  colours are warnings.
- Do not touch the spike detector or spike recorder; that is ticket 56.

## Acceptance criteria

- [x] **Burst constants.** `test_no_burst_constant_survives` (new,
  `tests/test_current_monitor_scale.py`): `hasattr(calibration_config,
  "AMP_BURST_RATING_MA")` and `hasattr(hardware_config, "AMP_MAX_MA_PK")` are both False.
- [x] **Planner.**
  - `test_levels_are_only_the_continuous_rating` (new,
    `tests/test_current_vs_frequency.py`): the levels dict equals
    `{"continuous_ma": 20.0}`.
  - `test_the_continuous_rating_line_has_the_right_level_and_style` (new,
    `tests/test_raster_planner_tab.py`, `TestCurrentVsFrequencyChart`): exactly one
    horizontal line, at 20.0.
  - `test_one_measured_plate_has_one_line_and_three_not_measured_entries` (rewritten in
    place): it now asserts no legend entry contains "burst". The class attribute
    `BURST` no longer exists.
- [x] **Spike chart.** `test_only_the_continuous_rating_is_drawn` (new,
  `tests/test_load_characterization_tab.py`): the spike axes' artist labels contain
  neither "over burst rating" nor "beyond 4 ms burst". Its horizontal lines include
  `(20.0, 20.0)` and nothing at 100.
- [x] **Status.**
  - `test_a_reading_at_the_rail_is_at_limit` (new, `tests/test_amp_monitor.py`):
    `current_status(19.8)` and `current_status(-20.0)` are `"at_limit"`,
    `current_status(19.7)` is `"ok"`, and NaN is `"over"`. Over
    `np.linspace(-25, 25, 501)`, `"peak"` is never returned.
  - `test_current_dc_band_ok` (rewritten in place): uses 0.0, 19.7 and -19.7.
  - `test_current_over` (rewritten in place): asserts only NaN gives `"over"`.
- [x] **`monitor_to_ma`.** `test_monitor_to_ma_is_nan_only_beyond_the_input_range` (new):
  `monitor_to_ma(10.5) == 21.0` and `monitor_to_ma(11.5)` is NaN.
- [x] **Amp tab colours.** `test_every_current_status_has_a_colour` (new,
  `tests/test_amp_tab_status_colours.py`): every name in `amp_monitor.CURRENT_STATUSES` is
  a key of `amp_tab.STATUS_COLOR`. This prevents a KeyError on the live Amplifiers tab
  when a reading reaches the rail.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile --durations=25

## Comments

2026-10-08: Complete.
- Removed burst constants `AMP_BURST_RATING_MA` from `calibration_config` and `AMP_MAX_MA_PK` from `hardware_config`. Covered by `test_no_burst_constant_survives`.
- Planner levels dict restricted to `{"continuous_ma": AMP_CONTINUOUS_RATING_MA}`. Covered by `test_levels_are_only_the_continuous_rating`.
- Planner chart draws only continuous rating line, with y_lim `levels["continuous_ma"] * 3`. Covered by `test_the_continuous_rating_line_has_the_right_level_and_style` and `test_one_measured_plate_has_one_line_and_three_not_measured_entries`.
- Spike chart in Load Characterization tab draws only continuous rating line without burst zones. Covered by `test_only_the_continuous_rating_is_drawn`.
- `monitor_to_ma` returns NaN only on NaN or |voltage| > 11.0. Covered by `test_monitor_to_ma_is_nan_only_beyond_the_input_range`.
- `current_status` returns `"at_limit"` at rail (|ma| >= 19.8 mA), `"over"` for NaN, `"ok"` otherwise, and never `"peak"`. Added `CURRENT_STATUSES`. Covered by `test_a_reading_at_the_rail_is_at_limit`, `test_current_dc_band_ok`, and `test_current_over`.
- `amp_tab.STATUS_COLOR` exposed publicly with `"at_limit": theme.WARN`, and mapped in `_refresh_monitors`. Covered by `test_every_current_status_has_a_colour`.
- Bench verification: when operating near the rail on live hardware, verify amplifier tab displays current in amber without exceptions.
