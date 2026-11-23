# 178: The edge-return test runs HM and records the stop and the release edge

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 176

**Spec:** `.scratch/slit-homing-hm/spec.md` ("Edge-return test", the `creep_to_release(speed)` bullet of "Homing routine"; user stories 30-37, 39)
**Binding:** `docs/adr/0010-slits-homed-with-hm-as-nec-specifies.md`, `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/hardware/galil_workers.py` (`AxisHomeRoutine.__init__`, `pre_home_move`, `home_move`, `approach`, `creep_to_release`, `_restore_speeds`; `EdgeReturnRoutine.run`; `EdgeReturnWorker`; `SwitchMapRoutine.run`, which keeps using `back_out_of_home`), `src/rbl/hardware/edge_return.py` (`EdgeReturnSummary`, `analyse_edge_returns`), `src/rbl/services/edge_return_writer.py` (`CSV_COLUMNS`, `write_edge_return_csv`), `src/rbl/gui/motor_tab.py` (`MotorTab._on_edge_run_clicked`, `_on_edge_return_done`), `tests/test_edge_return_routine.py`, `tests/test_edge_return_tab.py` (its fake worker), `tests/test_edge_return_writer.py`, `tests/sim_slit.py` (`slip_on_home`).

## What to build

Each edge-return cycle now runs the same `HM` that homing runs, at the approach speed, and records two numbers: the stop position and the release edge. Comparing the two columns tells lost steps on the ordinary moves apart from lost steps on the one-count steps.

1. `src/rbl/hardware/edge_return.py`: new `@dataclass(frozen=True) class EdgeReturnReading` with `stop_counts: int` and `edge_counts: int`, beside `analyse_edge_returns` (which keeps taking `list[int]`).
2. `AxisHomeRoutine`: `creep_to_release(speed: int)` sets `speed` instead of `self.approach_speed`; its behaviour is otherwise unchanged. Remove the constructor's `approach_speed` argument and attribute, and remove `approach()`. `back_out_of_home` stays: `SwitchMapRoutine` uses it, and its docstring says so.
3. `EdgeReturnRoutine.run() -> (ok, message, readings: list[EdgeReturnReading])`, same constructor. `pre_home_move()` once, then each cycle: `set_speed(axis, approach_speed)`, `move_relative(axis, out_counts)`, `wait_idle(timeout=60.0)` (timeout message unchanged), `home_move(approach_speed)`, `creep_to_release(approach_speed)`, then append `EdgeReturnReading(stop, edge)` and log progress `"<axis>: cycle k/N — stop at S cts, edge at E cts"`. A failed step returns `(False, message, readings so far)`. The success message is unchanged. It never calls `define_zero` or `define_encoder_zero`. `_restore_speeds()` runs in the `finally`, as now.
4. `EdgeReturnWorker.done(bool, str, list)` carries the `EdgeReturnReading` list.
5. `edge_return_writer.py`: `CSV_COLUMNS = ["axis", "slit", "pass_speed_cps", "cycle", "stop_counts", "stop_delta_from_first_counts", "edge_counts", "edge_delta_from_first_counts"]`. `write_edge_return_csv(axis_letter, runs, directory=None, now=None)` where `runs` is `[(pass_speed_cps, [EdgeReturnReading, …]), …]`: one row per reading, each delta taken from that run's first reading of the same column. The file name is unchanged.
6. `MotorTab._on_edge_return_done`: the summary line is computed over `[r.edge_counts for r in readings]`, the same single line as today (ticket 179 adds the labelled stop and edge lines). The CSV is written from the reading list when there are at least two readings, on success and on failure, as now.

## Acceptance criteria

- [ ] Test `test_edge_return_records_stop_and_edge_each_cycle` in `tests/test_edge_return_routine.py` (`SimGalil`, `time.sleep` patched out): axis A starts clear; 3 cycles at approach speed 600, out 2000. The result is ok with 3 `EdgeReturnReading`s. Each `stop_counts` equals the commanded position read right after that cycle's `home`, and each `edge_counts` is greater than its `stop_counts`. There are exactly 3 `home` calls, each preceded by `set_speed("A", 600)`, and no `define_zero` or `define_encoder_zero` call. The progress lines read `A: cycle k/3 — stop at S cts, edge at E cts`.
- [ ] Test `test_edge_return_slip_shows_in_both_columns` in `tests/test_edge_return_routine.py`: with `slip_on_home={2: 8}`, cycles 2 and 3 have `stop_counts` and `edge_counts` each exactly 8 lower than cycle 1's.
- [ ] Test `test_edge_return_starts_off_the_switch_and_stops_on_a_bad_stop` in `tests/test_edge_return_routine.py`: starting on the home switch, the first motion call is `move_relative("A", 2000)`, preceded by `set_speed("A", 1800)`. With a `MagicMock(spec=GalilController)` whose second home move ends with the reverse limit tripped and the home switch clear, `run()` returns False, the message is the reverse-limit message, and there is 1 reading.
- [ ] Test `test_edge_return_csv_has_stop_and_edge_columns` in `tests/test_edge_return_writer.py`: two runs (speeds 600 and 900, three readings each) written to `tmp_path`. The header equals the step 5 columns. In each run, both delta columns start at 0 and equal that column minus the run's first value of the same column.
- [ ] (no test: in-place rewrites of existing tests, which CI runs) Rewrite in place, same names, without lowering any test's assert count: every test in `tests/test_edge_return_routine.py` (readings become `EdgeReturnReading`s; slips use `slip_on_home`; test_edge_return_moves_out_at_the_approach_speed also checks the speed set before each `home`); in `tests/test_edge_return_tab.py`, test_edge_return_runs_once_at_the_approach_speed, test_edge_return_writes_one_csv_for_the_whole_run, test_a_failed_speed_ends_the_edge_return_run, test_edge_return_refuses_while_homing, test_edge_return_refuses_without_a_galil and test_edge_return_never_defines_zero (fake worker payloads become reading lists); and test_edge_return_csv_rows in `tests/test_edge_return_writer.py` (new header and rows).
- [ ] (no test: CI's full suite) No test is deleted. The switch-map routine and tab tests pass unchanged.

**What tests may fake:** the controller (`SimGalil`, or `MagicMock(spec=GalilController)` for the bad stop), `time.sleep`, and the worker in the tab tests. The routine, the writer (on `tmp_path`) and the tab are real.

**Out of scope:** the labelled stop and edge summary lines and moving the Approach speed box (179); homing itself (176); the switch map; changing `analyse_edge_returns`.

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile --max-worker-restart=0 tests/test_edge_return_routine.py tests/test_edge_return_tab.py tests/test_edge_return_writer.py tests/test_auto_home.py tests/test_switch_map_routine.py
```

Run pytest on the touched test files only (implement-ticket-parallel, section 4). CI runs the full suite on the PR.

## Comments
