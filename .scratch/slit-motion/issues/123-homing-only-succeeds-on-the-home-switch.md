# 123: Homing only succeeds when a pass stopped on the home switch

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/slit-motion/spec.md`
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/hardware/galil_workers.py` (module docstring, `AxisHomeRoutine.seek_home_limit`,
`_at_home_limit`, `home_passes`; `MultiAxisHomeRoutine.seek_home_limits`, `_at_home_limit`,
`home_passes`), `src/rbl/hardware/galil_driver.py` (`get_position`, `is_moving`),
`tests/test_auto_home.py` (`_galil`, `_jogging_galil`, `TestSeekHomeLimit`,
`TestSeekAgainstTheRealDriver`, `TestRoutineEndToEnd`, `TestMultiAxisHomeRoutine`),
`tests/test_galil_protocol.py` (`FakeGalilSocket`).

## What to build

Today no home pass checks **why** the axis stopped. A pass that runs into the reverse limit
also ends with the axis stopped, `wait_idle` returns `True`, and `define_zero` puts zero on
the limit. The seek also treats the reverse limit as "home reached". A wrong zero is worse
than a failed home, so after this ticket:

1. `galil_driver.py`: add `GalilController.get_stop_code(self, axis: str) -> int` returning
   `int(round(float(self.cmd(f"MG _SC{axis}"))))`.
2. `hardware_config.py`: add `HOME_STOP_CODE = 10` under the automatic-homing constants, with a
   comment: Galil SC code 10 is "stopped after homing (HM) or find index (FI)"; 2/3 are the
   forward/reverse limit or soft limit, 4 is ST, 7 is AB (DMC-40x0/41x3 command reference, SC).
3. `AxisHomeRoutine.home_passes`: after each pass's `wait_idle` succeeds, read
   `code = g.get_stop_code(axis)`. If `code != SC.HOME_STOP_CODE`: call `self._restore_speeds()`
   and return `(False, f"{axis}: pass {pass_num+1}/{n} did not stop on the home switch (stop code {code}) — zero not set")`.
   `define_zero` is reached only if every pass returned the home code.
4. `AxisHomeRoutine.seek_home_limit` and `_at_home_limit`: arriving means the **home switch**
   only. `_at_home_limit` returns `sw["home_switch"]`. If the reverse limit is active (at the
   start, or at any poll during the jog) and the home switch is not, stop the axis and return
   `(False, f"{axis}: reached the reverse limit before the home switch — check the home switch and its cable")`.
   The "jog stopped before reaching the home limit" branch keeps its current message.
5. `MultiAxisHomeRoutine`: the same two rules. Its `_at_home_limit` is home-switch only; an axis
   whose reverse limit is active without its home switch fails its seek with the message above
   (reported through `axis_done`, and the run returns `False` with no axis homed, as an existing
   seek failure does). After each pass, read `get_stop_code` for every axis; if any is not
   `HOME_STOP_CODE`, return `(False, "Homing stopped off the home switch on " + ", ".join(bad) + " — no zero set")`
   where each entry of `bad` is `f"{axis} (stop code {code})"`, and do not call
   `define_zero_multi`.
6. Rewrite the module docstring's homing section to say a pass is accepted only on stop code 10,
   and why (the reverse-limit incident this ticket describes).

Test helpers: `_galil()` in `tests/test_auto_home.py` gains `g.get_stop_code.return_value = SC.HOME_STOP_CODE`
so existing successful-run tests keep describing a successful run. That is a fixture change,
not a weakened test.

## Acceptance criteria

- [ ] Test `test_get_stop_code_reads_mg_sc` in `tests/test_galil_protocol.py`: with
      `FakeGalilSocket(responses={"MG _SCB": "10.0000"})`, `get_stop_code("B") == 10` and the
      socket's `sent` list contains `"MG _SCB"`.
- [ ] Test `test_a_pass_that_stops_on_a_limit_sets_no_zero` in `tests/test_auto_home.py`:
      `_galil()` with `get_stop_code.side_effect = [10, 3]`; `AxisHomeRoutine(g, "A").run()`
      returns `(False, msg)` with `"pass 2/3 did not stop on the home switch (stop code 3)"` in
      `msg`; `g.define_zero` was never called; `g.set_speed` was called with
      `("A", SC.DEFAULT_SPEED_COUNTS_PER_SEC)` (speeds restored on the failure path).
- [ ] Test `test_seek_fails_on_the_reverse_limit_before_home` in `tests/test_auto_home.py`:
      `_jogging_galil(_switches(), _switches(reverse=True))`; `seek_home_limit()` returns
      `(False, msg)` with `"reached the reverse limit before the home switch"` in `msg`, and
      `g.stop` was called with `"A"`. The existing test `test_stops_on_the_reverse_limit_too`
      is rewritten in place (same name) to assert this failure, and
      `test_an_axis_on_its_reverse_limit_is_detected` is rewritten in place to assert that an
      axis with only its reverse limit active is NOT at the home limit.
- [ ] Test `test_together_sets_no_zero_when_one_axis_stops_off_home` in `tests/test_auto_home.py`
      (class `TestMultiAxisHomeRoutine`): a `_galil()` whose `get_stop_code` returns 10 for A, B, D
      and 3 for C on the first pass; `MultiAxisHomeRoutine(g).run(seek_first=False)` returns
      `False` with `"C (stop code 3)"` in the message, and `g.define_zero_multi` was never called.
- [ ] Every other test in `tests/test_auto_home.py` passes with only the `_galil()` helper change. (no test: regression check by the existing suite)

**What tests may fake:** the controller (`MagicMock(spec=GalilController)` via the file's helpers,
or `FakeGalilSocket` for the driver test) and `time.sleep` (the file's `no_sleep` fixture).
The routines under test are real.

**Out of scope:** the back-out at the start of homing (ticket 124). Adjustable pass speeds
(ticket 125). Directions, CN, limits. The Stepper Motors tab's messages (a failed run already
raises its "Homing failed" dialog with the returned message).

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile
```

## Comments
