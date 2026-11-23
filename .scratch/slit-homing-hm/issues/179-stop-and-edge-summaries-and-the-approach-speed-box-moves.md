# 179: Stop and edge summary lines, and the Approach speed box moves into the edge-return box

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 177 (both edit `MotorTab._build_auto_home_box`), 178 (the reading pairs)

**Spec:** `.scratch/slit-homing-hm/spec.md` (`summary_line` in "Edge-return test"; the Approach speed box and the edge-return note in "Stepper Motors tab"; user stories 38, 40-42)
**Binding:** `docs/adr/0010-slits-homed-with-hm-as-nec-specifies.md`, `docs/adr/0001-tests-first-and-no-muted-failures.md`

**Read first:** `src/rbl/hardware/edge_return.py` (`summary_line`, `analyse_edge_returns`, `EdgeReturnReading` from ticket 178), `src/rbl/gui/motor_tab.py` (`_build_auto_home_box`, the `speeds_row` holding `spn_approach_speed` and its tooltip; `_EDGE_NOTE_STANDING`; `_build_edge_return_box`; `_on_edge_return_done`; `_update_approach_speed_enabled`), `tests/test_edge_return.py` (test_summary_line_text), `tests/test_edge_return_tab.py` (its `tab` fixture and fake worker), `tests/test_auto_home.py` (`TestMotorTabAutoHome`'s approach-speed-box tests, which must keep passing).

## What to build

1. `summary_line(summary, axis_letter, pass_speed_cps, label: str | None = None)`: with `label=None` the text is exactly today's. With a label, the head reads `"<axis> [<slit>] @ <speed> cps, <label>: n=…"`, and the rest is unchanged.
2. `MotorTab._on_edge_return_done` on success logs two lines, in this order: `summary_line(analyse_edge_returns([r.stop_counts for r in readings]), axis, speed, "stop")`, then the same over `edge_counts` with `"edge"`. These replace the single unlabelled line. The failure path and the CSV are unchanged.
3. Move the Approach speed control from the auto-home box into the edge-return box's row, after the Out (counts) box. Remove `speeds_row` from `_build_auto_home_box`. The label becomes `Edge-return approach speed (cps)`. `spn_approach_speed` keeps its name, its binding to `Beamline.home_approach_speed`, its range, its starting value and its locking in `_update_approach_speed_enabled`. Its tooltip (also on the label) becomes `Speed of the edge-return test: each cycle moves out, runs HM and steps back out to the release edge at this speed. It does not affect homing, which always runs at 900 cps.`, with 900 from `SC.HOMING_SPEED_COUNTS_PER_SEC`.
4. `_EDGE_NOTE_STANDING` becomes exactly: `Moves the axis out and runs HM at the approach speed, cycle after cycle, recording where it stopped and where the home switch releases on the way back out. Never sets zero. A spread of a few counts is switch jitter; jumps of about 8 counts are lost steps.`

## Acceptance criteria

- [ ] Test `test_summary_line_with_a_label` in `tests/test_edge_return.py`: for `analyse_edge_returns([1000, 1001, 999, 1000, 1001])` on axis A at 58 cps, `summary_line(…, "stop")` equals `A [X+] @ 58 cps, stop: n=5, spread 2 cts (3.2 µm), drift +1 cts, σ 0.75 cts, lost-step jumps: 0`. With no label it equals the existing expected text.
- [ ] Test `test_edge_return_logs_a_stop_line_and_an_edge_line` in `tests/test_edge_return_tab.py` (fake worker, CSV dir on `tmp_path`): a run that finishes with three `EdgeReturnReading`s whose stops and edges differ logs the labelled `stop` line and then the labelled `edge` line, each equal to `summary_line` over its column. No unlabelled summary line is logged.
- [ ] Test `test_approach_speed_box_sits_in_the_edge_return_box` in `tests/test_edge_return_tab.py`: the nearest `QGroupBox` ancestor of `tab.spn_approach_speed` has the title `Edge-Return Test — Homing Repeatability`. Its tooltip contains `does not affect homing`, and `tab.lbl_edge_note.text()` equals the step 4 text exactly.
- [ ] (no test: CI's full suite) No test is deleted. `TestMotorTabAutoHome`'s approach-speed-box tests (starting value, range, edits reach the Beamline, locked while homing) pass unchanged.

**What tests may fake:** the edge-return worker (fake with the real `done` signature). `summary_line`, the tab and its widgets are real.

**Out of scope:** the routine and CSV (178); the Auto-Home texts (177); the switch map box.

## Gate

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile --max-worker-restart=0 tests/test_edge_return.py tests/test_edge_return_tab.py tests/test_auto_home.py
```

Run pytest on the touched test files only (implement-ticket-parallel, section 4). CI runs the full suite on the PR.

## Comments
