# 133: Remove the dead slit signals and the second counts-to-mm conversion

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 128, 132

**Spec:** `.scratch/slit-motion/spec.md`
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `AGENTS.md` §2 ("One conversion"), `src/rbl/gui/motor_tab.py` (`MotorTab.slit_state`,
`_on_state`, `_do_disconnect`), `src/rbl/gui/raster_planner_tab.py` (`slit_targets_ready`,
`_apply_slits`), `src/rbl/gui/app.py` (the comment near the `raw_state_changed` wiring),
`src/rbl/gui/logamp_tab.py` (`set_slit_state` docstring).

## What to build

Two signals are emitted and connected to nothing: `MotorTab.slit_state` and
`RasterPlannerTab.slit_targets_ready`. The first also converts counts to mm inside a widget, a second
conversion beside `Beamline.ingest_motor_poll`, which `AGENTS.md` invariant 2 forbids. Slit geometry
already reaches every consumer through `Beamline.motors_changed`. Remove the dead paths.

1. `MotorTab`: delete the `slit_state` signal, its comment block, the `positions` computation in
   `_on_state`, and the `slit_state.emit(...)` in `_do_disconnect` (`motors_disconnected` already
   tells `Beamline`).
2. `RasterPlannerTab`: delete `slit_targets_ready` and its emit in `_apply_slits`.
3. Fix the references in comments and docstrings: the `app.py` comment that compares against
   `slit_state`, and `LogAmpTab.set_slit_state`'s docstring, which should say the geometry comes from
   `Beamline.motors_changed` via `on_motor_state`. `set_slit_state` itself stays: `on_motor_state` and
   the beam indicator use it.
4. `grep -rn "slit_state\b\|slit_targets_ready" src --include=*.py` then matches only `set_slit_state`
   definitions and calls.

## Acceptance criteria

- [ ] Test `test_motor_tab_has_no_private_slit_geometry_signal` in `tests/test_slit_cross_screen.py`:
      `not hasattr(MotorTab, "slit_state")` and `not hasattr(RasterPlannerTab, "slit_targets_ready")`.
- [ ] The grep in step 4 matches nothing else. (by hand)
- [ ] Every existing test passes unedited. (no test: this ticket only removes code)

**What tests may fake:** the Galil (`_FakeGalil`). `MotorTab`, `RasterPlannerTab` and `Beamline` are real.

**Out of scope:** `AxisControls.update_state`'s own mm label (it formats the raw poll for the Stepper
Motors tab's readout; moving it is a separate refactor). Any behaviour change.

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile
```

## Comments
