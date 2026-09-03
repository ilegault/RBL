"""
tests/test_bpm_calibration.py
Offline tests for the fiducial-mark calibration - milliseconds to millimetres.

WHAT THESE PIN
--------------
The calibration is one division: 60 mm across the measured gap between two
peaks.  Everything that can go wrong goes wrong in getting an honest gap:

  * the THIRD peak.  A fiducial trace carries the trigger peak as well as
    the two calibration marks, and the written procedure warns in so many
    words not to confuse them.  Measuring trigger-to-mark instead of
    mark-to-mark gives a scale that is wrong by whatever fraction of the
    span the trigger sits at, with no symptom - the beam just reads the
    wrong size in millimetres for the rest of the session.
  * SUB-SAMPLE position.  The gap is a difference of two apex positions, so
    rounding each to the nearest sample lets two half-sample errors add.
  * a scale that is applied but not SAID.  An uncalibrated tab reads in
    milliseconds; a wrongly-calibrated one reads in millimetres and looks
    exactly like a right one.  Hence: NaN when uncalibrated, seconds always
    emitted alongside millimetres, and the auto-pick reporting its own
    confidence rather than being trusted silently.
"""
import math
import random

import pytest

from rbl.hardware.bpm_calibration import (
    CAL_ACTIVE_KEY,
    CAL_STORE_KEY,
    CalibrationError,
    active_mm_per_second,
    analyse_fiducials,
    calibration_entry,
    load_calibrations,
    mm_per_second,
    refine_apex,
    seconds_to_mm,
    select_fiducial_peaks,
)

N     = 2500
XINCR = 4e-6            # 10 ms record
SPACING_MM = 60.0


def fiducial_trace(*, sep_ms=6.0, first_ms=2.0, trigger_ms=0.6,
                   trigger_amp=2.0, mark_amp=1.0, mark_sigma_ms=0.06,
                   noise=0.0, seed=7, order=("trigger", "x", "y")):
    """A trace like the BPM's fiducial output: trigger peak + two marks."""
    rng = random.Random(seed)
    centres = {
        "trigger": (trigger_ms, trigger_amp),
        "x":       (first_ms, mark_amp),
        "y":       (first_ms + sep_ms, mark_amp),
    }
    out = []
    for i in range(N):
        t = i * XINCR * 1e3
        y = 0.0
        for key in order:
            mu, amp = centres[key]
            y += amp * math.exp(-0.5 * ((t - mu) / mark_sigma_ms) ** 2)
        if noise:
            y += rng.gauss(0.0, noise)
        out.append(y)
    return out


# ---------------------------------------------------------------------------
# The division
# ---------------------------------------------------------------------------

def test_mm_per_second_is_spacing_over_separation():
    assert mm_per_second(6e-3, 60.0) == pytest.approx(10_000.0)


@pytest.mark.parametrize("sep", [0.0, -1e-3, float("nan")])
def test_a_non_positive_separation_is_refused(sep):
    with pytest.raises(CalibrationError):
        mm_per_second(sep, 60.0)


def test_seconds_to_mm_is_nan_without_a_scale():
    """Uncalibrated must read as ABSENT, not as zero.

    Zero millimetres is a number, and a number gets written down."""
    assert math.isnan(seconds_to_mm(1.5e-3, float("nan")))
    assert math.isnan(seconds_to_mm(float("nan"), 10_000.0))
    assert seconds_to_mm(1.5e-3, 10_000.0) == pytest.approx(15.0)


# ---------------------------------------------------------------------------
# Telling the trigger peak from the marks
# ---------------------------------------------------------------------------

def test_the_tallest_peak_is_dropped_as_the_trigger():
    choice = select_fiducial_peaks([2.0, 1.0, 1.0])
    assert choice["pair"] == (1, 2)
    assert choice["trigger"] == 0
    assert choice["confident"]


def test_the_trigger_is_dropped_wherever_it_sits():
    assert select_fiducial_peaks([1.0, 2.0, 1.0])["pair"] == (0, 2)
    assert select_fiducial_peaks([1.0, 1.0, 2.0])["pair"] == (0, 1)


def test_a_trigger_that_barely_stands_out_is_reported_unconfident():
    """The auto-pick keys off height, so it must say when height is close.

    Silently dividing by whichever peak happened to be a few millivolts
    taller is how a session ends up scaled to trigger-to-mark."""
    choice = select_fiducial_peaks([1.0, 1.05, 1.0])
    assert not choice["confident"]
    assert "confirm" in choice["note"].lower()


def test_two_peaks_are_usable_but_never_confident():
    choice = select_fiducial_peaks([1.0, 1.0])
    assert choice["pair"] == (0, 1)
    assert choice["trigger"] is None
    assert not choice["confident"]


def test_one_peak_is_refused_outright():
    with pytest.raises(CalibrationError):
        select_fiducial_peaks([1.0])


def test_an_override_is_taken_as_given():
    choice = select_fiducial_peaks([2.0, 1.0, 1.0], override=(0, 1))
    assert choice["pair"] == (0, 1)
    assert choice["source"] == "override"
    assert choice["confident"]          # a person looked


def test_an_override_off_the_end_is_refused():
    with pytest.raises(CalibrationError):
        select_fiducial_peaks([2.0, 1.0, 1.0], override=(1, 9))


def test_extra_furniture_keeps_the_two_tallest_survivors():
    choice = select_fiducial_peaks([3.0, 1.0, 0.9, 0.2])
    assert choice["trigger"] == 0
    assert choice["pair"] == (1, 2)


# ---------------------------------------------------------------------------
# Sub-sample apex
# ---------------------------------------------------------------------------

def test_refine_apex_finds_the_vertex_between_samples():
    # Parabola peaking at 10.25
    volts = [-((i - 10.25) ** 2) for i in range(21)]
    assert refine_apex(volts, 10) == pytest.approx(10.25, abs=1e-9)


def test_refine_apex_declines_at_the_edges_and_on_a_flat_top():
    assert refine_apex([1.0, 2.0, 3.0], 0) == 0.0
    assert refine_apex([1.0, 2.0, 3.0], 2) == 2.0
    assert refine_apex([2.0, 2.0, 2.0], 1) == 1.0


# ---------------------------------------------------------------------------
# End to end on a synthetic fiducial trace
# ---------------------------------------------------------------------------

def test_a_clean_fiducial_trace_recovers_the_scale():
    volts = fiducial_trace(sep_ms=6.0)
    r = analyse_fiducials(volts, XINCR, spacing_mm=SPACING_MM)
    assert r["separation_ms"] == pytest.approx(6.0, abs=0.01)
    assert r["mm_per_ms"] == pytest.approx(10.0, rel=1e-3)
    assert r["confident"]
    roles = [p["role"] for p in r["peaks"]]
    assert roles.count("fiducial") == 2
    assert roles.count("trigger") == 1


def test_the_trigger_peak_is_never_one_of_the_two_measured():
    """The failure this whole module exists to prevent.

    Trigger at 0.6 ms, marks at 2.0 and 8.0 ms.  Measuring the trigger to
    the far mark would give 7.4 ms and a scale 19 % low - a plausible-looking
    number that is simply wrong."""
    volts = fiducial_trace(trigger_ms=0.6, first_ms=2.0, sep_ms=6.0)
    r = analyse_fiducials(volts, XINCR, spacing_mm=SPACING_MM)
    assert r["separation_ms"] == pytest.approx(6.0, abs=0.02)
    assert r["separation_ms"] != pytest.approx(7.4, abs=0.1)
    trigger = [p for p in r["peaks"] if p["role"] == "trigger"][0]
    assert trigger["apex_seconds"] * 1e3 == pytest.approx(0.6, abs=0.02)


def test_noise_does_not_move_the_scale_by_more_than_a_percent():
    volts = fiducial_trace(sep_ms=6.0, noise=0.02, seed=11)
    r = analyse_fiducials(volts, XINCR, spacing_mm=SPACING_MM)
    assert r["mm_per_ms"] == pytest.approx(10.0, rel=0.01)


def test_the_scale_does_not_depend_on_the_timebase():
    """mm/s is a property of the BPM's sweep, not of the oscilloscope.

    The SAME physical marks, digitised at two different sample intervals,
    must give the same scale.  This is why the app does not reproduce the
    procedure's "adjust until the peaks are 6 divisions apart" step: the
    knob that step turns is exactly the thing this result is immune to.
    """
    def trace(xincr):
        out = []
        for i in range(N):
            t = i * xincr * 1e3
            y  = 2.0 * math.exp(-0.5 * ((t - 0.6) / 0.06) ** 2)   # trigger
            y +=       math.exp(-0.5 * ((t - 2.0) / 0.06) ** 2)   # X mark
            y +=       math.exp(-0.5 * ((t - 5.0) / 0.06) ** 2)   # Y mark
            out.append(y)
        return out

    slow = analyse_fiducials(trace(4.0e-6), 4.0e-6, spacing_mm=SPACING_MM)
    fast = analyse_fiducials(trace(2.4e-6), 2.4e-6, spacing_mm=SPACING_MM)

    assert slow["separation_ms"] == pytest.approx(3.0, abs=0.02)
    assert fast["separation_ms"] == pytest.approx(3.0, abs=0.02)
    assert fast["mm_per_second"] == pytest.approx(slow["mm_per_second"],
                                                  rel=2e-3)


def test_an_override_measures_the_pair_the_operator_chose():
    volts = fiducial_trace(trigger_ms=0.6, first_ms=2.0, sep_ms=6.0)
    r = analyse_fiducials(volts, XINCR, spacing_mm=SPACING_MM,
                          override=(0, 1))
    assert r["separation_ms"] == pytest.approx(1.4, abs=0.02)
    assert r["source"] == "override"


def test_a_flat_trace_yields_no_calibration():
    with pytest.raises(CalibrationError):
        analyse_fiducials([0.0] * N, XINCR, spacing_mm=SPACING_MM)


def test_a_non_positive_xincr_is_refused():
    with pytest.raises(CalibrationError):
        analyse_fiducials(fiducial_trace(), 0.0, spacing_mm=SPACING_MM)


def test_a_different_head_spacing_scales_the_answer():
    volts = fiducial_trace(sep_ms=6.0)
    r = analyse_fiducials(volts, XINCR, spacing_mm=30.0)
    assert r["mm_per_ms"] == pytest.approx(5.0, rel=1e-3)


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------

def test_calibrations_are_stored_and_read_back_per_bpm():
    volts = fiducial_trace(sep_ms=6.0)
    r = analyse_fiducials(volts, XINCR, spacing_mm=SPACING_MM)
    entry = calibration_entry("BPM 2", r, channel="CH1", xincr=XINCR,
                              saved_iso="2026-09-01T12:00:00+00:00")
    cfg = {CAL_STORE_KEY: {"BPM 2": entry}, CAL_ACTIVE_KEY: "BPM 2"}
    entries, active = load_calibrations(cfg)
    assert active == "BPM 2"
    assert entries["BPM 2"]["mm_per_second"] == pytest.approx(r["mm_per_second"])
    assert active_mm_per_second(cfg) == pytest.approx(r["mm_per_second"])


def test_an_active_name_with_no_entry_reads_as_uncalibrated():
    """Selecting a BPM that was never calibrated must not inherit another's.

    Silently keeping the previous BPM's scale is the single worst outcome
    available here: every reading stays in millimetres and every one of them
    is wrong."""
    cfg = {CAL_STORE_KEY: {"BPM 1": {"mm_per_second": 10_000.0}},
           CAL_ACTIVE_KEY: "BPM 3"}
    entries, active = load_calibrations(cfg)
    assert active == ""
    assert math.isnan(active_mm_per_second(cfg))


def test_an_empty_or_broken_config_reads_as_uncalibrated():
    assert math.isnan(active_mm_per_second({}))
    assert math.isnan(active_mm_per_second({CAL_STORE_KEY: "not a dict"}))
    assert math.isnan(active_mm_per_second(
        {CAL_STORE_KEY: {"a": {"mm_per_second": "oops"}}, CAL_ACTIVE_KEY: "a"}))
    assert math.isnan(active_mm_per_second(
        {CAL_STORE_KEY: {"a": {"mm_per_second": -3.0}}, CAL_ACTIVE_KEY: "a"}))
