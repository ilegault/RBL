# 85: Faraday Cup tab controls for dwell mode, both dwells and the target

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 84

**Spec:** `.scratch/cup-target-uncertainty/spec.md`
**Binding:** `docs/adr/0003-commanded-and-confirmed-cup-position.md` (amendment D, and C1), `docs/adr/0001-tests-first-and-no-muted-failures.md`, `docs/adr/0002-cup-acquisition-triggered-by-current.md` (amendments A5-A8, B3)

**Read first:** `src/rbl/gui/faraday_cup_tab.py` (`_on_cycle_dwell_changed`, `_on_cycle_period_changed`, `_on_settings_changed`, `_save_current_settings`, `lbl_cycle_pending`); `src/rbl/gui/widgets/acquisition_settings.py` (load-warning display).

## What to build

The operator switches mode and edits the target and both dwells from the Faraday Cup tab.

1. A dwell mode toggle (two radio buttons: `"Fixed dwell"`, `"Target uncertainty"`)
   in the sampling cycle group.
2. Two separate fields, labelled exactly `"Fixed dwell"` (edits `cycle_dwell_s`, the
   existing field relabelled) and `"Max dwell (target uncertainty)"` (edits
   `max_dwell_s`). Only the active mode's field is enabled; the other stays visible and
   disabled. Editing one never changes the other.
3. A `"Target uncertainty"` field in percent (0.5 to 50.0, one decimal), stored as a
   fraction. One-line help text under it: `"Precision of each insertion's mean, not of
   the dose."` Enabled only in target-uncertainty mode.
4. While automatic insertion is running, a change to the mode, either dwell or the
   target is a pending change: queued on the scheduler (or held by the tab for the
   target and applied at the next `CycleInsert`), and shown in `lbl_cycle_pending` with
   "applies at the next insertion", as `_on_cycle_dwell_changed` does for the dwell
   today. None of these fields is ever locked by the settings lock.
5. Every committed change writes a settings-change marker via `_on_settings_changed`
   (keys `dwell_mode`, `max_dwell_s`, `target_uncertainty`) and saves the settings file.

## Acceptance criteria

- [ ] A tab test asserts that selecting `"Target uncertainty"` enables the max dwell and
      target fields and disables the fixed dwell field, and the reverse for fixed.
- [ ] A tab test asserts editing max dwell to 7.0 leaves `cycle_dwell_s` unchanged in the
      saved settings file (temp path), and vice versa.
- [ ] A tab test with automatic insertion running asserts changing the target during an
      insertion leaves that insertion's summary row with the old target and the next
      insertion's row with the new one; `lbl_cycle_pending` shows the pending text in
      between.
- [ ] A tab test asserts each of the three changes writes one settings-change row with
      old and new values.
- [ ] A tab test asserts the three new controls stay enabled while an acquisition run is
      open (they are not under the settings lock).

May fake: instrument feeds, modal dialogs, the clock. Must be real: the tab, scheduler,
settings store on a temp file, cup log writer on `tmp_path`.

## Gate

Run all four, in CI's order (`.github/workflows/tests.yml`). All must pass.

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile
```

## Comments
