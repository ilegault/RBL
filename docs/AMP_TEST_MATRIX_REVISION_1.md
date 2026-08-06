# Amplifier Test Matrix — Revision 1

**Read this together with `AMP_TEST_MATRIX_IMPLEMENTATION.md`. It supersedes that
document's Phase 2, Phase 6, and parts of Phases 1, 5, 7, 9 and 10. Everything not
named here is unchanged.**

Two changes: the groups are renumbered into the running order below, and the test
count drops from 33 to 21. The architecture is unchanged — same phases, same
files, same raw-capture and interlock machinery.

---

## What to do right now

`rbl/config/amp_test_config.py` is committed (`edf7976`). `rbl/config/amp_test_matrix.py`
exists but is **uncommitted** — it has 33 specs and 10 groups and is now wrong.

1. Apply the two Phase 1 edits in §3 below and amend the Phase 1 commit.
2. **Delete and rewrite `rbl/config/amp_test_matrix.py`** against §1 and §2. Do not
   try to edit 33 specs down to 21 in place; a clean rewrite from the new table is
   shorter and less error-prone.
3. Re-run `python -m rbl.config.amp_test_matrix` and commit Phase 2.
4. Continue to Phase 3, with the deltas in §4 applied when you reach each phase.

No other committed work is affected.

---

## 1. The nine groups, in running order

The order is now meaningful and must be preserved in the GUI: **G1 measures the
current limit and G2 measures the capacitance, and every later group's envelope
guard and trip interlock depend on those two numbers.** See §2.

| # | Group | Tests | Answers |
|---|---|---|---|
| 1 | Trip threshold & inrush | 2 | What is the current limit on each amplifier |
| 2 | Load capacitance | 1 | Plate + cable capacitance, back-solved in place |
| 3 | Noise floor | 3 | Amps off, amps on at 0 V, and whether noise changes over an hour |
| 4 | DC transfer function | 2 | Full ±5 kV ladder, repeated after the Y+ work |
| 5 | Y+ instability diagnosis | 1 | Does the fault follow the amplifier or the cable/plate |
| 6 | Chain isolation | 3 | Is the −0.39 %/kV error in the generator or downstream |
| 7 | AC characterisation | 5 | Amplitude linearity and magnitude response to 10 kHz |
| 8 | Step response | 2 | Whether the capacitive load, not the amplifier, sets the slew limit |
| 9 | Endurance | 2 | Does anything drift over a 2 h AC hold |

**Group 0 is deleted.** No front-panel dialog, no gate, no staleness check.

---

## 2. Full spec table — 21 tests

Everything below carries over from the original document unchanged except its
`test_id`, `group_num` and `group_name`. The "was" column is the old ID; copy that
spec's `title`, `proves`, `drive`, `amps`, `profile`, `target_ains`, `factor`,
`levels`, `level_labels`, `load_condition`, `raw_mode`, `expect_trip`,
`operator_paced`, `hold_s` and `settle_s` verbatim from the original Phase 2 text.

| New ID | was | Title | Profile | Raw | Notes |
|---|---|---|---|---|---|
| **G1.1** | G2.1 | Measure the actual trip current per amp | WAVEFORM | — | `expect_trip=True`. **Writes `trip_ma` to `measured_limits.json`** — see §4.3 |
| **G1.2** | G2.3 | Largest DC step that does NOT trip, at fixed settle | SINGLE_FAST | FULL | Current monitor target. Result feeds G8.2 |
| **G2.1** | G3.1 | Back-solve capacitance from the AC current monitor | WAVEFORM | — | **Writes `load_cap_pf` to `measured_limits.json`** |
| **G3.1** | G4.1 | Wiring + DC floor, amplifiers POWERED OFF | WAVEFORM | DECIMATED | `ON_PLATES_NO_HV`, `DriveSet.NONE` |
| **G3.2** | G4.2 | Quiescent noise, amps ON at commanded 0 V | WAVEFORM | DECIMATED | |
| **G3.3** | G4.3 | 1 h record at 0 V | WAVEFORM | DECIMATED | Must stay DECIMATED — raw would be ~1.4 GB |
| **G4.1** | G5.1 | Full ladder ±5 kV, 0.2 kV steps, 3 pass types | WAVEFORM | — | `notes="delegate:CalibrationRunner.start_sweep"` |
| **G4.2** | G5.2 | Repeat once the Y+ fault is resolved | WAVEFORM | — | delegate |
| **G5.1** | G1.5 | AMPLIFIER SWAP: drive the Y+ cable from the X+ amplifier | WAVEFORM | — | `DriveSet.SWAPPED`, `operator_paced=True` |
| **G6.1** | G6.1 | DMM at each function generator output | — | — | `ANALYSIS_ONLY`, no HV. Commanded values are **generator volts**, not kV |
| **G6.2** | G6.2 | LabJack AIN4 reads the generator output | — | — | **STILL BLOCKED** — Appendix A.1. Register, grey out, show the reason |
| **G6.3** | G6.3 | GENERATOR SWAP: drive the X amps from the Y generator | WAVEFORM | — | `operator_paced=True` |
| **G7.1** | G7.1 | Amplitude ladder at 100 Hz | WAVEFORM | — | |
| **G7.2** | G7.2 | Amplitude ladder at 1 kHz | WAVEFORM | — | |
| **G7.3** | G7.3 | Amplitude ladder at 2 kHz | WAVEFORM | — | |
| **G7.4** | G7.4 | Frequency ladder at 2 kV peak, 1 Hz – 2 kHz | WAVEFORM | — | Settle = `max(settle_s, 3.0/freq_hz)` — 3 s at 1 Hz |
| **G7.5** | G7.5 | Frequency ladder above 3 kHz, single channel | SINGLE_FAST | FULL | Voltage monitor target |
| **G8.1** | G8.1 | Small-signal step, ±0.5 kV, square-wave edge method | SINGLE_FAST | FULL | 5 Hz square, `hold_s=30.0` |
| **G8.2** | G8.2 | Large-signal step, as large as the trip setting allows | SINGLE_FAST | FULL | Level resolved from **G1.2**'s result, else operator-entered |
| **G9.1** | G9.1 | 2 h AC hold at the operating point, attended | WAVEFORM | DECIMATED | Gain probes every 20 min stay a **boolean modifier** on this spec |
| **G9.2** | G9.3 | Post-endurance repeat of the G4 ladder | WAVEFORM | — | delegate |

### Deleted — do not implement

| was | Title | Why it's gone |
|---|---|---|
| G0.1 | Front-panel state capture | Dropped. Consequences in §4.3 |
| G1.1–G1.4 | Y+ / X+ raw captures at 0/1/3 kV, 60 s at 0 V, pot sweep | Group 5 is the swap only |
| G2.2 | Common pot value across all four amps | Depended on G0 |
| G2.4 | Ramped vs stepped command | Also removes the SCPI-ramp caveat from Phase 8.3 |
| G2.5 | Trip recovery in LIMIT vs TRIP mode | Operator-paced, depended on G0 |
| G4.4 | Noise spectrum (Welch PSD) per channel | |
| G5.3 | Settle-time confirmation, 0.5 s vs 5 s | **Also removes the Phase 9.2 `settle_s` kwarg edit to `CalibrationRunner`** |
| G7.6 | Trip-limited operating envelope walk | Removes the only two-dimensional sequence (Phase 8.5) |

---

## 3. Phase 1 — two edits to the committed file

In `rbl/config/amp_test_config.py`:

1. Rename `AMT_FRONT_PANEL_JSON = "front_panel_state.json"` to
   `AMT_MEASURED_LIMITS_JSON = "measured_limits.json"`, and update its comment:
   this file now holds values the tests **measure**, not values a human types.
2. Delete `RAMP_SUBSTEPS` and `RAMP_SUBSTEP_MS` — the only consumer was G2.4.

Everything else in that file stands. `git commit --amend`.

---

## 4. Phase deltas

### 4.1 Phase 2 — registry

- `groups()` returns 9 entries, 1–9. Update the self-test's group-count assert
  from 10 to 9 and its ID regex to reject `G0.*`.
- The `by_id` self-test should assert all 21 IDs resolve and that none of the
  eight deleted IDs exist.
- Keep every other assert in the original §2.5 as written.

### 4.2 Phase 5 — one column rename

In `AMT_CSV_COLUMNS`, rename `front_panel_hash` to `limits_hash`. Everything else
about the schema — including the rule that the first 20 columns are
`calibration_writer.CSV_COLUMNS` verbatim, in order — is unchanged.

### 4.3 Phase 6 — replaced, and much smaller

`rbl/services/front_panel_state.py` becomes `rbl/services/measured_limits.py`.
**No dialog, no validation of human input, no staleness check, no run gate.**

```python
# data/amp_tests/measured_limits.json
{
  "updated_iso": "...",
  "amps": {
    "X+": {"trip_ma": null, "trip_measured_iso": null,
           "load_cap_pf": null, "cap_measured_iso": null},
    "X-": {...}, "Y+": {...}, "Y-": {...}
  }
}

def load() -> dict                        # returns the empty skeleton if absent
def record_trip_ma(amp, ma) -> None       # called by G1.1 on completion
def record_load_cap_pf(amp, pf) -> None   # called by G2.1 on completion
def trip_ma(amp) -> tuple[float, bool]    # (value, is_measured)
def load_cap_pf(amp) -> tuple[float, bool]
def hash_state() -> str                   # short sha256 prefix -> the CSV's limits_hash
```

**Fallbacks when a value has not been measured yet** — both must print a warning
on every run that uses them, and both must set `is_measured=False` into the CSV:

| Consumer | Fallback | Why this one |
|---|---|---|
| Trip interlock | `AMP_MAX_MA_DC` (20 mA) | The amplifier's own DC rating. The pot may be set lower, so this under-protects — hence the warning |
| Envelope guard | `POT_RANGE_MA[1]` (10 mA) | The most the pot can be. Optimistic, so the guard is **advisory** until G1 runs |
| Envelope guard | `LOAD_CAP_PF_DEFAULT` (130 pF) | Unchanged from the original document |

Write into `docs/amp_test_matrix.md`: **until G1 and G2 have run, the trip
interlock and envelope guard are running on unverified numbers.** That is the
reason the running order is G1 → G2 → everything else, and the GUI should say so.

`[OK]` self-test: round-trip through a temp dir, assert `hash_state` is stable
and changes when one `trip_ma` changes, assert both fallbacks return
`is_measured=False` on an empty file and `True` after a `record_*` call.

**Commit:** `feat(amp-test): measured-limits store written by G1 and G2`

### 4.4 Phase 7 — the gate is gone

Delete §6.3's gate entirely. `AmpTestRunner.start(spec)` no longer refuses on
missing front-panel state; it reads `measured_limits` and warns. Everything else
in Phase 7 — the profile handshake, the stride check, single-channel awareness,
the trip interlock, the envelope pre-check, the byte budget, operator pacing —
is unchanged.

Two implementation notes:

- **G1.1 and G2.1 must write their results back.** On successful completion,
  G1.1 calls `record_trip_ma(amp, measured)` for each amp it tripped, and G2.1
  calls `record_load_cap_pf(amp, computed)`. G2.1 computing
  `C = I_pk / (2*pi*f*V_pk)` is the **one** derived number this feature produces,
  because the guard needs it at run time; everything else still goes to
  `processing/`. Record the raw `peak_v` / `rms_v` in the CSV as well so the
  computation can be redone offline.
- The tests in §7.9 that referenced the front-panel gate are replaced by: `start()`
  proceeds with a warning when `measured_limits` is empty, and the CSV row carries
  `limits_hash` plus `is_measured=False`.

### 4.5 Phases 8 and 9 — three deletions

- **Delete Phase 8.3 entirely** (ramped vs stepped, and the SCPI-ramp caveat). It
  was G2.4's only consumer. The caveat no longer needs writing into the docs.
- **Delete Phase 8.5 entirely** (the nested envelope walk). It was G7.6's only
  consumer, and it was the only two-dimensional sequence — the sequence builder
  can now stay flat.
- **Delete Phase 9.2 entirely** (the `settle_s` kwarg on
  `CalibrationRunner.start_sweep`). It was G5.3's only consumer. **This means
  `rbl/services/calibration_runner.py` now receives only the three Phase 3
  delegation edits and nothing else.**

Phase 9.1 (delegation), 9.3 (G6.1 DMM entry), 9.4 (G6.2 blocked) and 9.5
(endurance, including the `ENDURANCE_ON_PLATES_MAX_H` progression and the extra
unattended acknowledgement branch) are unchanged. Update the delegating IDs to
G4.1, G4.2 and G9.2.

### 4.6 Phase 10 — checklist and ordering

- Drop the G0 entry, `_FrontPanelDialog`, and the "start G1–G9 with no state →
  open the dialog first" path.
- Drop the `expect_trip` checklist row for the deleted tests; **keep** it for
  G1.1, which is the only remaining `expect_trip=True` test.
- Keep every other checklist row from §10.3 as written.
- The group combo must present groups in numeric order 1–9, which is the running
  order. Add a one-line hint under the selector: *"Run G1 and G2 first — the trip
  interlock and envelope guard use their results."*
- When `measured_limits` has no value for the selected amp, show it in the spec
  detail panel as an amber line, not a blocker.

### 4.7 Phase 11 — unchanged

The one added line in `app.py`, the full `pytest -v` gate, and
`docs/amp_test_matrix.md` all stand. In that doc, replace the G0 section with the
`measured_limits` fallback rule from §4.3, and drop the SCPI-ramp limitation from
the limitations list.

---

## 5. Unchanged — do not touch

Phases 3 (`AmpDrive` extraction + the three anchored `calibration_runner` edits),
4 (`raw_capture_writer.py`), 7's core state machine, and 11.1's `app.py` line are
all exactly as originally specified. The rules of engagement in §0 of the original
document apply in full.

The three items in **Appendix A** still stand and still need Isaac:

1. **G6.2 is blocked** — `AIN4` is spare and excluded from every stream profile.
   Register it, grey it out, do not relax the import-time assert.
2. **The G9 operating point** — now depends on G1.1's measured trip current,
   which is one more reason to run G1 first.
3. **`LOAD_CAP_PF_DEFAULT = 130` is a guess** until G2.1 measures it — and now
   G2.1 writes it back automatically, so the guess is self-correcting after one run.

Appendix A item 4 (G2.2's common pot value) is void — that test is deleted.
