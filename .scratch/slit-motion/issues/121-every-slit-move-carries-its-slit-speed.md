# 121: Every slit move carries its slit speed

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/slit-motion/spec.md`
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/state/motor_control.py` (`MotorControlMixin.move_slit`, `_init_motors`),
`src/rbl/hardware/galil_driver.py` (`move_absolute`, `set_speed`, `axis_vector`),
`tests/test_beamline.py` (`TestMoveSlit`), `tests/test_slit_cross_screen.py` (`_FakeGalil`, `screens`).

## What to build

The Galil keeps ONE speed per axis, shared by jog, `PA`/`PR` moves and HM's first stage.
Today `Beamline.move_slit` (used by the Overview and the Raster Planner) sends `PA` + `BG`
and never a speed, so a move runs at whatever speed the last jog or homing left behind.
After this ticket every move that goes through `Beamline` sends the axis's **slit speed**
immediately before the `PA`.

1. `src/rbl/hardware/galil_driver.py`: add `GalilController.move_absolute_at(self, axis: str,
   position_counts: int, speed_counts_per_sec: int)`. It sends exactly three commands, in
   this order, using the same positional-prefix form as `move_absolute`:
   `SP <prefix><speed>`, `PA <prefix><position>`, `BG <axis>`. `move_absolute` is unchanged
   (homing still uses it).
2. `src/rbl/state/motor_control.py`, in `MotorControlMixin`:
   - `_init_motors` creates `self._slit_speed_cps: dict[str, int]` with every letter in
     `SC.AXIS_LETTERS` set to `SC.DEFAULT_SPEED_COUNTS_PER_SEC`.
   - `slit_speed_cps(self, axis_letter: str) -> int` returns it.
   - `set_slit_speed_cps(self, axis_letter: str, cps: int) -> None` stores `int(cps)`;
     raises `ValueError` if `cps < 1` (store nothing in that case).
   - `move_axis_counts(self, axis_letter: str, counts: int) -> bool` calls
     `self.galil.move_absolute_at(axis_letter, counts, self.slit_speed_cps(axis_letter))`
     and returns `True`. It does not catch driver exceptions: callers handle them.
     (Ticket 128 adds a refusal path that returns `False`; keep the `bool` return.)
   - `move_slit` sends through `move_axis_counts` instead of `galil.move_absolute`. Its
     console line becomes
     `f"> SP {axis_letter}={speed} ; PA {axis_letter}={counts} ({reachable_mm:+.3f} mm) ; BG {axis_letter}   [{slit}]"`.
     Everything else about `move_slit` (publish target first, log refusals) is unchanged.
3. Update the module docstring of `motor_control.py` with a short **WHY EVERY MOVE SENDS
   ITS SPEED** paragraph: the Galil's single per-axis speed register, the jog-on-one-tab /
   crawl-on-the-Overview symptom, and the rule that no move relies on speed state left on
   the controller.

`_FakeGalil` in `tests/test_slit_cross_screen.py` is a test helper: give it a
`move_absolute_at(axis, counts, speed)` that appends `(axis, counts)` to `self.moves`
(unchanged shape) and `speed` to a new `self.speeds` list. `MagicMock` galils in
`tests/test_beamline.py` need no change.

## Acceptance criteria

- [ ] Test `test_move_absolute_at_sends_sp_then_pa_then_bg` in `tests/test_galil_protocol.py`:
      using that file's `FakeGalilSocket`, `move_absolute_at("C", 3653, 750)` puts exactly
      `["SP ,,750", "PA ,,3653", "BG C"]` on the socket, in that order.
- [ ] Test `test_move_slit_sends_the_axis_slit_speed` in `tests/test_beamline.py`: with
      `beamline.galil = MagicMock(connected=True)`, `set_slit_speed_cps("A", 640)` then
      `move_slit("X+", 5.0)`; `beamline.galil.move_absolute_at` was called once with
      `("A", SC.mm_to_counts("A", 5.0), 640)` and `beamline.galil.move_absolute` was never called.
- [ ] Test `test_slit_speed_defaults_and_rejects_below_one` in `tests/test_beamline.py`: a fresh
      `Beamline` returns `SC.DEFAULT_SPEED_COUNTS_PER_SEC` from `slit_speed_cps(axis)` for all
      four letters; `set_slit_speed_cps("B", 0)` raises `ValueError` and
      `slit_speed_cps("B")` is still the default afterwards.
- [ ] Test `test_overview_move_carries_the_slit_speed` in `tests/test_slit_cross_screen.py`: on
      the `screens` fixture, `beamline.set_slit_speed_cps("C", 300)`, then
      `beamline.move_slit("Y+", 4.0)` (the Overview's path, as the file's existing console test drives it);
      `beamline.galil.speeds == [300]` and the console text contains `"SP C=300"`.
- [ ] The existing test `test_move_slit_converts_label_to_axis_and_mm_to_counts` in
      `tests/test_beamline.py` is rewritten in place (same name) to read the axis letter from
      `beamline.galil.move_absolute_at.call_args.args[0]`. Every other existing test passes
      unedited apart from the `_FakeGalil` helper change above.

**What tests may fake:** the Galil (`MagicMock`, `_FakeGalil`, or `FakeGalilSocket`). `Beamline`,
`MotorControlMixin`, `OverviewTab` and `MotorTab` are real.

**Out of scope:** the Stepper Motors tab's Speed box, its Move and Jog (ticket 122). Homing
speeds (tickets 123–125). Any floor on targets (ticket 128).

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile
```

## Comments
