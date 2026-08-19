"""
calibration_config.py
Constants and small pure helpers for the HV amplifier calibration feature.

WHAT THIS FEATURE DOES AND DOES NOT DO
---------------------------------------
This sweeps each deflection channel through a bipolar DC ladder and records
what the EEL5000 VOLTAGE MONITOR reports back via the LabJack T7, alongside
what was commanded. The voltage monitor is itself a link in the measurement
chain (command -> Rigol output -> amplifier gain -> monitor divider -> T7
ADC), so this data CANNOT distinguish "the amplifier under-produces" from
"the monitor under-reads" — both produce a byte-identical CSV.

This module therefore holds no correction-factor constant and no "apply"
path. It logs and displays; it never corrects. See docs/calibration.md.

UNCERTAINTY BUDGET
-------------------
From the EEL5000 manual: amplifier accuracy 0.5% FS (+/-25 V at +/-5 kV FS),
voltage monitor accuracy 0.1% FS (+/-5 V), plus LabJack T7 error on the
+/-10 V range and <500 mVrms output noise (0.5 mV at the monitor). Combined,
legitimate disagreement between commanded and measured is roughly +/-30 V.
Deviations inside CAL_UNCERTAINTY_V are noise, not findings.
"""
import math
import random
from enum import Enum
from pathlib import Path

from rbl.config.load_calibration_store import capacitance_pf_for
from rbl.hardware.funcgen_driver import MAX_GEN_VOLTS

# --- Sweep ---------------------------------------------------------------

CAL_MAX_KV  = 5.0    # must equal-or-undercut MAX_GEN_VOLTS; asserted below
CAL_STEP_KV = 0.2     # -> 51 points across -5.0 .. +5.0 inclusive

CAL_SETTLE_S  = 1.5   # discarded after each setpoint change (amplifier + monitor settle)
CAL_COLLECT_S = 1.0   # averaged

# --- Step approach --------------------------------------------------------
#
# A ladder normally walks rung to rung: 2.8 kV -> 3.0 kV -> 3.2 kV, each step
# taken with the load already charged to the previous level. RETURN-TO-ZERO
# instead drops to 0 V and dwells before commanding the next rung, so every
# amplitude is approached from rest.
#
# WHY THE OPTION EXISTS. The EEL5000 changes internal operating range at
# certain output levels, and a range change taken while the load is already
# charged is a much harsher event than the same change taken from 0 V — the
# stage switches under load and the stored energy has somewhere to go. Trips
# clustering at particular amplitudes rather than at the top of the ladder is
# what that looks like from outside.
#
# WHY IT MATTERS BEYOND COMFORT. The application holds one amplitude and one
# frequency for 8+ hours; it never steps between amplitudes. So a limit found
# by stepping may be a limit on STEPPING, not a limit on the amplitude — and
# capping the envelope with it would under-report what the amplifier can
# actually sustain. Return-to-zero measures the amplitude in the condition
# the application will actually see.
#
# Costs one extra settle per rung, so a 51-point pass grows by roughly
# 51 * CAL_ZERO_DWELL_S. That is the trade, and it is why both modes exist.
CAL_ZERO_DWELL_S = 1.0   # hold at 0 V between rungs in return-to-zero mode

CAL_PASSES = ("up", "down", "random")

# --- AC sweep -------------------------------------------------------------
# AC mode commands a sine wave at CAL_AC_FREQ_HZ and sweeps the amplitude
# from 0 to CAL_MAX_KV in CAL_STEP_KV increments.  The setpoints are
# peak output kV (= Vpp/2 * AMP_GAIN / 1000).
CAL_AC_FREQ_HZ      = 1000.0   # 1 kHz — well within the EEL5000 bandwidth (default)
# Quick-pick frequencies in the GUI.  Extended upward in 2026-08 to support
# mapping the capacitive-drive envelope: the 2026-08-11 data showed the load
# is ~1200 pF, not the 130 pF previously assumed, so current — not amplifier
# bandwidth — is the first wall you hit going up in frequency, and it needs
# to be characterised rather than guessed.  See AC_MAX_PEAK_KV below.
CAL_AC_FREQ_PRESETS = [64.0, 250.0, 517.0, 1000.0, 2000.0, 3000.0, 5000.0]
CAL_AC_SETTLE_S  = 2.0      # AC needs more settle time (waveform stabilization)
CAL_AC_COLLECT_S = 2.0      # average over more cycles for a stable RMS reading

# --- AC current safety ----------------------------------------------------
#
# A capacitive load draws  I_pk = 2*pi*f*C*V_pk.  Both f and V are things the
# operator sets, so an AC sweep can walk itself into an over-current simply by
# being asked for a high frequency at full amplitude — the sweep does not have
# to be misconfigured, only ambitious.
#
# Two independent protections, because they fail differently:
#
#   1. PREDICTIVE (AC_MAX_PEAK_KV below).  Before the run, shorten the
#      amplitude ladder so its top rung is under the current limit at the
#      requested frequency.  Cheap, and means a well-posed run never trips.
#   2. REACTIVE (CAL_AC_TRIP_MA, enforced in calibration_runner).  Watch the
#      driven channel's current monitor every window and abort if it exceeds
#      the limit.  This is what catches the cases prediction cannot: a wrong
#      capacitance estimate, an arc, a short, a failing amplifier.
#
# Prediction alone is not enough — it trusts a number.  The interlock alone is
# not enough either: it only ever fires after the amplifier has already been
# over-driven.  Keep both.

# Abort threshold on the driven channel's current monitor, mA peak.
# Set to the EEL5000's continuous DC rating.  This is deliberately NOT the
# 100 mA / 4 ms transient rating: that is a survival spec for inrush, not a
# level a sweep should ever sit at.
CAL_AC_TRIP_MA = 20.0

# --- Interlock qualification ---------------------------------------------
#
# The sweep profile streams the driven pair at 50 kS/s per channel, so a
# 100 ms window is 5000 samples and ONE sample is 20 us.  A bare
# `max(abs(window))` therefore trips on a single 20 us excursion — shorter
# than the amplifier's own 4 ms transient rating, invisible on the live plot,
# and indistinguishable from an ADC glitch or the step transient at the start
# of a new rung in the ladder.  That is a false trip, not a protection.
#
# So the soft interlock qualifies an excursion two ways before acting:
#
#   IN TIME WITHIN A WINDOW  - at least CAL_TRIP_MIN_DURATION_S worth of
#       samples must be over the limit.  Counts samples, not peaks, so a
#       lone spike contributes 20 us and is ignored.
#   ACROSS WINDOWS           - CAL_TRIP_CONSEC_WINDOWS windows in a row must
#       each qualify.  One qualifying window followed by a clean one resets
#       the count.
#
# Plus a blanking period: the first CAL_TRIP_BLANK_WINDOWS windows after a
# setpoint is commanded are exempt from the SOFT limit, because charging a
# 1200 pF load to a new voltage is inrush by definition.
#
# The HARD limit is exempt from all of the above and trips on the first
# window that sees it, with no confirmation and no blanking.  It sits under
# the 100 mA / 4 ms survival spec, so a genuine short still stops fast.

# Instant-trip threshold, mA peak.  No confirmation, no blanking.
CAL_TRIP_HARD_MA = 60.0

# Cumulative time above CAL_AC_TRIP_MA required within one window for that
# window to count as an over-current.  2 ms at 50 kS/s = 100 samples.
CAL_TRIP_MIN_DURATION_S = 0.002

# Consecutive qualifying windows required to trip the soft limit.  At
# GUI_REFRESH_HZ = 10 each window is 100 ms, so 2 => ~200 ms of sustained
# over-current before the abort.  Well inside the amplifier's continuous
# rating headroom, and far longer than any step transient.
CAL_TRIP_CONSEC_WINDOWS = 2

# Windows after a setpoint command during which the SOFT limit is not
# enforced (step inrush).  The hard limit still applies.
CAL_TRIP_BLANK_WINDOWS = 2

# Measured mean load capacitance per channel, pF.  From the 2026-08-11 AC
# sweeps, back-solved at 64 Hz and 517 Hz independently (they agreed to
# within 8%).  Replaces the 130 pF guess that amp_test_config still carries
# as LOAD_CAP_PF_DEFAULT.
#
# This is used ONLY to shorten the ladder before a run.  Nothing downstream
# corrects a measurement with it, and the reactive interlock does not consult
# it — so if this number is wrong the sweep is merely conservative or gets
# stopped by the interlock, never silently mis-recorded.
CAL_LOAD_CAP_PF = 1200.0


# Waveform shape -> the constant in  I_pk = k * f * C * V_pk  for a capacitive
# load.  Peak current is set by the STEEPEST dV/dt the shape reaches, and that
# differs by shape at the same peak voltage:
#
#   Sine      V = V_pk sin(wt),  dV/dt|max = 2*pi*f*V_pk       -> k = 2*pi = 6.283
#   Triangle  slews +/-V_pk in half a period, so |dV/dt| is the
#             constant 4*f*V_pk everywhere                     -> k = 4
#   Square    an ideal square has infinite dV/dt; the real limit is the
#             amplifier's slew rate, not the load.  Treated as sine here so
#             the guard stays conservative rather than pretending to model a
#             slew-limited edge it has no number for.
#
# This mattered: the ladder cap used the sine constant unconditionally while
# AmpDrive._command_ac defaults to Triangle, so every triangle run was capped
# as if it drew 2*pi/4 = 1.571x the current it actually draws.  Conservative,
# but it held the amplitude ladder ~57% below the real current wall on exactly
# the envelope these runs exist to map.
_AC_PEAK_CURRENT_K = {
    "sine":     2.0 * math.pi,
    "triangle": 4.0,
    "ramp":     4.0,     # DG1022Z calls a symmetric triangle RAMP
    "square":   2.0 * math.pi,
}
AC_DEFAULT_SHAPE = "triangle"   # matches AmpDrive._command_ac's default


def ac_shape_k(shape: str = None) -> float:
    """Constant k in I_pk = k*f*C*V_pk for *shape*. Unknown shapes get sine.

    Defaulting an unrecognised shape to the sine constant is deliberate: it is
    the largest k in the table, so an unknown waveform is guarded as the most
    demanding one rather than the least.
    """
    if not shape:
        shape = AC_DEFAULT_SHAPE
    return _AC_PEAK_CURRENT_K.get(str(shape).strip().lower(), 2.0 * math.pi)


def _resolve_load_pf(load_pf: float = None, amp_label: str = None) -> float:
    """load_pf if given; else the Phase 1 measured value for amp_label if one
    has ever been recorded; else CAL_LOAD_CAP_PF.

    Per-channel measurement (rbl.config.load_calibration_store) shortens the
    ladder more accurately than the single global guess once it exists, but
    the fallback chain ends at CAL_LOAD_CAP_PF exactly as before this
    existed — see that constant's docstring: this is used only to size a
    ladder before a run, never to correct a measurement already recorded.
    """
    if load_pf is not None:
        return load_pf
    if amp_label is not None:
        measured = capacitance_pf_for(amp_label)
        if measured is not None:
            return measured
    return CAL_LOAD_CAP_PF


def ac_peak_current_ma(freq_hz: float, peak_kv: float, load_pf: float = None,
                       shape: str = None, amp_label: str = None) -> float:
    """I_pk in mA for a capacitive load driven at *freq_hz* to *peak_kv*.

    I_pk = k * f * C * V_pk, with k from the waveform shape (see ac_shape_k)
    and the unit folding worked out once:
        C [F]     = load_pf * 1e-12
        V_pk [V]  = peak_kv * 1000
        I_pk [mA] = k * f * load_pf * peak_kv * 1e-6

    `load_pf` wins if given; otherwise `amp_label` (if given) tries the
    Phase 1 per-channel measurement store before falling back to
    CAL_LOAD_CAP_PF — see `_resolve_load_pf`.

    PREDICTIVE ONLY.  This sizes the ladder before a run from an assumed
    capacitance; it is not how current is measured.  The measured current comes
    straight off the amplifier's CURRENT monitor and is interpreted from the raw
    samples (see rbl.hardware.ac_metrics) — nothing downstream back-solves a
    capacitance to get it.
    """
    load_pf = _resolve_load_pf(load_pf, amp_label)
    return ac_shape_k(shape) * freq_hz * load_pf * peak_kv * 1e-6


def ac_max_peak_kv(freq_hz: float, load_pf: float = None, trip_ma: float = None,
                   shape: str = None, amp_label: str = None) -> float:
    """Highest AC peak amplitude that stays under the current limit.

    Inverts ac_peak_current_ma for the given shape, then clamps to the
    amplifier's own kV rating.  At low frequency the kV rating binds and this
    just returns CAL_MAX_KV; with CAL_LOAD_CAP_PF and a 20 mA limit the
    crossover sits near 530 Hz for a sine and near 830 Hz for a triangle,
    because a triangle draws less peak current at the same amplitude.

    `load_pf`/`amp_label` resolve exactly as in `ac_peak_current_ma` — this
    stays the authoritative clamp for the calibration runner regardless of
    which load figure it resolves to; nothing else clamps to the hardware
    (docs/AMP_ENVELOPE_AND_HV_SAFETY_PLAN.md Section 9).

    Returns 0.0 for a non-positive frequency rather than raising: callers use
    this to size a ladder, and a zero-length ladder is a better failure than
    an exception inside sequence construction.
    """
    if freq_hz <= 0:
        return 0.0
    load_pf = _resolve_load_pf(load_pf, amp_label)
    if trip_ma is None:
        trip_ma = CAL_AC_TRIP_MA
    denom = ac_shape_k(shape) * freq_hz * load_pf * 1e-6
    if denom <= 0:
        return CAL_MAX_KV
    return min(CAL_MAX_KV, trip_ma / denom)

# Combined amp + monitor + DAQ budget, in volts at the output. See module
# docstring. Deviations inside this band must not be reported as findings.
CAL_UNCERTAINTY_V = 30.0

# The all-8-amp-monitor-AIN stream profile this feature must run under (see
# Phase 0 recon: WAVEFORM and FULL both carry all 8 amp AINs; WAVEFORM is the
# higher per-channel rate of the two and doesn't need the log amps this
# feature has no use for).
CAL_PROFILE = "WAVEFORM"

# Profile for SINGLE-CHANNEL sweeps (DC ladder and AC amplitude ramp).
#
# A sweep drives one amplifier and commands the other three to zero, so seven
# of the eight monitors WAVEFORM digitises are known-quiet by construction.
# AMP_PAIR spends the whole 100 kS/s budget on the two monitors that carry the
# measurement instead, giving 5 000 samples per channel per window against
# WAVEFORM's 1 250 — 4x the sample density, and the headroom to sample a
# multi-kHz drive properly.
#
# THE TRADE-OFF, STATED PLAINLY: the undriven channels are no longer sampled,
# so a pair-mode sweep records NO CROSSTALK DATA.  That is not hypothetical —
# the 2026-08-11 DC sweeps are what revealed the 2.56% Y- -> Y+ coupling, and
# that finding would have been invisible in pair mode.  Run at least one
# WAVEFORM sweep per campaign if crosstalk still matters to you; set this back
# to "WAVEFORM" to make every sweep do it at 1/4 the sample density.
CAL_SWEEP_PROFILE = "AMP_PAIR"

# Drift mode drives all four channels at once, so it must keep every monitor
# in the scan list.  Named separately from CAL_PROFILE so the reason survives.
CAL_DRIFT_PROFILE = "WAVEFORM"

CAL_OUTPUT_DIR = Path.home() / "Desktop" / "RBL_log" / "data" / "calibration"

# --- Drift -----------------------------------------------------------------

DRIFT_DEFAULT_KV       = 3.0
DRIFT_LOG_INTERVAL_S   = 1.0
DRIFT_MAX_UNATTENDED_H = 12.0   # only when load condition is DISCONNECTED
DRIFT_MAX_ATTENDED_H   = 2.0    # hard cap when load condition is ON_PLATES


class LoadCondition(Enum):
    """What is physically connected to the amplifier's HV output.

    The app cannot verify this in software — see the pre-run checklist
    dialog in calibration_tab.py — but it gates the drift-duration guard
    (rbl/services/calibration_runner.py) and is recorded in every CSV's
    metadata sidecar.
    """
    DISCONNECTED = "DISCONNECTED"
    ON_PLATES    = "ON_PLATES"


# --- Sweep point generation -------------------------------------------------

def _base_ladder() -> list:
    """The bipolar ladder from -CAL_MAX_KV to +CAL_MAX_KV, ascending."""
    n = round(2 * CAL_MAX_KV / CAL_STEP_KV) + 1
    return [round(-CAL_MAX_KV + i * CAL_STEP_KV, 10) for i in range(n)]


def _positive_half() -> list:
    """0.0, step, 2*step, ..., CAL_MAX_KV (ascending, excluding 0)."""
    n = round(CAL_MAX_KV / CAL_STEP_KV)
    return [round((i + 1) * CAL_STEP_KV, 10) for i in range(n)]


def sweep_points(pass_type: str, seed: int = None) -> list:
    """The commanded setpoints (kV) for one sweep pass, starting from zero.

    All passes start at 0 and ramp outward so the amplifier never sees a
    large voltage step from rest.  The largest step between consecutive
    points is CAL_STEP_KV (except "random", which shuffles freely but still
    starts and ends at 0).

    "up"     -> 0 .. +CAL_MAX_KV, back through 0, then 0 .. -CAL_MAX_KV, 0
                (positive ascending, then negative descending)
    "down"   -> 0 .. -CAL_MAX_KV, back through 0, then 0 .. +CAL_MAX_KV, 0
                (negative descending first, then positive ascending)
    "random" -> the full ladder shuffled with a seeded RNG (reproducible;
                the seed is recorded in the metadata sidecar)

    Every pass is prefixed and suffixed with an explicit 0.0 point —
    the zero-drift tracker.
    """
    if pass_type == "up":
        pos = _positive_half()          # 0.2, 0.4, ..., 5.0
        neg = [-v for v in pos]         # -0.2, -0.4, ..., -5.0
        seq = pos + list(reversed(pos)) + [0.0] + neg + list(reversed(neg))
    elif pass_type == "down":
        pos = _positive_half()
        neg = [-v for v in pos]
        seq = neg + list(reversed(neg)) + [0.0] + pos + list(reversed(pos))
    elif pass_type == "random":
        rng = random.Random(seed)
        seq = list(_base_ladder())
        rng.shuffle(seq)
    else:
        raise ValueError(f"Unknown pass_type: {pass_type!r}")
    return [0.0] + seq + [0.0]


def ac_sweep_points(max_peak_kv: float = None) -> list:
    """Amplitude setpoints (peak kV) for the AC sweep: 0 → ceiling → 0.

    The sweep ramps amplitude up from 0 in CAL_STEP_KV steps, then back down
    to 0 — one complete up-down cycle.  Every value is a non-negative peak
    output kV (the waveform swings ±this around zero offset).

    max_peak_kv caps the ladder.  Pass ac_max_peak_kv(freq_hz) to keep a
    high-frequency run under the current limit by construction, so the
    interlock is a backstop rather than the thing that ends every run.
    Defaults to CAL_MAX_KV, which reproduces the original behaviour exactly.

    The cap TRUNCATES the standard ladder rather than rescaling it: the rungs
    stay on the same 0.2 kV grid at every frequency, so amplitudes are
    directly comparable across runs. Rescaling would put each frequency on its
    own grid and make cross-frequency comparison an interpolation exercise.
    """
    if max_peak_kv is None:
        max_peak_kv = CAL_MAX_KV
    # 1e-9 guards float representation: a computed ceiling of 4.999999999
    # must not silently drop the 5.0 rung.
    pos = [v for v in _positive_half() if v <= max_peak_kv + 1e-9]
    if not pos:
        return [0.0]
    return [0.0] + pos + list(reversed(pos)) + [0.0]


# --- Self-check at import: fail loudly, not silently over the safety cap ---

assert CAL_MAX_KV <= MAX_GEN_VOLTS, (
    f"CAL_MAX_KV={CAL_MAX_KV} exceeds MAX_GEN_VOLTS={MAX_GEN_VOLTS} — the "
    "function generator's own safety cap. If MAX_GEN_VOLTS was lowered, "
    "CAL_MAX_KV must come down with it, not silently command over it."
)


# --- Self-test ---------------------------------------------------------------

if __name__ == "__main__":
    up = sweep_points("up")
    down = sweep_points("down")

    assert up[0] == 0.0 and up[-1] == 0.0
    assert down[0] == 0.0 and down[-1] == 0.0
    print(f"[OK] sweep_points('up') length {len(up)}, starts and ends at 0.0")
    print(f"[OK] sweep_points('down') length {len(down)}, starts and ends at 0.0")

    # Up pass: first non-zero value should be positive (ramps positive first)
    first_nonzero = next(v for v in up if v != 0.0)
    assert first_nonzero > 0, f"up pass should start positive, got {first_nonzero}"
    print(f"[OK] up pass starts positive ({first_nonzero})")

    # Down pass: first non-zero value should be negative (ramps negative first)
    first_nonzero = next(v for v in down if v != 0.0)
    assert first_nonzero < 0, f"down pass should start negative, got {first_nonzero}"
    print(f"[OK] down pass starts negative ({first_nonzero})")

    # Max step between consecutive points in up/down (excluding random)
    for name, pts in [("up", up), ("down", down)]:
        max_step = max(abs(pts[i+1] - pts[i]) for i in range(len(pts)-1))
        assert max_step <= CAL_STEP_KV + 1e-9, \
            f"{name}: max step {max_step} exceeds {CAL_STEP_KV}"
        print(f"[OK] {name} max consecutive step: {max_step:.3f} kV")

    # Both passes cover the full range
    for name, pts in [("up", up), ("down", down)]:
        assert max(pts) >= CAL_MAX_KV - 1e-9, f"{name} doesn't reach +{CAL_MAX_KV}"
        assert min(pts) <= -CAL_MAX_KV + 1e-9, f"{name} doesn't reach -{CAL_MAX_KV}"
    print("[OK] both passes cover full +/- range")

    r1 = sweep_points("random", seed=42)
    r2 = sweep_points("random", seed=42)
    assert r1 == r2, "same seed must reproduce the same order"
    print("[OK] sweep_points('random', seed=42) is deterministic across two calls")

    for pass_type in CAL_PASSES:
        pts = sweep_points(pass_type, seed=1)
        assert pts[0] == 0.0 and pts[-1] == 0.0, f"{pass_type} pass must bracket 0.0"
    print("[OK] every pass starts and ends at 0.0")

    for pass_type in CAL_PASSES:
        pts = sweep_points(pass_type, seed=1)
        assert all(abs(p) <= CAL_MAX_KV + 1e-12 for p in pts), \
            f"{pass_type} pass exceeds CAL_MAX_KV"
    print("[OK] no point exceeds CAL_MAX_KV in magnitude")

    assert CAL_MAX_KV <= MAX_GEN_VOLTS
    print(f"[OK] CAL_MAX_KV ({CAL_MAX_KV}) <= MAX_GEN_VOLTS ({MAX_GEN_VOLTS})")

    print("[OK] calibration_config self-test passed")
