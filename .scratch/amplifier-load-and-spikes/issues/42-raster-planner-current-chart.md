# 42: The Raster Planner shows steady current vs frequency, from measured C only

**Status:** done

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

- [x] With no results, `_channel_capacitance()` returns `(None, 'not measured')` for all four plates and no line label on the chart contains `pF`.
- [x] With only X+ measured at 1600 pF, the chart has exactly one current line, three legend entries ending `not measured`, and the two level lines with the labels above.
- [x] An operating point of 12 mA makes the envelope label use `theme.WARN`; 17 mA uses `theme.FAULT`.
- [x] A plate with only a disconnected result reads `not measured`.
- [x] A result written while the tab is open appears as a line after `refresh_capacitance()`.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile --durations=25

## Comments

2026-10-07: Done. The Raster Planner plots steady current vs frequency (log-log) from measured on-plates capacitance only; the old kV wall chart and every fallback number are gone from the tab.
- `RasterPlannerTab(now_fn=...)`; `_channel_capacitance()` returns `(c_pf, "measured, <age>")` or `(None, "not measured")` from `characterization_history.newest_on_plates` (added here: the newest on-plates result that carries a capacitance, so a newer clamp result does not hide it). `age_text()` (today / N days ago / N weeks ago, weeks from 60 days) is added to `characterization_history` for ticket 38 to reuse.
- The readout, the verdict colour and the chart all come from one `current_vs_frequency` model so they cannot disagree. The kV headroom number is kept (envelope_status over the measured plates only).
- The five named tests were rewritten in place; the `TestEnvelopeCheck` tests and `test_frequency_does_change_the_predicted_current` also needed measured capacitances now that there is no fallback, so they use a `measured_tab` fixture. `tab.envelope_axes` is a new read-only property so the chart is tested without private access.
- Fixed on the way: drawing the log-log chart with nothing measured raised (log scale on empty data), so limits are set before the scale.
