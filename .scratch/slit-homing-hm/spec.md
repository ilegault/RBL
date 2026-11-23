# Spec: Slits homed with HM, as NEC specifies, and the edge-return test records the stop

Status: ready-for-agent
Date: 2026-11-23
Related: `docs/adr/0010-slits-homed-with-hm-as-nec-specifies.md` (read first; it supersedes
`docs/adr/0009-slits-homed-on-the-release-edge.md`), `docs/adr/0001-tests-first-and-no-muted-failures.md`,
`CONTEXT.md` (Slit motion: pre-home move, homing speed, home pass, stop position, release edge,
approach speed, reverse limit, at rest, lost step, edge-return test, switch map),
`.scratch/slit-homing-release-edge/spec.md` (the effort this replaces; its bench ticket 142 is retired by this one)

> Read ADR 0010 before starting. It records why homing goes back to the Galil's `HM`, why a stop
> on the reverse limit (stop code 3) is now an accepted home, and why the edge-return test keeps
> the release edge as a second column.

## Problem Statement

Homing the slits does not work on the beamline. On 2026-11-20, Auto-Home All stopped on axis A
("0/4 axes homed; failed: A; not attempted: B, C, D"), and the dialog did not say why.

The bench data the developer took that day (switch maps on all four axes at 900 cps, edge-return
runs at 50-1800 cps) shows the switches are healthy: on every axis the home switch and the
reverse limit trip on the same count, with one clean trip and one clean release. What fails is
the motion. The edge found by the release-edge pass (ADR 0009) moves the same way every cycle,
which is lost steps: about +167 counts per cycle on A at 900 cps, about -10 on B, under 1.5 on C.
Two release-edge passes on A can never agree within 4 counts, so A never homes.

The developer wants to always know where the slits are. They want the app to home the way NEC's
own software does (`SP 900`, `HM`, `BG`, `DP 0`, NEC support e-mail 5-12496), because that is the
procedure the hardware is specified for, and they suspect the one-count steps of the release-edge
pass may themselves lose steps. They cannot measure jaw position independently (no ruler, caliper
or indicator fits in the chamber or the BPM), so the only evidence available is what the app
records against the switches. Today the edge-return test records only the release edge, so it
cannot say whether steps are lost on the ordinary moves or on the one-count steps.

## Solution

Homing goes back to NEC's procedure. A home pass is: a pre-home move off the switch if the axis
is on one (`PR 2000` at the slit speed, then both switches must read clear), then `SP 900`, `HM`,
`BG`, and wait until the axis is at rest. The pass is accepted when the home switch reads tripped
and the stop code is 3 or 10. Homing runs two passes; if their stop positions agree within 4
counts, zero is defined at the second stop (`DP 0`, `DE 0`), and the speed goes back to 1800.
The homing speed is fixed at 900; nobody can change it from the screen.

Each axis panel on the Stepper Motors tab has one Home button; "Seek + Home" is gone. Auto-Home
All still homes one axis at a time and still stops at the first failure, but its failure dialog
now lists every failed axis with the reason.

The edge-return test sends the same `HM` homing sends, at the Approach speed box's speed, and
records two numbers every cycle: the stop position (what homing would zero on) and the release
edge (found by stepping out one count at a time, as today). Its CSV gains the stop column, and
the console prints a summary line for each column. Comparing the two columns on one run answers
whether steps are lost on the ordinary moves or on the one-count steps. The Approach speed box
moves into the edge-return box and only affects that test.

A new bench ticket replaces bench ticket 142: the `MG _TPx` encoder check, Home on each axis,
one Auto-Home All, and edge-return on all four axes with the stop column.

## User Stories

1. As the beamline operator, I want each slit homed with the Galil's `HM` at 900 cps, so that homing is the procedure NEC specifies for this controller.
2. As the beamline operator, I want homing to move the axis off its switch first when it is sitting on the home switch or the reverse limit, so that `HM` always starts from outside the switch, as NEC's procedure does.
3. As the beamline operator, I want the pre-home move to be one 2000-count move out at the slit speed, so that it matches NEC's `PRA=2000`.
4. As the beamline operator, I want homing to fail with a clear message if the axis is still on a switch after the pre-home move, so that a stuck or broken switch never produces a zero.
5. As the beamline operator, I want no pre-home move when the axis is already clear, so that homing does not move an axis it does not need to.
6. As the beamline operator, I want no pre-home move when the axis sits on the forward limit, so that homing does not try to drive further into the wall-side limit.
7. As the beamline operator, I want a home accepted when the axis stops with its home switch tripped and the stop code is 3, so that the reverse limit ending `HM` on these slits counts as a successful home.
8. As the beamline operator, I want a home accepted with stop code 10 as well, so that homing still works if a slit is ever rewired with the reverse limit beyond the home switch.
9. As the beamline operator, I want a home rejected when the reverse limit is tripped and the home switch is clear, with the message that the reverse limit was reached before the home switch, so that a broken home switch is caught before the jaws can collide.
10. As the beamline operator, I want a home rejected when neither switch is tripped, with the stop code in the message, so that I know the axis stopped short and why.
11. As the beamline operator, I want a home rejected when the home switch is tripped but the stop code is neither 3 nor 10, with the stop code in the message, so that an unexpected stop is never treated as a home.
12. As the beamline operator, I want every switch and stop-code reading of a home pass taken only after the axis is at rest, so that a reading is never taken while step pulses are still going out.
13. As the beamline operator, I want homing to run two `HM` passes and compare where they stopped, so that lost steps between the passes stop me from trusting a bad zero.
14. As the beamline operator, I want homing to fail without setting zero when the two stop positions differ by more than 4 counts, with both positions and the limit in the message, so that I can see how far apart they were.
15. As the beamline operator, I want zero defined at the second pass's stop position when the two agree, so that the zero comes from the most recent pass.
16. As the beamline operator, I want homing to send both `DP 0` and `DE 0` when it defines zero, so that it does exactly what NEC's example does.
17. As the beamline operator, I want the axis speed put back to 1800 cps after homing, whether it succeeded or failed, so that the next jog or move runs at the slit speed.
18. As the beamline operator, I want the progress console to show each pass's stop position, so that I can watch homing repeatability without opening a file.
19. As the beamline operator, I want the success message to show both stop positions, so that I have a record of how well they agreed.
20. As the beamline operator, I want cancelling a home to stop the axis and set no zero, so that a cancel never leaves a half-defined zero.
21. As the beamline operator, I want homing to give up with a timeout message if `HM` has not stopped after the seek timeout, so that a stalled axis does not hang the app.
22. As the beamline operator, I want one Home button per axis, so that I do not have to choose between two buttons that now do the same thing.
23. As the beamline operator, I want the Home button to turn into Cancel while it runs and lock the jog and move controls on that axis, so that I cannot command a second motion mid-home.
24. As the beamline operator, I want the axis's controls to stay locked after a failed home if the Galil link dropped, so that I am not offered controls that cannot work.
25. As the beamline operator, I want homing speed fixed at 900 cps regardless of what the Approach speed box says, so that a test speed I left in the box can never change how the slits are homed.
26. As the beamline operator, I want Auto-Home All to home A, then B, then C, then D, one axis at a time, so that only one axis moves at once.
27. As the beamline operator, I want Auto-Home All to stop at the first failed axis and leave the rest untouched, so that a broken switch on one jaw cannot lead to the opposite jaw being driven into it.
28. As the beamline operator, I want the Auto-Home All failure dialog to list each failed axis with its reason, so that I do not have to scroll the console to learn why homing stopped.
29. As the beamline operator, I want the Auto-Home All confirmation and the text under its button to describe `HM` homing at 900 cps and the 4-count agreement, so that the screen describes what the button actually does.
30. As the beamline operator, I want the edge-return test to send the same `HM` that homing sends, so that the test measures the homing I use.
31. As the beamline operator, I want each edge-return cycle to move out by the out-counts at the approach speed, then run `HM` at the approach speed, so that I can compare speeds with the same procedure.
32. As the beamline operator, I want each edge-return cycle to record the stop position, so that I can see how repeatable `HM` homing is.
33. As the beamline operator, I want each edge-return cycle to also step out one count at a time to the release edge and record it, so that I can compare the stop and the edge on the same run.
34. As the beamline operator, I want the edge-return test never to define zero, so that running it does not disturb a homed axis's zero.
35. As the beamline operator, I want the edge-return test to start with the same pre-home move as homing when the axis is on a switch, so that its first `HM` starts from outside the switch.
36. As the beamline operator, I want the edge-return test to apply the same stop check as homing and stop with a message if a cycle fails it, keeping the readings so far, so that a broken switch ends the run instead of producing junk.
37. As the beamline operator, I want the edge-return CSV to have a stop column and an edge column, each with its delta from the first reading, so that I can plot both drifts from one file.
38. As the beamline operator, I want the console to print one summary line for the stops and one for the edges, so that I can compare spread, drift and lost-step jumps at a glance.
39. As the beamline operator, I want a cancelled or failed edge-return run still to write its CSV when it has at least two readings, so that a partial run is not lost.
40. As the beamline operator, I want the Approach speed box inside the edge-return box, labelled as that test's speed, so that it is obvious it does not affect homing.
41. As the beamline operator, I want the Approach speed box to keep its 2 to 2000 cps range and 900 default, so that I can still compare slow and fast approaches in the test.
42. As the beamline operator, I want the Approach speed box locked while any homing, edge-return test or switch map runs, so that the speed cannot change mid-run.
43. As the beamline operator, I want the switch map to work exactly as it does today, so that its readings stay comparable with the ones I already have.
44. As the developer, I want a bench ticket that checks whether an encoder answers on `MG _TPx` on each axis, so that the contradiction in NEC's e-mail is settled by measurement.
45. As the developer, I want the bench ticket to record both stop positions of a Home on each axis and one Auto-Home All summary, so that I know homing works on the beamline.
46. As the developer, I want the bench ticket to run edge-return on all four axes at 900 cps with the stop column, so that I can decide whether 4 counts is the right agreement limit.
47. As the developer, I want bench ticket 142 retired with a pointer to its replacement, so that nobody works the release-edge bench checklist.
48. As an implementing agent, I want the simulated controller to model `HM` on these slits (stop on the reverse limit with stop code 3, or at the home transition with stop code 10 when the reverse limit is deeper), so that the routine can be tested without hardware.
49. As an implementing agent, I want the simulated controller to be able to lose steps on a chosen `HM` call, so that the agreement check and the edge-return stop column can be tested.

## Implementation Decisions

**Controller driver (`GalilController`)**
- New `home(axis)`: sends `HM <axis>` then `BG <axis>` (for example `HM C`, `BG C`). It does not set the speed; callers set it first. Do not name it `begin_home` (an existing test asserts that name is gone).
- New `define_encoder_zero(axis)`: sends `DE` with the axis's positional comma prefix and `0`, the same shape as `define_zero` (`DE ,,0` for C).
- `define_zero`, `jog_start`, `move_relative`, `get_stop_code` and the switch reads are unchanged.

**Configuration constants**
- New: `HOMING_SPEED_COUNTS_PER_SEC = 900`, `PRE_HOME_MOVE_COUNTS = 2000`, `HOME_STOP_AGREEMENT_COUNTS = 4`, `HOME_ACCEPTED_STOP_CODES = (3, 10)`.
- Removed: `HOME_START_BACKOFF_COUNTS`, `HOME_START_BACKOFF_TRIES`, `HOME_PASS2_BACKOFF_COUNTS`, `HOME_EDGE_AGREEMENT_COUNTS`.
- Kept and still used: `DEFAULT_SPEED_COUNTS_PER_SEC` (1800; the pre-home move and the restore after homing use it), `HOME_SEEK_TIMEOUT_S` (now the home move's timeout), `HOME_SEEK_SPEED_COUNTS_PER_SEC` (switch map only), `HOME_APPROACH_SPEED_COUNTS_PER_SEC` and its 2..2000 bounds (edge-return only), `HOME_CREEP_STEP_COUNTS`, `HOME_CREEP_MAX_COUNTS` (edge-return creep only). `STEPS_PER_MM` (629.92126) and `MM_ZERO_OFFSET` are not touched.

**Homing routine (`AxisHomeRoutine`)**
- Constructor: `(galil, axis, progress=None, cancelled=None)`. The `approach_speed` argument is removed: homing never reads the approach speed.
- `run() -> (ok, message)`, never raises; the `seek_first` argument is removed. Sequence: pass 1 = `pre_home_move()` then `home_move(HOMING_SPEED_COUNTS_PER_SEC)`; pass 2 = the same again; agreement check; on success `define_zero(axis)` then `define_encoder_zero(axis)`. The speed is set back to `DEFAULT_SPEED_COUNTS_PER_SEC` in a `finally`, on success and failure.
- `pre_home_move() -> (ok, message)` replaces `back_out_of_home()`. On the forward limit: no move, success. Home switch and reverse limit both clear: no move, success. Otherwise: `set_speed(DEFAULT_SPEED_COUNTS_PER_SEC)`, one `move_relative(PRE_HOME_MOVE_COUNTS)`, `wait_idle`, then both switches must read clear, else fail with `"<axis>: still on the home switch after the pre-home move of 2000 counts — zero not set"`.
- `home_move(speed) -> (ok, message, stop_position | None)` replaces `seek_home_limit()` and `approach()`: `set_speed(speed)`, `galil.home(axis)`, then poll `is_moving` (cancel → `stop(axis)` and fail `"<axis>: cancelled during the home move"`; past `HOME_SEEK_TIMEOUT_S` → `stop(axis)` and fail `"<axis>: timed out after 180 s in the home move"`), then `wait_at_rest`, then read the switches and `get_stop_code`. Accept only home switch tripped and stop code in `HOME_ACCEPTED_STOP_CODES`; the stop position is `get_position(axis)` read at rest. Failures: reverse limit tripped with home clear → the existing reverse-limit message; neither tripped → `"<axis>: home move stopped before reaching the home switch (stop code N)"`; home tripped with another code → `"<axis>: home move ended with stop code N — zero not set"`. If the axis already reads the home switch tripped when `home_move` is called, it does not send `HM`; that is a caller error and fails with `"<axis>: on the home switch before the home move — run the pre-home move first"`.
- Agreement: `abs(stop2 - stop1) > HOME_STOP_AGREEMENT_COUNTS` fails with `"<axis>: home stops disagree — pass 1 at S1 cts, pass 2 at S2 cts (limit 4) — zero not set"`. Progress lines: `"<axis>: pass k/2 — stopped at S cts"`. Success message: `"<axis>: homed — stops S1, S2 cts, DP=0 at pass 2"`.
- `creep_to_release(speed) -> (ok, message, edge | None)` keeps today's behaviour (one-count steps out, read at rest, fail after `HOME_CREEP_MAX_COUNTS`) but takes the speed as an argument. Only the edge-return test calls it.
- Kept unchanged: `wait_idle`, `wait_at_rest`, `_arrival`, `_at_home_limit`, `_reverse_msg`. Removed: `seek_home_limit`, `approach`, `home_passes`, `back_out_of_home`.

**Workers**
- `HomingWorker(galil, axis, parent=None)`: the `seek_first` and `approach_speed` arguments are removed.
- `AutoHomeAllWorker(galil, axes=None, parent=None, stop_on_failure=True)`: the `seek_first` and `approach_speed` arguments are removed. Behaviour otherwise unchanged: one axis at a time, `axis_started` / `axis_finished(axis, ok, msg)` / `done(ok, summary)`, stops at the first failure with the remaining axes listed as not attempted.

**Edge-return test**
- New frozen dataclass `EdgeReturnReading(stop_counts: int, edge_counts: int)` in the pure edge-return analysis module (beside `analyse_edge_returns`).
- `EdgeReturnRoutine(galil, axis, approach_speed, cycles, out_counts, progress=None, cancelled=None)` keeps its signature. `run()` returns `(ok, message, readings: list[EdgeReturnReading])`. Sequence: `pre_home_move()` once; then each cycle: `set_speed(approach_speed)`, `move_relative(out_counts)`, `wait_idle`, `home_move(approach_speed)`, `creep_to_release(approach_speed)`, append the reading. It never calls `define_zero` or `define_encoder_zero`. Progress per cycle: `"<axis>: cycle k/N — stop at S cts, edge at E cts"`. Success message unchanged: `"<axis>: N edge returns at S cps"`. A failed cycle returns the readings collected so far.
- `EdgeReturnWorker`'s `done(bool, str, list)` carries the list of `EdgeReturnReading`.
- `summary_line(summary, axis_letter, pass_speed_cps, label=None)`: with `label=None` the text is exactly today's; with a label it reads `"<axis> [<slit>] @ <speed> cps, <label>: n=…"`. On success the Stepper Motors tab logs two lines, labels `"stop"` then `"edge"`, each from `analyse_edge_returns` over that column.
- CSV header becomes `axis,slit,pass_speed_cps,cycle,stop_counts,stop_delta_from_first_counts,edge_counts,edge_delta_from_first_counts`; one row per reading; deltas are from that run's first reading of the same column. `write_edge_return_csv(axis_letter, runs, directory=None, now=None)` where `runs` is `[(pass_speed_cps, [EdgeReturnReading, …]), …]`. File name unchanged.

**Stepper Motors tab**
- Each axis panel loses `btn_seek_home`. `btn_home` is labelled `"Home <axis>"`, its tooltip describes the pre-home move, `HM` at 900 cps twice and the 4-count agreement, and `_start_homing()` takes no arguments. Every list of per-axis controls that named `btn_seek_home` drops it. Cancel and lock behaviour is unchanged.
- The Approach speed box (`spn_approach_speed`, still bound to `Beamline.home_approach_speed`, range and default unchanged) moves from the auto-home box into the edge-return box. Its tooltip says it sets the edge-return test's speed and does not affect homing. It is still locked while any homing, edge-return test or switch map runs. Homing and Auto-Home All no longer read it.
- Auto-Home All confirmation text: `"Each axis will be moved off its switch if needed, homed with HM at 900 cps twice, and zeroed at the second stop if the two stops agree within 4 counts — A, then B, then C, then D.\n\nOnly one axis moves at a time. Make sure the slits are clear to travel.\n\nStart?"`. Standing note under the button: `"Homes all four slits, one at a time, with HM at 900 cps: twice per axis, zeroed at the second stop if the two agree within 4 counts. Stops at the first axis that fails. Start it and work on another tab."`
- The Auto-Home All failure dialog body is the summary line, a blank line, then one line per failed axis: the message that axis's `axis_finished` carried (it already begins with the axis letter). The tab collects these from `axis_finished` and clears the list when a run starts.
- Edge-return standing note: `"Moves the axis out and runs HM at the approach speed, cycle after cycle, recording where it stopped and where the home switch releases on the way back out. Never sets zero. A spread of a few counts is switch jitter; jumps of about 8 counts are lost steps."`
- Module and class docstrings that describe the release-edge homing or the seek are rewritten to describe `HM` homing.

**Simulated controller (test support, `SimGalil` / `SimSlit`)**
- `SimGalil.home(axis)`: records the call `("home", (axis,))`. Raises the same `GalilError` 22 as a jog if the reverse limit is already tripped. Otherwise drives negative one count at a time until the home switch or the reverse limit trips. If the reverse limit tripped: overshoot by the existing speed rule at the slit's current speed, stop code 3. If only the home switch tripped (reverse limit deeper): stop at that count, stop code 10 (models `HM`'s second stage ending at the transition). Applies `slip_on_home` like `slip_on_jog`, keyed by the 1-based count of `home` calls on that axis.
- `SimSlit` gains `slip_on_home: dict[int, int] | None = None`. `SimGalil.define_encoder_zero(axis)` records the call and changes nothing else.
- The switches keep following the physical position; nothing else about the simulator changes.

## Testing Decisions

- A good test drives the routine, worker or tab the way the operator or the caller does, and asserts what the controller was told and what the operator sees: the commands recorded by `SimGalil` (and their order), where zero lands (`zeroed_at`), the messages, the CSV text, the dialog text. Tests do not assert private helpers' call counts unless the call order is the behaviour (for example "switches are read only after `get_step_count` shows the axis at rest").
- Seams, all existing: (1) `AxisHomeRoutine`, `HomingWorker`, `AutoHomeAllWorker` and `EdgeReturnRoutine` against `SimGalil`, run synchronously with `time.sleep` patched out, the prior art being the current `tests/test_auto_home.py` and `tests/test_edge_return_routine.py`; (2) the Stepper Motors tab through real button clicks with workers patched or faked, prior art `tests/test_auto_home.py::TestMotorTabAutoHome`, `tests/test_edge_return_tab.py`, `tests/test_switch_map_tab.py`; (3) the driver's command strings through `make_galil` and its fake socket, prior art `tests/test_galil_protocol.py` (`test_define_zero`, `test_jog_start_negative_on_axis_c`); (4) the CSV writer and the edge-return analysis as pure functions, prior art `tests/test_edge_return_writer.py` and `tests/test_edge_return.py`.
- A failure path (unexpected stop code, timeout, cancel) that `SimGalil` cannot produce is driven with a `MagicMock(spec=GalilController)`, as the current tests do.
- New tests to write (names are the implementer's): `home` sends `HM C` then `BG C`; `define_encoder_zero` sends `DE ,,0`; a home from clear sends `SP 900`, `HM`, `BG` with no `jog_start` and zeroes at pass 2's stop with both `define_zero` and `define_encoder_zero` called; a home starting on the switch makes exactly one `move_relative(2000)` at 1800 before the first `home`; a pre-home move that leaves the axis on the switch fails with the exact message and sends no `HM`; pass 2 also starts with `move_relative(2000)`; stop code 3 with home tripped is accepted; stop code 10 with home tripped is accepted (reverse limit deeper in the sim); reverse only, neither switch, and an unexpected code each fail with their exact messages and no zero; `slip_on_home={2: 8}` fails with the disagree message; cancel during the home move calls `stop` and sets no zero; the timeout fails with its message; switches and stop code are read only after the axis is at rest; the speed ends at 1800 after success and after failure; homing never sets a speed other than 900 or 1800 even with the Approach speed box at 40; the tab has no `btn_seek_home` on any panel; the Home button cancels, locks and unlocks as before; the Auto-Home All failure dialog lists each failed axis's message; edge-return records `(stop, edge)` pairs, calls `home` once per cycle at the approach speed, shows a slip in both columns, and never defines zero; the CSV has the new header and both deltas; `summary_line` with a label inserts it and without one is unchanged; the tab logs a stop line and an edge line.
- Guard tests: none are planned. If an implementer writes a new test that already passes on today's code, the ticket must say so, because the integrity gate holds that PR (`Auto-merge: no`).

**Existing tests this work deletes** (each becomes a `Deletes tests:` line on the ticket that removes it; the behaviour each still worth checking is covered by a new test listed above):
- `tests/test_auto_home.py::TestSeekHomeLimit::test_jogs_negative_until_the_home_switch_trips`
- `tests/test_auto_home.py::TestSeekHomeLimit::test_stops_on_the_reverse_limit_too`
- `tests/test_auto_home.py::TestSeekHomeLimit::test_seek_fails_on_the_reverse_limit_before_home`
- `tests/test_auto_home.py::TestSeekHomeLimit::test_seek_fails_when_it_starts_on_the_reverse_limit`
- `tests/test_auto_home.py::TestSeekHomeLimit::test_no_jog_when_already_on_the_limit`
- `tests/test_auto_home.py::TestSeekHomeLimit::test_reports_a_jog_that_stopped_short`
- `tests/test_auto_home.py::TestSeekHomeLimit::test_times_out_rather_than_jogging_forever`
- `tests/test_auto_home.py::TestSeekHomeLimit::test_cancel_stops_the_jog`
- `tests/test_auto_home.py::TestRoutineEndToEnd::test_a_failed_seek_never_reaches_hm`
- `tests/test_auto_home.py::TestRoutineEndToEnd::test_without_seek_it_is_the_old_routine`
- `tests/test_auto_home.py::TestHomingWorkerSeekFlag::test_seek_first_is_off_by_default`
- `tests/test_auto_home.py::TestHomingWorkerSeekFlag::test_seek_first_adds_the_jog`
- `tests/test_auto_home.py::TestAutoHomeAllWorker::test_defaults_to_all_four_axes_with_the_seek`
- `tests/test_auto_home.py::TestMotorTabAutoHome::test_approach_speed_reaches_the_next_auto_home`
- `tests/test_auto_home.py::TestMotorTabAutoHome::test_approach_speed_reaches_a_per_axis_home`
- `tests/test_auto_home.py::TestSeekAndHomeButton::test_each_panel_offers_the_two_step_procedure_in_one_click`
- `tests/test_auto_home.py::TestSeekAndHomeButton::test_it_becomes_cancel_while_running`
- `tests/test_auto_home.py::TestSeekAndHomeButton::test_the_axis_cannot_be_jogged_while_it_homes`
- `tests/test_auto_home.py::TestSeekAndHomeButton::test_the_controls_stay_locked_if_the_link_dropped`
- `tests/test_auto_home.py::TestSeekAndHomeButton::test_plain_home_still_skips_the_seek`
- `tests/test_auto_home.py::TestSeekAndHomeButton::test_seek_and_home_button_backs_out_first`
- `tests/test_auto_home.py::TestReleaseEdgeHoming::test_homing_zeroes_on_the_release_edge`
- `tests/test_auto_home.py::TestReleaseEdgeHoming::test_approach_speed_does_not_move_the_edge`
- `tests/test_auto_home.py::TestReleaseEdgeHoming::test_a_lost_step_between_passes_sets_no_zero`
- `tests/test_auto_home.py::TestReleaseEdgeHoming::test_a_switch_that_never_releases_sets_no_zero`
- `tests/test_auto_home.py::TestReleaseEdgeHoming::test_an_approach_stopped_by_the_limit_off_home_fails`
- `tests/test_auto_home.py::TestReleaseEdgeHoming::test_auto_home_all_zeroes_every_axis_on_its_release_edge`
- `tests/test_auto_home.py::TestReleaseEdgeHoming::test_homing_seeks_approaches_and_backs_off_at_900`
- `tests/test_auto_home.py::TestReleaseEdgeHoming::test_homing_reads_the_edge_at_rest_when_pulses_lag`
- `tests/test_auto_home.py::TestReleaseEdgeHoming::test_approach_checks_rest_before_reading_the_switches`
- `tests/test_auto_home.py::TestReleaseEdgeHoming::test_wait_at_rest_names_the_pending_pulses`

The `TestSeekAndHomeButton` behaviours (cancel, lock, stays locked if the link dropped, pre-home move first) move to a new class `TestHomeButton` on `btn_home`. The `TestReleaseEdgeHoming` behaviours that still apply (lost step between passes, reverse limit before home, at-rest reads, `wait_at_rest` naming the pending pulses) move to a new class `TestHmHoming`.

**Existing tests rewritten in place, same names** (not deletions):
- `tests/test_auto_home.py::TestRoutineEndToEnd::test_the_speeds_go_back_after_a_failure_too` — `run()` without `seek_first`; the failure comes from stops that disagree.
- `tests/test_auto_home.py::TestBackOutOfHome::test_homing_backs_out_before_anything_else` — the first motion is `move_relative(2000)`.
- `tests/test_auto_home.py::TestBackOutOfHome::test_back_out_gives_up_after_the_last_try` — one pre-home move, then the new failure message; no `home`, no zero.
- `tests/test_auto_home.py::TestBackOutOfHome::test_no_back_out_on_the_forward_limit` — calls `pre_home_move()`.
- `tests/test_auto_home.py::TestAutoHomeAllWorker::test_homes_every_axis_in_order`, `::test_one_axis_at_a_time` (ordering read from `home`, `move_relative`, `define_zero` calls), `::test_a_failure_stops_the_run_and_names_what_was_skipped` (B's `HM` stops on the reverse limit with home clear), `::test_cancel_leaves_the_remaining_axes_untouched` (`home` and `define_zero` not called) — no `seek_first` argument.
- `tests/test_auto_home.py::TestMotorTabAutoHome::test_no_homing_text_mentions_the_old_passes` — banned phrases become `("three-pass", "Together", "Pass 1", "pass speed", "Seek + Home")`; `"HM "` is no longer banned.
- Every test in `tests/test_edge_return_routine.py` — readings are `EdgeReturnReading` pairs; slips use `slip_on_home`; `test_edge_return_moves_out_at_the_approach_speed` also checks the speed set before each `home`.
- `tests/test_edge_return_tab.py::test_edge_return_runs_once_at_the_approach_speed`, `::test_edge_return_writes_one_csv_for_the_whole_run`, `::test_a_failed_speed_ends_the_edge_return_run`, `::test_edge_return_refuses_while_homing`, `::test_edge_return_refuses_without_a_galil`, `::test_edge_return_never_defines_zero` — fake worker payloads become reading pairs.
- `tests/test_edge_return_writer.py::test_edge_return_csv_rows` — new header and rows.
- `tests/test_switch_map_tab.py::test_homing_refuses_while_a_switch_map_runs` (the definition that runs, the second one in the file) — drops the `btn_seek_home` click.

## Out of Scope

- Encoders, `TP` readings and stepper position maintenance (`YS`, `QS`, `OE`). The bench ticket only checks whether `MG _TPx` changes with motion; acting on the answer is a separate effort.
- Changing the agreement limit from 4 counts, `MM_ZERO_OFFSET`, `STEPS_PER_MM`, `AC`/`DC`, `SD`, `KS`, `LC` or the slit speed.
- The switch map: its routine, speeds, CSV and box are unchanged.
- Auto-Home All continuing past a failed axis (decided against: a broken switch could let the jaws collide).
- A travel check against the forward limit, a "check zero" button, or any independent position measurement.
- `FE`, simultaneous homing of several axes, and the "Zero here" button and console `DP` (they do not send `DE`).
- Fixing the duplicate test definitions in `tests/test_switch_map_tab.py` (`test_switch_map_refuses_while_homing` and `test_homing_refuses_while_a_switch_map_runs` are each defined twice; the first copies never run). Only the second `test_homing_refuses_while_a_switch_map_runs` is touched here.
- Mechanical work on axis A (inspection, couplings, swapping cables with C).

## Further Notes

- **Bench ticket (developer, `ready-for-developer`).** Blocked by every agent ticket in this effort. Checklist: (1) in the Stepper Motors tab's Command Console or any Galil terminal, for each axis: `MO x`, `MG _TPx`, turn the drive a little by hand, `MG _TPx` again, and `SH x` afterwards; record both numbers and anything seen on the back of each motor (a cable, a label); (2) Home on each axis A-D, pasting the success line (`stops S1, S2 cts`) or the failure; (3) one Auto-Home All, pasting the summary and the dialog text if it failed; (4) edge-return on each axis, 10 cycles, out counts 2000, approach speed 900, naming the four CSVs and pasting both summary lines per axis; (5) write down whether the stop column drifts with the edge column (lost steps on the ordinary moves) or stays steady while the edge drifts (the one-count steps), and whether 4 counts is the right agreement limit given the stop spread. Each "change it" becomes a new ticket.
- **Retiring bench ticket 142.** `.scratch/slit-homing-release-edge/issues/142-bench-switch-map-and-release-edge-homing.md` gets `Status: done` and a `## Comments` entry saying it is retired, superseded by this effort's bench ticket, with the path. It was never run as written; the 2026-11-19/20 switch maps and edge-return runs listed in ADR 0010 are its partial results.
- **Before-and-after numbers to expect.** On the 2026-11-20 data, A drifts about +167 counts per cycle and B about -10 at 900 cps. With `HM` homing, A will most likely still fail the 4-count agreement: that is the check working, not a bug. C should home cleanly.
- **`HM` direction.** On 2026-11-18, `HM`'s first stage drove axis B in and was stopped by the reverse limit, so the current `CN 1,1,-1,0,0` sends `HM` the right way. If the bench ever sees `HM` drive out toward the forward limit, stop and report; do not change `CN` in a ticket.
- **Ordering hint for `/ticket-set`.** The routine and its simulator change break the tab's calls to `HomingWorker` and `AutoHomeAllWorker`, so the routine rewrite and the Home-button merge land together. The edge-return change depends on `home_move` existing. The failure-dialog change touches the same tab file as the button merge.
