# 148: Switch readings wait until the axis is at rest

**Status:** done

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 147

**Spec:** `.scratch/slit-homing-release-edge/spec.md` (Addendum 2026-11-20)
**Binding:** `docs/adr/0009-slits-homed-on-the-release-edge.md` (Decision 2, amended 2026-11-20: at rest
means `_TD` equals `_RP`), `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `CONTEXT.md` (**At rest**), `src/rbl/hardware/galil_driver.py`
(`GalilController.get_stop_code` for the shape of an `MG` read), `src/rbl/hardware/galil_workers.py`
(`AxisHomeRoutine.wait_idle`, `AxisHomeRoutine.approach` and its final `_arrival()` read,
`AxisHomeRoutine.creep_to_release`, `SwitchMapRoutine.run` and its inward and outward step loops),
`tests/sim_slit.py` (all of it: `SimSlit`, `SimGalil._record`, `SimGalil.move_relative`),
`tests/test_galil_protocol.py` (`TestReads`, `make_galil`), `tests/test_auto_home.py`
(`TestReleaseEdgeHoming`), `tests/test_switch_map_routine.py` (`test_switch_map_finds_a_shared_switch`).

## What to build

Homing's creep and the switch map read the switches after every one-count step, "at rest". Today the
code treats the axis as at rest as soon as `_BG` reads 0, which only says the controller has finished
planning the move. The DMC-41x3 manual (Monitoring Generated Pulses vs. Commanded Pulses) says that on
steppers the smoothing filter can still be sending step pulses after that. The pulses actually sent are
`_TD`; the commanded position is `_RP`. After this ticket, every switch reading in a home pass and in
the switch map waits until both say the move is over.

1. `src/rbl/hardware/galil_driver.py`: add `GalilController.get_step_count(self, axis: str) -> int`,
   returning `int(round(float(self.cmd(f"MG _TD{axis}"))))`, with a docstring: the step pulses actually
   sent to the motor (`_TDx`); it trails `_RPx` while the smoothing filter is still sending pulses.
2. `tests/sim_slit.py`: the simulated slit can make its pulses trail the command. This is a
   requirement, not an option: without it no test can tell a read at `_BG` from a read at rest.
   - `SimSlit.__init__` gains `pulse_lag_calls: int = 0`, and `SimGalil(**slit_kwargs)` passes it through
     like the other slit keywords.
   - When `pulse_lag_calls` is above 0, `SimGalil.move_relative` does not move the jaw at once. It
     raises the limit `GalilError` exactly as now when the move cannot begin, and otherwise holds the
     whole delta as pending. The next `pulse_lag_calls` calls to any `SimGalil` method (counted in
     `_record`) still see the jaw where it was. The call after that, before it does anything else,
     applies the pending delta with today's stepping rules (outward: `_step_by`; inward: one count at a
     time, stopping on the reverse limit with stop code 3). A new `move_relative` first applies any
     pending delta at once, then handles its own.
   - Add `SimGalil.get_step_count(axis)`: records the call and returns the jaw's position from the
     pulses sent so far (`phys + offset`). `get_position` returns the commanded position: that plus
     any pending delta. The switches follow the pulses sent (`phys`), never the command.
   - With `pulse_lag_calls=0` every existing behaviour is unchanged.
3. `src/rbl/hardware/galil_workers.py`: add
   `AxisHomeRoutine.wait_at_rest(self, timeout: float = 15.0) -> tuple[bool, str]`. Within one deadline
   it polls `is_moving` as `wait_idle` does, then polls `get_step_count(axis)` and `get_position(axis)`
   every `_IDLE_POLL_S` until they are equal. Returns `(True, "")` at rest; `(False, "")` if cancelled
   or still moving at the deadline; and `(False, f"step pulses still pending: TD {td}, RP {rp}")` with
   the last values read if the profiler finished but the two never matched. `wait_idle` itself is
   unchanged, and so are its other callers.
4. Use `wait_at_rest` before these reads, and only these:
   - `AxisHomeRoutine.creep_to_release`: in place of `self.wait_idle(timeout=15.0)`. A non-empty message
     returns `(False, f"{axis}: {msg}", None)`; an empty one keeps today's cancel and timeout returns.
   - `AxisHomeRoutine.approach`: after the `while g.is_moving(axis)` loop, before the final
     `self._arrival()`. A non-empty message returns `(False, f"{axis}: {msg}")`; an empty one returns
     the cancel message if cancelled, otherwise
     `(False, f"{axis}: timeout waiting for the approach to come to rest")`.
   - `SwitchMapRoutine.run`: in place of `home.wait_idle(timeout=15.0)` in both step loops. A non-empty
     message returns `(False, f"{axis}: {msg}", rows)`; an empty one keeps today's returns.
5. Docstrings of `creep_to_release` and `SwitchMapRoutine` say "at rest (every step pulse sent)".

## Acceptance criteria

- [x] Test `test_get_step_count_reads_mg_td` in `tests/test_galil_protocol.py` (in `TestReads`, built
      with `make_galil`): with `"MG _TDC"` answering `"-125.0000"`, `get_step_count("C")` returns -125
      and `"MG _TDC"` is in `g.sock.sent`.
- [x] Test `test_homing_reads_the_edge_at_rest_when_pulses_lag` in `tests/test_auto_home.py` (in
      `TestReleaseEdgeHoming`): `AxisHomeRoutine(sim, "A").run(seek_first=False)` on
      `SimGalil(trip_at=-3, release_at=6, pulse_lag_calls=2)` succeeds, its message contains
      "edges 6, 6 cts", and `sim.slits["A"].zeroed_at == [6]`. (Reading at `_BG` alone reports the edge
      one count late.)
- [x] Test `test_switch_map_reads_switches_at_rest` in `tests/test_switch_map_routine.py`:
      `SwitchMapRoutine(sim, "A").run()` on `SimGalil(trip_at=-3, release_at=6, start_at=2000,
      pulse_lag_calls=2)` succeeds and `find_switch_edges(rows) == SwitchEdges(-3, -3, 6, 6)`, the same
      edges the no-lag test finds.
- [x] Test `test_approach_checks_rest_before_reading_the_switches` in `tests/test_auto_home.py` (in
      `TestReleaseEdgeHoming`): after a successful `AxisHomeRoutine(sim, "A").run(seek_first=False)` on
      `SimGalil(trip_at=-3, release_at=6)`, for every `jog_start` in `sim.calls`, a `get_step_count`
      call for axis A comes after it and before the next `get_switch_states` call.
- [x] Test `test_wait_at_rest_names_the_pending_pulses` in `tests/test_auto_home.py` (in
      `TestReleaseEdgeHoming`): on `SimGalil(trip_at=-3, release_at=6, start_at=2000,
      pulse_lag_calls=10**9)`, after `sim.move_relative("A", 5)`,
      `AxisHomeRoutine(sim, "A").wait_at_rest(timeout=0.05)` returns
      `(False, "step pulses still pending: TD 2000, RP 2005")`.
- [x] (no test: CI's full suite) No test is deleted; every existing homing, edge-return and switch-map
      test passes unchanged with the default `pulse_lag_calls=0`.

**What tests may fake:** the Galil, through `SimGalil` (with the lag above) or `make_galil`, and
`time.sleep` (the existing `no_sleep` fixtures). The routines and the driver's parsing are real.

**Out of scope:** speeds (147); `KS` (149); other `wait_idle` callers (back-offs, the seek, the
edge-return move out); stepper position maintenance and encoders; `LC`.

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile --max-worker-restart=0 tests/test_galil_protocol.py tests/test_auto_home.py tests/test_switch_map_routine.py tests/test_edge_return_routine.py
```

pytest runs on the touched test files only; CI runs the full suite on the PR.

## Comments

2026-11-20: Added `GalilController.get_step_count` (`MG _TDx`), `SimSlit`/`SimGalil` `pulse_lag_calls` simulation in `tests/sim_slit.py`, `AxisHomeRoutine.wait_at_rest` polling `_BG == 0` then `_TD == _RP`, and switched `approach` arrival read, `creep_to_release`, and `SwitchMapRoutine.run` step loops to `wait_at_rest`. Added all five named tests across `tests/test_galil_protocol.py`, `tests/test_auto_home.py`, and `tests/test_switch_map_routine.py`. Local gate passed (`ruff check .`, `check_tests_first.py`, `type_gate.py`, and 121 tests in the four Galil/homing/switch-map/edge-return test files). Bench verification (`MG _TDx,_RPx` at rest) remains in ticket 142.
