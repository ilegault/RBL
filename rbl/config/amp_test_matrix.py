"""
amp_test_matrix.py
Registry of all ten test groups from the EEL5000 amplifier testing matrix.

SINGLE SOURCE OF TRUTH
-----------------------
This module is the only place that says "what tests exist". The runner and
the GUI both read it; neither hardcodes a test name, profile, or setpoint
sequence. Changes to the matrix go here and propagate automatically.

SCOPE
-----
Data only. No Qt, no hardware imports beyond config. Every field is a plain
Python value or enum. See amp_test_config.py for the numeric constants the
runner uses to gate each run (size caps, interlock thresholds, etc.).
"""
import re
from dataclasses import dataclass
from enum import Enum

from rbl.config.amp_test_config import RAW_MAX_SAMPLES_PER_CAPTURE, RAW_MAX_BYTES_PER_RUN
from rbl.config.hardware_config import AIN_TO_AMP, AMP_LABELS
from rbl.config.labjack_stream_config import (
    STREAM_PROFILES, GUI_REFRESH_HZ, AMP_CHANNELS, window_samples,
)

# ---------------------------------------------------------------------------
# Convenience channel sets — ascending physical AIN order throughout
# ---------------------------------------------------------------------------

_ALL_8_AINS = tuple(AMP_CHANNELS)                      # AIN6 … AIN13
_CURRENT_AINS = ("AIN6", "AIN8", "AIN10", "AIN12")    # Y-, Y+, X-, X+ current
_VOLTAGE_AINS = ("AIN7", "AIN9", "AIN11", "AIN13")    # Y-, Y+, X-, X+ voltage

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class RawMode(Enum):
    NONE      = "none"       # window statistics only — no raw samples on disk
    FULL      = "full"       # every sample written to .npz
    DECIMATED = "decimated"  # window statistics at GUI_REFRESH_HZ (long runs)


class DriveSet(Enum):
    NONE    = "none"      # amplifiers not driven at all
    SINGLE  = "single"    # exactly one amp (or a named subset), named by the spec
    EACH    = "each"      # one at a time, iterating all four
    ALL     = "all"       # all four simultaneously
    SWAPPED = "swapped"   # operator has re-plugged; see checklist


# ---------------------------------------------------------------------------
# TestSpec
# ---------------------------------------------------------------------------

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
    target_ains:     tuple          # capture targets; for single-channel profiles these are
                                    # run as SEQUENTIAL sub-captures, one per AIN
    factor:          str            # the spreadsheet's "Factor" cell
    levels:          tuple          # machine-readable levels; see level encoding in docstring
    level_labels:    tuple          # human labels, same length as levels
    load_condition:  str            # "ON_PLATES" | "ANALYSIS_ONLY" | "ON_PLATES_NO_HV"
    raw_mode:        RawMode
    expect_trip:     bool           # True: trip IS the measurement; disarms abort
    operator_paced:  bool           # True: runner pauses between levels for a physical action
    hold_s:          float          # seconds held at each level
    settle_s:        float          # discarded after each level change
    notes:           str

    def estimated_raw_bytes(self) -> int:
        """Projected .npz payload for this whole test. 0 when raw_mode is not FULL.

        Formula: window_samples(profile) * GUI_REFRESH_HZ * hold_s
                 * len(levels) * len(target_ains) * 4 bytes (float32)
        """
        if self.raw_mode is not RawMode.FULL:
            return 0
        if not self.profile or self.profile not in STREAM_PROFILES:
            return 0
        n_levels = len(self.levels) if self.levels else 0
        if n_levels == 0 or not self.target_ains:
            return 0
        return int(
            window_samples(self.profile)
            * GUI_REFRESH_HZ
            * self.hold_s
            * n_levels
            * len(self.target_ains)
            * 4
        )


# ---------------------------------------------------------------------------
# Level encoding reference
# ---------------------------------------------------------------------------
# factor           | element type | meaning
# "commanded kV"   | float        | DC setpoint, kV
# "peak kV"        | float        | AC peak amplitude, kV
# "frequency"      | float        | Hz, fixed peak given in notes
# "step size"      | float        | kV, stepped from 0
# "time"           | float        | hold seconds (usually a 1-element tuple)
# "settle"         | float        | settle seconds
# "pot position" / "mode" / "generator" /
# "command shape" / "trip setting" | str | operator-paced label
# "channel"        | str          | an AIN name; runner iterates targets

# ---------------------------------------------------------------------------
# G2.1 ramp levels — explicit ascending ladder 0.0 → 5.0 in 0.1 kV steps
# ---------------------------------------------------------------------------

_G2_1_LEVELS = tuple(round(i * 0.1, 1) for i in range(51))   # 51 values
_G2_1_LABELS = tuple(f"{v:.1f} kV" for v in _G2_1_LEVELS)

# ---------------------------------------------------------------------------
# The test matrix
# ---------------------------------------------------------------------------

TEST_MATRIX: tuple[TestSpec, ...] = (

    # ========================================================================
    # Group 0 — Front-panel state
    # Blocking prerequisite for G1–G9. None of the four front-panel controls
    # can be read back over any interface; they have been uncontrolled
    # variables in every measurement taken so far.
    # ========================================================================

    TestSpec(
        test_id="G0.1",
        group_num=0,
        group_name="Front-panel state",
        title="Record CURRENT ADJUSTMENT, LIMIT/TRIP, DYNAMIC ADJUSTMENT and "
              "LOCAL/REMOTE for all four amplifiers",
        proves=(
            "None of the four front-panel controls can be read back over any "
            "interface, and none was recorded for the 08-04 runs. They have been "
            "uncontrolled variables in every measurement taken so far."
        ),
        drive=DriveSet.NONE,
        amps=(),
        profile="",
        target_ains=(),
        factor="",
        levels=(),
        level_labels=(),
        load_condition="ANALYSIS_ONLY",
        raw_mode=RawMode.NONE,
        expect_trip=False,
        operator_paced=True,
        hold_s=0.0,
        settle_s=0.0,
        notes="Opens the front-panel state dialog. No hardware commanded.",
    ),

    # ========================================================================
    # Group 1 — Y+ instability diagnosis
    # ========================================================================

    TestSpec(
        test_id="G1.1",
        group_num=1,
        group_name="Y+ instability",
        title="Raw waveform capture on Y+ at a fixed setpoint",
        proves=(
            "Documents the anomaly on Y+: 355 V peak-to-peak within-window "
            "voltage swing and 0.641 mA quiescent current at 0 V command, "
            "as seen in the 08-04 measurement, and whether they persist at "
            "higher commanded setpoints."
        ),
        drive=DriveSet.SINGLE,
        amps=("Y+",),
        profile="SINGLE_FAST",
        target_ains=("AIN9", "AIN8"),   # Y+ voltage, then Y+ current
        factor="commanded kV",
        levels=(0.0, 1.0, 3.0),
        level_labels=("0.0 kV", "1.0 kV", "3.0 kV"),
        load_condition="ON_PLATES",
        raw_mode=RawMode.FULL,
        expect_trip=False,
        operator_paced=False,
        hold_s=10.0,
        settle_s=2.0,
        notes=(
            "Two target AINs per level: in SINGLE_FAST only one channel is "
            "streamed, so voltage and current are not simultaneous. The runner "
            "performs the same level twice, once per target, back to back. "
            "SAFETY: current-based trip interlock is unavailable when streaming "
            "the voltage monitor (AIN9). Operator must watch the front-panel LEDs."
        ),
    ),

    TestSpec(
        test_id="G1.2",
        group_num=1,
        group_name="Y+ instability",
        title="Same raw capture on X+ as the control",
        proves=(
            "Establishes whether the instability seen on Y+ is specific to that "
            "channel or present on all amplifiers. X+ is the control measurement."
        ),
        drive=DriveSet.SINGLE,
        amps=("X+",),
        profile="SINGLE_FAST",
        target_ains=("AIN13", "AIN12"),   # X+ voltage, then X+ current
        factor="commanded kV",
        levels=(0.0, 1.0, 3.0),
        level_labels=("0.0 kV", "1.0 kV", "3.0 kV"),
        load_condition="ON_PLATES",
        raw_mode=RawMode.FULL,
        expect_trip=False,
        operator_paced=False,
        hold_s=10.0,
        settle_s=2.0,
        notes=(
            "Control measurement, identical procedure to G1.1 but on X+. "
            "SAFETY: current-based trip interlock is unavailable when streaming "
            "the voltage monitor (AIN13). Operator must watch the front-panel LEDs."
        ),
    ),

    TestSpec(
        test_id="G1.3",
        group_num=1,
        group_name="Y+ instability",
        title="Capture at commanded ZERO volts specifically",
        proves=(
            "Isolates the 0.641 mA quiescent current anomaly on Y+ — a 60 s "
            "capture at commanded 0 V on the voltage and current monitors. "
            "Sufficient length for frequency-domain analysis of the drift source."
        ),
        drive=DriveSet.SINGLE,
        amps=("Y+",),
        profile="SINGLE_FAST",
        target_ains=("AIN9", "AIN8"),
        factor="time",
        levels=(60.0,),
        level_labels=("60 s",),
        load_condition="ON_PLATES",
        raw_mode=RawMode.FULL,
        expect_trip=False,
        operator_paced=False,
        hold_s=60.0,
        settle_s=2.0,
        notes=(
            "Single level: 60 s hold at 0 V command. "
            "SAFETY: current-based trip interlock unavailable on voltage monitor target. "
            "Operator must watch the front-panel LEDs."
        ),
    ),

    TestSpec(
        test_id="G1.4",
        group_num=1,
        group_name="Y+ instability",
        title="Sweep the DYNAMIC ADJUSTMENT pot on Y+, re-capture",
        proves=(
            "Tests whether the DYNAMIC ADJUSTMENT pot on the Y+ amplifier is "
            "the source of the instability. Captures the waveform at the "
            "original pot position and after the operator has swept it."
        ),
        drive=DriveSet.SINGLE,
        amps=("Y+",),
        profile="SINGLE_FAST",
        target_ains=("AIN9", "AIN8"),
        factor="pot position",
        levels=("original", "swept"),
        level_labels=("original", "swept"),
        load_condition="ON_PLATES",
        raw_mode=RawMode.FULL,
        expect_trip=False,
        operator_paced=True,
        hold_s=10.0,
        settle_s=2.0,
        notes=(
            "Operator-paced: between 'original' and 'swept' levels, the runner "
            "commands 0 kV and turns off the output, then prompts the operator to "
            "physically sweep the DYNAMIC ADJUSTMENT pot. "
            "SAFETY: current-based trip interlock unavailable on voltage monitor target. "
            "Operator must watch the front-panel LEDs."
        ),
    ),

    TestSpec(
        test_id="G1.5",
        group_num=1,
        group_name="Y+ instability",
        title="AMPLIFIER SWAP: drive the Y+ cable from the X+ amplifier",
        proves=(
            "Determines whether the instability follows the cable/plate (load) "
            "or the amplifier. Operator re-plugs the Y+ steerer cable into the "
            "X+ amplifier output before the run."
        ),
        drive=DriveSet.SWAPPED,
        amps=("Y+", "X+"),
        profile="WAVEFORM",
        target_ains=_ALL_8_AINS,
        factor="commanded kV",
        levels=(-5.0, -4.0, -3.0, -2.0, -1.0, 0.0, 1.0, 2.0, 3.0, 4.0, 5.0),
        level_labels=(
            "-5.0 kV", "-4.0 kV", "-3.0 kV", "-2.0 kV", "-1.0 kV",
            "0.0 kV", "1.0 kV", "2.0 kV", "3.0 kV", "4.0 kV", "5.0 kV",
        ),
        load_condition="ON_PLATES",
        raw_mode=RawMode.NONE,
        expect_trip=False,
        operator_paced=True,
        hold_s=10.0,
        settle_s=2.0,
        notes=(
            "Cables must be swapped at the AMPLIFIER OUTPUTS only — the steerer "
            "connection is not touched. Operator-paced: acknowledge swap before run. "
            "After the run, operator must restore the original cabling."
        ),
    ),

    # ========================================================================
    # Group 2 — Trip threshold & inrush
    # ========================================================================

    TestSpec(
        test_id="G2.1",
        group_num=2,
        group_name="Trip & inrush",
        title="Measure the actual trip current per amplifier",
        proves=(
            "Establishes the true trip threshold of each amplifier as-configured, "
            "which is an uncontrolled variable in all prior measurements. The trip "
            "level is found by walking up a 0.1 kV ladder until the interlock fires, "
            "then backing off to 0 kV."
        ),
        drive=DriveSet.EACH,
        amps=(),
        profile="WAVEFORM",
        target_ains=_ALL_8_AINS,
        factor="commanded kV",
        levels=_G2_1_LEVELS,
        level_labels=_G2_1_LABELS,
        load_condition="ON_PLATES",
        raw_mode=RawMode.NONE,
        expect_trip=True,
        operator_paced=False,
        hold_s=10.0,
        settle_s=1.0,
        notes=(
            "Slow ramp to trip: walk the ladder ascending, record the level and "
            "current monitor reading where the interlock fires, then command 0 kV. "
            "If the ladder completes without a trip, record 'did not trip below 5 kV'."
        ),
    ),

    TestSpec(
        test_id="G2.2",
        group_num=2,
        group_name="Trip & inrush",
        title="Set all four pots to a common documented value, re-verify",
        proves=(
            "After G2.1 establishes each amplifier's trip threshold, this test "
            "brings all four to a common pot value derived from the AC operating "
            "point current, and verifies that the setting holds."
        ),
        drive=DriveSet.EACH,
        amps=(),
        profile="WAVEFORM",
        target_ains=_ALL_8_AINS,
        factor="trip setting",
        levels=("common value",),
        level_labels=("common value",),
        load_condition="ON_PLATES",
        raw_mode=RawMode.NONE,
        expect_trip=False,
        operator_paced=True,
        hold_s=10.0,
        settle_s=1.0,
        notes=(
            "Operator-paced. The 'common value' is chosen from G2.1 results and "
            "the G3 capacitance measurement (see Appendix A item 4). Operator "
            "physically sets each amplifier's front-panel CURRENT ADJUSTMENT pot "
            "before the run. G0.1 must be re-run after this to update the record."
        ),
    ),

    TestSpec(
        test_id="G2.3",
        group_num=2,
        group_name="Trip & inrush",
        title="Largest DC step that does NOT trip, at fixed settle",
        proves=(
            "Characterises inrush current as a function of step amplitude. The "
            "current monitor is targeted (not voltage) so the interlock is active "
            "throughout. The largest non-tripping step informs G8.2."
        ),
        drive=DriveSet.EACH,
        amps=(),
        profile="SINGLE_FAST",
        target_ains=_CURRENT_AINS,   # current monitors only
        factor="step size",
        levels=(0.1, 0.2, 0.5, 1.0),
        level_labels=("0.1 kV", "0.2 kV", "0.5 kV", "1.0 kV"),
        load_condition="ON_PLATES",
        raw_mode=RawMode.FULL,
        expect_trip=False,
        operator_paced=False,
        hold_s=10.0,
        settle_s=2.0,
        notes=(
            "The capture must be open BEFORE the step is commanded so the inrush "
            "transient (first 20-70 samples at 100 kS/s) is not missed. "
            "Current monitor target keeps the trip interlock armed throughout."
        ),
    ),

    TestSpec(
        test_id="G2.4",
        group_num=2,
        group_name="Trip & inrush",
        title="Ramped command vs stepped command",
        proves=(
            "Tests whether sub-stepping the command (N smaller SCPI writes via "
            "QTimer) reduces peak inrush current compared to a single step. "
            "Limitation: SCPI round-trip latency (~ms) means true microsecond "
            "ramping is not achievable; see docs/amp_test_matrix.md."
        ),
        drive=DriveSet.EACH,
        amps=(),
        profile="SINGLE_FAST",
        target_ains=_CURRENT_AINS,
        factor="command shape",
        levels=("step", "ramped"),
        level_labels=("step", "ramped"),
        load_condition="ON_PLATES",
        raw_mode=RawMode.FULL,
        expect_trip=False,
        operator_paced=False,
        hold_s=10.0,
        settle_s=2.0,
        notes=(
            "Ramp is implemented as RAMP_SUBSTEPS=10 command_dc calls spaced by "
            "RAMP_SUBSTEP_MS=20 ms via QTimer (see amp_test_config). This is NOT "
            "a microsecond ramp — the Findings sheet's 26 µs figure requires the "
            "generator's own sweep/burst mode or an analog slew-limited path. "
            "FLAG TO ISAAC: microsecond ramping is out of scope here."
        ),
    ),

    TestSpec(
        test_id="G2.5",
        group_num=2,
        group_name="Trip & inrush",
        title="Trip recovery behaviour in LIMIT vs TRIP mode",
        proves=(
            "Documents how each amplifier recovers after a trip in LIMIT mode "
            "(output capped) vs TRIP mode (output cut). Required to understand "
            "whether automatic recovery is safe during a beamline run."
        ),
        drive=DriveSet.SINGLE,
        amps=("Y-", "X+"),
        profile="WAVEFORM",
        target_ains=_ALL_8_AINS,
        factor="mode",
        levels=("LIMIT", "TRIP"),
        level_labels=("LIMIT", "TRIP"),
        load_condition="ON_PLATES",
        raw_mode=RawMode.NONE,
        expect_trip=True,
        operator_paced=True,
        hold_s=30.0,
        settle_s=2.0,
        notes=(
            "Operator-paced: between LIMIT and TRIP modes, the operator toggles "
            "the front-panel LIMIT/TRIP switch. The runner commands 0 kV and "
            "turns off the output before prompting. Run on Y- and X+ in sequence."
        ),
    ),

    # ========================================================================
    # Group 3 — Load capacitance
    # ========================================================================

    TestSpec(
        test_id="G3.1",
        group_num=3,
        group_name="Load capacitance",
        title="Back-solve capacitance from the AC current monitor",
        proves=(
            "Measures the total capacitive load on each amplifier output by "
            "applying a known AC voltage and measuring the resulting reactive "
            "current: C = I_pk / (2*pi*f*V_pk). Replaces the 130 pF guess used "
            "by the envelope guard in amp_test_config.LOAD_CAP_PF_DEFAULT."
        ),
        drive=DriveSet.EACH,
        amps=(),
        profile="WAVEFORM",
        target_ains=_ALL_8_AINS,
        factor="frequency",
        levels=(100.0, 500.0, 1000.0),
        level_labels=("100 Hz", "500 Hz", "1000 Hz"),
        load_condition="ON_PLATES",
        raw_mode=RawMode.NONE,
        expect_trip=False,
        operator_paced=False,
        hold_s=10.0,
        settle_s=3.0,
        notes=(
            "Fixed peak amplitude of 2.0 kV at every frequency. "
            "The CSV records peak and rms of both voltage and current monitors so "
            "C = I_pk / (2*pi*f*V_pk) can be recovered in processing/. "
            "Do NOT compute C here. After running, record load_cap_pf_measured "
            "in front_panel_state.json to replace the LOAD_CAP_PF_DEFAULT guess."
        ),
    ),

    # ========================================================================
    # Group 4 — Noise floor
    # ========================================================================

    TestSpec(
        test_id="G4.1",
        group_num=4,
        group_name="Noise floor",
        title="Wiring + DC floor, amplifiers POWERED OFF",
        proves=(
            "Establishes the measurement chain's intrinsic noise floor with no "
            "amplifier contribution: cable, terminal board, and T7 ADC noise only. "
            "Baseline for G4.2 and G4.3."
        ),
        drive=DriveSet.NONE,
        amps=(),
        profile="WAVEFORM",
        target_ains=_ALL_8_AINS,
        factor="time",
        levels=(300.0,),
        level_labels=("300 s",),
        load_condition="ON_PLATES_NO_HV",
        raw_mode=RawMode.DECIMATED,
        expect_trip=False,
        operator_paced=False,
        hold_s=300.0,
        settle_s=0.0,
        notes=(
            "ALL FOUR EEL5000 MAINS SWITCHES MUST BE OFF AND UNITS UNPLUGGED. "
            "The runner asserts that no generator command is issued for the "
            "duration. Checklist must require explicit acknowledgement of the "
            "powered-off state before the run is permitted."
        ),
    ),

    TestSpec(
        test_id="G4.2",
        group_num=4,
        group_name="Noise floor",
        title="Quiescent noise, amplifiers ON at commanded 0 V",
        proves=(
            "Separates the amplifier's own quiescent noise from wiring noise. "
            "All four amplifiers are energised and commanding 0 V. Comparing with "
            "G4.1 isolates the amplifier contribution."
        ),
        drive=DriveSet.ALL,
        amps=(),
        profile="WAVEFORM",
        target_ains=_ALL_8_AINS,
        factor="time",
        levels=(300.0,),
        level_labels=("300 s",),
        load_condition="ON_PLATES",
        raw_mode=RawMode.DECIMATED,
        expect_trip=False,
        operator_paced=False,
        hold_s=300.0,
        settle_s=2.0,
        notes="All four channels commanded to 0 V. No excitation; quiescent only.",
    ),

    TestSpec(
        test_id="G4.3",
        group_num=4,
        group_name="Noise floor",
        title="Allan deviation record at commanded 0 V, 1 h",
        proves=(
            "Provides sufficient length for Allan deviation analysis out to "
            "tau = 1000 s, fully resolved by the 10 Hz window statistics the "
            "existing CSV path writes. Raw capture would be ~1.4 GB for 1 h of "
            "WAVEFORM data and is not needed for this analysis."
        ),
        drive=DriveSet.ALL,
        amps=(),
        profile="WAVEFORM",
        target_ains=_ALL_8_AINS,
        factor="time",
        levels=(3600.0,),
        level_labels=("3600 s",),
        load_condition="ON_PLATES",
        raw_mode=RawMode.DECIMATED,   # MUST be DECIMATED — see proves text
        expect_trip=False,
        operator_paced=False,
        hold_s=3600.0,
        settle_s=2.0,
        notes=(
            "MUST be DECIMATED, not FULL. 1 h of WAVEFORM raw is "
            "~360 M samples (≈1.4 GB) and Allan deviation to tau=1000 s is "
            "fully resolved by the 10 Hz window statistics already in the CSV."
        ),
    ),

    TestSpec(
        test_id="G4.4",
        group_num=4,
        group_name="Noise floor",
        title="Noise spectrum (Welch PSD) at 0 V, one channel at a time",
        proves=(
            "Provides 10 s of 100 kS/s data per channel, giving 0.1 Hz frequency "
            "resolution — sufficient to separate a 60 Hz power-line component from "
            "its 180 Hz third harmonic. All eight amp monitors are characterised."
        ),
        drive=DriveSet.ALL,
        amps=(),
        profile="SINGLE_FAST",
        target_ains=_ALL_8_AINS,
        factor="channel",
        levels=_ALL_8_AINS,            # runner iterates AINs rather than setpoints
        level_labels=_ALL_8_AINS,
        load_condition="ON_PLATES",
        raw_mode=RawMode.FULL,
        expect_trip=False,
        operator_paced=False,
        hold_s=10.0,
        settle_s=2.0,
        notes=(
            "10 s at 100 kS/s gives 0.1 Hz frequency resolution. "
            "Levels are AIN names; the runner iterates through each channel "
            "as a separate single-channel stream capture."
        ),
    ),

    # ========================================================================
    # Group 5 — DC transfer function
    # ========================================================================

    TestSpec(
        test_id="G5.1",
        group_num=5,
        group_name="DC transfer",
        title="Full ±5 kV ladder, 3 pass types",
        proves=(
            "Maps the commanded-vs-measured DC transfer function of each amplifier "
            "over the full output range. Establishes gain, offset and hysteresis "
            "before the Y+ instability is resolved."
        ),
        drive=DriveSet.EACH,
        amps=(),
        profile="WAVEFORM",
        target_ains=_ALL_8_AINS,
        factor="commanded kV",
        levels=(),                     # delegated to CalibrationRunner.start_sweep
        level_labels=(),
        load_condition="ON_PLATES",
        raw_mode=RawMode.NONE,
        expect_trip=False,
        operator_paced=False,
        hold_s=1.0,
        settle_s=1.5,
        notes="delegate:CalibrationRunner.start_sweep",
    ),

    TestSpec(
        test_id="G5.2",
        group_num=5,
        group_name="DC transfer",
        title="Repeat the full ±5 kV ladder once the Y+ fault is resolved",
        proves=(
            "Confirms that resolving the Y+ instability changes the transfer "
            "function. Identical to G5.1 in procedure; differs only in when "
            "it is run (after G1 diagnosis and fix)."
        ),
        drive=DriveSet.EACH,
        amps=(),
        profile="WAVEFORM",
        target_ains=_ALL_8_AINS,
        factor="commanded kV",
        levels=(),
        level_labels=(),
        load_condition="ON_PLATES",
        raw_mode=RawMode.NONE,
        expect_trip=False,
        operator_paced=False,
        hold_s=1.0,
        settle_s=1.5,
        notes="delegate:CalibrationRunner.start_sweep",
    ),

    TestSpec(
        test_id="G5.3",
        group_num=5,
        group_name="DC transfer",
        title="Settle-time confirmation: 0.5 s vs 5 s",
        proves=(
            "Determines whether the 1.5 s default settle time is adequate or "
            "whether a longer settle materially changes the measured transfer "
            "function. Informs the choice of CAL_SETTLE_S."
        ),
        drive=DriveSet.EACH,
        amps=(),
        profile="WAVEFORM",
        target_ains=_ALL_8_AINS,
        factor="settle",
        levels=(0.5, 5.0),
        level_labels=("0.5 s", "5.0 s"),
        load_condition="ON_PLATES",
        raw_mode=RawMode.NONE,
        expect_trip=False,
        operator_paced=False,
        hold_s=1.0,
        settle_s=0.5,
        notes=(
            "Two full ladders, one at each settle time. Uses CalibrationRunner."
            "start_sweep(settle_s=...) — the optional settle_s kwarg added in "
            "Phase 9. Both runs written to the same run folder."
        ),
    ),

    # ========================================================================
    # Group 6 — Chain isolation
    # ========================================================================

    TestSpec(
        test_id="G6.1",
        group_num=6,
        group_name="Chain isolation",
        title="DMM at each function generator output",
        proves=(
            "Isolates the generator's own DC accuracy from the amplifier chain, "
            "with the amplifier disconnected. Commanded generator volts vs DMM "
            "reading; no high voltage anywhere."
        ),
        drive=DriveSet.NONE,
        amps=(),
        profile="",
        target_ains=(),
        factor="commanded V",
        levels=(-5.0, -4.0, -3.0, -2.0, -1.0, 0.0, 1.0, 2.0, 3.0, 4.0, 5.0),
        level_labels=(
            "-5.0 V", "-4.0 V", "-3.0 V", "-2.0 V", "-1.0 V",
            "0.0 V",
            "1.0 V", "2.0 V", "3.0 V", "4.0 V", "5.0 V",
        ),
        load_condition="ANALYSIS_ONLY",
        raw_mode=RawMode.NONE,
        expect_trip=False,
        operator_paced=True,
        hold_s=10.0,
        settle_s=1.0,
        notes=(
            "Commanded values are GENERATOR VOLTS, not kV — do NOT multiply by "
            "_AMP_GAIN. The amplifier must be disconnected from the generator loop "
            "before this test. The GUI shows an editable grid for DMM readings; "
            "typed values go to converted_value with converted_unit='V_DMM'."
        ),
    ),

    TestSpec(
        test_id="G6.2",
        group_num=6,
        group_name="Chain isolation",
        title="LabJack AIN4 reads the generator output, simultaneous with the DMM",
        proves=(
            "Would separate T7 ADC gain error from generator error, attributing "
            "the −0.39 %/kV scale error between the two instruments. "
            "Currently BLOCKED: AIN4 is spare and excluded from all stream profiles."
        ),
        drive=DriveSet.NONE,
        amps=(),
        profile="",
        target_ains=(),
        factor="commanded V",
        levels=(-5.0, -4.0, -3.0, -2.0, -1.0, 0.0, 1.0, 2.0, 3.0, 4.0, 5.0),
        level_labels=(
            "-5.0 V", "-4.0 V", "-3.0 V", "-2.0 V", "-1.0 V",
            "0.0 V",
            "1.0 V", "2.0 V", "3.0 V", "4.0 V", "5.0 V",
        ),
        load_condition="ANALYSIS_ONLY",
        raw_mode=RawMode.NONE,
        expect_trip=False,
        operator_paced=False,
        hold_s=0.0,
        settle_s=0.0,
        notes=(
            "BLOCKED: see Appendix A item 1. AIN4 is a spare channel excluded from "
            "every stream profile. labjack_stream_config asserts at import that every "
            "single-channel choice is an amp monitor. Three options (Isaac to decide): "
            "(a) add a GEN_PROBE profile; (b) command-response outside the stream; "
            "(c) drop the test. The −0.39 %/kV scale error stays unattributed if dropped."
        ),
    ),

    TestSpec(
        test_id="G6.3",
        group_num=6,
        group_name="Chain isolation",
        title="GENERATOR SWAP: drive the X amplifiers from the Y generator",
        proves=(
            "Tests whether the instability observed on Y+ follows the generator or "
            "the amplifier path by swapping the A/B generator assignment."
        ),
        drive=DriveSet.EACH,
        amps=(),
        profile="WAVEFORM",
        target_ains=_ALL_8_AINS,
        factor="generator",
        levels=("A/B swapped",),
        level_labels=("A/B swapped",),
        load_condition="ON_PLATES",
        raw_mode=RawMode.NONE,
        expect_trip=False,
        operator_paced=True,
        hold_s=10.0,
        settle_s=2.0,
        notes=(
            "Operator-paced: operator swaps the VISA/USB cable assignments between "
            "generator A and generator B before the run starts. Restore after. "
            "CHANNEL_ROLE mapping must be reflected in the run metadata."
        ),
    ),

    # ========================================================================
    # Group 7 — AC characterisation
    # ========================================================================

    TestSpec(
        test_id="G7.1",
        group_num=7,
        group_name="AC characterisation",
        title="Amplitude ladder at 100 Hz",
        proves=(
            "Measures the AC voltage transfer function (Bode magnitude) at 100 Hz "
            "across the full amplitude range. Combined with G7.2 and G7.3, maps "
            "how gain varies with frequency."
        ),
        drive=DriveSet.EACH,
        amps=(),
        profile="WAVEFORM",
        target_ains=_ALL_8_AINS,
        factor="peak kV",
        levels=(0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0),
        level_labels=(
            "0.5 kV", "1.0 kV", "1.5 kV", "2.0 kV", "2.5 kV",
            "3.0 kV", "3.5 kV", "4.0 kV", "4.5 kV", "5.0 kV",
        ),
        load_condition="ON_PLATES",
        raw_mode=RawMode.NONE,
        expect_trip=False,
        operator_paced=False,
        hold_s=10.0,
        settle_s=2.0,
        notes="Fixed frequency: 100 Hz. Amplitude swept 0.5 → 5.0 kV peak.",
    ),

    TestSpec(
        test_id="G7.2",
        group_num=7,
        group_name="AC characterisation",
        title="Amplitude ladder at 1 kHz",
        proves=(
            "Same as G7.1 but at 1 kHz — near the EEL5000 bandwidth boundary. "
            "Reveals whether gain starts to roll off approaching 1 kHz."
        ),
        drive=DriveSet.EACH,
        amps=(),
        profile="WAVEFORM",
        target_ains=_ALL_8_AINS,
        factor="peak kV",
        levels=(0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0),
        level_labels=(
            "0.5 kV", "1.0 kV", "1.5 kV", "2.0 kV", "2.5 kV",
            "3.0 kV", "3.5 kV", "4.0 kV", "4.5 kV", "5.0 kV",
        ),
        load_condition="ON_PLATES",
        raw_mode=RawMode.NONE,
        expect_trip=False,
        operator_paced=False,
        hold_s=10.0,
        settle_s=2.0,
        notes="Fixed frequency: 1000 Hz. Amplitude swept 0.5 → 5.0 kV peak.",
    ),

    TestSpec(
        test_id="G7.3",
        group_num=7,
        group_name="AC characterisation",
        title="Amplitude ladder at 2 kHz",
        proves=(
            "Same as G7.1 but at 2 kHz — the intended fast-axis frequency. "
            "Reveals gain at the operating point and envelope constraint at "
            "full amplitude."
        ),
        drive=DriveSet.EACH,
        amps=(),
        profile="WAVEFORM",
        target_ains=_ALL_8_AINS,
        factor="peak kV",
        levels=(0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0),
        level_labels=(
            "0.5 kV", "1.0 kV", "1.5 kV", "2.0 kV", "2.5 kV",
            "3.0 kV", "3.5 kV", "4.0 kV", "4.5 kV", "5.0 kV",
        ),
        load_condition="ON_PLATES",
        raw_mode=RawMode.NONE,
        expect_trip=False,
        operator_paced=False,
        hold_s=10.0,
        settle_s=2.0,
        notes="Fixed frequency: 2000 Hz. Amplitude swept 0.5 → 5.0 kV peak.",
    ),

    TestSpec(
        test_id="G7.4",
        group_num=7,
        group_name="AC characterisation",
        title="Frequency ladder at 2 kV peak",
        proves=(
            "Maps the frequency response (Bode magnitude) from 1 Hz to 2 kHz "
            "at a fixed 2 kV peak. Combined with G7.1–G7.3, fully characterises "
            "the AC transfer function across both frequency and amplitude axes."
        ),
        drive=DriveSet.EACH,
        amps=(),
        profile="WAVEFORM",
        target_ains=_ALL_8_AINS,
        factor="frequency",
        levels=(1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0, 200.0, 500.0, 1000.0, 2000.0),
        level_labels=(
            "1 Hz", "2 Hz", "5 Hz", "10 Hz", "20 Hz", "50 Hz",
            "100 Hz", "200 Hz", "500 Hz", "1000 Hz", "2000 Hz",
        ),
        load_condition="ON_PLATES",
        raw_mode=RawMode.NONE,
        expect_trip=False,
        operator_paced=False,
        hold_s=10.0,
        settle_s=2.0,
        notes=(
            "Fixed peak amplitude: 2.0 kV. Settle at each frequency is computed "
            "at run time as max(settle_s, 3.0 / freq_hz) — at 1 Hz this gives 3 s."
        ),
    ),

    TestSpec(
        test_id="G7.5",
        group_num=7,
        group_name="AC characterisation",
        title="Frequency ladder above 3 kHz, single-channel voltage monitor",
        proves=(
            "Extends the frequency sweep above 3 kHz where WAVEFORM gives only "
            "~4 samples/cycle. SINGLE_FAST provides 100 kS/s for accurate "
            "amplitude measurement. The monitor BNC rolls off near 11 kHz so "
            "nothing above that is faithfully captured."
        ),
        drive=DriveSet.EACH,
        amps=(),
        profile="SINGLE_FAST",
        target_ains=_VOLTAGE_AINS,   # voltage monitor — magnitude response is the measurement
        factor="frequency",
        levels=(3000.0, 6000.0, 10000.0),
        level_labels=("3000 Hz", "6000 Hz", "10000 Hz"),
        load_condition="ON_PLATES",
        raw_mode=RawMode.FULL,
        expect_trip=False,
        operator_paced=False,
        hold_s=10.0,
        settle_s=2.0,
        notes=(
            "Voltage monitor target: magnitude response is the measurement. "
            "SAFETY: current-based trip interlock unavailable when streaming "
            "voltage monitor. Operator must watch the front-panel LEDs. "
            "Fixed peak amplitude: 2.0 kV."
        ),
    ),

    TestSpec(
        test_id="G7.6",
        group_num=7,
        group_name="AC characterisation",
        title="Trip-limited operating envelope",
        proves=(
            "Maps the (frequency, amplitude) pair at which each amplifier trips. "
            "At each frequency, amplitude ascends from 0.5 kV in 0.25 kV steps "
            "until the interlock fires or the envelope ceiling is reached. "
            "Directly validates the envelope_ceiling_kv model in amp_test_config."
        ),
        drive=DriveSet.EACH,
        amps=(),
        profile="SINGLE_FAST",
        target_ains=_CURRENT_AINS,   # current monitor: interlock must be armed
        factor="frequency",
        levels=(100.0, 500.0, 1000.0, 2000.0, 5000.0, 10000.0),
        level_labels=("100 Hz", "500 Hz", "1000 Hz", "2000 Hz", "5000 Hz", "10000 Hz"),
        load_condition="ON_PLATES",
        raw_mode=RawMode.NONE,
        expect_trip=True,
        operator_paced=False,
        hold_s=5.0,
        settle_s=2.0,
        notes=(
            "Nested walk: at each frequency, amplitude ascends from 0.5 kV in "
            "0.25 kV steps until the interlock fires or envelope_ceiling_kv is "
            "reached. The sequence is flattened at build time. Current monitor "
            "target keeps the trip interlock armed."
        ),
    ),

    # ========================================================================
    # Group 8 — Step response
    # ========================================================================

    TestSpec(
        test_id="G8.1",
        group_num=8,
        group_name="Step response",
        title="Small-signal step, ±0.5 kV, square-wave edge method",
        proves=(
            "Characterises the small-signal step response: rise time, overshoot, "
            "and ringing. A 5 Hz square wave gives ~150 hardware-timed edges in "
            "30 s with no SCPI latency in the measurement path."
        ),
        drive=DriveSet.EACH,
        amps=(),
        profile="SINGLE_FAST",
        target_ains=_VOLTAGE_AINS,
        factor="step size",
        levels=(0.5,),
        level_labels=("0.5 kV",),
        load_condition="ON_PLATES",
        raw_mode=RawMode.FULL,
        expect_trip=False,
        operator_paced=False,
        hold_s=30.0,
        settle_s=2.0,
        notes=(
            "Drive: 5 Hz square wave (STEP_RESPONSE_FREQ_HZ = 5.0 in runner). "
            "30 s hold yields ~150 hardware-timed edges. "
            "SAFETY: current-based trip interlock unavailable on voltage monitor. "
            "Operator must watch the front-panel LEDs."
        ),
    ),

    TestSpec(
        test_id="G8.2",
        group_num=8,
        group_name="Step response",
        title="Large-signal step, as large as the trip setting allows",
        proves=(
            "Characterises large-signal step response including slew-rate "
            "saturation and inrush. The step amplitude is the largest from G2.3 "
            "that did not trip; if G2.3 has not been run, the operator enters it."
        ),
        drive=DriveSet.EACH,
        amps=(),
        profile="SINGLE_FAST",
        target_ains=_VOLTAGE_AINS,
        factor="step size",
        levels=("largest non-tripping",),
        level_labels=("largest non-tripping",),
        load_condition="ON_PLATES",
        raw_mode=RawMode.FULL,
        expect_trip=False,
        operator_paced=False,
        hold_s=30.0,
        settle_s=2.0,
        notes=(
            "The step amplitude is resolved at run time from G2.3's recorded "
            "result if one exists in the run folder; otherwise the operator enters "
            "it. Drive: 5 Hz square wave. "
            "SAFETY: current-based trip interlock unavailable on voltage monitor. "
            "Operator must watch the front-panel LEDs."
        ),
    ),

    # ========================================================================
    # Group 9 — Endurance
    # ========================================================================

    TestSpec(
        test_id="G9.1",
        group_num=9,
        group_name="Endurance",
        title="2 h AC hold at the operating point, attended",
        proves=(
            "Demonstrates that all four amplifiers can sustain the beamline "
            "operating point for the full scan duration without trips, gain drift, "
            "or thermal runaway. The attended cap is ENDURANCE_ON_PLATES_MAX_H; "
            "raising it above DRIFT_MAX_ATTENDED_H requires unattended-HV approval."
        ),
        drive=DriveSet.ALL,
        amps=(),
        profile="WAVEFORM",
        target_ains=_ALL_8_AINS,
        factor="time",
        levels=(7200.0,),
        level_labels=("7200 s (2 h)",),
        load_condition="ON_PLATES",
        raw_mode=RawMode.DECIMATED,
        expect_trip=False,
        operator_paced=False,
        hold_s=7200.0,
        settle_s=2.0,
        notes=(
            "Operating point (frequency, amplitude) are run parameters, defaulted "
            "from envelope_ceiling_kv at the chosen frequency, and shown in the "
            "GUI before the run starts — see Appendix A item 2. "
            "G9.2 modifier: if enabled, a short gain probe is interleaved every "
            "ENDURANCE_PROBE_INTERVAL_S=1200 s (6 probes in 2 h)."
        ),
    ),

    TestSpec(
        test_id="G9.2",
        group_num=9,
        group_name="Endurance",
        title="Interleaved gain probes every 20 min during the hold",
        proves=(
            "Detects whether amplifier gain drifts during the endurance hold. "
            "Probe rows are tagged pass_type='probe' in the CSV so they separate "
            "cleanly from hold rows in processing/."
        ),
        drive=DriveSet.ALL,
        amps=(),
        profile="WAVEFORM",
        target_ains=_ALL_8_AINS,
        factor="probe interval",
        levels=(1200.0,),
        level_labels=("1200 s",),
        load_condition="ON_PLATES",
        raw_mode=RawMode.DECIMATED,
        expect_trip=False,
        operator_paced=False,
        hold_s=1200.0,
        settle_s=2.0,
        notes=(
            "THIS IS A MODIFIER ON G9.1, NOT A SEPARATELY STARTABLE TEST. "
            "Enable it via the G9.1 checklist before starting the hold. "
            "The runner interleaves a short gain probe (25%, 50%, 100% of "
            "operating point amplitude, settle_s each) every ENDURANCE_PROBE_INTERVAL_S. "
            "Six probes in a 2 h block."
        ),
    ),

    TestSpec(
        test_id="G9.3",
        group_num=9,
        group_name="Endurance",
        title="Post-endurance repeat of the G5 DC ladder",
        proves=(
            "Confirms that the DC transfer function has not changed after 2 h of "
            "AC drive at the operating point. A shift here indicates gain drift, "
            "thermal offset, or physical change in the amplifier or load."
        ),
        drive=DriveSet.EACH,
        amps=(),
        profile="WAVEFORM",
        target_ains=_ALL_8_AINS,
        factor="commanded kV",
        levels=(),
        level_labels=(),
        load_condition="ON_PLATES",
        raw_mode=RawMode.NONE,
        expect_trip=False,
        operator_paced=False,
        hold_s=1.0,
        settle_s=1.5,
        notes="delegate:CalibrationRunner.start_sweep",
    ),
)

# ---------------------------------------------------------------------------
# Registry helpers
# ---------------------------------------------------------------------------

def groups() -> list[tuple[int, str]]:
    """Ordered, deduplicated list of (group_num, group_name) pairs."""
    seen: set[int] = set()
    result = []
    for spec in TEST_MATRIX:
        if spec.group_num not in seen:
            seen.add(spec.group_num)
            result.append((spec.group_num, spec.group_name))
    return result


def tests_in_group(group_num: int) -> list[TestSpec]:
    """All TestSpecs belonging to *group_num*, in declaration order."""
    return [s for s in TEST_MATRIX if s.group_num == group_num]


def by_id(test_id: str) -> TestSpec:
    """Return the TestSpec for *test_id*. Raises KeyError with a clear message."""
    for spec in TEST_MATRIX:
        if spec.test_id == test_id:
            return spec
    available = [s.test_id for s in TEST_MATRIX]
    raise KeyError(
        f"Unknown test_id {test_id!r}. Available: {available}"
    )


def total_estimated_bytes(specs) -> int:
    """Sum of estimated_raw_bytes() across an iterable of TestSpecs."""
    return sum(s.estimated_raw_bytes() for s in specs)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import math as _math

    print("=== amp_test_matrix self-test ===")

    # 1. Every test_id is unique and matches ^G[0-9]\.[0-9]+$
    _id_pattern = re.compile(r"^G[0-9]\.[0-9]+$")
    _seen_ids: set[str] = set()
    for _spec in TEST_MATRIX:
        assert _id_pattern.match(_spec.test_id), \
            f"{_spec.test_id!r} does not match ^G[0-9].[0-9]+$"
        assert _spec.test_id not in _seen_ids, f"Duplicate test_id: {_spec.test_id}"
        _seen_ids.add(_spec.test_id)
    print(f"[OK] {len(TEST_MATRIX)} tests — all IDs unique and match pattern")

    # 2. Every profile is "" or a key of STREAM_PROFILES
    for _spec in TEST_MATRIX:
        assert _spec.profile == "" or _spec.profile in STREAM_PROFILES, \
            f"{_spec.test_id}: unknown profile {_spec.profile!r}"
    print("[OK] all profiles are empty or valid STREAM_PROFILES keys")

    # 3. Every AIN in target_ains is a key of AIN_TO_AMP (except profile="")
    for _spec in TEST_MATRIX:
        if _spec.profile == "":
            continue
        for _ain in _spec.target_ains:
            assert _ain in AIN_TO_AMP, \
                f"{_spec.test_id}: target AIN {_ain!r} not in AIN_TO_AMP"
    print("[OK] all target AINs are valid amp monitor channels")

    # 4. Single-channel profiles have len(target_ains) >= 1
    from rbl.config.labjack_stream_config import is_single_channel
    for _spec in TEST_MATRIX:
        if _spec.profile and is_single_channel(_spec.profile):
            assert len(_spec.target_ains) >= 1, \
                f"{_spec.test_id}: single-channel profile needs target_ains"
    print("[OK] all single-channel specs have at least one target AIN")

    # 5. Multi-channel + FULL specs have estimated bytes under cap per capture
    for _spec in TEST_MATRIX:
        if not _spec.profile or _spec.raw_mode is not RawMode.FULL:
            continue
        if is_single_channel(_spec.profile):
            continue
        _bytes = _spec.estimated_raw_bytes()
        assert _bytes < RAW_MAX_SAMPLES_PER_CAPTURE * 4, \
            f"{_spec.test_id}: multi-channel FULL estimate {_bytes} exceeds cap"
    print("[OK] no multi-channel FULL spec exceeds raw capture cap")

    # 6. len(levels) == len(level_labels) for every spec
    for _spec in TEST_MATRIX:
        assert len(_spec.levels) == len(_spec.level_labels), \
            (f"{_spec.test_id}: len(levels)={len(_spec.levels)} != "
             f"len(level_labels)={len(_spec.level_labels)}")
    print("[OK] levels and level_labels have equal length on all specs")

    # 7. SINGLE / SWAPPED specs name amps in AMP_LABELS
    for _spec in TEST_MATRIX:
        if _spec.drive in (DriveSet.SINGLE, DriveSet.SWAPPED):
            assert _spec.amps, f"{_spec.test_id}: SINGLE/SWAPPED must name amps"
            for _amp in _spec.amps:
                assert _amp in AMP_LABELS, \
                    f"{_spec.test_id}: amp {_amp!r} not in AMP_LABELS"
    print("[OK] all SINGLE/SWAPPED specs name valid amp labels")

    # 8. expect_trip=True requires load_condition == "ON_PLATES"
    for _spec in TEST_MATRIX:
        if _spec.expect_trip:
            assert _spec.load_condition == "ON_PLATES", \
                (f"{_spec.test_id}: expect_trip=True but "
                 f"load_condition={_spec.load_condition!r}")
    print("[OK] all expect_trip specs have load_condition ON_PLATES")

    # 9. Groups 0–9 are all present; exactly 10 groups
    _grps = groups()
    _group_nums = [g[0] for g in _grps]
    assert _group_nums == list(range(10)), \
        f"Expected groups 0-9, got: {_group_nums}"
    assert len(_grps) == 10
    print(f"[OK] exactly 10 groups present (G0–G9)")

    # 10. Summary table
    print()
    print(f"  {'Group':<5} {'Name':<24} {'Tests':>5} {'Raw MB':>8}")
    print("  " + "-" * 46)
    for _gnum, _gname in _grps:
        _tests = tests_in_group(_gnum)
        _mb = total_estimated_bytes(_tests) / 1024 ** 2
        print(f"  G{_gnum:<4} {_gname:<24} {len(_tests):>5} {_mb:>8.1f}")

    # 11. Per-group raw bytes under RAW_MAX_BYTES_PER_RUN
    _grand_total = 0
    for _gnum, _gname in _grps:
        _group_bytes = total_estimated_bytes(tests_in_group(_gnum))
        _grand_total += _group_bytes
        assert _group_bytes < RAW_MAX_BYTES_PER_RUN, \
            (f"Group G{_gnum} estimated raw data "
             f"{_group_bytes / 1024**3:.2f} GiB exceeds "
             f"RAW_MAX_BYTES_PER_RUN={RAW_MAX_BYTES_PER_RUN / 1024**3:.0f} GiB")
    print()
    print(f"  Grand total estimated raw: {_grand_total / 1024**2:.1f} MB")
    print(f"  (summed per-group; no single group exceeds "
          f"{RAW_MAX_BYTES_PER_RUN / 1024**3:.0f} GiB)")
    print()
    print("[OK] amp_test_matrix self-test passed")
