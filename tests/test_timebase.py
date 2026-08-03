"""
The 10 MHz cross-unit timebase lock, and the push-pull phase check it exists
to make visible.

The lock moved into Beamline when the Overview tab gained its own checkbox:
the both-EXT guard protects the instruments, so it cannot live in whichever
of two widgets the operator happened to reach for. These tests drive Beamline
directly, with no widget involved, plus the pair-correlation maths that turns
"are these two traces mirror images?" into a number.
"""
import math

import pytest
from unittest.mock import MagicMock

from rbl.hardware.amp_monitor import pair_correlation
from rbl.hardware.amp_trace import AmpTraceBuilder
from rbl.state.beamline import Beamline


def _triangle(peak, n=64, invert=False, shift=0):
    out = []
    for i in range(n):
        f = ((i + shift) % n) / n
        v = 4 * f - 1 if f < 0.5 else 3 - 4 * f
        out.append(peak * (-v if invert else v))
    return out


# ---- Pair correlation --------------------------------------------------------

class TestPairCorrelation:
    def test_mirror_images_correlate_at_minus_one(self):
        a = _triangle(2.0)
        assert pair_correlation(a, _triangle(2.0, invert=True)) == pytest.approx(-1.0)

    def test_amplitude_does_not_change_the_answer(self):
        """It is a PHASE check. A pair driven hard and a pair driven gently
        are equally anti-phase, and a disguised amplitude comparison would
        say otherwise."""
        assert pair_correlation(_triangle(4.0), _triangle(0.2, invert=True)) \
            == pytest.approx(-1.0)

    def test_in_phase_channels_correlate_at_plus_one(self):
        assert pair_correlation(_triangle(2.0), _triangle(2.0)) == pytest.approx(1.0)

    def test_a_quarter_cycle_slip_lands_between(self):
        corr = pair_correlation(_triangle(2.0), _triangle(2.0, invert=True, shift=16))
        assert -0.9 < corr < 0.9

    def test_a_flat_channel_has_no_phase(self):
        """A dead plate is not 'out of phase' — reporting 0 would read as a
        real, measured relationship."""
        assert math.isnan(pair_correlation(_triangle(2.0), [0.0] * 64))

    def test_too_few_samples_is_unknown(self):
        assert math.isnan(pair_correlation([], []))
        assert math.isnan(pair_correlation([1.0], [1.0]))

    def test_nan_samples_are_unknown(self):
        assert math.isnan(pair_correlation([1.0, float("nan"), 2.0], [1.0, 2.0, 3.0]))


# ---- Waveform decimation -----------------------------------------------------

class TestWaveDecimation:
    def test_two_channels_share_one_grid(self):
        """A pair decimated onto different grids shows a phase difference that
        is pure resampling artefact — which is the one thing the overlaid
        trace must never invent."""
        a = _triangle(2.0, n=1000)
        b = _triangle(2.0, n=1000, invert=True)
        da = AmpTraceBuilder.decimate(a)
        db = AmpTraceBuilder.decimate(b)
        assert len(da) == len(db)
        assert pair_correlation(da, db) == pytest.approx(-1.0, abs=1e-6)

    def test_point_count_is_bounded(self):
        assert len(AmpTraceBuilder.decimate(list(range(100_000)))) <= AmpTraceBuilder.WAVE_POINTS

    def test_a_short_window_is_passed_through(self):
        assert len(AmpTraceBuilder.decimate([1.0, 2.0, 3.0])) == 3

    def test_no_waveform_is_empty_not_zeros(self):
        assert AmpTraceBuilder.decimate(None) == ()
        assert AmpTraceBuilder.decimate([]) == ()


# ---- The timebase lock -------------------------------------------------------

@pytest.fixture
def beamline():
    bl = Beamline()
    mgr = MagicMock()
    mgr.A.get_reference_clock.return_value = "INT"
    mgr.B.get_reference_clock.return_value = "INT"
    mgr.B.verify_external_lock.return_value = (True, "EXT")
    bl.dg_a, bl.dg_b = mgr.A, mgr.B
    bl.SETTLE_S = 0.0        # no need to really wait for a mocked PLL
    return bl, mgr


class TestSharedTimebase:
    def test_enabling_sets_only_gen_b_external(self, beamline):
        """Gen A is never set EXT: the [10MHz In/Out] connector is
        bidirectional, so both units driving it damages them."""
        bl, mgr = beamline
        mgr.B.get_reference_clock.return_value = "EXT"

        ok, msg = bl.set_shared_timebase(True)

        assert ok is True
        mgr.A.set_reference_clock.assert_called_once_with("INTernal")
        mgr.B.verify_external_lock.assert_called_once()
        assert "EXT" not in [c.args[0] for c in mgr.A.set_reference_clock.call_args_list]

    def test_both_ext_is_refused_before_anything_is_written(self, beamline):
        bl, mgr = beamline
        mgr.A.get_reference_clock.return_value = "EXT"

        ok, msg = bl.set_shared_timebase(True)

        assert ok is False
        assert "damage" in msg
        mgr.B.verify_external_lock.assert_not_called()
        mgr.B.set_reference_clock.assert_not_called()

    def test_a_failed_lock_reports_failure(self, beamline):
        """The DG1022Z silently falls back to INT with no valid reference, so
        a lock is not a lock until the readback says so."""
        bl, mgr = beamline
        mgr.B.verify_external_lock.return_value = (False, "INT")

        ok, msg = bl.set_shared_timebase(True)

        assert ok is False
        assert "INT" in msg

    def test_disabling_returns_both_to_internal(self, beamline):
        bl, mgr = beamline
        ok, msg = bl.set_shared_timebase(False)
        assert ok is True
        mgr.A.set_reference_clock.assert_called_with("INTernal")
        mgr.B.set_reference_clock.assert_called_with("INTernal")

    def test_one_generator_alone_cannot_share_anything(self, beamline):
        bl, mgr = beamline
        bl.dg_b = None
        ok, msg = bl.set_shared_timebase(True)
        assert ok is False
        assert "connected" in msg
        mgr.A.set_reference_clock.assert_not_called()

    def test_locked_only_for_the_one_correct_configuration(self, beamline):
        bl, mgr = beamline
        mgr.B.get_reference_clock.return_value = "EXT"
        bl.read_timebase()
        assert bl.timebase_locked is True

        mgr.B.get_reference_clock.return_value = "INT"
        bl.read_timebase()
        assert bl.timebase_locked is False

    def test_the_cached_clock_is_published_on_funcgen_state(self, beamline):
        """So a second screen can show the lock without polling the clock
        source at readback rate."""
        bl, mgr = beamline
        mgr.B.get_reference_clock.return_value = "EXT"
        bl.read_timebase()

        seen = []
        bl.funcgens_changed.connect(seen.append)
        bl.ingest_funcgen_readback({"A": True, "B": True}, {}, {})

        assert seen[0].timebase == {"A": "INT", "B": "EXT"}

    def test_a_driver_error_is_reported_not_raised(self, beamline):
        """A bad command from a widget must not take down the event loop."""
        bl, mgr = beamline
        mgr.A.get_reference_clock.side_effect = RuntimeError("VISA timeout")
        failures = []
        bl.command_failed.connect(lambda sub, msg: failures.append((sub, msg)))

        ok, msg = bl.set_shared_timebase(True)

        assert ok is False
        assert failures and failures[0][0] == "funcgen"
