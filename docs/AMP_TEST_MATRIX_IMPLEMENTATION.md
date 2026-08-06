# EEL5000 Amplifier Test Matrix — Implementation Instructions

**Audience:** Claude Sonnet, executing in Claude Code against the `RBL` repo.
**Author's intent:** turn the ten-group *Amplifier Testing Matrix* spreadsheet into
ten selectable, individually runnable test sections inside the existing **HV
Calibration** tab, each writing clean raw data to disk. **No analysis is
implemented here** — analysis lives in `processing/` and is written separately.

---

## 0. Rules of engagement — read before writing any code

1. **Phases are ordered and gated.** Do not start phase *N+1* until phase *N*'s
   `[OK]` self-test prints clean and you have committed. If a phase fails, stop
   and report — do not "work around" it into the next phase.
2. **New functionality goes in new files.** The only existing files you may edit
   are the four named in Phase 3, 10 and 11, and every edit there is anchored to
   a unique string given verbatim in this document. Do not reformat, re-order, or
   "tidy" anything you are not explicitly told to change.
3. **`raster_tool/` is stale. Never edit it.** If it does not exist, ignore this.
4. **Nothing may block the Qt event loop.** Read the module docstring of
   `rbl/services/calibration_runner.py` — the "WHY NOT A FOR-LOOP + time.sleep"
   section — before writing any runner code. Every state transition is a slot or
   a `QTimer` callback. No `time.sleep`, no `processEvents`, ever. This is not a
   style preference; a sleeping main thread means `window_ready` is *not
   delivered at all*, and the failure presents as "the LabJack froze".
5. **A clean `pyvisa` write is not confirmation.** All generator commands must go
   through `DG1022Z.set_waveform` / `output_on` / `output_off`, which already
   query `:SYSTem:ERRor?` after every write. Never issue a raw `.write()`.
6. **Safety constraints go in code, not in comments.** Every duration cap,
   current ceiling and load-condition gate in this document must be an assertion
   or an early-return in the runner, not a warning label in the GUI. The GUI may
   *also* check, but the runner is the authority — a future headless entry point
   must inherit the rule rather than re-derive it.
7. **Print everything to the terminal.** Every commanded setpoint, every profile
   switch, every abort reason, every SCPI failure. `[AMT]` is the prefix for this
   feature (the existing calibration feature uses `[CAL]`; keep them distinct).
8. **This feature never applies a correction factor.** Same rule as the existing
   calibration feature — see `rbl/config/calibration_config.py`'s module
   docstring. It logs and displays; it never corrects. Do not add an "Apply"
   control anywhere.

### Naming used throughout

| Term | Meaning |
|---|---|
| **group** | One of the ten sections, `G0`–`G9`, matching the spreadsheet's `Group` column |
| **test** | One row of the spreadsheet, e.g. `G1.3` |
| **level** | One value of that test's `Factor`, e.g. `3 kV` for factor `commanded kV` |
| **capture** | One contiguous raw acquisition on one AIN at one level |

---

## Phase 0 — Recon. Read only. Produce a written report. No edits.

Read, in full, and report back a short summary of each before proceeding:

```
rbl/config/calibration_config.py
rbl/config/labjack_stream_config.py
rbl/config/hardware_config.py          (the AMP_* block, lines ~220-265)
rbl/services/calibration_runner.py     (all 716 lines — especially the docstring)
rbl/services/calibration_writer.py
rbl/gui/calibration_tab.py
rbl/hardware/labjack_stream_worker.py  (the window_ready payload docstring, lines 24-52)
rbl/hardware/funcgen_safety.py
rbl/hardware/funcgen_driver.py         (set_waveform, output_on, output_off, get_state)
rbl/state/labjack_link.py              (set_stream_profile, set_stream_channel, _restart_stream_worker)
rbl/gui/app.py                         (lines 95-135 — the LabJack / calibration wiring block)
tests/test_calibration_runner.py
tests/test_calibration_app_wiring.py
tests/conftest.py and tests/payloads.py
```

**In your recon report, confirm each of these facts. If any is false, STOP and say so
— the rest of this document is built on them:**

- [ ] `STREAM_PROFILES` contains exactly `WAVEFORM`, `FULL`, `SINGLE_FAST`, `SINGLE_HIRES`.
- [ ] `SINGLE_FAST` = 1 channel @ 100 000 Hz, resolution index 1, `window_samples` = 10 000.
- [ ] `SINGLE_HIRES` = 1 channel @ 1 000 Hz, resolution index 8, `window_samples` = 100.
- [ ] `GUI_REFRESH_HZ` = 10, so **every** `window_ready` payload spans exactly 0.1 s of
      real time regardless of profile. All window-counting arithmetic depends on this.
- [ ] In a single-channel profile, `payload["channels"][ain]` is `None` for the seven
      amp AINs **not** being streamed, and for all four log-amp AINs.
- [ ] `AMP_CHANNEL_MAP` is `{"X+": {"voltage": "AIN13", "current": "AIN12"}, "X-": {...AIN11/AIN10},
      "Y+": {...AIN9/AIN8}, "Y-": {...AIN7/AIN6}}`.
- [ ] `funcgen_safety.CHANNEL_ROLE` is `{"A1": "X+", "A2": "X-", "B1": "Y+", "B2": "Y-"}`
      and `funcgen_safety._AMP_GAIN` is `1000.0`.
- [ ] `funcgen_driver.MAX_GEN_VOLTS` is `5.0` and `MAX_AMP_VPP` is `10.0`.
- [ ] `CalibrationRunner` currently hardcodes `stream_profile=CAL_PROFILE` (`"WAVEFORM"`)
      into every CSV row and never switches profiles itself — the *tab* emits
      `profile_change_requested`.
- [ ] `CalibrationWriter.CSV_COLUMNS` stores **only** `mean_v / std_v / min_v / max_v`
      per window batch. **There is no raw-sample path anywhere in the repo.**
- [ ] `AIN4` and `AIN5` are spare and are excluded from *every* stream profile, and
      `labjack_stream_config` has an import-time assert that every
      `channel_choices` entry is in `AMP_CHANNELS`.
- [ ] `rbl/gui/app.py` wires `amp_tab.single_channel_change_requested` to
      `beamline.set_stream_channel`, but `calibration_tab` has **no** equivalent signal.

**Commit nothing in Phase 0.** Report, then wait for the go-ahead before Phase 1.

---

## Phase 1 — `rbl/config/amp_test_config.py` (new file)

Constants only. No Qt, no hardware imports beyond config. Everything the runner
and GUI need to be safe, in one auditable place.

Create `rbl/config/amp_test_config.py` containing:

```python
"""
amp_test_config.py
Constants for the ten-group EEL5000 amplifier test matrix.

SCOPE
-----
This feature CAPTURES. It does not analyse. Every number produced here is
written to disk in a documented, self-describing layout; Allan deviation,
Welch PSD, capacitance back-solve, Bode magnitude and settling-time
extraction all live in processing/ and read these files after the fact.

Nothing in this module applies a correction to anything.
"""
```

Then, with a short comment block above each section:

**Output layout**

- `AMT_OUTPUT_DIR` — mirror the `sys.frozen` branch in
  `calibration_config.CAL_OUTPUT_DIR` exactly, but ending in
  `data/amp_tests` instead of `data/calibration`.
- `AMT_RAW_SUBDIR = "raw"`
- `AMT_SUMMARY_CSV = "summary.csv"`
- `AMT_METADATA_JSON = "metadata.json"`
- `AMT_FRONT_PANEL_JSON = "front_panel_state.json"` (lives at the top of
  `AMT_OUTPUT_DIR`, not inside a run folder — it is shared state, not run state)

**Raw-capture size guards** — these exist because `SINGLE_FAST` produces
100 000 samples/second/channel and a careless 3600 s spec would try to write
1.4 GB:

- `RAW_DTYPE = "float32"` — the T7's 16-bit ADC on the ±10 V range has far less
  than 24 bits of real information; float32 is lossless with respect to the
  instrument and halves the file size.
- `RAW_MAX_SAMPLES_PER_CAPTURE = 20_000_000` (= 200 s at `SINGLE_FAST`; ~80 MB)
- `RAW_MAX_BYTES_PER_RUN = 8 * 1024**3`
- `RAW_ESTIMATE_REFUSE = True` — the runner computes the projected byte total
  from the spec *before* commanding anything and refuses the run if it exceeds
  `RAW_MAX_BYTES_PER_RUN`.

**Profile-switch handshake** — see Phase 7 for why this exists:

- `PROFILE_SWITCH_TIMEOUT_S = 5.0`
- `PROFILE_SETTLE_WINDOWS = 3` — windows discarded after the first correct
  payload arrives, covering the stream restart transient.

**Trip / fault interlock:**

- `TRIP_MARGIN = 1.15` — a measured current above `trip_set_ma * TRIP_MARGIN` on
  a test that does *not* expect a trip is a fault, not data.
- `COLLAPSE_RATIO = 0.20` — measured |kV| below this fraction of a commanded
  |kV| ≥ 0.5 kV means the output has collapsed.
- `COLLAPSE_WINDOWS = 5` — consecutive windows required before declaring it, so
  a single settling window is not mistaken for a trip.
- `TRIP_BACKOFF_KV = 0.0` — where the driven channel is commanded the instant a
  trip is detected.

**Operating-envelope guard** — implements the "Operating envelope on plates"
table from the *Findings & Limits* sheet, in code:

- `LOAD_CAP_PF_DEFAULT = 130.0` — a **guess** until G3 measures it. The runner
  must read the measured value from front-panel/run state when one exists and
  fall back to this only with a printed warning.
- `AMP_MAX_KV = 5.0`, `AMP_MAX_MA_DC = 20.0` — re-import these from
  `hardware_config` rather than redefining them; assert they match at import.
- `POT_RANGE_MA = (0.5, 10.0)` — the front-panel CURRENT ADJUSTMENT range.
- A pure function:

```python
def peak_current_ma(freq_hz: float, peak_kv: float, load_pf: float) -> float:
    """I_pk = 2*pi*f*C*V_pk, in mA.  Capacitive load only."""
```

- A pure function:

```python
def envelope_ceiling_kv(freq_hz: float, trip_set_ma: float, load_pf: float) -> tuple[float, str]:
    """(max commandable peak kV, what binds it).

    Returns the lower of the amplifier's 5 kV rating and the ceiling imposed by
    the front-panel current pot.  The second element is one of
    "amp 5 kV limit" | "CURRENT POT" | "20 mA rating", matching the
    Findings & Limits sheet's 'What binds' column so the two can be compared
    directly.
    """
```

**Endurance duration policy** (G9) — Isaac's stated progression is 2 h on plates
now, 12 h on plates later:

- `ENDURANCE_ON_PLATES_MAX_H = 2.0` — **the single constant to raise when the 12 h
  runs are approved.** Document in a comment directly above it: raising this
  above `calibration_config.DRIFT_MAX_ATTENDED_H` also arms the extra
  acknowledgement in the G9 checklist (Phase 10), and that is intentional.
- `ENDURANCE_PROBE_INTERVAL_S = 1200.0` (20 min, per matrix row 35)
- `ENDURANCE_LOG_INTERVAL_S = 1.0`

**Import-time asserts** (fail loudly, do not silently ship a bad config):

```python
assert AMP_MAX_KV <= MAX_GEN_VOLTS
assert POT_RANGE_MA[0] < POT_RANGE_MA[1] <= AMP_MAX_MA_DC
assert RAW_MAX_SAMPLES_PER_CAPTURE * 4 < RAW_MAX_BYTES_PER_RUN
assert ENDURANCE_ON_PLATES_MAX_H >= 0.0
```

**`[OK]` self-test** under `if __name__ == "__main__":` — must print and assert:

- `peak_current_ma(1000, 5.0, 130)` ≈ `4.084` mA (matches the Findings sheet's
  1000 Hz row to 3 decimal places).
- `peak_current_ma(2000, 5.0, 130)` ≈ `8.168` mA.
- `peak_current_ma(10000, 5.0, 130)` ≈ `40.841` mA.
- `envelope_ceiling_kv(2000, 10.0, 130)` returns `(5.0, "amp 5 kV limit")`.
- `envelope_ceiling_kv(3000, 10.0, 130)` returns `(≈4.0809, "CURRENT POT")`.
- `envelope_ceiling_kv(5000, 10.0, 130)` returns `(≈2.4485, "CURRENT POT")`.
- `envelope_ceiling_kv(10000, 10.0, 130)` returns `(≈1.2243, "CURRENT POT")`.
- `envelope_ceiling_kv(2000, 0.5, 130)` — a pot at its minimum, at the intended
  2 kHz fast axis — returns `(≈0.306, "CURRENT POT")`. This is the Findings
  sheet's "if it is currently set low, your operating point is not reachable"
  case, and it is the single most important value this function produces.
- `envelope_ceiling_kv(100, 0.5, 130)` returns `(5.0, "amp 5 kV limit")` — at
  100 Hz even a 0.5 mA pot allows 6.1 kV, so the amplifier's own rating binds.
  Include this case explicitly; it is the one that catches an inverted comparison.

All six of the numbers above were checked against the *Findings & Limits* sheet's
"Operating envelope on plates" table and agree to four decimal places. If your
implementation disagrees with any of them, your implementation is wrong.

Run it: `python -m rbl.config.amp_test_config`

**Commit:** `feat(amp-test): constants, envelope guard and raw-capture caps for the test matrix`

---

## Phase 2 — `rbl/config/amp_test_matrix.py` (new file)

The registry. This is the single source of truth for *what tests exist*; the
runner and the GUI both read it and neither hardcodes a test anywhere.

### 2.1 The data model

```python
class RawMode(Enum):
    NONE      = "none"       # window statistics only, no raw samples on disk
    FULL      = "full"       # every sample written to .npz
    DECIMATED = "decimated"  # window statistics at GUI_REFRESH_HZ, for long runs

class DriveSet(Enum):
    NONE        = "none"          # amplifiers not driven at all
    SINGLE      = "single"        # exactly one amp, named by the spec
    EACH        = "each"          # one at a time, iterating all four
    ALL         = "all"           # all four simultaneously
    SWAPPED     = "swapped"       # operator has re-plugged; see checklist

@dataclass(frozen=True)
class TestSpec:
    test_id:         str            # "G1.3"
    group_num:       int
    group_name:      str
    title:           str            # the spreadsheet's "Test" cell
    proves:          str            # the spreadsheet's "What it proves" cell, verbatim
    drive:           DriveSet
    amps:            tuple          # explicit amp labels when drive is SINGLE/SWAPPED
    profile:         str            # a key of STREAM_PROFILES, or "" for no-hardware tests
    target_ains:     tuple          # capture targets; for single-channel profiles these
                                    # are run as SEQUENTIAL sub-captures, one per AIN
    factor:          str            # the spreadsheet's "Factor" cell
    levels:          tuple          # machine-readable levels; see 2.2
    level_labels:    tuple          # human labels, same length as levels
    load_condition:  str            # "ON_PLATES" | "ANALYSIS_ONLY" | "ON_PLATES_NO_HV"
    raw_mode:        RawMode
    expect_trip:     bool           # True disarms the trip interlock's abort and makes
                                    # the trip itself the measurement
    operator_paced:  bool           # True => the runner pauses between levels for a
                                    # physical action (pot turn, re-plug, DMM read)
    hold_s:          float          # seconds held at each level
    settle_s:        float          # discarded after each level change
    notes:           str
```

Add a helper on the dataclass:

```python
def estimated_raw_bytes(self) -> int:
    """Projected .npz payload for this whole test. 0 when raw_mode is not FULL."""
```
using `window_samples(profile) * GUI_REFRESH_HZ * hold_s * len(levels) * len(target_ains) * 4`.

### 2.2 Level encoding

`levels` is always a tuple of *machine-usable* values, and its meaning depends on
`factor`:

| `factor` | element type | meaning |
|---|---|---|
| `"commanded kV"` | `float` | DC setpoint, kV |
| `"peak kV"` | `float` | AC peak amplitude, kV |
| `"frequency"` | `float` | Hz, at a fixed peak given in `notes` |
| `"step size"` | `float` | kV, stepped from 0 |
| `"time"` | `float` | hold seconds (a single-element tuple) |
| `"settle"` | `float` | settle seconds |
| `"pot position"` / `"mode"` / `"generator"` / `"command shape"` / `"trip setting"` | `str` | operator-paced label; the runner prompts and records, it does not command |
| `"channel"` | `str` | an AIN name; the runner iterates targets rather than setpoints |

`level_labels` always parallels `levels` and is what the GUI and CSV show.

### 2.3 The matrix itself

Define `TEST_MATRIX: tuple[TestSpec, ...]` with **every** row below. Copy the
`proves` text verbatim from the spreadsheet where quoted; it is the record of why
each test exists and must survive into the metadata.

> **Group 0 does not appear in the spreadsheet's Test Matrix sheet but is
> referenced repeatedly by the Findings sheet ("Group 0 fixes that permanently,
> and costs half an hour"). It is created here.**

**G0 — Front-panel state** *(no hardware driven; blocking prerequisite for G1–G9)*

| id | title | drive | profile | factor | levels | load |
|---|---|---|---|---|---|---|
| G0.1 | Record CURRENT ADJUSTMENT, LIMIT/TRIP, DYNAMIC ADJUSTMENT and LOCAL/REMOTE for all four amplifiers | NONE | `""` | — | — | ANALYSIS_ONLY |

`proves`: *"None of the four front-panel controls can be read back over any
interface, and none was recorded for the 08-04 runs. They have been uncontrolled
variables in every measurement taken so far."*

**G1 — Y+ instability diagnosis** — spreadsheet rows 5–9.

| id | title | drive | amps | profile | target_ains | factor | levels | raw |
|---|---|---|---|---|---|---|---|---|
| G1.1 | Raw waveform capture on Y+ at a fixed setpoint | SINGLE | `("Y+",)` | SINGLE_FAST | `("AIN9","AIN8")` | commanded kV | `(0.0, 1.0, 3.0)` | FULL |
| G1.2 | Same capture on X+ as the control | SINGLE | `("X+",)` | SINGLE_FAST | `("AIN13","AIN12")` | commanded kV | `(0.0, 1.0, 3.0)` | FULL |
| G1.3 | Capture at commanded ZERO volts specifically | SINGLE | `("Y+",)` | SINGLE_FAST | `("AIN9","AIN8")` | time | `(60.0,)` | FULL |
| G1.4 | Sweep the DYNAMIC ADJUSTMENT pot on Y+, re-capture | SINGLE | `("Y+",)` | SINGLE_FAST | `("AIN9","AIN8")` | pot position | `("original","swept")` | FULL — **operator_paced** |
| G1.5 | AMPLIFIER SWAP: drive the Y+ cable from the X+ amplifier | SWAPPED | `("Y+","X+")` | WAVEFORM | all 8 | commanded kV | `-5.0 … +5.0` step `1.0` | NONE — **operator_paced** |

`hold_s = 10.0` for G1.1/G1.2; `hold_s = 60.0` for G1.3.

**Why two target AINs per capture:** in `SINGLE_FAST` only one channel is
streamed, so voltage and current cannot be seen at once. The Findings sheet
identifies the anomaly on *both* — 0.641 mA at 0 V on the current monitor **and**
355 V within-window peak-to-peak on the voltage monitor. The runner therefore
performs the same level twice, once per target, back to back.

**G2 — Trip threshold & inrush** — rows 10–14.

| id | title | drive | profile | target_ains | factor | levels | raw | expect_trip |
|---|---|---|---|---|---|---|---|---|
| G2.1 | Measure the actual trip current per amp | EACH | WAVEFORM | all 8 | commanded kV | slow ramp — see below | NONE | **True** |
| G2.2 | Set all four pots to a common documented value, re-verify | EACH | WAVEFORM | all 8 | trip setting | `("common value",)` | NONE — operator_paced | False |
| G2.3 | Largest DC step that does NOT trip, at fixed settle | EACH | SINGLE_FAST | **current monitor only** | step size | `(0.1, 0.2, 0.5, 1.0)` | FULL | False |
| G2.4 | Ramped command vs stepped command | EACH | SINGLE_FAST | **current monitor only** | command shape | `("step","ramped")` | FULL | False |
| G2.5 | Trip recovery behaviour in LIMIT vs TRIP mode | SINGLE `("Y-","X+")` | WAVEFORM | all 8 | mode | `("LIMIT","TRIP")` | NONE — operator_paced | True |

For **G2.1** encode the ramp in `levels` as an explicit ascending tuple
`(0.0, 0.1, 0.2, … 5.0)` with `settle_s = 1.0`; "slow ramp to trip" is a ladder
the runner walks until the interlock fires, not a continuous sweep. Record the
current monitor reading at the level where it fires, then back off to 0 kV.

For **G2.3 / G2.4** the target is the **current** monitor (`AIN12/10/8/6`), not
the voltage monitor. Inrush is a current phenomenon, and — critically — the trip
interlock can only be armed in a single-channel profile when the streamed channel
*is* a current monitor. See the safety note in Phase 7.

**G3 — Load capacitance** — row 15.

| id | title | drive | profile | factor | levels | raw |
|---|---|---|---|---|---|---|
| G3.1 | Back-solve capacitance from the AC current monitor | EACH | WAVEFORM | frequency | `(100.0, 500.0, 1000.0)` | NONE |

`notes`: fixed peak amplitude of 2.0 kV at every frequency; the CSV records
`peak` and `rms` of both monitors so `C = I_pk / (2*pi*f*V_pk)` is recoverable in
`processing/`. Do **not** compute C here.

**G4 — Noise floor** — rows 16–19.

| id | title | drive | profile | factor | levels | raw | load |
|---|---|---|---|---|---|---|---|
| G4.1 | Wiring + DC floor, amplifiers POWERED OFF | NONE | WAVEFORM | time | `(300.0,)` | DECIMATED | **ON_PLATES_NO_HV** |
| G4.2 | Quiescent noise, amps ON at commanded 0 V | ALL | WAVEFORM | time | `(300.0,)` | DECIMATED | ON_PLATES |
| G4.3 | Allan deviation record at 0 V | ALL | WAVEFORM | time | `(3600.0,)` | **DECIMATED** | ON_PLATES |
| G4.4 | Noise spectrum (Welch PSD) at 0 V | ALL | SINGLE_FAST | channel | all 8 AINs | FULL | ON_PLATES |

**G4.3 must be `DECIMATED`, not `FULL`.** One hour of `WAVEFORM` raw is
12 500 × 8 × 3600 = 360 M samples ≈ 1.4 GB, and Allan deviation out to τ = 1000 s
is fully resolved by the 10 Hz window statistics the existing CSV path already
writes. Capturing raw here buys nothing and risks filling the disk mid-run.

**G4.4** `hold_s = 10.0` per channel — 10 s at 100 kS/s gives 0.1 Hz resolution,
ample to separate a 60 Hz line from its 180 Hz third harmonic.

**G4.1** is the only test whose load condition is `ON_PLATES_NO_HV`. Its
checklist must require an explicit acknowledgement that all four amplifier
mains switches are OFF, and the runner must refuse to command any generator
channel for the duration.

**G5 — DC transfer function** — rows 20–22.

| id | title | drive | profile | factor | levels | raw |
|---|---|---|---|---|---|---|
| G5.1 | Full ladder ±5 kV, 0.2 kV steps, 3 pass types | EACH | WAVEFORM | commanded kV | delegated | NONE |
| G5.2 | Repeat once the Y+ fault is resolved | EACH | WAVEFORM | commanded kV | delegated | NONE |
| G5.3 | Settle-time confirmation, 0.5 s vs 5 s | EACH | WAVEFORM | settle | `(0.5, 5.0)` | NONE |

**G5.1 and G5.2 delegate to the existing, working `CalibrationRunner.start_sweep()`.**
They are the same measurement; do not reimplement the ladder. Mark them in the
registry with `notes="delegate:CalibrationRunner.start_sweep"` and have the tab
dispatch accordingly (Phase 9). G5.2 differs from G5.1 only in the operator note
and in when it is run.

**G6 — Chain isolation** — rows 23–25.

| id | title | drive | profile | factor | levels | load |
|---|---|---|---|---|---|---|
| G6.1 | DMM at each function generator output | NONE | `""` | commanded V | `-5.0 … +5.0` step `1.0` | **ANALYSIS_ONLY** |
| G6.2 | LabJack AIN4 reads the generator output, simultaneous with the DMM | NONE | see decision | commanded V | `-5.0 … +5.0` step `1.0` | ANALYSIS_ONLY |
| G6.3 | GENERATOR SWAP: drive the X amps from the Y generator | EACH | WAVEFORM | generator | `("A/B swapped",)` | ON_PLATES — operator_paced |

**G6.1** commands the generators at low voltage with **the amplifiers
disconnected from the loop entirely** and presents an editable grid for the
operator to type DMM readings into. No high voltage anywhere. The commanded
values *are* generator volts here, not kV — the spreadsheet's `-5..+5 V, 1 V
steps`. Do not multiply by `_AMP_GAIN`.

**G6.2 requires a decision — see Appendix A, item 1.** `AIN4` is a spare and is
excluded from every stream profile, and `labjack_stream_config` asserts at import
that every single-channel choice is an amp monitor. Implement the registry entry
with `profile=""` and `notes="BLOCKED: see Appendix A item 1"` and make the GUI
show it greyed out with that reason. Do **not** relax the assert on your own
initiative.

**G7 — AC characterisation** — rows 26–31.

| id | title | drive | profile | factor | levels | raw | expect_trip |
|---|---|---|---|---|---|---|---|
| G7.1 | Amplitude ladder at 100 Hz | EACH | WAVEFORM | peak kV | `0.5 … 5.0` step `0.5` | NONE | False |
| G7.2 | Amplitude ladder at 1 kHz | EACH | WAVEFORM | peak kV | `0.5 … 5.0` step `0.5` | NONE | False |
| G7.3 | Amplitude ladder at 2 kHz | EACH | WAVEFORM | peak kV | `0.5 … 5.0` step `0.5` | NONE | False |
| G7.4 | Frequency ladder at 2 kV peak | EACH | WAVEFORM | frequency | `(1,2,5,10,20,50,100,200,500,1000,2000)` | NONE | False |
| G7.5 | Frequency ladder above 3 kHz, single channel | EACH | SINGLE_FAST | frequency | `(3000.0, 6000.0, 10000.0)` | FULL | False |
| G7.6 | Trip-limited operating envelope | EACH | SINGLE_FAST | frequency | `(100,500,1000,2000,5000,10000)` | NONE | **True** |

**G7.6** is a nested walk: at each frequency, amplitude ascends from 0.5 kV in
0.25 kV steps until the interlock fires or the envelope ceiling is reached.
Record the (frequency, amplitude) pair at which it fires.

**G7.5 target** is the **voltage** monitor (magnitude response is the
measurement). Note in `notes` that `WAVEFORM` gives only ~4 samples/cycle at
3 kHz, which is why this row leaves the multi-channel profile.

**G8 — Step response** — rows 32–33.

| id | title | drive | profile | target | factor | levels | raw |
|---|---|---|---|---|---|---|---|
| G8.1 | Small-signal step, ±0.5 kV, square-wave edge method | EACH | SINGLE_FAST | voltage monitor | step size | `(0.5,)` | FULL |
| G8.2 | Large-signal step, as large as the trip setting allows | EACH | SINGLE_FAST | voltage monitor | step size | `("largest non-tripping",)` | FULL |

Both drive a **5 Hz square wave**, not a sequence of SCPI setpoints — a 30 s hold
gives ~150 hardware-timed edges with no SCPI latency anywhere in the measurement.
`hold_s = 30.0`. Put the 5 Hz in `notes` and in a named constant, not a literal.

**G8.2's level** is resolved at run time from G2.3's recorded result if one exists
in the run folder, and is otherwise operator-entered. Do not guess it.

**G9 — Endurance** — rows 34–36.

| id | title | drive | profile | factor | levels | raw |
|---|---|---|---|---|---|---|
| G9.1 | 2 h AC hold at the operating point, attended | ALL | WAVEFORM | time | `(7200.0,)` | DECIMATED |
| G9.2 | Interleaved gain probes every 20 min during the hold | ALL | WAVEFORM | probe interval | `(1200.0,)` | DECIMATED |
| G9.3 | Post-endurance repeat of the G5 ladder | EACH | WAVEFORM | commanded kV | delegated | NONE |

**G9.1 and G9.2 are one run, not two.** G9.2 is a modifier: the hold is
interrupted every `ENDURANCE_PROBE_INTERVAL_S` by a short gain probe, and the
probe rows are tagged `pass_type="probe"` in the CSV so they separate cleanly
from the hold rows. Register G9.2 as a boolean option on G9.1 rather than as a
separately startable test, and say so in its `notes`.

**G9.3** delegates to `CalibrationRunner.start_sweep()` exactly like G5.1.

### 2.4 Registry helpers

```python
def groups() -> list[tuple[int, str]]          # ordered, deduplicated (num, name)
def tests_in_group(group_num: int) -> list[TestSpec]
def by_id(test_id: str) -> TestSpec            # raises KeyError with a clear message
def total_estimated_bytes(specs) -> int
```

### 2.5 `[OK]` self-test

Assert and print:

- Every `test_id` is unique and matches `^G[0-9]\.[0-9]+$`.
- Every `profile` is either `""` or a key of `STREAM_PROFILES`.
- Every AIN in every `target_ains` is a key of `AIN_TO_AMP`, **except** where the
  spec's profile is `""`.
- Every spec whose `profile` is single-channel has `len(target_ains) >= 1`, and
  every spec whose profile is multi-channel and whose `raw_mode is RawMode.FULL`
  has an estimate under `RAW_MAX_SAMPLES_PER_CAPTURE * 4` bytes per capture.
- `len(levels) == len(level_labels)` for every spec.
- Every `DriveSet.SINGLE` / `SWAPPED` spec names its amps and every named amp is
  in `AMP_LABELS`.
- Every spec with `expect_trip=True` has `load_condition == "ON_PLATES"`.
- Groups 0–9 are all present, and the group count is exactly 10.
- Print a summary table: group, test count, total estimated raw MB.
- Print the grand total estimated raw bytes and assert it is under
  `RAW_MAX_BYTES_PER_RUN` when summed per-group (not for the whole matrix — no
  one runs all ten groups in one sitting).

Run it: `python -m rbl.config.amp_test_matrix`

**Commit:** `feat(amp-test): registry of all ten test groups from the amplifier testing matrix`

---

## Phase 3 — `rbl/services/amp_drive.py` (new) + surgical delegation

The existing `CalibrationRunner` already contains carefully-reviewed code for the
dangerous part of this feature: clamping, `:SYSTem:ERRor?`-checked writes, the
unconditional zero-then-off shutdown, the restore-with-output-left-off rule, and
the `atexit` hook. **Do not write a second copy of that.** Extract it once and
have both runners use it.

### 3.1 Create `rbl/services/amp_drive.py`

A plain (non-Qt) class. Every method is synchronous and short — a single SCPI
write is milliseconds, which is safe inside a slot; nothing here sleeps or loops
over setpoints.

```python
class AmpDrive:
    """Owns no hardware. Wraps the {amp_label: (DG1022Z, channel)} map with
    clamped, error-checked, terminal-logged commands and one shutdown path."""

    def __init__(self, funcgen_map: dict, max_kv: float = AMP_MAX_KV,
                 log_prefix: str = "[AMT]")

    def snapshot_all(self) -> dict                       # label -> get_state(channel)
    def command_dc(self, label: str, kv: float) -> None
    def command_sine(self, label: str, peak_kv: float, freq_hz: float) -> None
    def command_square(self, label: str, peak_kv: float, freq_hz: float) -> None
    def zero_all(self) -> None                           # DC 0 V on all four, outputs left as-is
    def outputs_off_all(self) -> None
    def zero_and_off_all(self) -> None                   # the shutdown primitive
    def restore_all(self, snapshot: dict) -> None        # output stays OFF regardless
    def register_atexit(self) -> None                    # zero + off only, never restore
```

Rules this class must enforce internally:

- Clamp `kv` to `±max_kv` and `peak_kv` to `[0, max_kv]` **before** computing
  generator volts, independently of `set_waveform`'s own clamp.
- `gen_v = kv * 1000.0 / _AMP_GAIN` for DC;
  `gen_vpp = peak_kv * 2.0 * 1000.0 / _AMP_GAIN` for AC. Import `_AMP_GAIN` from
  `funcgen_safety`; do not hardcode 1000.
- Before any AC command, assert `gen_vpp <= MAX_AMP_VPP`; print and clamp if not.
- `set_waveform` returns a non-empty warning string when it clamped — print it,
  `log.warning` it, and never swallow it.
- Every method prints one line: `[AMT] X+ ch1: SIN 1000.0 Hz 4.0000 Vpp (2.0000 kV peak)`.
- `zero_and_off_all` is best-effort **per channel** — a failure on one channel
  must not prevent the other three from being zeroed. Wrap each in its own try.
- `register_atexit` registers a function that never raises.

`command_square` is new (G8 needs it). `set_waveform`'s shape mapping accepts
`"Square"` → `APPLy:SQUare`. Verify the exact accepted spelling against
`funcgen_driver.set_waveform`'s docstring during Phase 0 and use it verbatim;
the existing runner passes `"DC"` and `"SIN"`, so confirm whether the mapping is
case-sensitive before assuming `"SQU"` works.

### 3.2 Surgically delegate `CalibrationRunner` to `AmpDrive`

Three anchored edits in `rbl/services/calibration_runner.py`. Each anchor string
below appears **exactly once** in the file — verified. Do not change anything else
in this file.

**Edit 1 — construct the drive.** Anchor:

```
        atexit.register(self._atexit_shutdown)
```

Insert immediately **above** it:

```python
        self._drive = AmpDrive(funcgen_map, max_kv=CAL_MAX_KV, log_prefix="[CAL]")
```

and add the import alongside the existing `rbl.services` imports.

**Edit 2 — delegate DC commanding.** Anchor:

```
    def _command_channel(self, amp_label: str, value_kv: float):
```

Replace that method's **body** (not its signature, not its docstring in
`_command_channel_ac`) with a call to `self._drive.command_dc(amp_label, value_kv)`
wrapped in the existing try/except that re-raises after `self._print_err`.
Do the same for `_command_channel_ac` → `self._drive.command_sine(amp_label, peak_kv, CAL_AC_FREQ_HZ)`.

**Edit 3 — delegate shutdown.** Anchors:

```
    def _shutdown(self, restore: bool):
```
```
    def _atexit_shutdown(self):
```

In `_shutdown`, replace the per-channel zero/off loop and the restore loop with
`self._drive.zero_and_off_all()` and `self._drive.restore_all(self._orig_state)`.
**Keep** the `self._settle_timer.stop()`, `self._watchdog_timer.stop()` and
writer-close logic exactly as they are. In `_atexit_shutdown`, replace the body
with `self._drive.zero_and_off_all()`.

### 3.3 Regression gate — this is the whole point of doing it this way

```
pytest tests/test_calibration_runner.py tests/test_calibration_writer.py \
       tests/test_calibration_config.py tests/test_calibration_app_wiring.py -v
```

**Every one of these must still pass, unchanged.** If any fails, the refactor is
wrong — revert it and report, do not edit the tests to match. These tests are the
proof that the extraction preserved behaviour.

Also add `tests/test_amp_drive.py` with a `FakeGen` (record calls, return `""`
from `set_waveform`) asserting:

- `command_dc("X+", 9.0)` clamps to 5.0 kV → 5.0 generator volts.
- `command_sine("X+", 5.0, 1000)` produces 10.0 Vpp, exactly `MAX_AMP_VPP`.
- `command_sine("X+", 6.0, 1000)` clamps rather than exceeding `MAX_AMP_VPP`.
- `zero_and_off_all` calls `output_off` on all four channels **even when the
  second channel's `set_waveform` raises**.
- `restore_all` never calls `output_on`.

**Commit:** `refactor(amp): extract AmpDrive from CalibrationRunner; behaviour-preserving`

---

## Phase 4 — `rbl/services/raw_capture_writer.py` (new file)

The gap the Findings sheet names directly: *"The CSV stores only mean, std, min
and max per 0.1 s window — not the waveform. Whatever Y+ is doing between samples
is not in the file."* This closes it.

```python
class RawCaptureWriter:
    """One .npz per (test, level, target AIN). Accumulates window arrays in
    memory, writes once at capture close."""

    def __init__(self, run_dir: Path, test_id: str)

    def open_capture(self, level_label: str, ain: str, seq: int,
                     sample_period: float, metadata: dict) -> None
    def append(self, samples: np.ndarray) -> None   # one window's waveform array
    def close_capture(self) -> str                  # returns the written path
    def abort_capture(self) -> None                 # discards, deletes nothing
    @property
    def bytes_written(self) -> int
```

Requirements:

- Filename: `{test_id}__{level_label_slug}__{ain}__{seq:03d}.npz`. Slugify the
  level label (lower, non-alphanumerics → `_`) so `"3 kV"` → `3_kv`.
- Store with `np.savez_compressed`, arrays cast to `RAW_DTYPE`, keys:
  `samples`, plus a `meta` 0-d object array holding the metadata dict.
  Metadata **must** include: `test_id`, `level_label`, `level_value`, `ain`,
  `amp_label`, `kind`, `profile`, `sample_period`, `sample_rate_hz`,
  `n_samples`, `t0_iso`, `commanded_kv`, `driven_amp`, `run_id`,
  `front_panel_hash`.
- **Hard cap:** `append` refuses and raises `RawCaptureOverflow` once the
  accumulated sample count would exceed `RAW_MAX_SAMPLES_PER_CAPTURE`. The runner
  catches this, closes the capture cleanly, records a truncation flag in the CSV,
  and continues — a truncated capture is data; a crashed run is not.
- Accumulate in a Python list of arrays and `np.concatenate` once at close. Do
  **not** `np.append` in a loop; that reallocates the whole buffer every window
  and will visibly stall the GUI within a few seconds at 100 kS/s.
- `close_capture` on an empty capture writes nothing and returns `""`.

**`[OK]` self-test:** build a `RawCaptureWriter` against `tempfile.mkdtemp()`,
append 12 synthetic windows of 10 000 samples, close, reload with `np.load`, and
assert the round-tripped array is bit-identical after the float32 cast, that
`meta` survives, that the filename slug is right, and that appending past the cap
raises `RawCaptureOverflow` while leaving the already-appended data recoverable.

Run it: `python -m rbl.services.raw_capture_writer`

**Commit:** `feat(amp-test): raw .npz capture sink with per-capture size cap`

---

## Phase 5 — `rbl/services/amp_test_writer.py` (new file)

Run-folder layout and the summary CSV.

```
data/amp_tests/
├── front_panel_state.json
└── amt_20260806T143012__g1_y_plus_instability/
    ├── metadata.json
    ├── summary.csv
    ├── notes.md
    └── raw/
        ├── G1.1__0_kv__AIN9__000.npz
        ├── G1.1__0_kv__AIN8__001.npz
        └── ...
```

### 5.1 CSV schema — superset, not replacement

`AMT_CSV_COLUMNS` must begin with **`calibration_writer.CSV_COLUMNS` verbatim, in
the same order**, then append:

```
test_id, group_num, group_name, factor, level_label, level_value,
target_ain, raw_file, raw_truncated, trip_flag, expect_trip,
front_panel_hash, peak_v, rms_v, sample_rate_hz, operator_paced_ack
```

Import the base list rather than retyping it:
`from rbl.services.calibration_writer import CSV_COLUMNS as _BASE_COLUMNS`.
Assert at import that `AMT_CSV_COLUMNS[:len(_BASE_COLUMNS)] == _BASE_COLUMNS`.

**Why this matters:** `processing/analyze_calibration.py` already reads the base
schema. A strict superset in the same order means it keeps working on these files
unchanged, and any new analysis script can read both.

`peak_v` and `rms_v` come straight from the stream payload's per-window `peak`
and `rms` entries (already computed in `labjack_stream_worker._build_payload`) —
G3 and G7 need them and the base schema has neither.

### 5.2 The writer

```python
class AmpTestWriter:
    def __init__(self, spec: TestSpec, front_panel: dict, metadata: dict = None)
    @property
    def run_dir(self) -> Path
    def raw_writer(self) -> RawCaptureWriter
    def write_row(self, row: dict) -> None      # flushes every row, same as CalibrationWriter
    def update_metadata(self, **fields) -> None
    def write_note(self, text: str) -> None     # appends a timestamped line to notes.md
    def close(self) -> str
```

- `run_id = time.strftime("amt_%Y%m%dT%H%M%S") + "__" + slug(spec.test_id + "_" + spec.title)`,
  truncated to a sane path length.
- `metadata.json` must carry, at minimum: the full `TestSpec` as a dict (including
  its verbatim `proves` text), `front_panel_state` and its hash,
  `config_snapshot()` from `calibration_writer`, `git_commit_hash()`,
  `load_condition`, `operator_note`, the funcgen `get_state` snapshot,
  `stream_profile`, `labjack_serial`, `start_timestamp_iso`, `end_timestamp_iso`,
  and `aborted` + `abort_reason`.
- Flush after every row — a two-hour G9 run that dies at 110 minutes must leave
  110 minutes of usable CSV on disk. Same rule and same reason as
  `CalibrationWriter`.

**`[OK]` self-test:** write a run to a temp dir with two synthetic rows, close,
then assert the folder layout exists, the CSV header equals `AMT_CSV_COLUMNS`,
the first 20 columns equal the base schema, `metadata.json` parses and contains
`proves`, and `close()` is idempotent.

**Commit:** `feat(amp-test): run-folder writer with a strict superset of the calibration CSV schema`

---

## Phase 6 — `rbl/services/front_panel_state.py` + the G0 dialog

Group 0 exists because four physical controls have been uncontrolled variables in
every measurement taken so far, and none of them can be read back over any
interface. The only fix is to make a human record them and to make every
subsequent run refuse to start without that record.

### 6.1 `rbl/services/front_panel_state.py` (new file)

```python
FRONT_PANEL_FIELDS = {
    "current_adjustment_ma": float,   # 0.5 - 10.0, the CURRENT ADJUSTMENT pot
    "limit_or_trip":         str,     # "LIMIT" | "TRIP"
    "dynamic_adjustment":    str,     # free text: dial position / turns from stop
    "local_or_remote":       str,     # "LOCAL" | "REMOTE"
}

def load() -> dict | None            # None when never recorded
def save(state: dict) -> None        # writes AMT_FRONT_PANEL_JSON atomically
def hash_state(state: dict) -> str   # short stable sha256 prefix, stamped into every CSV row
def is_stale(state: dict, max_age_days: float = 30.0) -> tuple[bool, str]
def validate(state: dict) -> list[str]   # human-readable problems; empty list == valid
```

Stored shape:

```json
{
  "recorded_iso": "2026-08-06T14:30:12+00:00",
  "recorded_by": "",
  "amps": {
    "X+": {"current_adjustment_ma": 10.0, "limit_or_trip": "TRIP",
           "dynamic_adjustment": "3.5 turns from CCW stop", "local_or_remote": "REMOTE"},
    "X-": { ... }, "Y+": { ... }, "Y-": { ... }
  },
  "load_cap_pf_measured": null,
  "notes": ""
}
```

`load_cap_pf_measured` is filled in by hand after G3 and, when non-null,
overrides `LOAD_CAP_PF_DEFAULT` everywhere the envelope guard is evaluated. Print
a warning on every run that falls back to the default.

`validate` must reject a `current_adjustment_ma` outside `POT_RANGE_MA`, a
`limit_or_trip` that is not one of the two literals, and any missing amp.

### 6.2 The G0 dialog — `_FrontPanelDialog` in the tab (Phase 10)

A modal 4×4 grid, one row per amp, prefilled from `load()` when present. On
accept it validates, saves, and returns. It is reachable two ways: from the G0
entry in the test list, and automatically when a G1–G9 run is started with no
valid state on file.

### 6.3 The gate — enforce it in the runner, not only the dialog

`AmpTestRunner.start(spec)` returns early with an error signal when
`front_panel_state.load()` is `None` or `validate()` returns problems, for every
spec whose `group_num > 0`. Staleness is a **warning**, not a block — print it,
record it in metadata, and let the run proceed.

**`[OK]` self-test:** round-trip a state through `save`/`load` in a temp dir,
assert `hash_state` is stable across two calls and changes when one pot value
changes by 0.1, assert `validate` catches an out-of-range pot and a missing amp,
and assert `is_stale` flips at the boundary.

**Commit:** `feat(amp-test): front-panel state capture (Group 0) and the run gate that requires it`

---

## Phase 7 — `rbl/services/amp_test_runner.py`, part 1: core + the raw-capture family

This is the largest phase. Build the state machine and prove it on G1, G4 and G8
before adding the ladder tests in Phase 8.

### 7.1 States

```
IDLE
  → PREFLIGHT        estimate bytes, check the gate, check the envelope, snapshot generators
  → AWAIT_PROFILE    profile/channel switch requested; waiting for confirmation
  → SETTLE           level commanded; windows discarded
  → CAPTURE          windows accumulated (raw and/or statistics)
  → RECORD           synchronous: emit rows, close the capture, advance
  → AWAIT_OPERATOR   operator-paced tests only; blocked on a GUI acknowledgement
  → DONE | ABORTING
```

Signals, mirroring `CalibrationRunner`'s so the tab's wiring stays familiar:

```python
progress          = Signal(int, int, str)     # done, total, label
row_recorded      = Signal(dict)
capture_written   = Signal(str, int)          # path, bytes
operator_prompt   = Signal(str, str)          # title, instruction — GUI shows a modal
finished          = Signal(str)               # run_dir, or "" on failure
error             = Signal(str)
profile_change_requested = Signal(str)        # → beamline.set_stream_profile
channel_change_requested = Signal(str)        # → beamline.set_stream_channel  (NEW)
```

### 7.2 The profile-switch handshake — get this right or nothing downstream is trustworthy

Switching profiles is a full `eStreamStop → reconfigure → eStreamStart` cycle
(see `labjack_link._restart_stream_worker`), it clears `amp_traces`, and it takes
tens of milliseconds during which windows either stop arriving or arrive from the
*old* configuration. Commanding a setpoint before the new stream is confirmed
live produces data silently attributed to the wrong channel at the wrong rate.

`_enter_await_profile(spec)`:

1. Emit `profile_change_requested(spec.profile)`.
2. If `is_single_channel(spec.profile)`, emit `channel_change_requested(target_ain)`.
3. Arm a one-shot `QTimer` for `PROFILE_SWITCH_TIMEOUT_S`; on timeout, abort with
   a clear message naming the profile that never arrived.
4. In `on_window`, while in `AWAIT_PROFILE`, **discard** every payload that fails
   any of:
   - `payload["profile"] == spec.profile`
   - `payload["window_samples"] == window_samples(spec.profile)` ← **this is the
     de-interleave stride check; a mismatch here is the known failure mode after
     a profile switch and must be caught, not averaged in**
   - `payload["channels"][target_ain] is not None`
5. Once a payload passes all three, discard `PROFILE_SETTLE_WINDOWS` more, then
   transition to `SETTLE`.

Print each step. A run that silently sat in `AWAIT_PROFILE` is far worse than one
that aborted loudly.

### 7.3 Single-channel awareness — do not write NaN and call it data

In `SINGLE_FAST` / `SINGLE_HIRES`, seven of the eight amp AINs are `None`.
`CalibrationRunner._accumulate` skips `None` entries and `_window_stats` then
returns `NaN` — which is indistinguishable in the CSV from "the channel was
streamed and returned garbage".

In this runner: emit rows **only** for AINs actually present in the active scan
list. For the rest, either emit nothing, or emit a row with
`kind="not_streamed"` and every numeric field left empty. Pick one, do it
consistently, and document it in `docs/amp_test_matrix.md`. Never emit `NaN` for
a channel that was simply not being sampled.

### 7.4 Trip / fault interlock

Evaluated on every window while in `CAPTURE`, for every **current** monitor AIN
present in the active scan list:

```
i_ma = monitor_to_ma(max(abs(waveform)))
trip_ma = front_panel["amps"][amp]["current_adjustment_ma"]
if i_ma > trip_ma * TRIP_MARGIN:
    if spec.expect_trip:  record the level, command TRIP_BACKOFF_KV, advance
    else:                 abort, record trip_flag=True and the reason
```

Collapse detection, for **voltage** monitors, while commanded `|kV| >= 0.5`:

```
if abs(measured_kv) < COLLAPSE_RATIO * abs(commanded_kv) for COLLAPSE_WINDOWS
   consecutive windows:  treat exactly as a trip
```

**Safety note you must implement and document:** in a single-channel profile the
interlock can only see the one streamed channel. When the target is a **voltage**
monitor, the current-based interlock is **unavailable** — only collapse detection
is armed. The runner must print this at the start of every such capture, and the
pre-run checklist for those tests (G1.1–G1.4, G7.5, G8.1, G8.2) must carry an
explicit "operator is present and watching the front-panel LEDs" acknowledgement.
This is why G2.3, G2.4 and G7.6 — the tests that deliberately approach the trip —
target the **current** monitor.

### 7.5 Envelope pre-check

In `PREFLIGHT`, for every level of an AC test, compute
`peak_current_ma(freq, peak_kv, load_pf)` and compare against
`envelope_ceiling_kv`. Refuse the run — before commanding anything — if any level
exceeds the ceiling, unless `spec.expect_trip` is `True`. The refusal message must
name the offending (frequency, amplitude) pair, the computed current, and the pot
setting it exceeded. `load_pf` comes from `front_panel["load_cap_pf_measured"]`
when set, otherwise `LOAD_CAP_PF_DEFAULT` **with a printed warning that the guard
is running on a guess**.

### 7.6 Byte-budget pre-check

Also in `PREFLIGHT`: `spec.estimated_raw_bytes()`, compared against
`RAW_MAX_BYTES_PER_RUN` and against actual free disk space on `AMT_OUTPUT_DIR`'s
volume (`shutil.disk_usage`). Refuse with a clear number, do not half-fill a disk.

### 7.7 Operator-paced tests

For `spec.operator_paced`, between levels: enter `AWAIT_OPERATOR`, emit
`operator_prompt(title, instruction)`, and **stop**. The GUI shows a modal; its
acceptance calls `runner.operator_acknowledged(note)`, which records the note into
the CSV's `operator_paced_ack` column and resumes. There is no timeout — a human
turning a pot takes as long as it takes.

Before entering `AWAIT_OPERATOR`, command the driven channel to 0 kV and turn its
output off. Nobody reaches into a rack with the output live.

### 7.8 Implement these tests in this phase

- **G1.1, G1.2, G1.3** — fixed-setpoint raw capture, sequential per target AIN.
- **G1.4** — the same, operator-paced between the two pot positions.
- **G4.1** — `DriveSet.NONE` and `ON_PLATES_NO_HV`: assert that no generator
  command is issued for the whole run. Add a unit test that proves it.
- **G4.2, G4.3** — `DriveSet.ALL` at 0 kV, statistics only.
- **G4.4** — iterate all eight AINs as levels of the `channel` factor.
- **G8.1, G8.2** — square-wave drive via `AmpDrive.command_square`.

### 7.9 `[OK]` self-tests and unit tests

`if __name__ == "__main__":` block that runs the state machine headless against a
`FakeGen` and synthetic payloads built with the helpers in `tests/payloads.py`,
asserting the sequence `PREFLIGHT → AWAIT_PROFILE → SETTLE → CAPTURE → RECORD`
for a two-level spec, and that no state transition happens without either a
window or a timer.

`tests/test_amp_test_runner.py` must assert:

- A payload with the **wrong** `window_samples` during `AWAIT_PROFILE` is
  discarded and does not advance the state.
- A payload with the right profile but `channels[target] is None` is discarded.
- The handshake times out and aborts if the correct payload never arrives.
- With `expect_trip=False`, one window over `trip_ma * TRIP_MARGIN` aborts the
  run and sets `trip_flag`.
- With `expect_trip=True`, the same window records the level and advances instead.
- A `DriveSet.NONE` spec issues **zero** `set_waveform` calls.
- `RawCaptureOverflow` truncates the capture and continues the run.
- `start()` refuses when `front_panel_state.load()` returns `None`.
- Abort at any state leaves all four channels at 0 V with outputs off.

**Commit:** `feat(amp-test): runner core with profile handshake, trip interlock and raw capture (G1, G4, G8)`

---

## Phase 8 — Runner part 2: the ladder family (G2, G3, G7)

Extend the same state machine. No new states.

### 8.1 DC ladders with a trip target — G2.1

Walk `levels` ascending with `settle_s = 1.0`. On interlock fire: record the
level and the current monitor reading in the CSV with `trip_flag=True`, command
0 kV, and move to the next amp. If the ladder completes without a trip, record
that fact explicitly — "did not trip below 5 kV" is a result, not a null.

### 8.2 Step tests — G2.3

For each step size: settle at 0 kV, open the raw capture, then command the step
in a **single** `command_dc` call and hold for `hold_s`. The inrush transient
lives in the first few hundred microseconds; at 100 kS/s on the current monitor
that is 20–70 samples. Ensure the capture is open and accumulating **before** the
step is commanded — order matters and is easy to get backwards.

### 8.3 Ramped vs stepped — G2.4, and an honest caveat you must write into the code

The Findings sheet computes that a 200 V change spread over ≥26 µs would stay
under a 1 mA limit. **26 µs is not reachable over SCPI** — a single USB/VISA write
round-trip is on the order of milliseconds. What this test can actually do is
sub-step the move: split a 1.0 kV command into N smaller `command_dc` calls
spaced by a `QTimer`, which reduces the *per-step* amplitude and therefore the
peak inrush current by roughly the sub-step ratio, while each individual sub-step
still rises at the amplifier's full slew rate.

Implement it that way, with `RAMP_SUBSTEPS = 10` and `RAMP_SUBSTEP_MS = 20` as
named constants in `amp_test_config.py`, and put this paragraph — the limitation,
not just the mechanism — in the module docstring and in `docs/amp_test_matrix.md`.
The test still answers the question it is for ("should the runner ramp between
setpoints?"); it just does not achieve the microsecond figure, and the record must
say so. **Flag this to Isaac in your completion report** — if true microsecond
ramping is wanted, it needs the DG1022Z's own sweep/burst mode or an analog
slew-limited path, which is out of scope here.

The ramp must be `QTimer`-driven from the runner. Do **not** add a blocking ramp
helper to `AmpDrive`.

### 8.4 AC amplitude and frequency ladders — G3, G7.1–G7.5

Straightforward: `command_sine(amp, peak_kv, freq_hz)` per level, settle, capture,
record. Two details:

- `settle_s` for AC must be at least 3 cycles of the **lowest** frequency in the
  spec — at 1 Hz (G7.4's first level) that is 3 s, not the 2 s
  `CAL_AC_SETTLE_S` default. Compute it as `max(spec.settle_s, 3.0 / freq_hz)`.
- Record `peak_v` and `rms_v` from the payload alongside the existing
  mean/std/min/max. G3's capacitance back-solve and G7's Bode magnitude both need
  peak, and the base schema has no column for it — Phase 5 added one.

### 8.5 Nested envelope walk — G7.6

At each frequency, ascend amplitude from 0.5 kV in 0.25 kV steps until the
interlock fires or `envelope_ceiling_kv` is reached. Record the fire point per
(frequency, amplitude). This is the only test with a two-dimensional sequence;
flatten it into the existing `_sequence` list at build time rather than adding a
nested loop to the state machine.

### 8.6 Tests

Add to `tests/test_amp_test_runner.py`:

- G2.1 stops at the first level that trips and does not command higher.
- G2.4's ramp issues exactly `RAMP_SUBSTEPS` `set_waveform` calls per transition
  and the intermediate values are monotonic between endpoints.
- G7.4's computed settle at 1 Hz is ≥ 3.0 s.
- G7.6 flattens to the expected number of (freq, amplitude) pairs and stops
  ascending at a given frequency once the interlock fires there.
- An AC level above the envelope ceiling with `expect_trip=False` is refused in
  `PREFLIGHT` and issues zero generator commands.

**Commit:** `feat(amp-test): ladder, ramp and envelope-walk tests (G2, G3, G7)`

---

## Phase 9 — Runner part 3: delegation, manual entry, endurance (G5, G6, G9)

### 9.1 G5.1 / G5.2 / G9.3 — delegate, do not reimplement

These are the existing `CalibrationRunner.start_sweep()` measurement. The tab
dispatches on `spec.notes.startswith("delegate:")` and constructs a
`CalibrationRunner` exactly as `_on_run_clicked` does today, with two additions:

- the `CalibrationWriter` is pointed at the new run folder, and
- the front-panel state and `test_id` are merged into its metadata.

Do not modify `CalibrationRunner`'s sweep logic. Do not copy it.

### 9.2 G5.3 — settle-time confirmation

The only new capability is running the ladder at two different settle times.
Rather than touching `CAL_SETTLE_S`, add an **optional keyword argument**
`settle_s: float = None` to `CalibrationRunner.start_sweep` that defaults to
`CAL_SETTLE_S` when omitted. This is a one-line, backwards-compatible signature
change; verify `tests/test_calibration_runner.py` still passes.

### 9.3 G6.1 — DMM manual entry

No hardware runner involvement beyond commanding the generators at low voltage.
Build a modal grid: rows are the commanded generator volts `-5 … +5` step 1,
columns are the four channels, cells are editable floats. The runner commands
each row's value on all four generators, waits `settle_s`, prompts the operator to
read the DMM, and stores what they type.

Output goes to the same `summary.csv` with `driven_amp` set to the generator
channel, `commanded_kv` carrying the **generator volts** (not kV — say so in
`level_label`), and the typed DMM reading in `converted_value` with
`converted_unit="V_DMM"`.

Its checklist must state that the amplifier is disconnected from the loop and
that there is no high voltage anywhere.

### 9.4 G6.2 — blocked

Register it, grey it out, show the Appendix A reason. Do not implement.

### 9.5 G9 — endurance

Reuse `CalibrationRunner`'s drift machinery conceptually but implement it in
`AmpTestRunner` because it needs AC drive and interleaved probes, which drift
mode has neither of.

- Command all four channels to a sine at the operating point (frequency and
  amplitude are run parameters, defaulted from the envelope ceiling at that
  frequency, and shown in the GUI before the run starts).
- Log window statistics every `ENDURANCE_LOG_INTERVAL_S` with `pass_type="hold"`.
- Every `ENDURANCE_PROBE_INTERVAL_S`, run a short gain probe: three amplitudes
  (25%, 50%, 100% of the operating point), `settle_s` each, rows tagged
  `pass_type="probe"`, then return to the hold. Six probes in a 2 h block.
- **Duration guard, in the runner:**

```python
if load_condition == "ON_PLATES" and duration_h > ENDURANCE_ON_PLATES_MAX_H:
    refuse, emit error, return      # not a warning — a refusal
```

  Mirror the wording and structure of `CalibrationRunner.start_drift`'s existing
  guard so the two read the same. When `ENDURANCE_ON_PLATES_MAX_H` is later raised
  above `DRIFT_MAX_ATTENDED_H`, the runner must additionally require that the
  checklist's extra unattended-HV acknowledgement was ticked, and record that
  acknowledgement in `metadata.json`. Implement that branch now even though the
  constant is 2.0 today — it is three lines and it is the whole point of making
  the progression a config change rather than a code change.
- Arm the existing watchdog pattern (`WATCHDOG_S`, no window for 5 s = fault) for
  the entire hold. A two-hour run that silently recorded nothing after minute
  eight is the failure this prevents.

### 9.6 Tests

- G9 refuses `duration_h=12.0` with `ON_PLATES` while `ENDURANCE_ON_PLATES_MAX_H`
  is 2.0, and the refusal happens in the **runner**, not the GUI.
- Monkeypatching `ENDURANCE_ON_PLATES_MAX_H` to 12.0 **without** the extra
  acknowledgement still refuses; **with** it, proceeds.
- Probe rows are tagged `pass_type="probe"` and hold rows `pass_type="hold"`, and
  the probe count over a simulated 2 h equals 6.
- A delegated spec (G5.1) constructs a `CalibrationRunner` and never touches
  `AmpTestRunner`'s state machine.
- `start_sweep(settle_s=5.0)` uses 5.0 and `start_sweep()` still uses `CAL_SETTLE_S`.

**Commit:** `feat(amp-test): delegation, DMM manual entry and endurance with interleaved probes (G5, G6, G9)`

---

## Phase 10 — GUI: the group / test selector in `rbl/gui/calibration_tab.py`

Surgical, anchored edits only. The tab keeps its name, its `LabJackPanel`, its
`_lj_tabs` contract, its plot and its fit readout.

### 10.1 Replace the three radio buttons with two combo boxes

**Anchor** (appears exactly once):

```
        self.rb_sweep = QRadioButton("DC Sweep")
```

Replace the whole `mode_row` block — from that line through
`cfg_form.addRow("Mode:", mode_row)` — with:

- `self.cbo_group` — a `NoScrollComboBox` populated from
  `amp_test_matrix.groups()`, displaying `"G3 — Load capacitance"`.
- `self.cbo_test` — a `NoScrollComboBox` repopulated on group change from
  `tests_in_group()`, displaying `"G3.1 — Back-solve capacitance from the AC current monitor"`.
- Both added via `cfg_form.addRow("Group:", ...)` and `cfg_form.addRow("Test:", ...)`.

Keep `NoScrollComboBox` — the tab already imports it, and a scroll wheel changing
which high-voltage test is selected is exactly the accident it exists to prevent.

### 10.2 The spec detail panel

Below the selectors, a read-only panel that updates on test change and shows:

- **What it proves** — the verbatim `proves` text, word-wrapped. This is the most
  useful thing on the screen; give it room.
- Drive set, stream profile, target AINs, factor, levels, load condition.
- Estimated raw size in MB and estimated duration
  (`len(levels) * len(target_ains) * (settle_s + hold_s)`), both computed live.
- A red banner when the spec is blocked (G6.2) or when `expect_trip` is `True`
  ("This test drives the amplifier until it trips. That is the measurement.").
- A red banner when the profile is single-channel and the target is a voltage
  monitor: "Current-based trip protection is UNAVAILABLE for this capture.
  Watch the front-panel LEDs."

### 10.3 Per-test checklist

**Anchor** (appears exactly once):

```
        dialog = _PreRunChecklistDialog(load_condition, self)
```

Extend `_PreRunChecklistDialog.__init__` to take the `TestSpec` and append
spec-driven items to its existing list:

| condition | item |
|---|---|
| `load_condition == "ON_PLATES_NO_HV"` | "All four EEL5000 mains switches are OFF and the units are unplugged." |
| `load_condition == "ANALYSIS_ONLY"` | "The amplifier is disconnected from the generator loop. No high voltage anywhere." |
| `drive is SWAPPED` | "Cables have been swapped at the AMPLIFIER OUTPUTS only. The steerer connection has not been touched." |
| `expect_trip` | "This test will trip the amplifier deliberately. I am present and watching the front-panel LEDs." |
| single-channel + voltage target | "Current-based trip protection is unavailable for this capture. I am present." |
| G9 with `duration_h > DRIFT_MAX_ATTENDED_H` | "This run exceeds the attended cap. Unattended-HV approval is on file." |

Keep the two existing items and the existing `_MANUAL_LOAD_WARNING` behaviour
untouched.

### 10.4 Dispatch

**Anchor** (appears exactly once):

```
            self._runner.start_sweep()
```

Replace the `if self.rb_sweep.isChecked(): … elif … else …` dispatch block with:

```python
spec = amp_test_matrix.by_id(self.cbo_test.currentData())
if spec.notes.startswith("delegate:"):
    # G5.1 / G5.2 / G9.3 — the existing calibration sweep, unchanged.
    ...construct CalibrationRunner exactly as before...
else:
    ...construct AmpTestRunner(spec, ...)...
```

**Anchor** (appears exactly once):

```
        self.profile_change_requested.emit(CAL_PROFILE)
```

Replace with `self.profile_change_requested.emit(spec.profile or CAL_PROFILE)`,
and for single-channel profiles also emit the new channel signal.

### 10.5 New signal

**Anchor** (appears exactly once):

```
    profile_change_requested = Signal(str)
```

Add directly below it:

```python
    # Single-channel profiles need a target AIN as well as a profile. Mirrors
    # AmpTab.single_channel_change_requested; wired in app.py.
    channel_change_requested = Signal(str)
```

### 10.6 Operator prompt and G0 entry

- Connect `runner.operator_prompt` to a modal that shows the instruction, offers a
  free-text note field, and calls `runner.operator_acknowledged(note)` on accept
  and `runner.abort()` on reject.
- Selecting **G0.1** opens `_FrontPanelDialog` directly instead of starting a run.
- Starting any G1–G9 test with no valid front-panel state opens
  `_FrontPanelDialog` first, then continues if it is accepted.

### 10.7 Plot behaviour

The existing commanded-vs-measured scatter is meaningful for G5, G7 and G2 and
meaningless for G4 and G8. Do not delete it — hide it and show a simple live
strip chart of the target AIN's window mean when
`spec.factor in ("time", "channel")`. Keep this minimal; it is bench feedback, not
analysis.

### 10.8 Gate

`pytest tests/test_calibration_app_wiring.py -v` must still pass, and the app must
still launch: `python -m rbl.main`. Click through all ten groups with no hardware
connected and confirm no exceptions and that Run stays disabled while
disconnected.

**Commit:** `feat(amp-test): group/test selector, spec panel and per-test checklist in the calibration tab`

---

## Phase 11 — Wiring, tests, docs

### 11.1 `rbl/gui/app.py` — one added line

**Anchor** (appears exactly once):

```
        self.calibration_tab.profile_change_requested.connect(self.beamline.set_stream_profile)
```

Insert directly **below** it:

```python
        # Single-channel test profiles (SINGLE_FAST / SINGLE_HIRES) need a
        # target AIN as well as a profile — same path AmpTab already uses.
        self.calibration_tab.channel_change_requested.connect(self.beamline.set_stream_channel)
```

Change nothing else in `app.py`.

### 11.2 Full test run

```
pytest -v
```

**Everything that passed before this work must still pass.** Report any test you
changed and why. Changing an existing assertion to accommodate new code is
almost always the wrong move — raise it instead.

### 11.3 `docs/amp_test_matrix.md` (new file)

Not a restatement of this document. Write the operator-facing record:

- The ten groups, what each answers, and the recommended running order
  (G0 → G1 → G2 → G3 → G4 → G5 → G6 → G7 → G8 → G9, because each group's
  guards depend on the previous one's numbers).
- The output folder layout and the `summary.csv` schema, column by column, with
  the note that the first 20 columns are the calibration schema verbatim.
- How to load a `.npz` capture in three lines of Python.
- **The limitations, stated plainly:** the monitor BNC rolls off near 11 kHz so
  nothing faster is faithfully captured; `SINGLE_FAST` sees one channel so
  current and voltage are never simultaneous; SCPI ramping cannot reach the
  microsecond figure in the Findings sheet; `AIN4` is unavailable so G6.2 is
  blocked; the current-based trip interlock is disarmed during voltage-monitor
  single-channel captures.
- The `ENDURANCE_ON_PLATES_MAX_H` progression: what to change, and what extra
  acknowledgement that arms.
- That this feature never applies a correction factor, and why.

### 11.4 Completion report

Report to Isaac:

1. Every phase, its commit hash, and its `[OK]` output.
2. Every decision in Appendix A and its current status.
3. The G2.4 SCPI-ramp limitation from Phase 8.3, stated as a finding.
4. Any spreadsheet row you could not implement faithfully, and exactly why.
5. Total estimated raw data volume per group, from the registry.

**Commit:** `docs(amp-test): operator guide, limitations and output schema`

---

## Appendix A — Decisions that need Isaac, not you

Implement around these. Do not resolve them unilaterally.

**1. G6.2 — `AIN4` is not streamable.** The test needs the LabJack to read the
generator output directly, simultaneously with the DMM, to separate T7 ADC gain
from generator error. But `AIN4` is spare, absent from every stream profile, and
`labjack_stream_config` asserts at import that every single-channel choice is an
amp monitor. Three ways forward, all requiring a config change Isaac should sign
off on: (a) add a `GEN_PROBE` profile streaming `AIN4` alone at resolution index
8 and relax the assert to allow one documented non-amp channel; (b) read `AIN4`
by command-response through the existing `labjack_driver` outside the stream
entirely, which is simpler but cannot run while a stream is active; (c) drop the
test and accept that G6 separates the generator but not the ADC.
**Consequence of dropping it:** the −0.39 %/kV scale error stays unattributed
between the generator and the T7 ADC.

**2. The operating point for G9.** The endurance hold needs a frequency and an
amplitude. The Findings sheet implies a 2 kHz fast axis at up to 5 kV peak, which
needs 8.2 mA — inside the pot's 10 mA maximum but only near the top of its range.
Until G2 measures where the pots actually sit, the G9 operating point cannot be
chosen safely. Default the GUI field to the envelope ceiling at the chosen
frequency and require the operator to confirm it.

**3. `LOAD_CAP_PF_DEFAULT = 130` is a guess.** Every envelope calculation in the
guard scales with it. Once G3 runs, write the measured value into
`front_panel_state.json`'s `load_cap_pf_measured`. Until then the guard prints a
warning on every run — that warning is deliberate; do not silence it.

**4. G2.2's "common value" for the four pots.** The spreadsheet says to choose it
from the AC current the operating point needs, which depends on decision 2 and on
G3's capacitance. This is an operator decision made between G2.1 and G2.2, not a
constant.

---

## Appendix B — Test ID index

| ID | Group | Test | Profile | Raw |
|---|---|---|---|---|
| G0.1 | Front-panel state | Record all four controls on all four amps | — | — |
| G1.1 | Y+ instability | Raw capture on Y+ at fixed setpoints | SINGLE_FAST | FULL |
| G1.2 | Y+ instability | Same on X+ as the control | SINGLE_FAST | FULL |
| G1.3 | Y+ instability | 60 s at commanded zero | SINGLE_FAST | FULL |
| G1.4 | Y+ instability | DYNAMIC ADJUSTMENT pot sweep | SINGLE_FAST | FULL |
| G1.5 | Y+ instability | Amplifier swap, Y+ cable from X+ amp | WAVEFORM | — |
| G2.1 | Trip & inrush | Measure actual trip current per amp | WAVEFORM | — |
| G2.2 | Trip & inrush | Common pot value, re-verify | WAVEFORM | — |
| G2.3 | Trip & inrush | Largest non-tripping DC step | SINGLE_FAST | FULL |
| G2.4 | Trip & inrush | Ramped vs stepped command | SINGLE_FAST | FULL |
| G2.5 | Trip & inrush | Recovery in LIMIT vs TRIP mode | WAVEFORM | — |
| G3.1 | Load capacitance | Back-solve C from the AC current monitor | WAVEFORM | — |
| G4.1 | Noise floor | Wiring + DC floor, amps OFF | WAVEFORM | DECIMATED |
| G4.2 | Noise floor | Quiescent noise, amps ON at 0 V | WAVEFORM | DECIMATED |
| G4.3 | Noise floor | Allan deviation record, 1 h | WAVEFORM | DECIMATED |
| G4.4 | Noise floor | Noise spectrum per channel | SINGLE_FAST | FULL |
| G5.1 | DC transfer | Full ±5 kV ladder, 3 pass types | WAVEFORM | — |
| G5.2 | DC transfer | Repeat once Y+ is resolved | WAVEFORM | — |
| G5.3 | DC transfer | Settle-time confirmation, 0.5 s vs 5 s | WAVEFORM | — |
| G6.1 | Chain isolation | DMM at each generator output | — | — |
| G6.2 | Chain isolation | AIN4 vs DMM — **BLOCKED, Appendix A.1** | — | — |
| G6.3 | Chain isolation | Generator swap | WAVEFORM | — |
| G7.1 | AC characterisation | Amplitude ladder at 100 Hz | WAVEFORM | — |
| G7.2 | AC characterisation | Amplitude ladder at 1 kHz | WAVEFORM | — |
| G7.3 | AC characterisation | Amplitude ladder at 2 kHz | WAVEFORM | — |
| G7.4 | AC characterisation | Frequency ladder at 2 kV peak | WAVEFORM | — |
| G7.5 | AC characterisation | Frequency ladder above 3 kHz | SINGLE_FAST | FULL |
| G7.6 | AC characterisation | Trip-limited operating envelope | SINGLE_FAST | — |
| G8.1 | Step response | Small-signal step, ±0.5 kV | SINGLE_FAST | FULL |
| G8.2 | Step response | Large-signal step | SINGLE_FAST | FULL |
| G9.1 | Endurance | 2 h AC hold, attended | WAVEFORM | DECIMATED |
| G9.2 | Endurance | Interleaved gain probes (modifier on G9.1) | WAVEFORM | DECIMATED |
| G9.3 | Endurance | Post-endurance ladder repeat | WAVEFORM | — |

---

## Appendix C — Files created and touched

**New:**

```
rbl/config/amp_test_config.py
rbl/config/amp_test_matrix.py
rbl/services/amp_drive.py
rbl/services/raw_capture_writer.py
rbl/services/amp_test_writer.py
rbl/services/front_panel_state.py
rbl/services/amp_test_runner.py
tests/test_amp_drive.py
tests/test_amp_test_matrix.py
tests/test_amp_test_runner.py
tests/test_front_panel_state.py
tests/test_raw_capture_writer.py
docs/amp_test_matrix.md
```

**Edited, surgically, at the anchors given:**

```
rbl/services/calibration_runner.py   Phase 3 (3 edits), Phase 9.2 (1 kwarg)
rbl/gui/calibration_tab.py           Phase 10 (5 anchored edits)
rbl/gui/app.py                       Phase 11.1 (1 added line)
```

**Never edited:**

```
rbl/config/calibration_config.py
rbl/config/labjack_stream_config.py
rbl/config/hardware_config.py
rbl/services/calibration_writer.py
rbl/hardware/*
raster_tool/*
```
