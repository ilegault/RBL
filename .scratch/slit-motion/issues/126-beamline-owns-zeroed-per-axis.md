# 126: Beamline owns "zeroed", per axis, and it can turn off

**Status:** done

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 122

**Spec:** `.scratch/slit-motion/spec.md`
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/state/motor_control.py` (`ingest_motor_poll`, `emergency_stop`),
`src/rbl/snapshots.py` (`AxisSnapshot`, `MotorState`), `src/rbl/gui/motor_tab.py`
(`AxisControls.zeroed` and every write to it: `_define_zero`, `_on_homing_done`;
`MotorTab._on_auto_axis_finished`, the together-run's per-axis handler, `_on_state`,
`raw_state_changed`, `_do_connect`, `_emergency_stop`, `_send_manual`), `src/rbl/gui/app.py`
(the `raw_state_changed` → `ingest_motor_poll` connection), `tests/test_beamline.py`
(`TestMotorIngestion`, `TestBeamReconstruction`), `tests/test_auto_home.py` (`TestMotorTabAutoHome`),
`tests/test_e2e_session.py`.

## What to build

Today "zeroed" is an attribute on each Stepper Motors panel. It is set by Home and Zero and never
cleared, so after reconnecting to a Galil that was power-cycled every axis reads 0 counts and
still looks homed. A `DP` typed in the console does not set it. After this ticket **`Beamline` is
the single owner of zeroed, per axis** (glossary: *zeroed*), and every screen reads it from there.

1. `MotorControlMixin`: `_init_motors` creates `self._zeroed_axes: set[str] = set()`. Add
   `mark_zeroed(self, axis_letter: str)`, `clear_zeroed(self)` (all axes),
   `is_zeroed(self, axis_letter: str) -> bool`, `all_zeroed(self) -> bool` (every letter in
   `SC.AXIS_LETTERS`).
2. `AxisSnapshot` gains `zeroed: bool = False` (last field, defaulted, so other constructors keep
   working). `ingest_motor_poll(self, snapshot: dict)` drops its `zeroed` parameter, fills each
   `AxisSnapshot.zeroed` from `is_zeroed(letter)`, and sets `MotorState.zeroed = self.all_zeroed()`.
3. `MotorTab.raw_state_changed` becomes `Signal(dict)`; `_on_state` emits only the snapshot and no
   longer computes zeroed. `AxisControls.zeroed` is deleted. Every place that set it calls
   `self.beamline.mark_zeroed(axis)` instead: `AxisControls._define_zero` (after the `DP` succeeds),
   `AxisControls._on_homing_done` (success only), `MotorTab._on_auto_axis_finished` (ok only), and
   the together-run's per-axis finished handler (ok only).
4. Zeroed turns **off** for every axis on: a successful Galil connect (`MotorTab._do_connect`,
   after `startup_sequence` returns, before the poll worker starts) and an emergency stop.
   `Beamline.emergency_stop()` calls `clear_zeroed()` (whether or not the Galil is connected), and
   `MotorTab._emergency_stop` sends its abort through `self.beamline.emergency_stop()` instead of
   `self.galil.abort()`, keeping its `"> AB  (EMERGENCY STOP)"` console line. Limit trips and Motor
   Off do NOT clear it.
5. A `DP` typed in the Command Console turns zeroed on for the axes it names, only after the command
   succeeds. `MotorTab._send_manual` passes the upper-cased command to a pure module-level function
   `dp_axes(command: str) -> str` in `motor_tab.py` that returns the axis letters it defines:
   `DPA=0` / `DP A=0` → `"A"`; positional `DP 0,,5` → `"AC"` (each non-empty slot, by position,
   first four only); anything not starting with `DP`, or `DP` with no arguments → `""`.
   Pure so it is testable without a socket.
6. Update the docstrings that described the old panel flag (`AxisControls.__init__` comment,
   `MotorTab` `raw_state_changed` comment, `motor_control.py` module docstring) with a short
   **WHO OWNS ZEROED** paragraph: one owner, what turns it on, what turns it off and why limits
   and Motor Off do not (the controller decelerates and keeps counting; a de-energised stepper
   re-energises to the same position).

Existing tests rewritten in place (same names), because the behaviour they pinned moved:
`test_emits_motor_state_keyed_by_slit_label` and `test_pos_mm_matches_counts_to_mm` in
`tests/test_beamline.py` (call `mark_zeroed` for each axis where they relied on `zeroed=True`,
then `ingest_motor_poll(snapshot)`), `test_centred_beam_from_symmetric_currents` likewise,
`test_a_homed_axis_is_marked_referenced` and `test_together_marks_each_axis_referenced_as_it_reports`
in `tests/test_auto_home.py` (assert `beamline.is_zeroed(...)` instead of the panel attribute), and
`test_end_to_end_session_through_main_window` in `tests/test_e2e_session.py` (replace the
`p.zeroed = True` loop with `win.beamline.mark_zeroed(axis)` for every axis). What each asserts is
otherwise unchanged.

## Acceptance criteria

- [x] Test `test_reconnect_clears_zeroed` in `tests/test_motor_tab_zeroed.py` (new file): a real
      `Beamline` and `MotorTab`; `beamline.galil` replaced by a `MagicMock(connected=True)` whose
      `model_info` returns `"DMC4103"` and `get_soft_limits` returns
      `{"forward_counts": 0, "back_counts": 0}`; `rbl.gui.motor_tab.GalilPollWorker` patched with a
      `MagicMock` class. Mark all four axes zeroed, call `tab._do_connect()`;
      `beamline.all_zeroed() is False` and `beamline.is_zeroed("A") is False`.
- [x] Test `test_emergency_stop_clears_zeroed` in `tests/test_beamline.py`: with
      `beamline.galil = MagicMock(connected=True)`, `mark_zeroed("A")`, `mark_zeroed("C")`, then
      `emergency_stop()`; `galil.abort` called once and `is_zeroed("A")`, `is_zeroed("C")` are both
      `False`.
- [x] Test `test_motor_state_reports_zeroed_per_axis` in `tests/test_beamline.py`: `mark_zeroed("B")`
      only, then `ingest_motor_poll` with all four axes; the emitted state has
      `axes["X-"].zeroed is True`, `axes["X+"].zeroed is False`, and `state.zeroed is False`.
- [x] Test `test_dp_axes_parses_the_console_forms` in `tests/test_motor_tab_zeroed.py`:
      `dp_axes("DPA=0") == "A"`, `dp_axes("DP C=0") == "C"`, `dp_axes("DP 0,,5") == "AC"`,
      `dp_axes("DP ,,,0") == "D"`, `dp_axes("DP") == ""`, `dp_axes("MG _RPA") == ""`.
- [x] Test `test_console_dp_marks_the_named_axis_zeroed` in `tests/test_motor_tab_zeroed.py`: a real
      `Beamline` and `MotorTab` with `beamline.galil` replaced by a `MagicMock(connected=True)` whose
      `cmd` returns `""`; type `"DP ,0"` into `manual_cmd` and call `_send_manual()`;
      `beamline.is_zeroed("B") is True` and `is_zeroed("A") is False`. With `cmd` raising
      `GalilError`, nothing is marked.

**What tests may fake:** the Galil (`MagicMock`), `GalilPollWorker`, `QMessageBox`. `Beamline`,
`MotorTab`, `AxisControls` and the snapshot dataclasses are real.

**Out of scope:** the Raster Planner confirmation (ticket 127). The floor on targets (ticket 128).
Remembering zeroed across app restarts. Asking the controller whether it was reset.

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile
```

## Comments

### 2026-11-17 - blocked (implementer)

Implemented per the ticket: Beamline owns zeroed (mark/clear/is/all), AxisSnapshot.zeroed,
ingest_motor_poll(snapshot) with no zeroed parameter, raw_state_changed Signal(dict), connect and
E-stop clear it, console DP marks it, docstrings updated, rebased over 125 and 130. Ruff, tests-first
and type gate pass locally.

What fails: 6 tests in `tests/test_screenshot_overview.py` (test_scene_round_trips_through_the_real_conversion,
test_scene_reaches_the_overview_screen, test_feed_stops_when_real_data_arrives,
test_feed_stops_when_an_instrument_connects, test_feed_survives_its_own_pushes,
test_screenshot_mode_arranges_titles_and_unhooks_connect_all), all with
`TypeError: MotorControlMixin.ingest_motor_poll() takes 2 positional arguments but 3 were given`
at `scripts/screenshot_overview.py:157` (`beamline.ingest_motor_poll(scene.motor_poll, scene.zeroed)`).

Cause: the ticket drops the `zeroed` parameter, but the screenshot-mode script (ticket 61) still calls the
old signature. The tests are right; the fix belongs in `scripts/screenshot_overview.py`
(call `mark_zeroed` per axis when `scene.zeroed`, then `ingest_motor_poll(scene.motor_poll)`), and
implementers may not edit `scripts/`. I did not add a compatibility parameter, since that contradicts the ticket.

Decision needed: a human (or a ticket permitting it) updates `scripts/screenshot_overview.py` as above, then this branch
should go green. One further failure seen once under -n auto, `test_capture_client.py::test_hello_in_time_cancels_the_hello_timeout`,
passed on rerun (timing flake, unrelated).

### 2026-11-17 - block lifted

The block was lifted because the orchestrator clarified that scripts/screenshot_overview.py is editable (only the gate scripts are off limits); it is updated in this PR.
