# 139: Switch map measurement (routine, analysis, CSV)

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 135

**Spec:** `.scratch/slit-homing-release-edge/spec.md`
**Binding:** `docs/adr/0009-slits-homed-on-the-release-edge.md`, `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `CONTEXT.md` (Slit motion: *switch map*, *home switch*, *reverse limit*),
`src/rbl/hardware/galil_workers.py` (`AxisHomeRoutine.back_out_of_home`, `wait_idle`,
`_restore_speeds`; `EdgeReturnRoutine` / `EdgeReturnWorker` for the routine-plus-QThread shape
and the "never raises, returns (ok, msg, data)" contract), `src/rbl/hardware/edge_return.py`
(a pure analysis module with a frozen dataclass and a summary-line function),
`src/rbl/services/edge_return_writer.py` (`write_edge_return_csv`: directory resolution,
file naming, `paths.HOMING_DIR` read at call time), `tests/sim_slit.py` (`SimGalil`, `SimSlit`,
ticket 135), `tests/test_edge_return_writer.py`.

## What to build

A measurement of where an axis's switches change state, without touching zero: step the axis in
through the home switch and the reverse limit one count at a time, then back out, reading every
switch at rest after each step. This ticket delivers the routine, the analysis and the CSV; the
Stepper Motors tab box is ticket 140.

1. **Constants** in `hardware_config.py`, with comments: `SWITCH_MAP_STEP_COUNTS = 1`,
   `SWITCH_MAP_STEP_SPEED_COUNTS_PER_SEC = 25`, `SWITCH_MAP_BACKOFF_COUNTS = 300`,
   `SWITCH_MAP_MAX_PAST_FIRST_TRIP_COUNTS = 100` (less than the 0.2 mm, about 126 counts, from
   home to beam centre, so the map cannot reach an opposing jaw parked at home),
   `SWITCH_MAP_CLEAR_MARGIN_COUNTS = 50`, `SWITCH_MAP_MAX_OUT_COUNTS = 400`.
2. **`src/rbl/hardware/switch_map.py` (new), pure, no I/O.** This is a requirement, not a
   preference: the edge-finding is tested with plain data, without a controller.
   - `@dataclass(frozen=True) class SwitchMapRow: step: int; direction: str; position_counts: int; home: bool; reverse: bool; forward: bool`
     (`direction` is `"in"` or `"out"`; `True` = tripped).
   - `@dataclass(frozen=True) class SwitchEdges: home_trip: int | None; reverse_trip: int | None; home_release: int | None; reverse_release: int | None`.
   - `find_switch_edges(rows: list[SwitchMapRow]) -> SwitchEdges`: a switch's trip is the
     `position_counts` of the first `"in"` row where it reads tripped after reading clear on the
     row before (the first row counts as following a clear reading); its release is the first
     `"out"` row where it reads clear after reading tripped. `None` when that never happens.
   - `switch_change_line(axis, direction, switch_name, tripped, position) -> str`:
     `f"{axis}: {direction} — {switch_name} {'trips' if tripped else 'releases'} at {position} cts"`.
   - `switch_map_summary(axis, edges, inward_stop) -> str`:
     `f"{axis}: in — home trips at {h}, reverse trips at {r} (gap {gap}); out — home releases at {hr}, reverse releases at {rr}; inward stop: {inward_stop}"`
     where a `None` position prints `never` and `gap` is `r - h`, or `—` if either is `None`.
3. **`SwitchMapRoutine`** in `galil_workers.py`:
   `__init__(self, galil, axis, progress=None, cancelled=None, on_row=None)`;
   `run(self) -> tuple[bool, str, list[SwitchMapRow]]`, never raises, never calls `define_zero`.
   1. `AxisHomeRoutine(galil, axis, progress, cancelled).back_out_of_home()`.
   2. `set_speed(axis, SC.HOME_SEEK_SPEED_COUNTS_PER_SEC)`, `jog_start(axis, -SC.HOME_SEEK_SPEED_COUNTS_PER_SEC)`;
      poll the switches until the home switch or the reverse limit reads tripped (then `stop`)
      or the axis stops by itself; fail if neither is tripped, or after `SC.HOME_SEEK_TIMEOUT_S`.
   3. `move_relative(axis, SC.SWITCH_MAP_BACKOFF_COUNTS)` at the seek speed, wait idle.
   4. **In:** `set_speed(axis, SC.SWITCH_MAP_STEP_SPEED_COUNTS_PER_SEC)`; repeat
      `move_relative(axis, -SC.SWITCH_MAP_STEP_COUNTS)`, wait idle, read `get_position` and
      `get_switch_states`, record a `"in"` row. Stop after the row where the reverse limit reads
      tripped (`inward_stop = "reverse limit"`), or once the position is
      `SC.SWITCH_MAP_MAX_PAST_FIRST_TRIP_COUNTS` counts below the position where the first switch
      tripped (`inward_stop = "100-count cap"`). Never command an inward step while the reverse
      limit reads tripped.
   5. **Out:** repeat `move_relative(axis, +SC.SWITCH_MAP_STEP_COUNTS)`, wait idle, record an
      `"out"` row, until the home switch and the reverse limit both read clear, then
      `SC.SWITCH_MAP_CLEAR_MARGIN_COUNTS` more rows. If both are not clear within
      `SC.SWITCH_MAP_MAX_OUT_COUNTS`, return
      `(False, f"{axis}: switches did not clear within {SC.SWITCH_MAP_MAX_OUT_COUNTS} counts — check the sensor", rows)`.
   - After every row call `on_row(row)`. When a switch's state differs from the previous row,
     call `progress(switch_change_line(...))` with switch names `"home"`, `"reverse"`,
     `"forward"`. At the end call `progress(switch_map_summary(axis, find_switch_edges(rows), inward_stop))`
     and return `(True, that summary, rows)`.
   - Cancel is checked before every step and returns `(False, f"{axis}: switch map cancelled", rows)`.
     SP is restored to `SC.DEFAULT_SPEED_COUNTS_PER_SEC` on every exit.
4. **`SwitchMapWorker(QThread)`**: `__init__(self, galil, axis, parent=None)`, signals
   `progress = Signal(str)`, `row = Signal(object)`, `done = Signal(bool, str, list)`, `cancel()`;
   `run()` runs `SwitchMapRoutine` with `on_row=self.row.emit`.
5. **`src/rbl/services/switch_map_writer.py` (new)**:
   `write_switch_map_csv(axis_letter, rows, directory: Path | None = None, now: datetime | None = None) -> Path`.
   `directory=None` means `paths.HOMING_DIR`, read at call time; creates it. File
   `f"switch_map_{axis_letter}_{now:%Y%m%d_%H%M%S}.csv"`. First line
   `f"# axis={axis_letter} slit={SC.AXIS_NAMES[axis_letter]} step_counts={SC.SWITCH_MAP_STEP_COUNTS} step_speed_cps={SC.SWITCH_MAP_STEP_SPEED_COUNTS_PER_SEC}"`,
   then header `step,direction,position_counts,home,reverse,forward`, then one row per
   `SwitchMapRow` with switches as `1` (tripped) / `0`.

## Acceptance criteria

- [ ] Test `test_find_switch_edges_from_rows` in `tests/test_switch_map.py` (new file): hand-built
      rows where home and reverse trip on the in row at -3 and release on the out row at 6 give
      `SwitchEdges(-3, -3, 6, 6)`; rows where the reverse limit never reads tripped give
      `reverse_trip is None` and `reverse_release is None`.
- [ ] Test `test_switch_map_summary_line` in `tests/test_switch_map.py`:
      `switch_map_summary("A", SwitchEdges(-3, None, 6, None), "100-count cap")` is exactly
      `"A: in — home trips at -3, reverse trips at never (gap —); out — home releases at 6, reverse releases at never; inward stop: 100-count cap"`.
- [ ] Test `test_switch_map_finds_a_shared_switch` in `tests/test_switch_map_routine.py` (new
      file): `SimGalil(trip_at=-3, release_at=6, start_at=2000)`; `SwitchMapRoutine(sim, "A", progress=lines.append).run()`
      returns `(True, msg, rows)`; `find_switch_edges(rows) == SwitchEdges(-3, -3, 6, 6)`; the
      last `"in"` row has `reverse` True; `lines` contains `"A: in — home trips at -3 cts"` and
      `msg` contains `"inward stop: reverse limit"`; `sim.slits["A"].zeroed_at == []`.
- [ ] Test `test_switch_map_stops_at_the_cap_without_a_reverse_limit` in
      `tests/test_switch_map_routine.py`: `SimGalil(trip_at=-3, release_at=6, reverse_trip_at=-1000, reverse_release_at=-990)`;
      the run succeeds, the last `"in"` row is at position -103, and `msg` contains
      `"reverse trips at never"` and `"inward stop: 100-count cap"`.
- [ ] Test `test_switch_map_csv_rows` in `tests/test_switch_map_writer.py` (new file): two rows
      written with `directory=tmp_path, now=datetime(2026, 11, 18, 9, 5, 7)` for axis `"B"` produce
      `switch_map_B_20261118_090507.csv` whose lines are the `# axis=B slit=X- step_counts=1 step_speed_cps=25`
      comment, the header, and the two rows with `1`/`0` switch values.

**What tests may fake:** the controller with `SimGalil` from `tests/sim_slit.py`; `time.sleep`
(patch `galil_workers.time.sleep` as `tests/test_auto_home.py`'s `no_sleep` fixture does).
`SwitchMapRoutine`, `find_switch_edges`, `switch_map_summary` and `write_switch_map_csv` are real.

**Out of scope:** the Stepper Motors tab box, live status line and CSV writing from the tab
(140). Plots. The forward limit end of travel. Anything on the Overview.

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile
```

## Comments
