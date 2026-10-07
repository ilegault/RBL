# Spec: The current monitor reads 2 mA per volt, and everything built on it follows

Status: ready-for-agent
Date: 2026-10-07
Related: `docs/adr/0007-current-monitor-scale-is-2-ma-per-volt.md` (new, binding),
`docs/adr/0006-amplifier-faults-are-observed-not-stopped-during-a-session.md` (binding,
unchanged), `docs/adr/0001-tests-first-and-no-muted-failures.md` (binding on all test
work), `docs/hardware/eel5000-manufacturer-notes.md` (the manufacturer's answers and the
decisions taken from them - read sections 1, 3 and 6 before any ticket),
`CONTEXT.md` ("Current monitor scale", "At the rail", "Current limit setting", "Hard
trip", "Soft trip", "Burst rating"), `.scratch/amplifier-load-and-spikes/spec.md`
(amended by this spec where stated).

> ADR 0001 is binding: a failing test is fixed or escalated, never muted.

## Problem Statement

Every amplifier current RBL computes passes through `CURRENT_MONITOR_MA_PER_VOLT` in
`src/rbl/config/hardware_config.py`. It is `10.0`, from the EEL5000 manual. The
manufacturer has stated the manual is wrong: the monitor is **1 V = 2 mA**, so its ±10 V
span (the LabJack's input range) covers only ±20 mA. Every current, capacitance, noise
figure and current-derived threshold the application has produced is 5x too high. The
plate capacitance that read ~1.6 nF is ~320 pF.

Correcting the factor alone would break things:

- The **hard trip** fires on one sample above 60 mA. Once the factor is 2.0, a
  railed monitor reads 20 mA, so the hard trip can never fire.
- The **soft trip** (`CAL_AC_TRIP_MA`, 20 mA) can never fire either. With the
  current pot fully clockwise the amplifier clamps at 20 mA, which is exactly the
  rail.
- `CAL_AC_TRIP_MA` is also used as the Raster Planner's "continuous rating" line, so
  changing the soft-trip level would move the planner's rating line.
- The **burst rating** (100 mA / 4 ms) and everything drawn or warned from it
  describe the 100 mA AC configuration. The operator's working assumption is that
  the units are in the factory 20 mA DC configuration, which has no burst.
- The **spike recorder** reports peaks. Above the rail there is no peak to report,
  only the fact that the current reached at least 20 mA and for how long.
- **Stored results** (characterization results, the old load store, trip history,
  calibration files, spike files) are in the old units. Nothing in a stored file
  says which scale it used, so a new reading and an old one cannot be told apart.

## Solution

1. `CURRENT_MONITOR_MA_PER_VOLT` becomes `2.0`. It remains the only place the scale
   lives, and a contract test enforces that.
2. A sample is **at the rail** when its monitor voltage magnitude is ≥ 9.9 V. A pure
   tracker measures how long the monitor stays at the rail, carrying a run across
   stream windows.
3. The **hard trip** (calibration and characterization only) fires when the monitor
   has stayed at the rail for 5 ms or more.
4. The **soft trip** (calibration only) level becomes 19 mA. The continuous rating
   (20 mA) becomes a separate number used for display and planning.
5. Sessions and drift passes are never stopped by current (ADR 0006, unchanged). The
   Raster Planner's amber (≥ 10 mA) and red (> 16 mA) verdicts are warnings and never
   block a command.
6. The burst rating is removed from the application: the 100 mA levels, the 4 ms zone,
   the "two spikes under 100 ms apart" warning, and the `peak` status.
7. Spikes record time at the rail. A spike that reached the rail has its peak marked
   as a lower bound. The "above 20 mA" warning becomes "at the rail for N ms".
8. Every newly written result, spike and trip record carries the scale it was computed
   with. Results without it, or with a different one, are never planned from.
9. A one-shot script moves the old stores into an archive folder with a README. It
   never deletes or rescales anything.

## User Stories

### The scale

1. As the operator, I want every amplifier current computed with 1 V = 2 mA, so that
   currents, capacitances and margins are the real ones.
2. As the operator, I want the scale defined in one place only, so that no forgotten
   copy can keep the old factor.
3. As a future reader of the code, I want every comment that says "1 V = 10 mA" gone, so
   that no agent re-learns the wrong scale from a comment.

### The rail

4. As the operator, I want a reading at the end of the monitor's span treated as "at
   least 20 mA", never as a measured peak, so that I never present a clipped number as
   real.
5. As the operator, I want to know how long the current stayed at the rail, so that I
   can tell a 150 µs edge from a sustained clamp.

### Trips (calibration and characterization only)

6. As the operator, I want the hard trip to stop a calibration or characterization run
   when the monitor has been at the rail for 5 ms or more, so that a dead short still
   ends the run now that the monitor cannot read above 20 mA.
7. As the operator, I want a square edge that rails the monitor briefly (under 5 ms) not
   to trip, so that the Mode C ladder's high rungs can run.
8. As the operator, I want the calibration soft trip at 19 mA, so that a sustained
   overload is caught just under the rail and a calibration can still reach close to
   the continuous rating.
9. As the operator, I want sessions and drift passes never stopped by current, so that
   an irradiation can run at 19.9 mA if I choose (ADR 0006).

### Planner and display

10. As the operator, I want the Raster Planner's continuous-rating line at 20 mA and its
    amber/red colours as warnings only, so that I see the margin without being blocked.
11. As the operator, I want the 100 mA burst line removed from the planner chart, so that
    the chart does not show a capability my amplifiers probably do not have.
12. As the operator, I want a live current reading at the rail shown as "at limit"
    rather than as a number, so that the display never claims a clipped value.

### Spike recorder

13. As the operator, I want every spike to record its time at the rail, so that I can see
    how long the current was at or above 20 mA.
14. As the operator, I want a spike that reached the rail to have its peak marked as a
    lower bound, so that its peak is never taken as measured.
15. As the operator, I want the "spike above 20 mA" warning replaced by "current at the
    rail for N ms", so that the warning says what was actually observed.
16. As the operator, I want the "two spikes under 100 ms apart" warning and the burst
    zones on the spike chart removed until I understand the 100 mA configuration, so that
    nothing judges events against a rating I may not have.

### Characterization

17. As the operator, I want each Mode C rung to record whether any edge reached the rail,
    so that I know when an edge's charge may be under-read.
18. As the operator, I want the clamp test to record whether the clamp step reached the
    rail, so that a clamp at the pot's 20 mA maximum reads "at least 20 mA" rather than a
    number.
19. As the operator, I want to run the clamp test with the pot at dial 50 and see the
    current flatten near 10 mA (about 5 V on the monitor), so that I can see the
    corrected scale on real hardware.

### Old data

20. As the operator, I want every result, spike file and trip record to carry the scale
    it was computed with, so that old and new numbers can never be mixed.
21. As the operator, I want results with no scale recorded, or a different one, shown as
    "wrong monitor scale - remeasure" and never planned from, so that a 1.6 nF number
    can never come back.
22. As the operator, I want a script that moves the old amplifier-current stores into an
    archive folder with a README explaining why, so that the application starts clean and
    nothing is lost.
23. As the operator, I want that script to show what it would move before it moves
    anything, and to refuse to run a second time, so that I can check it on the
    beamline PC first.

## Implementation Decisions

### Part A - Scale and conversion

- `src/rbl/config/hardware_config.py`: `CURRENT_MONITOR_MA_PER_VOLT = 2.0`, with a
  comment citing ADR 0007 and saying the source is the manufacturer, not a bench
  measurement. Add `CURRENT_MONITOR_RAIL_VOLTS = 9.9` (the at-the-rail threshold) and
  `AMP_CONTINUOUS_RATING_MA = 20.0`. Remove `AMP_MAX_MA_PK`. `AMP_MAX_MA_DC` is
  replaced by `AMP_CONTINUOUS_RATING_MA` everywhere it is read.
- `src/rbl/hardware/amp_monitor.py`:
  - `monitor_to_ma` returns NaN when |voltage| > 11.0 V (beyond the LabJack range =
    garbage), instead of the 110 mA test.
  - `current_status` returns `"ok"` below the rail, `"at_limit"` at the rail, and
    `"over"` for NaN. `"peak"` is removed, and every caller that maps statuses to
    colours or text maps `"at_limit"` instead.
  - Add a pure `is_at_rail(voltage)`.
  - The `__main__` self-test assertions move to the 2 mA scale.
- `src/rbl/config/labjack_stream_config.py`: the AIN comments say `1 V = 2 mA`.
- `src/rbl/services/dynamic_adjustment.py`: the self-test's `raw_i = i_ma / 10.0` uses
  `ma_to_monitor`.
- Every remaining "1 V = 10 mA" text in `src/` is corrected.

### Part B - Rail tracking

- A pure **rail tracker** in `src/rbl/hardware/amp_monitor.py`:
  - **Input:** raw current-monitor volts for one channel, plus the sample interval.
  - **Output:** the duration of the current contiguous at-the-rail run, and the longest
    run completed in this window.
  - **State:** it holds only the open run, so a run spanning two windows joins.
  - **Tests:** it reads no clock and takes the sample interval as an argument, so tests
    drive it with synthetic arrays.
- `HARD_TRIP_RAIL_S = 0.005` in `src/rbl/config/calibration_config.py`. It replaces
  `CAL_TRIP_HARD_MA`, which is removed.

### Part C - Trips

- `CalibrationRunner` (`src/rbl/services/calibration_runner.py`, the over-current check
  around the "HARD (CAL_TRIP_HARD_MA)" docstring) and `LoadCharacterizer._hard_tripped`
  (`src/rbl/services/load_characterizer.py`) both use the rail tracker. They fire when
  an open or completed run reaches `HARD_TRIP_RAIL_S`. The rule name stays
  `"hard_trip"`. Both docstrings are rewritten to the rail definition.
- `CAL_AC_TRIP_MA = 19.0`. It remains the soft-trip level and the `ac_max_peak_kv`
  ladder-sizing level. Its comment says it is the calibration soft trip, just under
  the rail, and not the continuous rating. Blanking, minimum duration and consecutive
  windows are unchanged.
- No trip of any kind is added to, or reachable from, a session or a drift pass.

### Part D - Planner

- `src/rbl/hardware/raster_plan.py`: `envelope_walls` and the levels dict use
  `AMP_CONTINUOUS_RATING_MA` for `continuous_ma`. The `burst_ma` level is removed, and
  `AMP_BURST_RATING_MA` is removed from `calibration_config.py`. `MARGIN_AMBER_MA` (10)
  and `MARGIN_RED_MA` (16) are unchanged.
- `src/rbl/gui/raster_planner_tab.py`: the chart draws no burst line, and its docstring
  no longer mentions one. Verdicts colour the readout only; nothing in the planner
  disables or refuses a command because of a verdict.

### Part E - Spikes

- `src/rbl/hardware/spike_detector.py`:
  - **Rail inputs:** the detector receives raw monitor volts alongside mA, or a
    per-sample rail mask.
  - **New spike fields:** each spike gains `rail_s` (total time at the rail within the
    spike) and `at_rail` (`rail_s > 0`).
  - **Lower bound:** `peak_is_lower_bound` is True when `at_rail`, in addition to the
    existing short-spike rule.
- `src/rbl/services/spike_recorder.py`:
  - **New columns:** `spikes.csv` gains `rail_s`, `at_rail` and
    `current_monitor_ma_per_volt`.
  - **Docstrings:** they stop citing the 100 ms / 10 mA recovery period.
- **Warnings** (the work listed in `.scratch/amplifier-load-and-spikes/issues/48`):
  - "spike above 20 mA" becomes "current at the rail for N ms on <plate>".
  - "two spikes under 100 ms apart" is not built.
  - The other two warnings (reference vs prediction, regulation fault) are unchanged.
- **Spike chart** (from ticket 50):
  - The 100 mA zone and the 4 ms zone are removed.
  - The 20 mA continuous line stays.
  - Spikes with `at_rail` are drawn at 20 mA, hollow, with their duration on the x
    axis.

### Part F - Characterization

- **Mode C:** each rung's record gains `edge_at_rail` (any sample of any edge at the
  rail). It is recorded, never an abort rule.
- **Clamp test:** `clamp_result` gains `at_rail` (the clamp step's current reached the
  rail). When true, `clamp_ma` is reported as a lower bound in the result file and in
  the view.
- `CLAMP_FREQ_LADDER_HZ` and `CLAMP_RATIO_THRESHOLD` are unchanged.

### Part G - Recorded scale and history

- **Writers that record the scale:** every characterization result file, `spikes.csv`
  row, trip-history record and calibration output file gets the key
  `current_monitor_ma_per_volt`, holding the value in force when it was written.
- `src/rbl/config/characterization_history.py`:
  - **Never planned from:** a result whose `current_monitor_ma_per_volt` is missing or
    differs from `CURRENT_MONITOR_MA_PER_VOLT` is never returned as a newest result
    for planning.
  - **Still listed:** it is listed with the reason `"wrong monitor scale - remeasure"`.
- The Load Characterization view shows that reason in place of a value.

### Part H - Archive script

- `scripts/archive_pre_adr_0007.py` (new):
  - **Arguments:**
    - `--log-root`, default `rbl.config.paths.LOG_ROOT`.
    - `--config-dir`, default `rbl.config.paths.CONFIG_DIR`.
    - `--apply`. Without it the script only prints what it would move.
  - **What it moves** (only those that exist), from `LOG_ROOT/data/`:
    - `trip_history.jsonl`
    - `dynamic_adjustment_history.jsonl`
    - `conditioning_history.jsonl`
    - `calibration/`
    - `load_characterization/`

    These go into `LOG_ROOT/data/archive/pre-2026-10-07-current-scale/`.
    `load_calibration.json` moves from the config dir into
    `<config-dir>/archive/pre-2026-10-07-current-scale/`.
  - **Spike files:** every `spikes.csv` under `LOG_ROOT` is renamed in place to
    `spikes.pre-adr-0007.csv`, and every `spikes/` folder beside one to
    `spikes.pre-adr-0007/`.
  - **README:** each archive folder gets a `README.txt`: "Computed with the EEL5000
    manual's 1 V = 10 mA current-monitor scale. The manufacturer says the scale is
    1 V = 2 mA (docs/adr/0007). Every current, capacitance and current threshold in
    these files is 5x too high. Deliberately not rescaled."
  - **Safety:**
    - It refuses to run, and exits non-zero naming the folder, if either archive folder
      already exists.
    - It never deletes or overwrites a file.
    - It is a pure function of its two roots, with no reads of `Path.home()` or the
      environment beyond the argument defaults, so tests drive it with temp
      directories.
- The operator runs it once on the beamline PC.

### Amendments to `.scratch/amplifier-load-and-spikes`

- Its spec's story 34, story 48, story 49 and story 53 burst wording are superseded by
  Parts D and E.
- Its "Further Notes" line about a third chart line awaiting the manufacturer is
  answered by `docs/hardware/eel5000-manufacturer-notes.md`.
- Tickets 48, 51 and 52 must not be worked before the tickets from this spec that
  change the scale, the trips and the spike fields.

## Testing Decisions

- ADR 0001 is binding. Tests assert behaviour visible from outside a module, never
  private state.
- The suite's autouse redirections in `tests/conftest.py` stay in force. The archive
  script is tested only through its `--log-root` and `--config-dir` arguments, never
  against real home paths.
- **Required tests:**
  1. `monitor_to_ma(5.0) == 10.0` and `ma_to_monitor(20.0) == 10.0`. The test's
     docstring cites ADR 0007.
  2. Contract test over `src/rbl/**/*.py`:
     - `CURRENT_MONITOR_MA_PER_VOLT` is assigned only in `hardware_config.py`.
     - No file contains `1 V = 10 mA`, `1V=10mA` or `1 V == 10 mA`.
  3. Rail tracker:
     - a 1 ms run does not reach 5 ms;
     - a 6 ms run does;
     - 3 ms at the end of one window plus 3 ms at the start of the next joins to 6 ms;
     - one off-rail sample inside a run restarts it.
  4. `LoadCharacterizer` with synthetic windows (prior art `tests/test_load_characterizer.py`):
     - a 150 µs railed edge on every square edge does not trip, and sets
       `edge_at_rail` on that rung;
     - a continuous rail does trip with rule `"hard_trip"`.
  5. `CalibrationRunner` (prior art `tests/test_calibration_runner.py`):
     - a 6 ms rail ends the run as a hard trip;
     - a sustained 19.5 mA window sequence ends it as a soft trip;
     - a sustained 18.5 mA sequence does not.
  6. Spike detector:
     - a spike containing a 2 ms rail segment reports `at_rail` True, `rail_s` ≈ 0.002
       and `peak_is_lower_bound` True;
     - a spike well under the rail reports `at_rail` False.
  7. Spike recorder: the written `spikes.csv` header contains `rail_s`, `at_rail` and
     `current_monitor_ma_per_volt`, and a row's scale equals the constant.
  8. Characterization history:
     - a result file without `current_monitor_ma_per_volt` is never the newest result
       and is listed with `"wrong monitor scale - remeasure"`;
     - a result with the current scale is used.
  9. `raster_plan.current_vs_frequency`:
     - the levels contain `continuous_ma == 20.0` and no `burst_ma`;
     - verdict boundaries remain exactly 10 and 16 mA.
  10. Archive script against temp roots:
      - a dry run moves nothing;
      - `--apply` moves exactly the listed items, writes both READMEs and renames
        nested `spikes.csv`;
      - a second `--apply` exits non-zero and changes nothing.
- Any existing test asserting the 10 mA/V scale, the 60 mA hard trip, a `"peak"` status,
  a `burst_ma` level or the 100 ms warning is rewritten in place under the same name to
  the new behaviour. A ticket that cannot rewrite one lists it on a
  `**Deletes tests:**` line.

## Out of Scope

- The 100 mA AC configuration: bursts, recovery period, and any burst warning or chart
  level. That is a later experiment, after the manufacturer replies.
- DYNAMIC ADJUSTMENT guidance for a unit with no load connected (open with the
  manufacturer).
- Whether amplifier LIMIT ever shuts an output off on its own (open with the
  manufacturer). The regulation detector's existing "amplifier off" classification is
  unchanged.
- Rescaling or importing any old data.
- Any trip, stop or setpoint change during a session or drift pass.
- Changing the LabJack input range or the stream profiles.
- The PI presentation (the operator updates it from the bench campaign's results).
- Running the archive script on the beamline PC (operator).

## Further Notes

- The scale is the manufacturer's statement and is not bench-verified (ADR 0007). The
  operator's first look at the corrected current will be a clamp test with the pot at
  dial 50 (10 mA). The monitor should flatten near 5 V. If it flattens near 1 V, ADR
  0007 is revisited, and no second factor is added.
- Expected corrected magnitudes: plate load ~300-330 pF; usual operating-point currents
  ~0.24-1 mA; monitor noise ~0.28 mA rms. A Mode C edge at a 5 kV rung (10 kV step into
  ~300 pF at a 20 mA clamp) rails for ~150 µs.
- With the pot fully clockwise, the amplifier's LIMIT clamp (20 mA) and the monitor rail
  (~19.8 mA) are effectively the same level. A sustained rail during a session means
  the amplifier is clamping, which the regulation detector reports as current
  limited.
