# 175: The Galil driver and the simulated slit can run HM and define the encoder zero

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/slit-homing-hm/spec.md` ("Controller driver", "Configuration constants", "Simulated controller"; user stories 48-49)
**Binding:** `docs/adr/0010-slits-homed-with-hm-as-nec-specifies.md`, `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/hardware/galil_driver.py` (`GalilController.define_zero`, `move_relative`, `get_stop_code`), `src/rbl/config/hardware_config.py` (the homing constants around `HOME_START_BACKOFF_COUNTS` and `HOME_EDGE_AGREEMENT_COUNTS`), `tests/sim_slit.py` (`SimSlit.__init__`, `_step_by`, `SimGalil.jog_start` for the overshoot rule and `slip_on_jog`, `SimGalil.define_zero`, `SimGalil._record`), `tests/test_galil_protocol.py` (`TestMotionCommands.test_define_zero`, `test_jog_start_negative_on_axis_c`, `make_galil`).

## What to build

Additions only. Nothing calls them yet (ticket 176 does), so nothing on screen changes.

1. `GalilController.home(axis)`: sends `HM <axis>` then `BG <axis>` (for axis C: `HM C`, `BG C`). It does not set the speed. Do not name it `begin_home`: an existing test asserts that name is gone.
2. `GalilController.define_encoder_zero(axis)`: sends `DE` with the axis's positional comma prefix and `0`, the same shape as `define_zero` (`DE ,,0` for C).
3. `src/rbl/config/hardware_config.py`, beside the homing constants: `HOMING_SPEED_COUNTS_PER_SEC = 900`, `PRE_HOME_MOVE_COUNTS = 2000`, `HOME_STOP_AGREEMENT_COUNTS = 4`, `HOME_ACCEPTED_STOP_CODES = (3, 10)`, each with a one-line comment citing ADR 0010. Remove nothing here; 176 and 177 remove the old ones.
4. `tests/sim_slit.py`:
   - `SimSlit` gains `slip_on_home: dict[int, int] | None = None` and a `_home_count`, kept like `slip_on_jog` and `_jog_count`.
   - `SimGalil.home(axis)`: record `("home", (axis,))`. If the reverse limit is already tripped, raise the same `GalilError` 22 as `jog_start`. Otherwise increment `_home_count`, then step −1 at a time until the home switch or the reverse limit trips. If the reverse limit tripped: overshoot by `(speed // 100) * overshoot_per_100_cps` counts at the slit's current `speed` (the rule `jog_start` uses), stop code 3. If only the home switch tripped (the reverse limit is deeper, `reverse_trip_at < trip_at`): stop at that count, no overshoot, stop code 10. Then apply `slip_on_home[_home_count]` to `offset` as `jog_start` applies `slip_on_jog`.
   - `SimGalil.define_encoder_zero(axis)`: record the call; change nothing else.

## Acceptance criteria

- [ ] Test `test_home_sends_hm_then_bg` in `tests/test_galil_protocol.py`: through `make_galil` and its fake socket, `g.home("C")` sends exactly `HM C` then `BG C`, and no `SP`.
- [ ] Test `test_define_encoder_zero_sends_de_with_the_axis_prefix` in `tests/test_galil_protocol.py`: `define_encoder_zero("C")` sends `DE ,,0`, and `define_encoder_zero("A")` sends `DE 0`.
- [ ] Test `test_sim_hm_stops_on_the_reverse_limit_with_code_3` in `tests/test_sim_galil_home.py` (new file): a default `SimGalil` (home switch and reverse limit trip on the same count) with the axis at 2000 and speed 900. After `home("A")`, `get_stop_code("A") == 3`, both switches read tripped, the physical position is `trip_at` minus 9 counts of overshoot, and the recorded calls include `("home", ("A",))`. A second `home("A")` now raises `GalilError` with code 22.
- [ ] Test `test_sim_hm_stops_at_the_home_switch_with_code_10_when_the_limit_is_deeper` in `tests/test_sim_galil_home.py`: with `reverse_trip_at` 50 counts below `trip_at`, `home` stops with the home switch tripped, the reverse limit clear, the position exactly `trip_at`, and stop code 10.
- [ ] Test `test_sim_hm_loses_steps_on_the_chosen_call` in `tests/test_sim_galil_home.py`: with `slip_on_home={2: 8}`, the commanded position after the second `home` (after moving out 2000 between calls) is 8 counts lower than after the first. `define_encoder_zero` records `("define_encoder_zero", ("A",))` and leaves `zeroed_at` and the position unchanged.

**What tests may fake:** the socket (`make_galil`'s fake), and the controller itself (`SimGalil`) for the simulator tests.

**Out of scope:** any routine, worker or tab change (176+); removing old constants; `CN`.

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile --max-worker-restart=0 tests/test_galil_protocol.py tests/test_sim_galil_home.py
```

Run pytest on the touched test files only (implement-ticket-parallel, section 4). CI runs the full suite on the PR.

## Comments
