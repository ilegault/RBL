# Spec: Slit homing on the release edge, and the switch map

Status: ready-for-agent
Date: 2026-11-18
Related: `docs/adr/0009-slits-homed-on-the-release-edge.md` (read first),
`docs/adr/0001-tests-first-and-no-muted-failures.md`,
`CONTEXT.md` (Slit motion: home switch, reverse limit, forward limit, home pass, edge,
approach speed, zeroed, lost step, edge-return test, switch map),
`.scratch/slit-motion/spec.md` (the effort this follows: tickets 123, 124, 125, 129–131, 134)

> Read ADR 0009 before starting. It records why homing stops using the Galil's `HM`
> command and why the edge is read on the way OUT of the switch.

## Problem

Since ticket 123, Home, Seek + Home, Auto-Home All and the edge-return test all fail on
every axis with "pass 1/3 did not stop on the home switch (stop code 3) — zero not set"
(edge-return: "cycle 1/10 did not stop on the home switch (stop code 3)").

Cause, established on the bench on 2026-11-18: each slit's optical switch box (NEC
2HA062601, packaged, not to be opened) trips the home switch and the reverse limit at
the same point, 0.2 mm from beam centre. After a failed pass, axis B at rest showed
`F○ R● H●` on the Stepper Motors tab, and BL/FL read -2,147,483,648 / 2,147,483,647 on
all four axes (no soft limits). `HM`'s search reaches the home transition at the same
instant the reverse limit trips; the limit stop ends `HM` with stop code 3 before its
slow re-approach runs. No `HM` pass can end on stop code 10 on this hardware. Before
ticket 123 the zero was silently defined wherever the limit stopped the axis.

There is also no way to see, in counts, where each switch trips and releases, and the
Stepper Motors tab's status label reads "REV/HOME LIMIT active" for either switch, so
the operator cannot tell which one is tripped.

## Decisions

### Home pass (replaces every use of `HM`)

- **Approach**: drive the axis in (negative counts) until it stops. Accept the stop only
  if the home switch reads tripped; the reverse limit being tripped too is expected.
  Reverse limit tripped with the home switch clear fails with the existing
  `_reverse_msg` text ("reached the reverse limit before the home switch — check the
  home switch and its cable"). Stopped with neither tripped fails. A jog that never
  stops within `HOME_SEEK_TIMEOUT_S` fails.
- **Creep out**: repeat `PR +HOME_CREEP_STEP_COUNTS` (1), wait until idle, read the home
  switch, until it reads clear. The commanded position (`_RP`) at the first clear read is
  the pass's **edge**. If it has not released after `HOME_CREEP_MAX_COUNTS` (400) counts,
  the pass fails with
  `f"{axis}: home switch did not release within {HOME_CREEP_MAX_COUNTS} counts — check the sensor"`.
- Moving out while the reverse limit is active is allowed by the controller, so the
  creep never needs the limit to clear first.

### Homing (one axis)

1. Back out first, unchanged (ticket 124's `back_out_of_home`).
2. Seek + Home: the existing seek at `HOME_SEEK_SPEED_COUNTS_PER_SEC` puts the axis on
   the switch; pass 1 then creeps out from there. Home (no seek): pass 1's approach runs
   at the approach speed.
3. Pass 2: move out `HOME_PASS2_BACKOFF_COUNTS` (300), confirm the home switch reads
   clear (fail otherwise), approach at the approach speed, creep out.
4. If `abs(edge2 - edge1) > HOME_EDGE_AGREEMENT_COUNTS` (4), fail with a message naming
   both edges, e.g. `"A: home edges disagree — pass 1 at -12 cts, pass 2 at -3 cts (limit 4) — zero not set"`.
   No zero is defined.
5. Otherwise the axis is resting on pass 2's edge: `DP 0`, success message
   `f"{axis}: homed — edges {edge1}, {edge2} cts, DP=0 at pass 2"`.
6. SP is restored to `DEFAULT_SPEED_COUNTS_PER_SEC` on success and failure. Homing no
   longer sets HV.
7. Each pass reports through the progress callback (Command Console): the approach,
   the edge found, and the agreement result.

### Approach speed (replaces the three pass speeds)

- One approach speed shared by all four axes, held on `Beamline`, default
  `HOME_APPROACH_SPEED_COUNTS_PER_SEC = 58`, range 1..`HOME_APPROACH_SPEED_MAX_COUNTS_PER_SEC`
  (500), not saved between runs. It replaces `Beamline.home_pass_speeds` /
  `set_home_pass_speeds` and `HOME_PASS_SPEEDS_COUNTS_PER_SEC` /
  `HOME_PASS_SPEED_MAX_COUNTS_PER_SEC`.
- The Stepper Motors tab's three "Pass N (cps)" boxes become one "Approach speed (cps)"
  box. Per-axis Home, Seek + Home, Auto-Home All and the edge-return test all use it.

### Retired

- "Auto-Home All — Together": the button and its handlers in `motor_tab.py`,
  `MultiAxisHomeRoutine`, `MultiAxisHomeWorker`, and their tests in
  `tests/test_auto_home.py`. Auto-Home All (one at a time) stays and runs the new
  routine on each axis in turn, stopping on the first failure as now.
- `GalilController.begin_home`, `begin_home_multi`, `set_home_velocity`,
  `set_home_velocity_multi`, and constants `HOME_STOP_CODE`,
  `DEFAULT_HOME_VELOCITY_COUNTS_PER_SEC`, once nothing calls them. Every deleted test is
  listed by name on a **Deletes tests:** line in its ticket; tests that still describe
  live behaviour are rewritten in place under the same name.

### Edge-return test

- Each cycle: move out `out_counts`, run one home pass (approach at the approach speed,
  creep out), record the edge. Never defines zero. One run per press at the approach
  speed (no longer one run per pass speed). The analysis (`analyse_edge_returns`,
  lost-step jumps) and the CSV are unchanged in meaning: readings are edges.

### Switch map (new, Stepper Motors tab only)

- A "Switch Map" box on the Stepper Motors tab, laid out like the Edge-Return Test box:
  axis picker, Run / Cancel, a live status line. Blocked while homing, an edge-return
  test or another switch map is running, and needs a connected Galil. Not on the
  Overview.
- Path for one axis, never calling `DP`:
  1. Back out (`back_out_of_home`).
  2. Jog in at `HOME_SEEK_SPEED_COUNTS_PER_SEC` until the home switch or the reverse
     limit trips; stop.
  3. Move out `SWITCH_MAP_BACKOFF_COUNTS` (300).
  4. **In**: step `-SWITCH_MAP_STEP_COUNTS` (1) at `SWITCH_MAP_STEP_SPEED_COUNTS_PER_SEC`
     (25), wait idle, read `_RP` and all three switches; record a row. Stop stepping in
     when the reverse limit reads tripped, or when the axis is
     `SWITCH_MAP_MAX_PAST_FIRST_TRIP_COUNTS` (100) counts past the first switch that
     tripped, whichever comes first. Do not command a step into an active reverse limit.
  5. **Out**: step `+1` the same way until the home switch and the reverse limit both
     read clear, then `SWITCH_MAP_CLEAR_MARGIN_COUNTS` (50) more steps. Fail if they are
     not clear within 400 counts.
- Rows: `step, direction ("in"/"out"), position_counts, home, reverse, forward`
  (1 = tripped). Written to a CSV in `paths.HOMING_DIR`, named
  `switch_map_<axis>_<YYYYmmdd_HHMMSS>.csv`, with the axis, step size and step speed in
  a header comment.
- Live feedback: the box's status line updates every step
  (`step 212 — -3 cts — F○ R● H●`). The Command Console gets one line each time any
  switch changes state (`A: in — home trips at -3 cts`), and one summary line at the
  end:
  `A: in — home trips at -3, reverse trips at -3 (gap 0); out — home releases at 6, reverse releases at 6`.
  A switch that never changed reads `never` in its slot.
- The analysis that turns rows into trip/release positions and the summary line is a
  pure function over the rows.

### Status label

- The Stepper Motors tab's per-axis status says exactly what is tripped:
  `HOME + REV LIMIT`, `HOME`, or `REV LIMIT` (in place of "REV/HOME LIMIT active").
  Forward limit display unchanged.

### Constants (`src/rbl/config/hardware_config.py`)

`HOME_APPROACH_SPEED_COUNTS_PER_SEC = 58`, `HOME_APPROACH_SPEED_MAX_COUNTS_PER_SEC = 500`,
`HOME_CREEP_STEP_COUNTS = 1`, `HOME_CREEP_MAX_COUNTS = 400`,
`HOME_PASS2_BACKOFF_COUNTS = 300`, `HOME_EDGE_AGREEMENT_COUNTS = 4`,
`SWITCH_MAP_STEP_COUNTS = 1`, `SWITCH_MAP_STEP_SPEED_COUNTS_PER_SEC = 25`,
`SWITCH_MAP_BACKOFF_COUNTS = 300`, `SWITCH_MAP_MAX_PAST_FIRST_TRIP_COUNTS = 100`,
`SWITCH_MAP_CLEAR_MARGIN_COUNTS = 50`. Each with a comment saying why its value is what
it is (ADR 0009 for the homing ones).

### Documentation

- `galil_workers.py`'s module docstring is rewritten to describe the release-edge pass
  and point at ADR 0009; the "PASS IS ACCEPTED ONLY ON STOP CODE 10" section and the
  `HM` two-stage explanation go.
- The Stepper Motors tab's help text for Home, Seek + Home and Auto-Home All describes
  the two-pass release-edge homing and the agreement check.

## Testability requirements (not preferences)

- The routines stay plain classes driven by a `GalilController`-shaped object, so they
  run without Qt or hardware.
- Their tests drive a **simulated axis**: a small fake controller in the tests that
  holds a position, moves it on `move_relative` / `jog_start`, stops a negative jog at
  the reverse limit, and reports the home and reverse switches from position against
  configurable trip and release points (hysteresis included). Scripted return-value
  lists alone are not enough, because the behaviour under test is "where does the edge
  land given where the switch is", and only a simulated position answers that. The
  simulated axis can also inject a lost step (position jumps by 8 counts) to exercise the
  agreement check.
- The switch-map analysis and the edge agreement check are pure functions, tested with
  plain data.

## Ticket shape (for the ticket set)

1. **Switch map** (routine, analysis, CSV writer, Stepper tab box). Independent.
2. **Status label**. Independent.
3. **Release-edge home pass and homing** (`AxisHomeRoutine`, constants, simulated-axis
   test fake, docstring). Independent.
4. **Approach speed and retiring Together** (`Beamline`, Stepper tab box, Auto-Home
   All, help text, removals). Blocked by 3.
5. **Edge-return on the release edge**. Blocked by 3.
6. **Bench verification** — `ready-for-developer`, blocked by 1–5: switch map on all
   four axes (attach CSVs, paste summary lines), Seek + Home and Auto-Home All on all
   four, edge-return on each axis (10 cycles), record the trip-to-release hysteresis,
   and note whether `MM_ZERO_OFFSET` needs a follow-up. Absorbs ticket 134's edge-return
   item.

## Not in this set

- Opening or rewiring the switch box.
- Changing CN, MT, motion directions, or the soft limits BL/FL.
- Recalibrating `MM_ZERO_OFFSET` (a follow-up, from the bench ticket's numbers).
- Encoders and lost-step detection (ticket 134's other items).
- Saving the approach speed between runs.
- Anything on the Overview tab.
