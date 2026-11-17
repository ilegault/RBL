# Slit motion: slit speed, trustworthy homing, zeroed, and the edge-return test

Settled in a planning session on 2026-11-17 (grilling, then a ticket set). Glossary terms
used here are defined in `CONTEXT.md` under **Slit motion**: slit speed, home switch,
home pass, zeroed, lost step, edge-return test.

## Problems this fixes

1. **Speed changes did not reach moves.** The Galil keeps ONE speed per axis: the
   manual's operand summary says `_SP` "returns the jog speed", and HM's first stage
   runs "at the slew speed specified by the JG or SP commands". The Stepper Motors
   tab only sent its Speed box on Jog (`JG`). A Move (`PA` + `BG`) never sent a speed,
   so it ran at whatever the last jog, connect (`SP 1000`) or homing restore left.
   Moves from the Overview and the Raster Planner (`Beamline.move_slit`) had the same
   problem: a slow jog on one tab made the next Overview move crawl.
2. **Homing could set zero in the wrong place without saying so.** No pass checked
   *why* the axis stopped. A pass that ran into the reverse limit also ends with the
   axis stopped, and the routine then defined zero there. The seek also accepted the
   reverse limit as "home reached".
3. **An axis turned off sitting on its home switch** gave homing a confusing start.
4. **"Zeroed" could only ever turn on.** It lived on each Stepper Motors panel, was set
   by Home and Zero, and was never cleared: not by reconnecting (after a Galil power
   cycle every axis reads 0 counts and still looked homed), not by an emergency stop.
   A `DP` typed in the console did not set it.
5. **The Raster Planner moved unzeroed slits without asking**, unlike the Overview.
6. **Nothing stopped a target inside the homed position.** The Overview accepted
   0.0–0.2 mm, which is up to 126 counts past home.
7. **The three home pass speeds were fixed in code**, and the operator suspects the
   speed affects where the zero lands.
8. **Nothing measured homing repeatability**, so "the home switch seems to be somewhere
   else when I come back" could not be told apart from lost steps.
9. **One poll error disconnected the Galil** and cancelled any homing run.
10. **Dead wiring**: `MotorTab.slit_state` and `RasterPlannerTab.slit_targets_ready`
    are emitted and received by nothing; the first converts counts to mm a second time.

## Decisions

- **Every motion command carries its own speed.** `SP <slit speed>` is sent right
  before every `PA`; a jog sends `JG ±<slit speed>`. No hidden controller speed state
  is relied on.
- **One slit speed per axis, owned by `Beamline`**, default
  `DEFAULT_SPEED_COUNTS_PER_SEC`. Jog and Move on the Stepper Motors tab, and moves from
  the Overview and Raster Planner, all use it. The Stepper Motors tab's Speed box edits
  it. A change takes effect on the next command. Not saved between runs.
- **A home pass succeeds only if the Galil's stop code for that axis is 10** ("stopped
  after homing (HM) or find index (FI)", DMC-40x0/41x3 command reference, SC). Anything
  else fails the run and no zero is defined. The seek succeeds only on the home switch;
  reaching the reverse limit first is a failure. A wrong zero is worse than a failed home.
- **Every homing run starts by backing out**: unless the forward limit is active, move
  +`HOME_START_BACKOFF_COUNTS` (1000) at the seek speed, up to
  `HOME_START_BACKOFF_TRIES` (3) times, until neither the home switch nor the reverse
  limit is active; otherwise fail. Directions are unchanged everywhere: positive counts
  are away from home on all four axes, as today.
- **The three home pass speeds are adjustable** on the Stepper Motors tab, shared by all
  four axes, held on `Beamline`, defaults `HOME_PASS_SPEEDS_COUNTS_PER_SEC = (225, 112, 58)`,
  not saved. Each speed is still applied to both of HM's stages (SP and HV), as today.
- **Zeroed is per axis and owned by `Beamline`.** On: a successful home of that axis,
  the Zero button, a `DP` typed in the console. Off (every axis): a successful Galil
  connect, an emergency stop. Not cleared by limit trips (the controller decelerates and
  keeps counting) or by Motor Off.
- **Floor**: on a zeroed axis no target below 0 counts (the homed position, 0.2 mm) is
  accepted from any screen, the Overview target box's minimum becomes 0.2 mm, and Jog −
  becomes a move to 0 counts. No floor on an axis that is not zeroed, and homing itself
  is never subject to it.
- **Edge-return test**: from the Stepper Motors tab, for one axis, at each of the three
  pass speeds: N cycles (default 10) of move out +D counts (default 2000), one home pass
  back to the edge at that speed, read `_RP` there. Never defines zero. A cycle-to-cycle
  change is a **lost-step jump** when it is within 2 counts of a non-zero multiple of 8
  counts (4 full steps at half-stepping). Results go to the Command Console and a CSV in
  `paths.HOMING_DIR` (`RBL_log/data/homing/`).
- **Poll**: the Galil is disconnected only after `GALIL_POLL_MAX_CONSECUTIVE_ERRORS` (3)
  poll failures in a row.

## Not in this set

- Stepper Position Maintenance (encoder-based lost-step detection). Only if the bench
  ticket finds encoders wired to the Galil, and then as its own set.
- Saving slit speed or pass speeds between runs.
- Changing any motion direction, CN, MT, or the soft limits BL/FL.
- Splitting SP and HV within a pass (fast search, slow re-approach). Revisit after the
  edge-return data.
- Hardware fixes for lost steps (current, acceleration, mechanics).
