# Spec: Measured amplifier loads with history, a current-vs-frequency planner, and a spike recorder that observes long runs

Status: ready-for-agent
Date: 2026-10-06
Related: `docs/adr/0005-drift-pass-on-plates-guarded-by-protections-not-a-clock.md` (new,
binding), `docs/adr/0006-amplifier-faults-are-observed-not-stopped-during-a-session.md`
(new, binding), `docs/adr/0001-tests-first-and-no-muted-failures.md` (binding on all test
work), `CONTEXT.md` sections "Amplifier limits and load" (new) and "Known collisions"
("Trip" means two things), `docs/AMP_ENVELOPE_AND_HV_SAFETY_PLAN.md` (physics reference;
its section 1.4 capacitance figure is stale - see Part A).

> ADR 0001 is binding: a failing test is fixed or escalated, never muted.

## Problem Statement

The operator is about to run the four EEL5000 amplifiers for irradiations of 12 hours or
more, and has to explain to a PI exactly what each amplifier drives and how close to its
limits it runs. The application cannot currently support that explanation:

**The capacitance it plans from is not trustworthy.** A plate position with no stored
measurement silently uses `CAL_LOAD_CAP_PF` (1500 pF), while the physics plan says 1200 pF
and the last four-channel measurements read 1528-1650 pF. The store keeps one record per
(plate position, load condition) and overwrites it, so there is no history, no age, and
no way to tell which physical amplifier a number belongs to. The operator plans to swap
the X and Y amplifiers; after a swap, "X+" would silently mean a different amplifier.

**The load cannot be broken down.** Only two load conditions exist (DISCONNECTED,
ON_PLATES), so the application cannot say how much of the ~1.6 nF is the amplifier, the
cable, or the plates - the question a PI will ask first, given geometry predicts ~125 pF.

**Mode C measures one voltage only.** It cannot show that C is independent of voltage
(the proof the load is a linear capacitor) or how the edge spike grows with step size.

**The Raster Planner plots the wrong quantity.** Its envelope chart plots kV against
frequency with a single 20 mA "current wall". The operator reasons in current, and the
chart says nothing about the 100 mA / 4 ms burst rating that spikes live under.

**Nothing watches the amplifiers during an irradiation.** The regulation detector exists
(`RegulationMonitor`, `RegulationResponder`) but is wired to nothing outside calibration,
and its response turns the channel off and opens a modal dialog - the opposite of what
an irradiation needs (ADR 0006). Spikes are not recorded at all.

**The 12-hour test that matches real operation is refused.** A drift pass on the plates is
capped at 2 h (ADR 0005 removes the cap).

## Solution

1. Every characterization run writes a **characterization result** that is kept forever,
   tagged with the **amplifier** (serial number), **plate position**, **load condition**,
   method and time. The application plans from the newest result for each combination and
   always shows its age. With no result it says "not measured" and gives no verdict - it
   never substitutes a number.
2. The operator records **amplifier swaps** and **hardware changes** with a date; results
   older than the latest hardware change are shown as predating it.
3. A third load condition, **cable only**, lets the operator measure amplifier alone, then
   + cable, then + feedthrough and plates.
4. Mode C becomes a **voltage ladder** (0.5 to 5 kV) with four abort rules, reporting C and
   the edge spike at every rung. A new **clamp test** finds the current at which an
   amplifier stops following its input.
5. The Raster Planner's envelope plot is replaced by **steady current vs. frequency**, one
   line per plate position from its own measured C, with the continuous (20 mA) and burst
   (100 mA) ratings as labelled reference levels and a green/amber/red verdict.
6. During a session and during a drift pass, a **spike recorder** compares every sample to
   the **reference current** for the **operating point**, logs every **current spike**,
   saves the raw waveform around it, and plots spikes on a log-log current-vs-duration
   chart. The regulation detector runs alongside it in an observe-only mode. Neither ever
   turns an output off or opens a blocking dialog during a session or drift pass.
7. A drift pass on the plates has no time cap, but runs only while the HV interlock and
   the spike recorder are running, and drives the experiment's own waveform.

## User Stories

### Amplifier identity, swaps and history

1. As the operator, I want to enter each amplifier's serial number once, so that every
   measurement can be tied to the physical unit that produced it.
2. As the operator, I want to record which amplifier drives which plate position, with a
   date, so that measurements stay attached to the right unit when I move amplifiers.
3. As the operator, I want a "Record amplifier swap" action that asks for the new
   assignment, the date (defaulting to now) and an optional note, so that a swap is a
   recorded event rather than something I have to remember.
4. As the operator, I want to record a "hardware change" (for example a new cable or
   feedthrough) with a date and a note, so that the application knows older results may no
   longer describe the load.
5. As the operator, I want results measured before the latest hardware change shown in
   amber with "measured before the hardware change on <date>", so that I never plan from a
   number that may be out of date without being told.
6. As the operator, I want every characterization result kept rather than overwritten, so
   that I can see how each load changes over weeks and months.
7. As the operator, I want each result displayed with its age ("1612 pF, Mode C, on
   plates, 32 days ago"), so that I know how fresh every number is.
8. As the operator, I want results grouped by axis (X+/X- together, Y+/Y- together), so
   that I can compare the two plates of an axis at a glance.
9. As the operator, I want a plate position with no result to say "not measured", so that
   no guessed capacitance ever appears beside measured ones.
10. As the operator, I want the old single-record store retired rather than imported, so
    that today's measurements start from ground zero with no unverified numbers.
11. As the operator, after swapping the X and Y amplifiers, I want results from before the
    swap to stay attached to the amplifier that produced them, so that I can tell whether
    a bad channel follows the amplifier or stays with the plate.
12. As the operator, I want the assignment history saved across restarts, so that it is a
    permanent record of the beamline's configuration.

### Load conditions and decomposition

13. As the operator, I want a "cable only" load condition (HV cable attached, far end
    open), so that I can measure the cable's contribution separately.
14. As the operator, I want the Load Characterization tab to show, for each plate position,
    the newest result for each of the three load conditions side by side with the
    differences between them, so that I can see how much capacitance the amplifier, the
    cable and the feedthrough-plus-plates each contribute.
15. As the operator, I want the load condition chosen explicitly before every run and
    recorded on every result, so that a disconnected measurement is never mistaken for an
    on-plates one.

### Mode C voltage ladder

16. As the operator, I want Mode C to step through 0.5, 1, 2, 3, 4 and 5 kV, so that one run
    shows whether C stays constant with voltage.
17. As the operator, I want C (mean and spread over edges) reported at every rung, so that
    a change with voltage, which would mean discharge or corona, is visible.
18. As the operator, I want each rung's edge spike reported (measured peak, duration,
    charge), so that I can see how the step spike grows with step size.
19. As the operator, I want an edge peak shorter than about 100 us labelled as a lower
    bound, so that I never present a smoothed reading as the true peak.
20. As the operator, I want the ladder to abort if the hard trip fires, so that a dead
    short stops immediately.
21. As the operator, I want the ladder to abort if C at a rung differs from the first rung
    by more than 10 %, so that an incipient discharge stops the run.
22. As the operator, I want the ladder to abort if the leakage between edges exceeds 50 uA,
    so that a developing leak path stops the run.
23. As the operator, I want the ladder to abort if the measured voltage swing at a rung is
    less than 90 % of the commanded swing, so that a run where the amplifier stopped
    following its input never produces a capacitance.
24. As the operator, I want each rung to start only if the HV interlock permits that
    voltage at the current pressure, so that the ladder never commands what the vacuum
    cannot hold.
25. As the operator, I want an aborted ladder to say which rule stopped it and at which
    rung, and to keep the rungs measured before the abort in its result file, so that an
    abort is itself a finding.

### Clamp test

26. As the operator, I want a clamp test that drives a triangle wave at a fixed amplitude
    and raises the frequency step by step, so that I can find the current at which an
    amplifier stops following its input.
27. As the operator, I want the clamp test to stop at the first step where the measured
    voltage falls below 95 % of the commanded voltage, and report the steady current at
    that step as the clamp current, so that the real limit is measured rather than read
    from the manual.
28. As the operator, I want the clamp current stored as a characterization result for that
    amplifier, so that it appears in the history alongside capacitance.

### Current-vs-frequency planner chart

29. As the operator, I want the Raster Planner's envelope chart to show predicted steady
    current (mA) against frequency (Hz) on log-log axes, so that I reason about the
    quantity that is actually limited.
30. As the operator, I want one line per plate position, drawn from that position's own
    newest measured C at the planned amplitude of its axis, so that each plate's real load
    is shown.
31. As the operator, I want a plate position with no result shown in the legend as "not
    measured" with no line, so that the chart never draws a guess.
32. As the operator, I want each axis's planned operating point marked on its plates'
    lines, so that I can see where I am.
33. As the operator, I want a labelled horizontal line at 20 mA ("continuous rating"), so
    that I see the level a raster must stay under.
34. As the operator, I want a labelled horizontal line at 100 mA ("burst only: 4 ms or
    less, then 100 ms at 10 mA or less; never an operating point"), so that the spike
    headroom is visible without being mistaken for an operating limit.
35. As the operator, I want each plate's predicted steady current shown green below 10 mA,
    amber from 10 to 16 mA and red above 16 mA, so that I keep a margin under the
    continuous rating.
36. As the operator, I want the kV headroom kept as a number in the readout, so that I
    still see how much amplitude remains.
37. As the operator, I want the prediction to use the shape constant of the commanded
    waveform (4 for a triangle), so that the chart matches the drive.

### Spike recorder

38. As the operator, I want spikes recorded automatically whenever a session is recording
    or a drift pass is running, for every amplifier whose output is on, so that I never
    have to remember to start it.
39. As the operator, I want the reference current captured during the first minute at each
    operating point, so that spikes are judged against what that drive normally draws.
40. As the operator, I want the reference captured again whenever the frequency, amplitude
    or shape changes, so that a new drive never inherits the old drive's reference.
41. As the operator, I want the noise measured during the same minute, so that the spike
    threshold accounts for how noisy the monitor is.
42. As the operator, I want the spike threshold to be twice the reference, raised only as
    far as needed to sit five noise widths above it, so that small rasters with a 2 mA
    baseline still catch their 6 mA spikes when the monitor is quiet, and noise never
    floods the log when it is not.
43. As the operator, I want each spike recorded with plate position, amplifier, start
    time, duration, peak, charge above the reference, and the time since the previous
    spike on that plate, so that every event is fully described.
44. As the operator, I want spike peaks from spikes shorter than about 100 us marked as
    lower bounds, and the sample interval recorded with every spike file, so that the
    limits of the measurement travel with the data.
45. As the operator, I want 2 s of both monitors before and after every spike saved, so
    that I can look at the actual trace of any event later.
46. As the operator, I want 10 s of both monitors before any "amplifier off" event saved,
    so that I can study what leads up to an amplifier shutting itself down.
47. As the operator, I want spikes saved in the session folder (one summary file and one
    waveform file per event), so that each irradiation carries its own evidence.
48. As the operator, I want a non-blocking warning on the Overview tab when a spike exceeds
    20 mA, so that I know the burst headroom is being used.
49. As the operator, I want a non-blocking warning when two spikes on one plate are less
    than 100 ms apart, so that I know the manual's recovery period may have been violated.
50. As the operator, I want a non-blocking warning when the reference current differs from
    the prediction from the newest measured C by more than 10 %, so that I know the load
    has changed since it was characterized.
51. As the operator, I want each warning to stay until I dismiss it, so that an event
    during an unattended run is still visible when I return.
52. As the operator, I want a running spike count per plate visible during a run, so that I
    see at a glance whether anything is happening.
53. As the operator, I want a log-log chart of spike peak current against spike duration,
    with the continuous rating, the burst rating and the 4 ms burst limit drawn as zones,
    and spikes coloured by plate (hollow where the peak is a lower bound), so that I can see
    every event relative to the amplifier's ratings.
54. As the operator, I want that chart to show the current session live and to open a past
    session's spike file, so that I can compare runs.
55. As the operator, I want the spike recorder never to turn an output off, change a
    setpoint, or open a blocking dialog, so that a tool still under development cannot end
    an irradiation (ADR 0006).

### Regulation detector, observe-only

56. As the operator, I want the regulation detector to run on every driven channel during
    a session and a drift pass, so that a "current limited" or "amplifier off" event is
    never missed.
57. As the operator, I want a confirmed regulation fault logged to the trip history with
    the full operating conditions, so that over months the history maps where the real
    limits are.
58. As the operator, I want a confirmed regulation fault shown as a non-blocking warning
    that stays until dismissed, so that I learn of it without the run stopping.
59. As the operator, I want the regulation detector never to turn an output off during a
    session or drift pass, so that the amplifier's own LIMIT and TRIP remain the only
    protection (ADR 0006).
60. As the operator, I want calibration and characterization runs to keep their existing
    stopping behaviour, so that no reading taken while an amplifier is limiting is ever
    recorded as valid.

### Drift pass

61. As the operator, I want to run a drift pass on the plates for as long as I choose, so
    that I can test the exact 12-hour condition of an irradiation (ADR 0005).
62. As the operator, I want a drift pass on the plates to start only when the HV interlock
    reports a known pressure that permits the commanded voltage and the spike recorder is
    running, so that a long run is never unwatched.
63. As the operator, I want the drift pass to end, stating which protection stopped, if the
    HV interlock or the spike recorder stops during the run, so that I know why it ended.
64. As the operator, I want to enter the experiment's shape, frequency and amplitude for
    the drift pass and have it drive exactly that, so that the test is the real condition.
65. As the operator, I want a drift pass to observe and warn on spikes and regulation
    faults but not abort on them, so that it behaves like the irradiation it stands for.

### No stale numbers

66. As the operator, I want every place that used the global fallback capacitance to use
    the newest measured result for that plate position instead, so that one consistent,
    measured number is used everywhere.
67. As the operator, I want the only remaining assumed capacitance to be a clearly named
    safety assumption (3000 pF) used solely to size the first amplitude of a
    characterization run, or a ramp, on a plate with no result, so that an unmeasured load
    is treated as large rather than small.
68. As a future reader of the physics plan, I want its section 1.4 capacitance figure
    marked as superseded by measured results, so that the document stops contradicting the
    application.

## Implementation Decisions

### Part A - Amplifier identity, characterization results, history

- **Amplifier assignment history** is a new append-only store in the config directory
  (alongside the existing stores in `rbl.config.paths`). Each record: date, mapping of
  plate position to amplifier serial for all four positions, kind (`initial`, `swap`),
  note. A **hardware change** record: date, kind `hardware_change`, note. The newest
  assignment record is the assignment in force. Records are never edited or deleted.
- **Characterization results** are written one file per run into a new characterization
  folder under the data directory. Each file holds: amplifier serial, plate position, load
  condition, method (`impedance_sweep`, `charge_integral_ladder`, `clamp_test`), timestamp,
  result values (C pF and G uS, or clamp current mA), the per-point or per-rung data, abort
  rule and rung if aborted, and the assignment in force. Files are never overwritten;
  names are unique per run.
- A **pure history module** reads the folder and the assignment history and answers:
  newest result per (plate position, load condition) for the amplifier currently at that
  position, its age, and whether it predates the latest hardware change. It also answers
  the same per amplifier regardless of position. It takes "now" as an argument and never
  reads a clock, so ages are tested by passing times.
- The planner and every consumer of capacitance ask the history module for the newest
  **on plates** result for the amplifier currently assigned to that plate position. A
  cable-only or disconnected result is never used for planning.
- The existing single-record store (`load_calibration_store`) is retired: no longer read
  or written by any planning or characterization path. Its file on disk is left alone.
  Its tests are rewritten in place to the new history module where they assert behaviour
  that still exists.
- `CAL_LOAD_CAP_PF` is removed from every planning, display and interlock path. A new,
  explicitly named safety assumption of 3000 pF exists only to size the first amplitude of
  a characterization run or a ramp on a plate position with no result; its name and
  comment say it is an assumption, not a measurement. Every module that currently imports
  `CAL_LOAD_CAP_PF` or `capacitance_pf_for` is changed accordingly (config, hardware and
  service modules and the two tabs).
- `LoadCondition` gains `CABLE_ONLY`. All three values are offered on the Load
  Characterization tab; the existing pre-run checklist wording is extended to describe it.
- The Load Characterization tab gains an **Amplifiers** panel: the four plate positions
  with the assigned serial, a "Record amplifier swap" button (dialog: new mapping, date,
  note), and a "Record hardware change" button. The first time it is used with no
  assignment on record, the dialog records an `initial` assignment. Dialogs sit behind
  replaceable methods so tests drive them without a modal.
- The four-channel comparison table is replaced by a per-plate view grouped by axis:
  newest result per load condition, method, age, the differences between conditions, and
  amber styling for results predating the latest hardware change. "Not measured" where
  there is no result.
- The physics plan's section 1.4 gets a superseded note pointing to measured results.

### Part B - Mode C voltage ladder

- Mode C's single 1 kV step becomes a ladder of rungs at 0.5, 1, 2, 3, 4, 5 kV, each a
  10 Hz square wave collected for the existing stream window, using the existing
  edge-finding and charge-integral arithmetic per rung.
- Per rung: C mean, C spread, edge count, measured voltage swing, edge peak current,
  edge duration (time the edge current stays above half its peak), edge charge,
  inter-edge leakage (mean current between edges), and a lower-bound flag when the edge
  duration is under 100 us.
- Abort rules, checked after each rung, any one ends the ladder: (1) the existing hard
  trip on the driven channel; (2) rung C differs from the first rung's C by more than
  10 %; (3) inter-edge leakage above the existing Mode B leakage threshold (50 uA);
  (4) measured voltage swing below 90 % of the commanded swing. Before each rung, the HV
  interlock must permit that rung's voltage at the current pressure, or the ladder ends.
- On completion the result C is the mean of rung C values. An aborted ladder writes a
  result file with its completed rungs, the rule and the rung, and no headline C.
- Mode A is unchanged in what it measures; it writes its result through Part A.

### Part C - Clamp test

- A new characterization method on the Load Characterization tab: triangle at a fixed
  amplitude (default 1 kV), frequency stepped up a fixed ladder until the regulation ratio
  (measured over commanded voltage, lock-in fundamental) falls below 0.95 or the
  frequency ladder ends. The steady current at that step is the clamp current.
- It is a characterization run: the existing hard trip applies. It writes a result
  through Part A with method `clamp_test`.

### Part D - Current-vs-frequency chart

- A pure chart model (beside the existing `raster_plan.envelope_status`) takes, per plate
  position, the newest measured C or "not measured", the planned amplitude and frequency
  of its axis, and the shape constant, and returns: frequency grid, predicted current
  series per measured plate, the operating point per plate, the two reference levels, and
  a verdict per plate: `green` (< 10 mA), `amber` (10 to 16 mA), `red` (> 16 mA), or
  `not_measured`.
- The Raster Planner's envelope plot is replaced by this chart (log-log, current mA vs
  frequency Hz). The kV headroom number remains in the readout. The continuous (20 mA)
  and burst (100 mA) levels are labelled as stated in user stories 33-34.

### Part E - Spike recorder

- A pure **spike detector** takes raw current-monitor samples for one plate (in mA), the
  sample interval, the reference current and the noise width, and returns spikes. It holds
  only the state needed to join a spike that spans two windows. Spike threshold =
  max(2 x reference, reference + 5 x noise). A spike is a contiguous run of samples whose
  magnitude exceeds the threshold. Each spike: start index/time, duration, peak, charge
  above the reference, lower-bound flag (duration < 100 us).
- **Reference capture** is a pure function over one minute of samples: reference current =
  median of |I|; noise = 1.4826 x median absolute deviation of |I| from that median. No
  spikes are recorded for a plate until its reference exists.
- A **spike recorder service** subscribes to `Beamline.raw_window_ready` while a session is
  recording (`SessionRecorder.session_started` / `session_stopped`) or a drift pass is
  running, for every plate whose output is on per `FuncGenSetpoints`. It detects operating
  point changes from `FuncGenSetpoints.changed` and recaptures the reference. It keeps a
  rolling 10 s buffer of both monitors per driven plate.
- On each spike it appends a row to `spikes.csv` in the session folder (or the drift
  pass's output folder): plate position, amplifier serial, time, duration, peak,
  lower-bound flag, charge, gap to previous spike on that plate, sample interval,
  reference, threshold. It writes 2 s before and after into one waveform file per event
  under a `spikes/` subfolder (overlapping windows merge into one file). It also writes an
  entry to the session's existing event log.
- Warnings (non-blocking, Overview tab, persistent until dismissed): spike peak above
  20 mA; two spikes on one plate less than 100 ms apart; reference differs from the
  prediction from the newest measured C by more than 10 %; confirmed regulation fault
  (Part F). A spike count per plate is shown during a run.
- The spike chart (log-log, peak current vs duration, zones for 20 mA, 100 mA and the 4 ms
  burst limit; colour by plate; hollow for lower bounds) lives on the Load
  Characterization tab, showing the active run live and able to open a past `spikes.csv`.
- The recorder never commands hardware.

### Part F - Regulation detector, observe-only

- `RegulationMonitor` is wired to the live stream during sessions and drift passes. The
  commanded voltage per plate comes from `FuncGenSetpoints` (generator amplitude x the
  amplifier gain); the measured voltage is the lock-in fundamental for AC (existing
  `ac_metrics`) and the window mean for DC; the current limit passed is the continuous
  rating.
- `RegulationResponder` gains an observe-only mode used for sessions and drift passes: it
  appends to the trip history with operating conditions, triggers the spike recorder's
  10 s pre-event save on "amplifier off", and raises the non-blocking warning. In this mode
  it never calls `output_off` and never opens a dialog. Its existing stopping mode is
  unchanged for calibration use.

### Part G - Drift pass

- `CalibrationRunner.start_drift` drops the plate-run time cap (`DRIFT_MAX_ATTENDED_H` no
  longer applies to ON_PLATES). For an ON_PLATES pass it requires, at start and for the
  whole run, a permitted HV interlock state for the commanded voltage and a running spike
  recorder; if either stops, the pass ends with a message naming which. The disconnected
  12 h limit is unchanged.
- A drift pass on the plates does not abort on the soft or hard trip (ADR 0006); spikes
  and regulation faults are recorded and warned as in a session.
- The drift pass takes the experiment's shape, frequency and amplitude per axis and
  drives exactly that.

## Testing Decisions

- ADR 0001 is binding: tests are written first, a failing test is fixed or escalated,
  never muted. Tests assert behaviour visible from outside a module, never private state.
- **Every new store and folder is redirected to a temp path by an autouse fixture in
  `tests/conftest.py`** - the assignment history, the characterization folder, and the
  spike output. This is a requirement, not a preference: the suite once wrote a fabricated
  capacitance into the operator's real store, and the planner then labelled it
  "measured".
- Seams (agreed with the operator):
  1. History module: write result files and assignment records into a temp folder, then
     query newest-per-combination, age (by passing "now"), predates-hardware-change, and
     the before/after-swap attribution.
  2. Spike detector and reference capture: synthetic sample arrays with known spikes,
     known noise, spikes split across two windows, a 2 mA baseline with 6 mA spikes and a
     quiet monitor (threshold must be 4 mA), and a noisy monitor (threshold must rise to
     reference + 5 x noise).
  3. Current-vs-frequency chart model: given C values and amplitudes, the series, the
     operating points, `not_measured` for a missing plate, and the green/amber/red
     boundaries at exactly 10 and 16 mA.
  4. `LoadCharacterizer` fed synthetic stream windows (prior art:
     `tests/test_load_characterizer.py`): a clean ladder; each of the four abort rules
     firing at a chosen rung; the interlock refusing a rung; clamp test stopping at the
     right step.
  5. `CalibrationRunner.start_drift` (prior art: `tests/test_calibration_runner.py`): a
     long ON_PLATES pass accepted with protections running, refused without them, ended
     when one stops; no abort on a synthetic over-current in an ON_PLATES pass.
  6. `RegulationResponder` with a fake amplifier drive (prior art:
     `tests/test_regulation_response.py`): in observe-only mode `output_off` is never
     called and no dialog is created, while the trip history still gains the record.
- Prior art for pure-math tests: `tests/test_load_model.py`, `tests/test_raster_plan.py`.

## Out of Scope

- Wiring the amplifiers' LIMIT/TRIP or OUT OF REG monitor outputs to the LabJack.
- Any automatic stop, setpoint change or output change in response to a spike or
  regulation fault during a session or drift pass (ADR 0006).
- Changing the stream profile or sample rate used during sessions.
- Oscilloscope capture of the amplifier monitors.
- Importing old `load_calibration.json` records into the new history.
- Changing the vacuum HV interlock's thresholds or behaviour.
- The PI slide deck and the manufacturer email.
- Mode B's behaviour (it only writes its result through Part A).

## Further Notes

- The manufacturer has been asked how LIMIT and TRIP work and how long recoveries last.
  Its answer may add a third line (a sustained clamp below 20 mA) to the Part D chart;
  that is a later change, not part of this spec.
- During sessions the stream samples each monitor every ~133 us (FULL profile). Spikes
  shorter than a few samples can be missed entirely; the spike file records the sample
  interval so this limit travels with the data.
- The operator intends to measure all four plates in all three load conditions, run the
  Mode C ladder and the clamp test, swap the X and Y amplifiers and repeat, then run a
  12-hour drift pass at the experiment's operating point. This spec exists to make each of
  those produce recorded, comparable results.
