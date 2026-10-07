# 38: The Load Characterization tab shows results by axis, with age and three load conditions

**Status:** done

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 37

**Spec:** `.scratch/amplifier-load-and-spikes/spec.md` (Part A)
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/gui/load_characterization_tab.py` (`_refresh_table` - replaced), `src/rbl/config/characterization_history.py` (`newest`), `tests/test_load_characterization_tab.py` (`test_unmeasured_channels_show_placeholder`, `test_measured_channel_shows_values`, `test_outlier_conductance_is_flagged`).

## What to build

The four-channel table is replaced by a table with one row per plate position in the order
X+, X-, Y+, Y- (an "X axis" / "Y axis" label row above each pair) and columns `Disconnected`,
`Cable only`, `On plates`, `Cable minus amplifier`, `Plates minus cable`. Each condition cell
shows `<C> pF, <method>, <age>` from `characterization_history.newest` (age as `today`,
`N days ago`, `N weeks ago`), or `not measured`. A cell whose result `predates_hardware_change`
uses the `theme.WARN` background with tooltip `measured before the hardware change on <date>`.
Difference cells show the subtraction in pF when both inputs exist, else blank. The
conductance outlier highlight is kept, computed from on-plates results.

The tab takes `now_fn` for tests. Rewrite the three named tests in place, same names, against
the history module (write results with `write_result`).

Tests may fake: `now_fn`. History files are real (temp folder).

## Acceptance criteria

- [x] With no results, every condition cell reads exactly `not measured`.
- [x] An on-plates result of 1612.0 pF by `charge_integral_ladder` written 32 days before `now`: the X+ `On plates` cell contains `1612`, `charge_integral_ladder` and `32 days ago`.
- [x] Cable-only 400 pF and on-plates 1600 pF for Y-: the Y- `Plates minus cable` cell reads `1200 pF`.
- [x] A result older than a recorded hardware change has background `theme.WARN` and the tooltip above; a newer result does not.
- [x] Rewritten `test_outlier_conductance_is_flagged` still flags the plate whose on-plates G is more than 3x the median.

## Gate

Run in CI's order (`.github/workflows/tests.yml`):

    ruff check .
    python scripts/check_tests_first.py
    python tools/type_gate.py
    pytest --tb=short -q -n auto --dist loadfile --durations=25

## Comments

2026-10-07: Done. The four-channel table is now a per-plate table grouped by axis (X axis / X+ / X- / Y axis / Y+ / Y-) with columns Plate position, Disconnected, Cable only, On plates, Cable minus amplifier, Plates minus cable. Cells read `<C> pF, <method>, <age>` from `characterization_history.newest_with_capacitance` (new here: the newest result for that condition that carries a capacitance, so a newer clamp result does not hide it) or `not measured`; results predating the latest hardware change use the `theme.WARN` background with the tooltip `measured before the hardware change on <date>`. The conductance outlier highlight is kept, judged on on-plates results only. `LoadCharacterizationTab(now_fn=...)`, and a public `refresh_results()` replaces the private `_refresh_table`.
Age wording: the ticket lists "today, N days ago, N weeks ago" but also needs "32 days ago"; `characterization_history.age_text` (added in ticket 42) switches to weeks from 60 days. The three named tests were rewritten in place (class renamed `TestComparisonTable`).
