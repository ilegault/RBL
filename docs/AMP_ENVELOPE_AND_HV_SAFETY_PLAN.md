# RBL — Amplifier Envelope, Load Characterization & HV Safety

**Implementation plan for Claude Sonnet**
Target repo: `C:\Users\IGLeg\PycharmProjects\RBL`
Written 2026-08-18. Author of record: Isaac (ilegault004@gmail.com), with design discussion by Claude Opus.

---

## 0. Read this first

This document specifies **seven** related features. They share a physics model
and a set of measurements, so Section 1 (Physics Reference) is load-bearing for
every phase that follows — do not skip it, and do not re-derive its constants.

**Build order is deliberate.** Phase 1 produces the measurement that every later
phase consumes. Do not start Phase 3 before Phase 1 lands.

**Do not implement an amplifier swap test.** The user will perform that on the
bench manually. It is documented in Appendix A for reference only.

### Style constraints for this repo

The existing codebase has strong, consistent conventions. Match them:

- Every module has a docstring explaining **why it exists**, not just what it
  does. Several existing docstrings record the bug that motivated the module.
  Continue that practice — when a design choice is non-obvious, write down the
  failure it prevents.
- Pure-math modules (`ac_metrics.py`, `amp_monitor.py`, `funcgen_safety.py`,
  `waveform_period.py`) contain **no Qt, no hardware, no config lookups**. They
  take arrays and return floats. Keep new math in that shape so it stays
  testable without a LabJack.
- Hardware drivers are thin. State mixins own lifecycle. Services own
  multi-step procedures. GUI tabs own widgets only.
- Every new module gets a `if __name__ == "__main__":` self-test block, matching
  the pattern in `amp_drive.py` and `hardware_config.py`.
- Every new feature gets a `tests/test_*.py` file.

---

## 1. Physics Reference (authoritative — do not re-derive)

### 1.1 The hardware chain

```
DG1022Z ch → BNC (3 ft X / 6 ft Y) → EEL5000.20.100 → HV coax (6 ft) →
  vacuum feedthrough → NEC electrostatic steerer plates
```

Monitoring:

```
EEL5000 VOLTAGE MONITOR (1000:1) ─┐
EEL5000 CURRENT MONITOR (1V=10mA)─┴→ LabJack T7 CB37 → AIN6..AIN13
```

### 1.2 Amplifier ratings (EEL5000.20.100 manual, p. 1-3)

| Parameter | Value |
|---|---|
| Output voltage | ±5 kV |
| Input gain | 1 V → 1000 V, non-inverting |
| Current | **±20 mA peak DC**, or **±100 mA peak AC for 4 ms** |
| Burst recovery | after 100 mA/4 ms → **10 mA for 100 ms** → 100 mA available again |
| Slew rate | > 300 V/µs (**not achieved into this load — see 1.5**) |
| Large-signal BW | > 10 kHz (no load, −3 dB) |
| Small-signal BW | > 35 kHz (no load, −3 dB) |
| Power | 100 W |
| Voltage monitor | 1000:1, 0.1% FS |
| Current monitor | 1 V = 10 mA, 1% FS, **BW > 11 kHz** |
| Current limit pot | user-adjustable **0.5 – 10 mA** |
| Capacitive load | **loads > 1 nF require factory adjustment** |

**Operating mode:** the user runs permanently in **LIMIT** mode, not TRIP, with
the current pot at maximum (10 mA). In LIMIT mode the amplifier clamps current
and **stops following its input** rather than shutting down — so the plate
voltage silently stops matching the commanded voltage. This is why Phase 6
exists.

### 1.3 The governing equation

For a capacitive load:

```
I = C · dV/dt
```

Peak current for a periodic drive:

```
I_pk = k · f · C · V_pk
```

where `k` depends on waveform shape. **This is already implemented** in
`rbl/config/calibration_config.py` as `ac_shape_k()` / `ac_peak_current_ma()`:

| Shape | k | Reason |
|---|---|---|
| Sine | 2π = 6.283 | `dV/dt|max = 2πf·V_pk` |
| Triangle / ramp | 4 | slews ±V_pk in half a period; `|dV/dt|` is constant |
| Square | 2π (conservative) | true limit is amplifier slew, not load |

Default drive shape is **triangle** (`AC_DEFAULT_SHAPE`), matching
`AmpDrive._command_ac`.

### 1.4 Measured load capacitance

```python
CAL_LOAD_CAP_PF = 1200.0   # rbl/config/calibration_config.py
```

Measured 2026-08-11 by back-solving AC sweeps at **64 Hz and 517 Hz
independently**; the two agreed within 8%. Two frequencies 8× apart yielding
the same C is the signature of a genuine capacitance — a noise floor or a
monitor offset would not scale correctly with frequency. **Treat 1200 pF as
trustworthy.**

**Open question — high value.** The expected capacitance from geometry is about
**125 pF**:

| Source | Estimate |
|---|---|
| 6 ft HV coax @ ~60 pF/m | ~110 pF |
| Vacuum feedthrough | ~10 pF |
| Steerer plates (ES5: 12.7 × 10.2 cm, 3.8 cm gap; `C = ε₀A/d`) | ~3 pF |
| **Total expected** | **~125 pF** |

So roughly **1 nF is unaccounted for**. It is most likely internal to the
amplifier (output filter network, HV feedback divider, compensation), in which
case nothing can be done. If it is external, eliminating it would move the
current wall at 5 kV from **833 Hz to ~5 kHz** — a ~6× expansion of the usable
research envelope. **Phase 1 must be able to answer this** (see 2.4).

### 1.5 What the amplifier actually does on a fast step

Measured: a shutoff from +2.6 kV produced a **35 mA** transient.

```
dV/dt = 35 mA / 1200 pF = 29 V/µs
```

The amplifier does **not** reach its 300 V/µs spec into this load. Its
fast-transient behaviour is **current-limited at roughly 35 mA**, so a step of
ΔV takes approximately:

```
t_transient ≈ C · ΔV / 35 mA
```

A 2.6 kV step → ~90 µs. A 5 kV step → ~171 µs.

**This is not damaging.** 35 mA for 90 µs is far inside the 100 mA / 4 ms burst
rating (roughly 0.2% of the allowed burst-duration budget). Do not build the
ramp engine to protect the amplifier from this — build it for the reasons in
Phase 4.

### 1.6 The operating envelope — four walls

With C = 1200 pF, triangle drive (k = 4), continuous limit 20 mA:

```
Current wall:    f · V_pk ≤ I_max / (k · C) = 0.020 / (4 · 1200e-12) = 4.17e6 V·Hz
Voltage wall:    V_pk ≤ 5 kV
Bandwidth wall:  f ≤ 10 kHz  (amplifier large-signal BW)
Slew wall:       k · f · V_pk ≤ 300 V/µs  →  f · V_pk ≤ 7.5e7  (never binds here)
```

The **current wall binds everywhere that matters**:

| V_pk | Max f (20 mA, triangle) | Max f (10 mA pot setting) |
|---|---|---|
| 5 kV | 833 Hz | 417 Hz |
| 4 kV | 1042 Hz | 521 Hz |
| 2 kV | 2083 Hz | 1042 Hz |
| 1 kV | 4167 Hz | 2083 Hz |

Current draw at the user's stated operating points:

| Operating point | I_pk | % of 20 mA |
|---|---|---|
| 64 Hz @ ±4 kV | 1.23 mA | 6% |
| 517 Hz @ ±2 kV | 4.96 mA | 25% |

Both are inside the envelope, but **517 Hz @ 2 kV is at a quarter of the
continuous rating** — this is not vast headroom, and future raster studies at
higher frequency or amplitude will hit the wall. The envelope is a real
constraint on the research, which is why Phase 3 matters.

### 1.7 Steerer deflection (NEC XY Steerer manual, §IV)

```
θ = V · l · q / (2 · d · E)          [radians]
x = θ · L                            [displacement at target]
                                     (SUPERSEDED: the l/2 pivot term was
                                      removed 24 Aug 2026 — the manual §IV
                                      and the lab deflection sheet both use
                                      the drift alone, and the app must
                                      agree with them)
```

| Symbol | Meaning | Units |
|---|---|---|
| V | **plate-to-plate** potential | V |
| l | plate length | cm (use consistent units with d) |
| d | plate separation | cm |
| q | ion charge state | elementary charges |
| E | beam energy | eV |
| L | drift distance, steerer exit → sample | same as l |

**Factor-of-two hazard.** The rig drives push-pull: X+ at +V and X− at −V gives
a plate-to-plate potential of **2V**. Every UI field and every function
signature must state explicitly whether it means per-plate or plate-to-plate.
Name them `plate_kv` and `differential_kv` and never abbreviate to `kv`.

Steerer geometries from the manual (user must select which is installed):

| Model | Plate length | Separation | Plate width | Rating |
|---|---|---|---|---|
| ES5 (2EA003100) | 12.7 cm | 3.8 cm | — | 5 kV |
| Single-axis 2EA032630 | 12.7 cm | 3.8 cm | 10.2 cm | 5 kV |
| Single-axis 2EA055030 | 7.30 cm | 3.8 cm | 10.2 cm | 5 kV |
| ES7 (2EA039291) | 10.2 cm | 3.2 cm | — | 10 kV |
| ES10 (2EA021440) | 12.7 cm | 3.8 cm | 10.2 cm | 10 kV |
| Duo-axis 2EA068900 | 7.30 cm | 3.8 cm | — | 5 kV |

> **SUPERSEDED (24 Aug 2026).** This table and the "steerer model
> dropdown" in §4.4 are gone. The beamline has exactly one steerer —
> NEC **2EA021441** — so its geometry is a constant in
> `rbl/config/steerer_geometry.py` and the Raster Planner displays it
> instead of asking. A picker could only ever be left on the wrong
> answer, and the gap scales every deflection number on the tab.
>
> Its numbers come from the lab's own reference sheet, *Hirst RHBL
> Deflection Information.xlsx* — **plates 12.5 cm long, 3.8 cm gap,
> 5 kV per plate** (10 kV plate-to-plate, push-pull). Note this is
> **not** the ES10 row above: the manual's spec tables are for the
> catalogue units, and 2EA021441 is a customer variant (manual §I).
> The same sheet gives the steerer-exit-to-sample drift, 97.48 in =
> **247.60 cm**, which is now the tab's default.


### 1.8 Noise floor

The current monitors carry a **~1.4 mA rms noise floor** (documented in
`ac_metrics.py`). This dominates every design decision about measurement:

- Steady-state current at the user's operating points (1.2–5 mA) is only
  1–3.5× the noise floor. Raw peak/RMS readings are unreliable.
- **The lock-in in `ac_metrics.py` is the answer** and is already built. Use
  single-bin DFT at the known drive frequency everywhere.
- For DC measurements, average: 10 s at 100 kS/s is 1e6 samples, reducing the
  floor by √N = 1000 → **~1.4 µA sensitivity**. This is what makes Phase 1's
  leakage measurement possible.

### 1.9 SCPI latency — the constraint on everything commanded

`DG1022Z._write_checked()` sends the command **and then queries
`:SYSTem:ERRor?`** — two USB-TMC round trips per write. Round trip is roughly
10–40 ms with several ms of jitter.

Consequences that appear repeatedly below:

- **You cannot phase-align anything over USB.** Jitter exceeds a full period at
  the user's operating frequencies. Never write code that tries to time a
  command to a waveform phase.
- **Any ramp is latency-bound, not physics-bound.** The load settles in tens of
  µs; the command path takes tens of ms. Steps can never be "too fast", only
  "too coarse".
- **`APPLy:` restarts the phase generator.** `DG1022Z.set_waveform()` uses
  `:SOURce{ch}:APPLy:...` for every shape. Calling it mid-ramp produces exactly
  the discontinuity a ramp exists to avoid. See Phase 4.1 for the fix.

---

## 2. Phase 1 — Load Characterization & Leakage  *(build first)*

### 2.1 Why

Produces the measurements every other phase consumes: per-channel capacitance,
per-channel leakage conductance, and the four-channel comparison that
diagnoses the Y-axis fault history (Appendix A). Currently `CAL_LOAD_CAP_PF` is
a single global constant hand-edited from a one-off analysis; it should be a
measurement the app can reproduce on demand, per channel.

### 2.2 New module: `rbl/hardware/load_model.py`

Pure math. No Qt, no hardware, no config.

```python
def admittance_from_fundamentals(i_fund_ma: float, v_fund_kv: float,
                                 phase_deg: float, freq_hz: float) -> dict:
    """Complex load admittance from lock-in fundamentals.

    Returns {"c_pf": float, "g_us": float, "loss_tangent": float}.

    A pure capacitor has current leading voltage by exactly 90 deg:
        Y = jwC  ->  real part zero.
    Any real part is conductance (leakage, corona, dielectric loss) and is
    the diagnostic that distinguishes a healthy channel from a leaky one.

        C  = |Y| * sin(phase) / (2*pi*f)
        G  = |Y| * cos(phase)
        tan(delta) = G / (2*pi*f*C)
    """

def capacitance_from_charge(current_ma, dt_s, baseline_ma, delta_v_kv) -> float:
    """C in pF from the charge integral across one step edge.

        integral(I dt) = Q = C * dV

    A low-pass filter has unity DC gain, so the 11 kHz monitor pole spreads
    the current pulse in time but preserves its AREA exactly. This measurement
    is therefore immune to the monitor's bandwidth — unlike any peak-based
    reading. Baseline must be subtracted before integrating or the DC offset
    dominates the result.
    """

def envelope_walls(load_pf: float, trip_ma: float, shape: str,
                   max_kv: float, max_f_hz: float) -> dict:
    """The four walls of Section 1.6 as f(V) curves, for plotting."""
```

### 2.3 New service: `rbl/services/load_characterizer.py`

A `QObject` service in the shape of `CalibrationRunner` (signals: `progress`,
`point_measured`, `finished`, `error`, plus an abort path). Three modes:

**Mode A — Impedance sweep (primary).**

For one channel, sweep frequency and measure complex admittance at each point.

- Stream profile: **`AMP_PAIR`** (50 kS/s × 2 channels) — already exists and is
  the right instrument.
- Waveform: **sine** (not triangle — the lock-in is cleanest on a pure tone and
  `k = 2π` is exact).
- Frequency ladder: geometric, ~10 points from 200 Hz to 3 kHz. **Choose
  amplitude per frequency** so the drawn current lands in a target band
  (2–6 mA): `V_pk = I_target / (2π·f·C_est)`. Starting from
  `CAL_LOAD_CAP_PF` is fine; refine after the first point. This is the whole
  trick — it puts the signal several times above the 1.4 mA noise floor at
  every point instead of only at the top of the sweep.
- **Clamp every computed amplitude through `ac_max_peak_kv()` and
  `funcgen_safety.peak_status()` before commanding.** No exceptions.
- At each point compute `c_pf`, `g_us`, `loss_tangent`.
- Output: a per-channel CSV plus a summary dict.

**Acceptance:** on a healthy channel, `c_pf` is constant across the sweep to
within ~10% and `g_us` is consistent with zero. A frequency-dependent `c_pf`
means the measurement is wrong (check for amplitude clipping or lock-in
frequency mismatch), not that the capacitor is exotic.

**Mode B — DC leakage vs. voltage.**

- Stream profile: single-channel 100 kS/s on the current monitor.
- Ladder: 0.5, 1, 2, 3, 4, 5 kV. **Reach each setpoint via the Phase 4 ramp**,
  not a step.
- At each setpoint, dwell and average ≥ 10 s → ~1.4 µA sensitivity.
- Record mean current, standard error, and chamber pressure at the moment of
  measurement (Phase 2 provides this).
- **Abort the ladder immediately** if leakage exceeds a configurable threshold
  (start at 50 µA) — that is a discharge starting, and continuing to climb the
  ladder into a developing discharge is how amplifiers die.

**Acceptance:** a healthy 1200 pF load reads flat near zero. A knee in the
curve is the discharge onset voltage and is the channel's real DC limit.

**Mode C — Charge integral (cross-check).**

- Square wave, 10 Hz, ±1 kV at the plates (2 Vpp at the generator).
- Single-channel 100 kS/s on the current monitor.
- Detect edges, integrate baseline-subtracted current over a window from
  −200 µs to +2 ms around each edge, average ≥ 100 edges.
- `C = Q / ΔV`, ΔV = 2000 V.

**Acceptance:** agrees with Mode A within 15%. Two methods with completely
different failure modes agreeing is what makes the number trustworthy.

### 2.4 The decomposition question — make it first-class

The ~1 nF discrepancy in §1.4 is answerable by running Mode A twice under the
two existing `LoadCondition` values:

- `LoadCondition.DISCONNECTED` → amplifier + internal network only
- `LoadCondition.ON_PLATES` → everything

The difference is the external load. The app must **tag every stored result
with its `LoadCondition`** and the results panel must let the user compare the
two directly and display the subtraction. If DISCONNECTED already reads
~1200 pF, the capacitance is internal to the amplifier and the envelope is
fixed — record that conclusion in the repo and stop chasing it.

### 2.5 Wiring `CAL_LOAD_CAP_PF` to measurement

Do **not** simply overwrite the constant.

- Add `rbl/config/persistence.py`-backed storage: `{amp_label: {c_pf, g_us,
  measured_at, load_condition, method}}`.
- `ac_peak_current_ma()` / `ac_max_peak_kv()` gain an optional `amp_label`; when
  a stored measurement exists for that channel, use it, else fall back to
  `CAL_LOAD_CAP_PF`.
- **Keep `CAL_LOAD_CAP_PF` as the documented fallback** and keep its existing
  provenance comment. The current docstring correctly notes this value only
  shortens the ladder and never corrects a measurement — preserve that
  property. Per-channel capacitance must never silently alter recorded data.

### 2.6 GUI

New sub-panel in `rbl/gui/calibration_tab.py` (or a sibling tab if the
calibration tab is already crowded — the user's call):

- Channel selector, mode selector (A/B/C), Run/Abort.
- Live plot: Mode A → C and G vs. frequency; Mode B → leakage vs. voltage.
- **Four-channel comparison view** — this is the point. A table of
  `{amp_label: c_pf, g_us, tan δ}` with the outlier highlighted. A nonzero `g_us`
  on Y+ or Y− that the X channels do not share is direct evidence of a leakage
  path and resolves the fault history without touching the amplifiers.

---

## 3. Phase 2 — Vacuum ↔ HV Interlock

### 3.1 Why

The user's only two genuine faults were an out-of-regulation event on Y− and a
hard self-shutdown on Y+, both during **held 0→5 kV DC steps**. §1.5 shows the
step current cannot explain a shutdown. The likely mechanism is **discharge in
the chamber**: held DC gives corona or glow discharge time to develop, draws
sustained current, and trips the amplifier. AC scanning at 64/517 Hz never
dwells long enough for that.

Both gauge controllers (VGC083, XGS600) are already integrated via
`VacuumLinkMixin`. This is mostly policy plumbing over existing data, and it
prevents the only failure mode that has actually occurred.

### 3.2 New module: `rbl/hardware/hv_interlock.py`

Pure math + policy. No Qt, no hardware.

```python
HV_PRESSURE_LIMITS = [
    # (max_kv_permitted, pressure_torr_required_below)
    (5.0, 1e-5),
    (3.0, 5e-5),
    (1.0, 1e-4),
]
HV_PRESSURE_ABSOLUTE_LOCKOUT_TORR = 1e-3   # no HV at all above this

def max_permitted_kv(pressure_torr: float) -> float: ...
def interlock_status(pressure_torr: float, commanded_kv: float) -> tuple[str, str]:
    """-> ("ok"|"warn"|"block", human-readable reason)"""
```

**These thresholds are placeholders.** Phase 1 Mode B measures the real
discharge onset. Once that data exists, replace them with measured values and
record the provenance in the docstring, in the style of `CAL_LOAD_CAP_PF`.

### 3.3 Integration

- `Beamline` gains `hv_interlock_changed = Signal(object)`, emitted whenever
  `vacuum_changed` fires or a setpoint changes.
- **Enforce in `FuncGenControlMixin.set_channel()` and `apply_all_channels()`**
  — the same place `funcgen_safety.peak_status()` is enforced. That is the
  chokepoint every path to the hardware passes through; do not enforce it in a
  tab.
- On a transition into `block` while HV is live: **ramp to zero** via Phase 4
  (do not hard-cut — a fast collapse into a chamber that is already at risky
  pressure is the wrong move), then log with pressure and setpoint recorded.
- **Stale-reading guard.** If the gauge has not reported within N seconds, or
  the gauge is disconnected, treat pressure as *unknown* and block HV increases.
  Do not treat "no data" as "good vacuum". Make the timeout configurable and
  the blocked-on-stale state visibly distinct in the UI from blocked-on-pressure.
- Interlock state must be visible on the Overview tab, not buried.

### 3.4 Config

New `rbl/config/hv_safety_config.py` holding the ladder, the lockout, the
staleness timeout, and their provenance comments.

---

## 4. Phase 3 — Envelope Model & Raster Calculator

### 4.1 Why

This is the research deliverable. The user will be defending raster parameter
choices in defect studies, and needs (a) the amplifier envelope and (b) the
amplitude required for uniform dwell given a measured beam FWHM.

### 4.2 New module: `rbl/hardware/raster_model.py`

Pure math. No Qt, no hardware.

```python
def deflection_mrad(differential_kv, plate_length_cm, plate_gap_cm,
                    charge_state, beam_energy_ev) -> float:
    """theta = V*l*q / (2*d*E), from the NEC steerer manual section IV."""

def displacement_mm(differential_kv, plate_length_cm, plate_gap_cm,
                    charge_state, beam_energy_ev, drift_cm) -> float:
    """x = theta * L.  (Was (L + l/2); see the note in §1.7.)"""

def required_differential_kv(target_half_width_mm, fwhm_mm, turnaround_k,
                             geometry, beam) -> float:
    """Plate-to-plate kV needed to put the raster turnaround off the sample.

    Uniform dwell requires the direction change to happen off the sample.
    Because the beam has finite width, the sample's edge does not reach
    uniform dose until the beam CENTRE has travelled roughly
    turnaround_k * FWHM past the sample edge:

        required half-amplitude = target_half_width_mm + turnaround_k * fwhm_mm

    turnaround_k defaults to 1.5; expose it, do not hard-code it.
    """

def dwell_uniformity(fwhm_mm, scan_half_width_mm, sample_half_width_mm,
                     n_points=501) -> dict:
    """Simulate dose vs position for a constant-velocity triangle scan
    convolved with a Gaussian beam. Returns
    {"uniformity_pct", "profile_mm", "profile_dose", "edge_rolloff_mm"}.

    This is what justifies turnaround_k to a reviewer instead of asserting it.
    """

def lissajous_metrics(f_fast_hz, f_slow_hz, span_fast_mm, span_slow_mm,
                      fwhm_mm) -> dict:
    """Raster pattern properties.

    Pattern repeat period = lcm(f_fast, f_slow) reduced -- for 517 Hz and
    64 Hz, gcd(517, 64) = 1 (517 = 11*47, 64 = 2^6), so the pattern does not
    close for 517 slow-axis cycles = 8.08 s. A near-integer ratio would give
    a standing striped pattern instead of a filling one, so this number is a
    real design constraint, not trivia.

    Returns {"repeat_period_s", "line_spacing_mm", "lines_per_fwhm",
             "fills_uniformly": bool}.
    """
```

`lines_per_fwhm < 2` means the raster under-samples the beam and leaves stripes
— flag it loudly in the UI.

### 4.3 New GUI panel: raster planner

Inputs: steerer model (dropdown, table from §1.7), drift distance, ion species
and charge state, beam energy, FWHM (from the existing profiler/scope feature
or typed), sample dimensions, desired f for each axis, `turnaround_k`.

Outputs:
- Required plate-to-plate kV per axis, **and** per-plate kV — both labelled.
- Predicted peak current per axis via `ac_peak_current_ma()`.
- Envelope plot (f on x, V_pk on y) with all four walls from
  `envelope_walls()`, the requested operating point marked, and clear
  in/out-of-envelope indication.
- Dwell uniformity profile plot.
- Lissajous metrics with the repeat period and line spacing.

**The envelope plot must use the per-channel measured capacitance from Phase 1
when available**, and label which capacitance it used. An envelope drawn from
the wrong C is worse than no envelope.

### 4.4 Extend `envelope_walls` consumers

`ac_max_peak_kv()` already inverts the current wall. Leave it as the
authoritative clamp for the calibration runner; `raster_model` is for planning
and plotting. **Do not create a second clamp path to the hardware.**

---

## 5. Phase 4 — Slew-Limited Ramp Engine

### 5.1 Why

Explicitly **not** to protect the amplifier from the shutoff transient — §1.5
shows that transient is harmless. Build it because:

1. **Discharge initiation is dV/dt-sensitive.** Streamer formation in vacuum is
   more likely on a fast edge than a slow ramp. This is the plausible mechanism
   behind the historical Y+ shutdown on a 0→5 kV step.
2. **It is the substrate for Phase 5** (HV conditioning), which cannot exist
   without it.
3. **Phase 2 needs a graceful shutdown path** when the interlock trips.
4. Phase 1 Mode B needs to walk a DC ladder without stepping.

### 5.2 New driver primitives — required, and the subtle part

`DG1022Z.set_waveform()` uses `:SOURce{ch}:APPLy:...`, which **reconfigures the
channel and restarts the phase generator**. Using it mid-ramp creates the exact
discontinuity the ramp exists to avoid.

Add to `rbl/hardware/funcgen_driver.py`:

```python
def set_amplitude(self, channel: int, vpp: float):
    """Change amplitude in place WITHOUT restarting phase.

    :SOURce{ch}:VOLTage scales the DAC output. Unlike :APPLy: it does not
    reconfigure the channel or reset the phase generator, so it is safe to
    call repeatedly on a running waveform. Using :APPLy: for this is the
    single most likely way to get this feature wrong.
    """
    self._write_checked(f":SOURce{int(channel)}:VOLTage {vpp}")

def set_offset(self, channel: int, volts: float):
    self._write_checked(f":SOURce{int(channel)}:VOLTage:OFFSet {volts}")

def write_fast(self, cmd: str):
    """Write WITHOUT the per-command :SYSTem:ERRor? poll.

    _write_checked costs two USB round trips. A 25-step ramp on four channels
    is 200 round trips = several seconds of wall time. Ramp steps use this;
    the caller MUST call get_error() once when the ramp completes, so errors
    are still detected, just not per-step.

    Do not use this outside a ramp.
    """
    self._inst.write(cmd)
```

Also clamp inside `set_amplitude`/`set_offset` exactly as `set_waveform` does —
the coarse backstop must not be bypassed by the new path.

### 5.3 New service: `rbl/services/ramp_engine.py`

A `QObject` with its own `QTimer` tick. **The GUI never writes a setpoint; it
writes a target.**

```python
class RampEngine(QObject):
    ramp_started  = Signal(str)            # amp_label
    ramp_progress = Signal(str, float, float)   # label, current_kv, target_kv
    ramp_finished = Signal(str)
    ramp_failed   = Signal(str, str)
```

Design rules — each of these prevents a specific failure:

- **Step size from a current budget.** `max_step_kv = I_budget / (C · 2π·f_bw)`
  using the amplifier's 35 kHz small-signal bandwidth. For 1200 pF and a 5 mA
  budget this is ~19 V; for 10 mA, ~38 V. Those are impractically small over
  USB, so in practice cap the step by **ramp duration**: pick a target ramp time
  (default ~1 s) and derive step count from measured SCPI round-trip time.
  **Log the resulting predicted peak current so it is never silently large.**
- **Measure round-trip time at startup** and store it; do not hard-code a tick.
- **Retarget, do not queue.** If the target changes mid-ramp, update the target
  and continue from the current position. Queuing produces surprising behaviour
  the user cannot predict.
- **Ramp all four channels in lockstep.** SCPI writes are serial, so channels
  will be up to one step out of sync — a per-step differential across a plate
  pair. Order writes so pair members are adjacent (X+, X−, Y+, Y−) to minimise
  it, and log the worst-case differential.
- **Abort path bypasses the ramp entirely.** On a genuine fault, hard-off
  (`zero_and_off_all()` plus pulling HV enable if wired) is correct — a fault is
  precisely when the fast transient is the lesser risk. Keep
  `AmpDrive.zero_and_off_all()` and its `atexit` registration exactly as they
  are; do not route them through the ramp.
- **Publish ramp state.** Phase 6's regulation detector must be able to see that
  a ramp is in progress, or it will fire a false alarm on every ramp (see 7.3).

### 5.4 Integration

- `AmpDrive` gains `command_dc_ramped()` and `command_ac_amplitude_ramped()`
  which delegate to `RampEngine`. Existing direct methods stay for the
  calibration runner, which deliberately steps.
- `FuncGenControlMixin.set_channel()` routes GUI-originated changes through the
  ramp; calibration/characterization services keep direct access.

---

## 6. Phase 5 — HV Conditioning

### 6.1 Why

Standard beamline practice for HV electrodes in vacuum: ramp up slowly, allow
micro-discharges to burn off surface asperities, back off on an event, retry.
Over several cycles the electrode holds progressively higher voltage. This is
the correct response to "a 0→5 kV step once killed an amp", and it produces a
conditioning curve that is genuinely useful data.

### 6.2 New service: `rbl/services/hv_conditioner.py`

Algorithm:

1. Ramp up (Phase 4) in increments toward a target.
2. At each level, dwell (default 30 s) while watching the current monitor and
   `OUT OF REGULATION` behaviour.
3. On an excursion above threshold: **back off** by N increments, log the event
   with voltage / current / pressure, dwell, then retry.
4. On M consecutive clean dwells, advance.
5. Terminate at target, or on repeated failure at the same level (that level is
   the conditioned ceiling), or on operator abort.
6. Emit a **conditioning curve**: achieved voltage vs. elapsed time, with every
   discharge event marked.

Must interoperate with the Phase 2 interlock — conditioning never overrides
the pressure lockout. Must log pressure at every step. Persist curves so
successive conditioning sessions can be compared; improvement across sessions
is the signal that conditioning is working.

---

## 7. Phase 6 — Regulation Detector

### 7.1 Why

In LIMIT mode the amplifier clamps current and **stops following its input
without shutting down**. The commanded voltage and the actual plate voltage
diverge silently. Any calibration point taken during such an event is invalid
and nothing currently detects it. The amplifier can also shut itself off
entirely (it has, once) and the app will happily keep commanding a dead channel.

The user has explicitly declined to wire the fault-monitor BNCs. This is the
software substitute.

### 7.2 New module: `rbl/hardware/regulation.py`

Pure math.

```python
def regulation_ratio(measured, commanded) -> float:
    """measured / commanded. ~1.0 is healthy."""

def classify(v_ratio: float, i_measured_ma: float, i_limit_ma: float,
             commanded_kv: float) -> tuple[str, str]:
    """-> (state, reason)

    Discriminating what went wrong needs BOTH monitors:

      V ~ 0 and I ~ 0                  -> "amp_off"
          amplifier tripped, HV enable open, or unplugged
      V low but nonzero, I at limit    -> "current_limited"
          still on; output is not following input; data is invalid
      V correct, I normal              -> "ok"
      commanded ~ 0                    -> "idle"  (ratio is undefined; do not
                                                   divide by it)
    """
```

Use the lock-in fundamental from `ac_metrics.py` for AC, and the window mean
for DC. One ratio, one threshold, both modes.

### 7.3 Three guards — omit any and it cries wolf

1. **Arm threshold.** Do not evaluate the ratio below a minimum commanded
   amplitude. The ratio is undefined near zero.
2. **Debounce.** Require N consecutive stream windows. Reuse the
   `CAL_TRIP_CONSEC_WINDOWS` pattern already proven in
   `CalibrationRunner._check_overcurrent`.
3. **Ramp coupling — the one that will bite you.** During a Phase 4 ramp the
   measured value *legitimately* lags the target. Compare against the **current
   ramp step**, not the final target, or suspend the detector until the ramp
   settles. Without this, every single ramp fires a false alarm. `_check_overcurrent`
   already solves the analogous problem with `CAL_TRIP_BLANK_WINDOWS`; follow
   that precedent.

### 7.4 Response

On `amp_off` or sustained `current_limited`:

1. Stop commanding that channel (zero it).
2. Raise a dialog listing the four front-panel fault reasons — **THERMAL LIMIT
   / CURRENT LIMIT-TRIP / OUT OF REGULATION / FAN FAULT** — and ask the user
   which LED is lit. The app cannot read the LED; the operator can.
3. Log the answer with full operating conditions: commanded kV, measured kV,
   peak and mean current, frequency, waveform, chamber pressure, timestamp,
   channel.
4. Append to a persistent **trip history** file.

That history is the highest-value artefact here: over months it becomes an
empirical map of where the real envelope is, built from actual failures rather
than predictions.

### 7.5 Calibration integration

`CalibrationRunner` must **mark any recorded row taken while regulation was
degraded**. Add a `regulation_state` column to the CSV. Do not silently drop
rows — flag them, so the user can decide.

---

## 8. Phase 7 — Dynamic Adjustment Panel

### 8.1 Why, and an honest caveat

The DYNAMIC ADJ front-panel pot compensates the amplifier's feedback network
for load capacitance — structurally the same problem as scope-probe
compensation. Mistuned high → the loop sees the output arriving late,
overdrives, and **overshoots and rings**. Mistuned low → it sees the output
arriving early, backs off, and gives a **slow corner with a settling tail**.

**Caveat to state in the UI:** at 1200 pF the rig is above the manual's stated
1 nF threshold, beyond which the manual says factory adjustment is required.
The pot may not be able to reach optimum. This panel should therefore be framed
as *characterizing and optimizing within the achievable range*, and its output
should be good enough to justify a factory-adjustment request if it comes to
that.

### 8.2 What is measurable, and what is not

Bandwidth reality check:

- The monitor BNCs are ~11 kHz → ~30 µs rise time.
- LabJack single-channel streaming reaches 100 kS/s → 10 µs/sample, so the
  **LabJack is not the limit; the monitor is.**
- **Not measurable:** the sub-10 µs edge corner and any ringing at the loop
  crossover. Do not build UI that pretends to show these.
- **Fully measurable:** flat-top behaviour from ~100 µs to hundreds of ms.
  This is where compensation error shows up as a slow exponential creep, and
  it is exactly the scope-probe-compensation picture. **The sign of the creep
  is the steering signal for which way to turn the pot.**
- **Immune to bandwidth:** the charge integral (§2.2), because a low-pass
  filter preserves pulse area.

### 8.3 Trial workflow

The pot change requires an HV off/on cycle, so this is an **A/B campaign**, not
a live meter. Design for that rather than fighting it.

1. Operator sets pot to a recorded position (mark the dial with a paint pen;
   record clock position). **Position entry is a required field before the
   trial can start.**
2. App runs an automated trial: 1 Hz square wave, **±500 V amplitude** (low, so
   a badly compensated setting cannot do harm — compensation optimum is
   amplitude-independent in the linear regime, so verify the winner at full
   amplitude only at the end), single-channel 100 kS/s on the voltage monitor,
   then a second pass on the current monitor.
3. Compute and store metrics.
4. Repeat for 5–8 pot positions.
5. Overlay all trials; declare a winner by figure of merit.

### 8.4 Metrics per trial

From the **voltage monitor**, on averaged edges:

- Overshoot % = `(V_peak − V_final) / V_final`
- Settling time to 1% and to 0.1%
- **Flat-top creep %** = `(V[t=+400 ms] − V[t=+10 ms]) / V_final` — **the sign
  of this is the pot direction hint**
- 10–90% rise time (monitor-limited, but comparable across trials)

From the **current monitor**:

- Peak current on the edge
- `∫I·dt` → C via `capacitance_from_charge()`
- Current tail duration (a long tail means the amplifier is still settling)

### 8.5 Two non-negotiable design points

1. **Store raw edge samples, not just derived metrics.** The user will change
   their mind about which metric matters. Re-running a whole campaign because
   only summaries were kept would be miserable. Store decimated raw traces
   alongside the metrics.
2. **The pot position is operator-entered and cannot be verified.** Treat it
   like `LoadCondition` — a recorded fact the app trusts but cannot check, and
   which must appear in every export.

### 8.6 Worth testing before building

Have the operator verify the HV off/on cycle is truly required: run a small AC
drive, turn the pot slowly, watch for a live response change. An analog trimmer
in a feedback network should not need a power cycle. It is plausible the
observed behaviour was the amplifier latching a fault that needed clearing —
which looks identical from the front panel but means live tuning is available
and this panel gets much simpler. **Confirm before committing to the A/B
workflow.**

---

## 9. Things not to do

- **Do not try to phase-align any SCPI command to a waveform zero crossing.**
  USB jitter exceeds a full period at these frequencies. It cannot work.
- **Do not use `:APPLy:` inside a ramp.** It restarts the phase generator.
- **Do not use `write_fast()` outside a ramp**, and always follow a ramp with
  one `get_error()`.
- **Do not let per-channel measured capacitance retroactively alter recorded
  measurements.** `CAL_LOAD_CAP_PF`'s existing docstring makes this promise;
  keep it.
- **Do not implement an amplifier swap test.** Operator-performed, manual.
- **Do not treat a missing vacuum reading as a good vacuum.**
- **Do not bypass `funcgen_safety.peak_status()` on any new path to the
  hardware.** Its module docstring exists because that check was once only
  behind one Apply button.
- **Do not build a second clamp path to the hardware.** `ac_max_peak_kv()`
  stays authoritative.

---

## 10. Build order & acceptance

| Phase | Feature | Gate to proceed |
|---|---|---|
| 1 | Load characterization | Modes A and C agree within 15%; four-channel comparison produced; DISCONNECTED vs ON_PLATES answered |
| 2 | Vacuum ↔ HV interlock | Blocks on high pressure and on stale readings; ramps down rather than hard-cutting; visible on Overview |
| 4 | Ramp engine | Reaches target within predicted time; retargets mid-ramp; abort bypasses; predicted peak current logged |
| 6 | Regulation detector | Zero false alarms across a full calibration sweep including all ramps |
| 3 | Envelope + raster calculator | Envelope reproduces §1.6 table using measured C; deflection matches the manual's worked example |
| 5 | HV conditioning | Produces a conditioning curve; backs off and retries on an event; respects the interlock |
| 7 | Dynamic adjustment panel | ≥ 5 trials stored with raw traces; creep sign gives a consistent direction hint |

Phases 4 and 6 are listed before 3 because 6 cannot be validated without 4, and
1/2/4/6 together form the safety and measurement substrate. Phase 3 is the
research deliverable and should not be built on an unmeasured envelope.

---

## Appendix A — Operator bench procedures (not software)

**A.1 Amplifier swap test.** Move the Y+ amplifier to an X channel and an X
amplifier to Y+. If the fault follows the amplifier, it is a bad amp. If it
stays with the Y axis, it is the feedthrough, in-vacuum wiring, or plates. Two
faults on the same axis across two different amplifiers is a load story, not an
amplifier story — this test is definitive.

**A.2 Capacitance decomposition.** Run Phase 1 Mode A with the HV cable
disconnected at the amplifier output, then again fully connected. The difference
is the external load. Resolves the ~1 nF discrepancy in §1.4. **Amplifier off,
and ground the centre conductor before touching it** — 5 kV on 1200 pF stores
15 mJ, which is not dangerous but will bite.

**A.3 Dynamic adjustment live-response check.** See §8.6.

---

## Appendix B — Existing code map

| Path | Role | Touched by |
|---|---|---|
| `rbl/hardware/funcgen_driver.py` | DG1022Z SCPI. `set_waveform` uses `APPLy`; `burst_on`/`set_burst` exist but no trigger source | 4 |
| `rbl/hardware/funcgen_safety.py` | `_AMP_GAIN = 1000`, `peak_status()` interlock | 2, 4 |
| `rbl/hardware/ac_metrics.py` | Single-bin DFT lock-in — **reuse, do not reimplement** | 1, 6, 7 |
| `rbl/hardware/amp_monitor.py` | Monitor V → kV / mA, `ma_unclamped`, `ma_to_monitor` | 1, 6, 7 |
| `rbl/services/amp_drive.py` | Clamped drive commands + single shutdown path | 4 |
| `rbl/services/calibration_runner.py` | `_check_overcurrent` / `_trip` — **precedent for debounce and blanking** | 6 |
| `rbl/config/calibration_config.py` | `CAL_LOAD_CAP_PF`, `ac_shape_k`, `ac_peak_current_ma`, `ac_max_peak_kv`, `LoadCondition` | 1, 3 |
| `rbl/config/labjack_stream_config.py` | Profiles: `AMP_PAIR` 50 kS/s × 2; single-channel 100 kS/s; `WAVEFORM` 12.5 kS/s × 8 | 1, 7 |
| `rbl/state/funcgen_control.py` | `set_channel`, `apply_all_channels`, `all_outputs_off` — **the chokepoint** | 2, 4 |
| `rbl/state/vacuum_link.py` | `VacuumLinkMixin`, `vacuum_changed` | 2 |
| `rbl/state/labjack_link.py` | `set_stream_profile`, `set_stream_pair`, `raw_window_ready` | 1, 7 |
| `rbl/state/beamline.py` | `Beamline(LabJackLink, FuncGenControl, MotorControl, VacuumLink, ScopeLink, QObject)` | 2, 4, 6 |
| `rbl/gui/calibration_tab.py` | `CalibrationTab` | 1, 7 |

---

## Appendix C — Source documents

- `Model EEL5000.20.100 Users Manual.pdf` — ratings §1-3, front panel §2-1..2-6
- `XY Steerer Manual.pdf` — deflection formula §IV, geometries §VII
- `DG1000Z User's Guide.pdf` — SCPI reference
- `VGC083` / `XGS600` manuals — gauge controllers
