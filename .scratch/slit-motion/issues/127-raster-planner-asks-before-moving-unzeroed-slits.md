# 127: The Raster Planner asks before moving slits that are not zeroed

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 126

**Spec:** `.scratch/slit-motion/spec.md`
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/gui/raster_planner_tab.py` (`RasterPlannerTab._apply_slits`),
`src/rbl/gui/overview_tab.py` (`OverviewTab._confirm_unzeroed_move` and how
`_on_move_requested` uses it: the dialog to copy), `src/rbl/state/motor_control.py`
(`is_zeroed`, `all_zeroed`, added by ticket 126), `tests/test_raster_planner_tab.py`
(the `FakeBeamline` classes inside the apply tests).

## What to build

The Overview asks before moving a slit that is not zeroed, because its mm figure is then an offset
from wherever the controller started, not a distance from beam centre. The Raster Planner's
**Apply to slits** commands all four blades with no such question. After this ticket it asks the
same kind of question first.

1. `RasterPlannerTab._confirm_unzeroed_apply(self, slits: list[str]) -> bool`: a
   `QMessageBox.warning` titled `"Slits not zeroed"` with the text
   `f"{', '.join(slits)} not zeroed since the Galil was last connected, so their positions in mm are not referenced to beam centre.\n\nMove all four blades anyway?"`
   and Yes/No buttons, default No. Returns `True` only for Yes. Its own method so tests can drive
   both answers without a live dialog, like the Overview's.
2. `_apply_slits`: after the existing early return, and before the existing "Move the slits?"
   question, build `unzeroed = [label for label in AMP_LABELS if not self._beamline.is_zeroed(self._beamline.axis_letter_for(label))]`.
   If it is non-empty and `_confirm_unzeroed_apply(unzeroed)` returns `False`, return without
   moving anything. Otherwise continue exactly as today.

Test helpers: the `FakeBeamline` classes in `tests/test_raster_planner_tab.py` gain
`is_zeroed(self, axis) -> True` and `axis_letter_for(self, label)` returning the matching letter
(`{"X+": "A", "X-": "B", "Y+": "C", "Y-": "D"}`), so the existing apply tests keep describing a
zeroed beamline. No existing assertion changes.

## Acceptance criteria

- [ ] Test `test_apply_to_unzeroed_slits_asks_and_can_be_declined` in
      `tests/test_raster_planner_tab.py`: a `FakeBeamline` whose `is_zeroed` returns `False` for
      `"C"` and `True` otherwise, recording `move_slit` calls; patch
      `RasterPlannerTab._confirm_unzeroed_apply` to record its argument and return `False`; patch
      `QMessageBox.question` to Yes; click `btn_apply_slits`; the recorded argument is `["Y+"]`
      and no `move_slit` call was made.
- [ ] Test `test_apply_to_unzeroed_slits_moves_when_accepted` in `tests/test_raster_planner_tab.py`:
      the same, with the confirmation returning `True`; `move_slit` was called for all four labels.
- [ ] Test `test_apply_to_zeroed_slits_does_not_ask` in `tests/test_raster_planner_tab.py`: every
      axis zeroed; `_confirm_unzeroed_apply` patched to raise `AssertionError` if called; clicking
      `btn_apply_slits` (question patched to Yes) moves all four.
- [ ] Test `test_apply_reads_zeroed_from_a_real_beamline` in `tests/test_raster_planner_tab.py`: a real
      `Beamline` with `move_slit` monkeypatched to record calls and `mark_zeroed` called for A, B, D
      only; with the confirmation patched to return `False`, nothing is recorded and the
      confirmation received `["Y+"]`.

**What tests may fake:** `QMessageBox.question`, `_confirm_unzeroed_apply`, `move_slit`, and the
`FakeBeamline` test doubles. In the last criterion `Beamline`'s zeroed state is real.

**Out of scope:** the floor on targets (ticket 128). Any change to the Overview's dialog.

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile
```

## Comments
