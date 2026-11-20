# 150: A value typed under a number box's minimum says "Min is N"

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/slit-homing-release-edge/spec.md` (Addendum 2026-11-20)
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/gui/widgets/inputs.py` (the module docstring, `QuietDoubleSpinBox.validate`
and the `QToolTip.showText` call that shows "Max is N", `QuietDoubleSpinBox.focusOutEvent`),
`tests/test_inputs.py` (the `make_int_box` fixture, `_focus_out`, and
`test_a_keystroke_over_the_maximum_shows_the_maximum_beside_the_box` for the click-then-type pattern
and the `QToolTip` checks). Do NOT use the `_type` helper: it drives the line edit directly and skips
the click.

## What to build

Typing a number under a box's minimum and committing it (Enter, or leaving the box) today snaps
back to the previous value with no explanation. After this ticket the box takes its minimum and says
why, the same way a keystroke over the maximum already does. It lives in the shared
`QuietDoubleSpinBox`, so every number box in the app gets it.

1. `src/rbl/gui/widgets/inputs.py`: move the `QToolTip.showText(...)` call out of
   `QuietDoubleSpinBox.validate` into a private method `_show_note(self, text: str)` (same position,
   same 2000 ms, no focus change); `validate` calls `self._show_note(f"Max is ...")` with exactly the
   text it shows now.
2. Override `QuietDoubleSpinBox.fixup(self, text)`. Qt calls it when an edit ending in Intermediate
   text is committed. If `float(text.strip())` parses and is below `self.minimum()`, call
   `self._show_note(f"Min is {self.textFromValue(self.minimum())}")` and return
   `self.textFromValue(self.minimum())`. Otherwise return what `super().fixup(text)` returns. Typing
   below the minimum is still allowed mid-word (validate is unchanged); only the commit corrects it.
3. Add one paragraph to the module docstring after the "Max is N" paragraph saying this.

## Acceptance criteria

- [ ] Test `test_typing_under_the_minimum_then_leaving_takes_the_minimum_and_says_so` in
      `tests/test_inputs.py`: `make_int_box(2, 2000, 900)`; click at the right end of its line edit,
      wait 20 ms, `QTest.keyClicks` "1", then `_focus_out`: `value() == 2`, the line edit reads "2",
      `QToolTip.isVisible()` and `QToolTip.text() == "Min is 2"`.
- [ ] Test `test_typing_under_the_minimum_then_enter_takes_the_minimum` in `tests/test_inputs.py`: the
      same box and typing, then `QTest.keyClick` Return instead of leaving: `value() == 2` and
      `QToolTip.text() == "Min is 2"`.
- [ ] Test `test_min_note_uses_the_boxes_decimals` in `tests/test_inputs.py`: a box from `make_int_box`
      then set to 2 decimals, range 0.01..100, value 5; click, type "0", leave: `value() == 0.01` and
      `QToolTip.text() == "Min is 0.01"`.
- [ ] Test `test_typing_in_range_then_leaving_shows_no_min_note` in `tests/test_inputs.py`: after
      `QToolTip.hideText()`, `make_int_box(2, 2000, 900)`, click, type "300", leave: `value() == 300`
      and `QToolTip.isVisible()` is false.
- [ ] (no test: CI's full suite) No test is deleted; every existing test in `tests/test_inputs.py`
      passes unchanged, including the "Max is N" tests and
      test_leading_zero_is_not_clamped_to_the_minimum_mid_word.

**What tests may fake:** nothing. The box is a real `QuietDoubleSpinBox` in a shown window, driven with
real mouse clicks, keystrokes and focus changes.

**Out of scope:** the slit and approach speed minimums (151); changing `validate`'s accept/refuse rules;
any per-screen code.

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile --max-worker-restart=0 tests/test_inputs.py
```

pytest runs on the touched test files only; CI runs the full suite on the PR.

## Comments
