# HV Amplifier Calibration

The `HV Calibration` tab checks, systematically, whether what the app
commands on a deflection channel agrees with what the EEL5000 amplifier's
own front-panel monitors report back — something nobody had ever measured
before this feature existed.

**Read the "What this cannot tell you" section before trusting a number out
of this tool.** It changes what a bad-looking result means.

---

## What this cannot tell you

The measurement chain is:

```
command  ->  Rigol DG1022Z output  ->  EEL5000 amplifier gain  ->
    VOLTAGE MONITOR divider  ->  LabJack T7 ADC  ->  this CSV
```

The voltage monitor is itself a link in that chain. If a channel's fitted
gain comes back at, say, 0.982, this data **cannot** tell you whether the
amplifier is under-producing by 1.8% or the monitor is under-reading by
1.8% — both produce a byte-identical CSV. Telling them apart needs an
independent reference at the HV output itself (a calibrated HV probe), which
this feature does not have and does not assume.

**Consequently: this feature only ever logs and displays. It never applies
a correction factor**, and no "Apply correction" control exists anywhere in
the Calibration tab, the runner, or the config. If a future session wants
that, it needs to be designed with an independent reference in hand, not
bolted onto this one.

---

## The ±30 V uncertainty budget

Deviations between commanded and measured that fall inside this band are
**noise, not findings** — don't chase them. The band is drawn as a shaded
region around the ideal `y = x` line on the live scatter plot, and the
number itself is `CAL_UNCERTAINTY_V` in `rbl/config/calibration_config.py`.

It's the root-sum of the EEL5000 manual's own published tolerances
(*Specifications*, p. 1-3) plus the LabJack T7's own ADC error on the
±10 V range:

| Source | Spec | At ±5 kV full scale |
|---|---|---|
| Amplifier accuracy | 0.5% of FS | ±25 V |
| Voltage monitor accuracy | 0.1% of FS | ±5 V |
| Output noise | <500 mVrms | <0.5 mV at the monitor (negligible) |
| LabJack T7 ADC error (±10 V range) | small, folded in | — |

Rounding up, the combined legitimate disagreement is roughly **±30 V**.
Anything well outside that band — tens of volts, not fractions of a volt —
is a real finding worth investigating; anything inside it is exactly what
the hardware's own datasheet says to expect.

Current monitor accuracy (1% of FS) matters for the current-monitor rows
but isn't part of this voltage-only budget.

---

## Running a Sweep

A Sweep drives each of the four deflection channels, one at a time, through
a bipolar DC ladder from `-CAL_MAX_KV` to `+CAL_MAX_KV` in `CAL_STEP_KV`
steps (default: ±4.0 kV in 0.2 kV steps, 41 points), bracketed by a leading
and trailing 0 kV point on every pass — the zero-drift tracker. Three
passes run per channel: **up** (ascending), **down** (descending, to catch
hysteresis), and **random** (a seeded, reproducible shuffle of the same
points, to catch anything that depends on step history). A full run is
4 channels × 3 passes × 43 points ≈ 13 minutes.

**All 8 amplifier monitors (voltage + current, all four channels) are
recorded at every setpoint**, even though only one channel is being driven.
This is free crosstalk data: if driving X+ to +4 kV makes Y− read anything
other than zero, that's real coupling between channels, not an artifact.

To run one:

1. Open the **HV Calibration** tab and connect the LabJack T7 if it isn't
   already (the connection panel is shared with the other hardware tabs —
   connecting from any one of them connects for all).
2. Select **Sweep** mode.
3. Select a **load condition** — `DISCONNECTED` or `ON_PLATES`. There is no
   default; you must choose, because the app cannot verify from software
   what's actually wired up.
4. Optionally enter an operator note — it's recorded in the CSV's metadata
   sidecar.
5. Click **Run**. A pre-run checklist appears — read it, verify each item
   against the physical hardware, and check every box before **Start Run**
   becomes available. If `DISCONNECTED` is selected, the checklist repeats
   the EEL5000 manual's own instruction that load connections must be made
   with the unit off and unplugged, so nobody swaps the load mid-session.
6. Watch the live scatter plot (commanded kV vs. measured kV, with the
   ideal line and the uncertainty band) and the live gain/offset/residual
   readout per channel — display only.
7. **Abort** stops the run at any point; both a normal finish and an abort
   ramp every channel back to 0 V and turn its output off automatically.

## Running a Drift check

A Drift run holds **all four channels** at one setpoint simultaneously and
logs every AIN once every `DRIFT_LOG_INTERVAL_S` (default 1 s), for a
chosen duration — the tool for catching slow thermal drift or intermittent
faults that a 13-minute sweep can't see.

**Duration is gated by load condition, and this is enforced in the runner
itself, not just the GUI:**

- `ON_PLATES` — capped at `DRIFT_MAX_ATTENDED_H` (2 h). Longer runs are
  refused outright.
- `DISCONNECTED` — unattended overnight runs are permitted, up to
  `DRIFT_MAX_UNATTENDED_H` (12 h).

The reasoning: nobody should leave an amplifier driving real plates
unattended for hours, but an amplifier sitting open-circuit can safely soak
overnight.

A **watchdog** aborts the run (zero volts, outputs off, one fault row
written noting why) if no LabJack window arrives for more than 5 seconds —
so a silent stream dropout two hours into a twelve-hour run doesn't quietly
turn into ten hours of nothing rather than a stopped, flagged run. Reaching
the requested duration does the same zero/off shutdown automatically, so an
overnight run never leaves voltage standing on an unattended amplifier at
6 a.m.

---

## CSV columns

One row per AIN (8 rows) per setpoint (Sweep) or per log interval (Drift).

| Column | Meaning |
|---|---|
| `run_id` | Identifies the run; shared with the metadata sidecar's filename |
| `timestamp_iso` | Wall-clock time this row was recorded, UTC ISO-8601 |
| `t_elapsed_s` | Seconds since the run started (monotonic clock) |
| `pass_index` | Which (channel, pass) combination this setpoint belongs to (Sweep only; `0` for Drift) |
| `pass_type` | `"up"`, `"down"`, `"random"`, or `"drift"` |
| `driven_amp` | Which channel was driven this row (`"X+"`/`"X-"`/`"Y+"`/`"Y-"` for Sweep, `"ALL"` for Drift) |
| `commanded_kv` | The setpoint sent to the driven channel(s), in kV |
| `commanded_gen_v` | The same setpoint in generator volts (pre-amplifier-gain) |
| `ain` | The physical LabJack input this row is about, e.g. `AIN13` |
| `amp_label` | Which amplifier this AIN belongs to |
| `kind` | `"voltage"` or `"current"` — which of the amplifier's two monitors |
| `mean_v`, `std_v`, `min_v`, `max_v` | Statistics over every raw sample collected for this AIN during the collect window, in raw ADC volts (unscaled) |
| `n_samples`, `n_windows` | How many raw samples / stream windows went into those statistics |
| `converted_value`, `converted_unit` | `mean_v` scaled to physical units (kV or mA) via `rbl/hardware/amp_monitor.py` |
| `stream_profile` | The LabJack stream profile active for this run (always `CAL_PROFILE`) |

`std_v` is a real measurement, not bookkeeping: the amplifier's spec'd
output noise is <500 mVrms (0.5 mV at the monitor). If `std_v` climbs
sharply — especially above roughly 3 kV — that's corona onset or partial
discharge, arguably the single most useful thing this test can catch.

Current-monitor rows matter too, even on a nominally-DC voltage test: at DC
into a capacitive load the current should be essentially nothing, so
anything else is a leakage path.

### Fault rows

A watchdog trip writes one additional row with `kind="fault"`,
`driven_amp="WATCHDOG_FAULT"`, every numeric statistic `NaN`, and the fault
reason as free text in `converted_unit` (there is no dedicated "reason"
column in this schema — repurposing that one free-text field was the
lowest-disruption option once the column list above was fixed).

### The metadata sidecar

Every `<run_id>.csv` has a matching `<run_id>.json` alongside it, written
once the run ends (most of its fields — the random-pass seed, the final
funcgen readback, the git commit hash — aren't known until the run has
actually happened). It carries the `load_condition`, each channel's
`:OUTPut:LOAD` readback (the driver defaults to `INFinity`; if the front
panel was set to `50 Ω` instead, the CSV's numbers are off by 2× and this
field is the only evidence), the LabJack profile/rate/resolution, a full
snapshot of every `calibration_config.py` constant at run time, and the
operator note.

---

## Where the numbers live

- `rbl/config/calibration_config.py` — every tunable constant (`CAL_MAX_KV`,
  `CAL_STEP_KV`, `CAL_UNCERTAINTY_V`, the drift caps, …) and the
  `sweep_points()` helper.
- `rbl/services/calibration_runner.py` — the non-blocking state machine that
  drives the hardware and builds each row.
- `rbl/services/calibration_writer.py` — the CSV + JSON sidecar writer.
- `rbl/gui/calibration_tab.py` — the GUI.

Deeper analysis of these CSVs (per-channel linear fits as a real
deliverable, hysteresis, repeatability across passes, the crosstalk matrix,
drift slope in V/hour) is deliberately out of scope here — see the Phase 9
note in the implementation history. This tool's job stops at producing
honest, complete, well-documented data; turning that into conclusions is a
separate piece of work, on purpose, so that the recording side never has an
incentive to look better than the data actually is.
