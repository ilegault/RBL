# 84: Automatic insertions retract on convergence (target-uncertainty mode, set in the settings file)

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 83

**Spec:** `.scratch/cup-target-uncertainty/spec.md`
**Binding:** `docs/adr/0003-commanded-and-confirmed-cup-position.md` (amendment D, and C1), `docs/adr/0001-tests-first-and-no-muted-failures.md`, `docs/adr/0002-cup-acquisition-triggered-by-current.md` (amendments A5-A8, B3)

**Read first:** `src/rbl/services/sampling_cycle.py` (whole module docstring, `CycleRetract`, `set_dwell`, `_apply_pending_dwell`, `tick`); `tests/test_sampling_cycle.py` (prior art: explicit timestamp sequences); `src/rbl/gui/faraday_cup_tab.py` (`_tick_cycle`, the readout from ticket 83).

## What to build

Target-uncertainty mode works end to end for automatic insertions. It is switched in the
settings file only; the tab controls come in the next ticket.

1. **Settings.** Add `dwell_mode` (`"fixed"` | `"target_uncertainty"`, default
   `"fixed"`) and `max_dwell_s` (float, default `CUP_MAX_DWELL_S = 5.0` in
   `cup_config.py`, must exceed `settle_window_s`) to `CupSettings`/`validate_settings`.
   The existing period rule ("must hold the dwell plus two moves") validates against
   `max_dwell_s` when `dwell_mode` is `target_uncertainty` and `cycle_dwell_s`
   otherwise. `cycle_dwell_s` is the fixed dwell and is unchanged.
2. **Scheduler** (stays clockless and never sees samples; the module's no-Qt/no-time
   test must keep passing). `SamplingCycleScheduler` gains:
   - constructor args `dwell_mode: str = "fixed"`, `max_dwell_s: float = 5.0`;
   - `set_dwell_mode(mode)` and `set_max_dwell(s)`, queued exactly like `set_dwell`
     and applied at the next insertion start (in `_apply_pending_dwell` or a sibling);
   - `notify_converged(t)`: during INSERTING in target-uncertainty mode, sets the
     retract due at `t`; ignored in fixed mode and outside INSERTING;
   - `CycleRetract` gains `reason: str = "dwell_elapsed"` (default keeps every existing
     constructor call valid). `tick` returns `CycleRetract(t, "converged")` after
     `notify_converged`, `CycleRetract(t_insert + max_dwell_s, "max_dwell")` when the
     max dwell elapses first in target mode, and `"dwell_elapsed"` in fixed mode.
   - `fixed dwell` and `max dwell` are independent: setting one never changes the other.
3. **Tab.** On the first `converged` result for an insertion whose origin is automatic,
   call `self.cycle.notify_converged(t_sample)` once. Latch `converged_after_s =
   result.post_settle_elapsed_s` at that moment. The `CycleRetract.reason` becomes the
   insertion's `end_reason` (overriding `dwell_elapsed` from ticket 83).
4. **Cup log.** In target mode write `dwell_mode="target_uncertainty"`,
   `target_uncertainty`, `max_dwell_s` and `converged_after_s` (blank if never
   converged). A not-converged automatic insertion still counts toward the dose exactly
   as before (ADR 0003 D3). Header gains `dwell_mode`, `target_uncertainty`,
   `max_dwell_s` in force at open.

## Acceptance criteria

- [ ] `tests/test_sampling_cycle.py`: target mode, arm at 0, insert at the boundary,
      `notify_converged(t_insert + 1.7)`, next `tick` returns `CycleRetract(t_insert +
      1.7, "converged")`; without `notify_converged`, `tick(t_insert + 5.0)` returns
      reason `"max_dwell"`; in fixed mode `notify_converged` changes nothing and the
      retract is at `t_insert + dwell_s` with `"dwell_elapsed"`.
- [ ] `tests/test_sampling_cycle.py`: `set_dwell_mode`/`set_max_dwell` during INSERTING
      do not change the current insertion and apply at the next; `set_max_dwell` never
      changes `dwell_s` and `set_dwell` never changes `max_dwell_s`.
- [ ] `tests/test_faraday_cup_tab.py`: with `dwell_mode = "target_uncertainty"` in the
      settings file under `tmp_path`, automatic insertion running and feeds that
      converge (steady current, 10+ post-settle samples), the tab commands cup OUT before
      the max dwell and the summary row has `end_reason == "converged"`, a non-blank
      `converged_after_s` and `counted_in_dose == True`.
- [ ] Same with a noisy feed that never reaches 5 %: cup OUT at max dwell,
      `end_reason == "max_dwell"`, `converged_after_s` blank, `counted_in_dose == True`.
- [ ] `tests/test_cup_settings_store.py`: bad `dwell_mode` and `max_dwell_s <=
      settle_window_s` fall back with a warning; the period rule uses the active mode's
      dwell.

May fake: instrument feeds, modal dialogs, and the clock passed to `_tick_cycle`. Must be
real: the scheduler, the tab, the cup log writer on `tmp_path`, the settings store on a
temp file.

## Gate

Run all four, in CI's order (`.github/workflows/tests.yml`). All must pass.

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile
```

## Comments
