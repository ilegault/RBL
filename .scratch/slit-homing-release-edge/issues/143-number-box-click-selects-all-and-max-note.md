# 143: Clicking into a number box selects the whole value, and a keystroke over the maximum says why

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/slit-homing-release-edge/spec.md` (Addendum 2026-11-19)
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/gui/widgets/inputs.py` (the module docstring, `QuietDoubleSpinBox.focusInEvent`,
and `ScientificDoubleSpinBox.validate` for the shape of a `validate` override), `tests/test_inputs.py`
(the `spin` fixture for a box in a shown window with focus parked on a sibling button, and `_focus_in`
/ `_focus_out`). Do NOT use the `_type` helper in the new tests: it calls `selectAll()` itself before
typing, which is exactly the step this ticket fixes, so a test built on it passes on the broken code.

## What to build

The operator clicks into any number box in the app and types a new value; what they typed replaces
the old value. Today `QuietDoubleSpinBox.focusInEvent` selects the whole value, but when focus arrives
by a mouse click, Qt delivers the click's press and release after the focus-in event, and they move
the cursor to where the pointer landed and clear the selection. The typed digits are spliced into the
old number, and Qt's validator silently drops any keystroke that would take the value over the
maximum. Reproduced: a box with range 1..500 showing 10, clicked at its right end, then typed "300",
reads "103"; showing 150, it stays "150".

1. `QuietDoubleSpinBox.focusInEvent` in `src/rbl/gui/widgets/inputs.py`: keep the `super()` call,
   and replace the direct `self.selectAll()` with `QTimer.singleShot(0, self, self.selectAll)` (import
   `QTimer` from `PySide6.QtCore`). The zero-delay timer runs after the click's press and release have
   been handled, so the selection survives; passing `self` as the context object means the call is
   dropped if the box is destroyed first. Rewrite the method's docstring to say this, including why a
   direct call does not survive a mouse click. A click into a box that already has focus does not
   re-select (there is no focus-in), so ordinary cursor editing inside a focused box is unchanged.
2. Add `QuietDoubleSpinBox.validate(self, text, pos)`: call `super().validate(text, pos)` and return its
   result unchanged in every case. Additionally, when the returned state is
   `QValidator.State.Invalid` and `float(text.strip())` succeeds and is greater than `self.maximum()`,
   show the note:
   `QToolTip.showText(self.mapToGlobal(QPoint(0, self.height())), f"Max is {self.textFromValue(self.maximum())}", self, QRect(), 2000)`
   (`QToolTip` from `PySide6.QtWidgets`, `QPoint` and `QRect` from `PySide6.QtCore`). A `ValueError`
   from `float()` means no note. The keystroke is still refused; the note only says why. It does not
   take focus. The box does not know its unit (units live in a label beside it, see `unit_row`), so
   the note gives the number only. `ScientificDoubleSpinBox` overrides `validate` without calling
   `super()` and is left as it is.
3. Add one short paragraph to the module docstring of `inputs.py` under the existing sections:
   clicking into a box selects all of it, and a keystroke past the maximum is refused with a
   "Max is N" note beside the box.

## Acceptance criteria

New tests go in `tests/test_inputs.py`. Each builds its own `QuietDoubleSpinBox` with
`setDecimals(0)`, inside a shown `QWidget` with a sibling `QPushButton` that holds focus first (copy
the `spin` fixture's shape; a second fixture is fine). Clicks are real `QTest.mouseClick` calls on
`box.lineEdit()`, keys are real `QTest.keyClicks` calls, and `QTest.qWait(20)` follows each click so
the zero-delay timer runs. Call `QToolTip.hideText()` at the start of each test that checks the note.

- [ ] Test `test_clicking_into_a_box_then_typing_replaces_the_value`: range 1..500, value 10. Click at
      the right end of the line edit (x = width - 3), wait, type "300", move focus to the sibling
      button. `box.value() == 300` and `box.lineEdit().text() == "300"`. On the current code the text
      is "103".
- [ ] Test `test_clicking_into_the_middle_of_a_box_selects_it_all`: range 1..500, value 150. Click at
      x = 4 (inside the first digit), wait. `box.lineEdit().selectedText() == "150"`.
- [ ] Test `test_a_keystroke_over_the_maximum_shows_the_maximum_beside_the_box`: range 1..2000, value
      150. Click in, wait, type "2500". The text reads "250" (the last "0" refused),
      `QToolTip.isVisible()` is true and `QToolTip.text() == "Max is 2000"`.
- [ ] Test `test_an_in_range_keystroke_shows_no_note`: range 1..2000, value 150. Click in, wait, type
      "300". The text reads "300" and `QToolTip.isVisible()` is false.
- [ ] (no test: covered by the gate) Every existing test in `tests/test_inputs.py` and
      `tests/test_no_scroll_inputs.py` passes unchanged.

**What tests may fake:** nothing. The box, the window, the mouse click, the keystrokes and the tooltip
are all real Qt under `QT_QPA_PLATFORM=offscreen` (checked: offscreen reports the tooltip's
visibility and text).

**Out of scope:** `NoScrollSpinBox` and `ScientificDoubleSpinBox`; a note for values below the
minimum; putting the unit in the note; any change to the Approach speed ceiling (144).

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile
```

## Comments
