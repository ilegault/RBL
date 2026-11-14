# 83: Show and log insertion uncertainty for every insertion (fixed dwell unchanged)

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 81

**Spec:** `.scratch/cup-target-uncertainty/spec.md`
**Binding:** `docs/adr/0003-commanded-and-confirmed-cup-position.md` (amendment D, and C1), `docs/adr/0001-tests-first-and-no-muted-failures.md`, `docs/adr/0002-cup-acquisition-triggered-by-current.md` (amendments A5-A8, B3)

**Read first:** `src/rbl/gui/faraday_cup_tab.py` (`on_cup_state`, `_close_insertion`, `_last_insertion_stats`, `_emit_cup_view`, the run-statistics labels `lbl_average`/`lbl_samples`); `src/rbl/services/cup_session_writer.py` (`CSV_COLUMNS`, `write_insertion_summary`, `_write_header_comments`); `src/rbl/config/cup_settings_store.py` (`CupSettings`, `validate_settings`).

## What to build

Every insertion now shows its insertion uncertainty live on the Faraday Cup tab and
writes it, with the reason the insertion ended, to the cup log. Insertion behaviour does
not change: fixed dwell governs exactly as today. This lets the operator watch real beam
noise before any stopping rule exists.

1. **Settings.** Add to `CupSettings` and `validate_settings` (same judge pattern,
   bad value -> default with a `SettingsLoadWarning`): `target_uncertainty` (float,
   `0.005 <= v <= 0.5`, default `0.05`) and `min_converge_samples` (int, `v >= 2`,
   default `10`). Defaults live in `src/rbl/config/cup_config.py` as
   `CUP_TARGET_UNCERTAINTY_DEFAULT = 0.05` and `CUP_MIN_CONVERGE_SAMPLES = 10`. No tab
   field yet.
2. **The tab keeps its own insertion samples.** In `FaradayCupTab`, a list
   `self._insertion_samples: list[tuple[float, float | None]]`, cleared on `RunOpened`
   and appended in `on_cup_state` on every reading while `self.acquisition.is_acquiring`
   (current, or None when over-range/invalid). It must not come from the cup log writer:
   the readout must work with no cup log open (ADR 0002 B3).
3. **Live readout.** On each appended sample call `compute_insertion_uncertainty` with
   the run start time, the live settle window, the settings target and minimum, and the
   live release threshold. Show it in a new `self.lbl_uncertainty` next to the run
   statistics: `f"{u*100:.1f} % (n = {n})"` when computable, otherwise
   `uncertainty_reason_text(...)`; `"—"` when no run is open.
4. **Last insertion line.** A new `self.lbl_last_insertion`, set in `_close_insertion`:
   `f"last: {u*100:.1f} % · {end_reason} · n = {n} · {dwell:.1f} s"` (reason text in
   place of the percentage when none).
5. **End reason.** `_close_insertion` determines `end_reason`: `fault` when the close
   follows a fault-forced stop, `operator` when the operator retracted or force-stopped,
   otherwise `dwell_elapsed`. Pass it through.
6. **Cup log.** Append to `CSV_COLUMNS`, after `total_beam_on_s`, in this order:
   `dwell_mode`, `target_uncertainty`, `min_converge_samples`, `max_dwell_s`,
   `insertion_uncertainty`, `uncertainty_reason`, `end_reason`, `converged_after_s`.
   `write_insertion_summary` gains matching keyword arguments (defaults blank).
   In this ticket: `dwell_mode="fixed"`, `target_uncertainty` blank, `max_dwell_s`
   blank, `converged_after_s` blank, `min_converge_samples` the setting,
   `insertion_uncertainty` the fraction (`.6e`) or blank, `uncertainty_reason` the enum
   value or blank. Other row types leave all eight blank. `_write_header_comments`
   adds `min_converge_samples` with the other file-only values.

## Acceptance criteria

- [ ] `tests/test_cup_session_writer.py`: a test asserts the eight new names are the
      last eight entries of `CSV_COLUMNS` in the order above and that every existing
      column keeps its index; a written insertion summary row carries the values given,
      and a sample row has the eight blank.
- [ ] `tests/test_faraday_cup_tab.py`: feeding readings through `CupFeed` (prior art in
      that file) for an insertion with a cup log open, `lbl_uncertainty.text()` reads
      `"too few samples (3/10)"` after three post-settle samples and a percentage with
      `n = 12` after twelve; after the run closes, the last row of the cup log has
      `dwell_mode == "fixed"`, `end_reason == "dwell_elapsed"`, and an
      `insertion_uncertainty` equal to `compute_insertion_uncertainty` on the same
      samples.
- [ ] The same readout test with **no cup log open** shows the same text and writes no
      file.
- [ ] `tests/test_cup_settings_store.py`: defaults, a value at each bound accepted, and
      `target_uncertainty = 0.9` and `min_converge_samples = 1` each fall back to the
      default with a warning naming the key.
- [ ] An existing insertion's timing is unchanged: the existing scheduler and tab dwell
      tests pass unchanged.

May fake: the instrument feeds (`CupFeed`, `CupActuationFeed`) and modal dialogs. Must be
real: the tab, the scheduler, the cup log writer writing to `tmp_path`, and the pure
functions from ticket 81.

## Gate

Run all four, in CI's order (`.github/workflows/tests.yml`). All must pass.

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile
```

## Comments
