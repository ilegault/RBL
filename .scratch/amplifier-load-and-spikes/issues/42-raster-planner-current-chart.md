# 42: The Raster Planner shows steady current vs frequency, from measured C only

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 36, 41

**Spec:** `.scratch/amplifier-load-and-spikes/spec.md` (Part D)
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/gui/raster_planner_tab.py` (`_channel_capacitance`, `_check_envelope`, `_draw_envelope`, `_show_capacitance_and_current`, `refresh_capacitance`), `src/rbl/hardware/raster_plan.py` (`current_vs_frequency`, from 41), `tests/test_raster_planner_tab.py` (`test_unmeasured_channels_fall_back_and_say_so`, `test_a_fallback_channel_is_flagged_not_silent`, `test_on_plates_wins_over_a_later_disconnected_sweep`, `test_a_disconnected_only_channel_says_which_condition_it_is`, `test_refresh_picks_up_a_measurement_taken_after_construction`).

## What to build

- `_channel_capacitance` returns `{plate: (c_pf, "measured, <age>")}` from
  `characterization_history.newest(plate, 'ON_PLATES', now)`, or `(None, "not measured")`.
  No fallback number appears anywhere on the tab.
- `_draw_envelope` is replaced: log-log axes, x "Raster frequency (Hz)", y "Steady current (mA)";
  one line per measured plate (legend `X+ (1612 pF, 3 days ago)`), a legend entry
  `Y- not measured` with no line; a marker at each plate's operating point; a solid line at
  20 mA labelled `20 mA continuous rating`; a dotted line at 100 mA labelled
  `100 mA burst only: 4 ms or less, then 100 ms at 10 mA or less; never an operating point`.
- The envelope label uses the worst measured plate's verdict colour (`theme.OK`, `theme.WARN`,
  `theme.FAULT`) and still shows the kV headroom number. Unmeasured plates are listed as
  `not measured: <plates>` with no verdict.
- Rewrite the five named tests in place, same names: "fallback" becomes "not measured";
  disconnected or cable-only results are never used for planning.

Tests may fake: `now_fn`. History files are real (temp folder).

## Acceptance criteria

- [ ] With no results, `_channel_capacitance()` returns `(None, 'not measured')` for all four plates and no line label on the chart contains `pF`.
- [ ] With only X+ measured at 1600 pF, the chart has exactly one current line, three legend entries ending `not measured`, and the two level lines with the labels above.
- [ ] An operating point of 12 mA makes the envelope label use `theme.WARN`; 17 mA uses `theme.FAULT`.
- [ ] A plate with only a disconnected result reads `not measured`.
- [ ] A result written while the tab is open appears as a line after `refresh_capacitance()`.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile --durations=25

## Comments
