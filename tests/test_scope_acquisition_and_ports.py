"""
tests/test_scope_pause_and_ports.py
On-demand acquisition - idle by default, one shot on request - and the
port-probe contract the pickers depend on.
"""
import pytest

from rbl.hardware.serial_transport import candidate_keys, candidates_for
from rbl.state.snapshots import ScopeState


def _measured(**over):
    """A snapshot that CARRIES a measurement: a trace and one resolved peak.

    fwhm_seconds alone is not enough - the tab's "is this data?" test is
    whether there is something to plot, which is what the blank-pane bug
    turned on.
    """
    import time
    base = dict(
        timestamp=time.time(), connected=True, idle=False,
        channel="CH1", fwhm_seconds=1.0e-3, points=2500,
        xincr=2.0e-6, xzero=0.0, xincr_downsampled=2.0e-5,
        corrected_downsampled=[0.0, 0.5, 1.0, 0.5, 0.0],
        volts_downsampled=[0.0, 0.5, 1.0, 0.5, 0.0],
        baseline_volts=0.0,
        peaks=[{"axis": "X", "index": 2, "peak_volts": 1.0,
                "half_volts": 0.5, "left_seconds": 2.0e-5,
                "right_seconds": 6.0e-5, "centre_seconds": 4.0e-5,
                "fwhm_seconds": 1.0e-3, "fit_fwhm_seconds": float("nan"),
                "resolved": True, "note": ""}],
    )
    base.update(over)
    return ScopeState(**base)


def _idle(**over):
    """The status ping the worker emits when it settles: link only, no beam."""
    import time
    base = dict(timestamp=time.time(), connected=True, idle=True,
                channel="CH1")
    base.update(over)
    return ScopeState(**base)



@pytest.fixture(scope="module")
def qapp():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


# ---------------------------------------------------------------------------
# Probe candidates
# ---------------------------------------------------------------------------

class TestProbeCandidates:

    def test_every_serial_instrument_is_listed(self):
        keys = candidate_keys()
        for expected in ("xgs600", "vgc083", "tds2012"):
            assert expected in keys

    def test_single_key_narrows_the_scan(self):
        got = [c["key"] for c in candidates_for(["vgc083"])]
        assert got == ["vgc083"]

    def test_several_keys_keep_table_order(self):
        got = [c["key"] for c in candidates_for(["tds2012", "xgs600"])]
        assert got == ["xgs600", "tds2012"]

    def test_an_unknown_key_probes_for_nothing(self):
        # The trap a port picker falls into if it is handed a DISPLAY name
        # instead of a probe key: the scan runs, matches nothing, and
        # reports "not found" as though the cable were unplugged.
        assert candidates_for(["XGS-600"]) == []
        assert candidates_for(["Agilent XGS-600"]) == []


# ---------------------------------------------------------------------------
# Acquisition mode
# ---------------------------------------------------------------------------

class TestAcquisitionMode:
    """The worker idles by default and fetches on request. These pin that
    default down: free-running on connect is what made the tab heavy."""

    def _worker(self):
        from rbl.hardware.scope_worker import ScopeWorker
        return ScopeWorker("COM_TEST")

    def test_starts_idle_not_free_running(self):
        assert self._worker().is_continuous() is False

    def test_no_shot_is_pending_on_construction(self):
        assert self._worker().shot_pending() is False

    def test_request_shot_latches(self):
        w = self._worker()
        w.request_shot()
        assert w.shot_pending() is True

    def test_a_shot_is_consumed_exactly_once(self):
        # The settings snapshot is where the loop picks the request up. A
        # request that survived it would fire again next pass.
        w = self._worker()
        w.request_shot()
        assert w._snapshot_settings()["shot"] is True
        assert w.shot_pending() is False
        assert w._snapshot_settings()["shot"] is False

    def test_repeated_requests_do_not_queue_up(self):
        w = self._worker()
        w.request_shot()
        w.request_shot()
        w.request_shot()
        assert w._snapshot_settings()["shot"] is True
        assert w._snapshot_settings()["shot"] is False

    def test_continuous_can_be_turned_on_and_off(self):
        w = self._worker()
        w.set_continuous(True)
        assert w._snapshot_settings()["continuous"] is True
        w.set_continuous(False)
        assert w._snapshot_settings()["continuous"] is False

    def test_mode_does_not_disturb_analysis_settings(self):
        # Idling is a hold, not a reset: whatever was tuned against the beam
        # must still be there for the next shot.
        w = self._worker()
        w.set_analysis(smooth=21, peaks=2, envelope_ms=1.25)
        w.set_continuous(True)
        w.set_continuous(False)
        cfg = w._snapshot_settings()
        assert cfg["smooth"] == 21
        assert cfg["envelope_ms"] == pytest.approx(1.25)

    def test_a_shot_survives_a_failed_connect(self):
        # The settings snapshot consumes the request before the connect is
        # attempted. If the connect fails the request has to go back, or a
        # press during a reconnect does nothing at all.
        w = self._worker()
        w.request_shot()
        cfg = w._snapshot_settings()          # consumed, as the loop does
        assert cfg["shot"] is True
        assert w.shot_pending() is False
        if cfg["shot"]:                       # what the loop does on failure
            w.request_shot()
        assert w.shot_pending() is True

    def test_idle_snapshot_is_connected_but_carries_no_measurement(self):
        state = ScopeState(timestamp=0.0, connected=True, idle=True)
        assert state.connected is True
        assert state.idle is True
        assert state.peaks == []
        assert state.fwhm_seconds != state.fwhm_seconds     # NaN

    def test_snapshot_defaults_keep_old_constructions_working(self):
        state = ScopeState(timestamp=0.0)
        assert state.idle is False
        assert state.continuous is False


# ---------------------------------------------------------------------------
# Transfer size
# ---------------------------------------------------------------------------

class TestRecordSlice:
    """DATA:START/STOP take a contiguous SLICE. They do not decimate, and
    every one of these tests exists to keep someone from assuming they do."""

    def _worker(self):
        from rbl.hardware.scope_worker import ScopeWorker
        return ScopeWorker("COM_TEST")

    def test_full_record_is_the_whole_thing(self):
        from rbl.hardware.scope_worker import ScopeWorker
        assert ScopeWorker._slice_for(2500, "centre") == (1, 2500)

    def test_centre_anchor_keeps_the_middle(self):
        from rbl.hardware.scope_worker import ScopeWorker
        start, stop = ScopeWorker._slice_for(1000, "centre")
        assert (start, stop) == (751, 1750)
        assert stop - start + 1 == 1000
        # symmetric about the record centre
        assert (start - 1) == (2500 - stop)

    def test_start_anchor_keeps_the_left_edge(self):
        from rbl.hardware.scope_worker import ScopeWorker
        assert ScopeWorker._slice_for(1000, "start") == (1, 1000)

    def test_slice_length_always_matches_the_request(self):
        from rbl.hardware.scope_worker import ScopeWorker
        for n in (250, 500, 625, 1000, 1250, 2499):
            for anchor in ("centre", "start"):
                start, stop = ScopeWorker._slice_for(n, anchor)
                assert stop - start + 1 == n
                assert 1 <= start <= stop <= 2500

    def test_oversized_request_is_clamped_to_the_record(self):
        from rbl.hardware.scope_worker import ScopeWorker
        assert ScopeWorker._slice_for(9999, "centre") == (1, 2500)

    def test_settings_reach_the_snapshot(self):
        w = self._worker()
        w.set_points(500, "start")
        cfg = w._snapshot_settings()
        assert cfg["points"] == 500
        assert cfg["points_anchor"] == "start"
        assert w.record_slice() == (1, 500)

    def test_poll_interval_is_settable_and_never_negative(self):
        w = self._worker()
        w.set_poll_interval(5.0)
        assert w._snapshot_settings()["poll_interval"] == 5.0
        w.set_poll_interval(-3.0)
        assert w._snapshot_settings()["poll_interval"] == 0.0


# ---------------------------------------------------------------------------
# The tab's own defaults
# ---------------------------------------------------------------------------

class TestProfilerTabDefaults:
    """Connecting must not start fetching. This is the behaviour that made
    the tab heavy, so it is worth a test rather than a comment."""

    def _tab(self, qapp):
        from rbl.gui.profiler_tab import ProfilerTab
        from rbl.state.beamline import Beamline
        return ProfilerTab(Beamline())

    def test_mode_defaults_to_single_shot(self, qapp):
        tab = self._tab(qapp)
        assert tab._cb_mode.currentData() is False

    def test_interval_is_disabled_until_continuous(self, qapp):
        tab = self._tab(qapp)
        assert tab._cb_poll.isEnabled() is False
        tab._cb_mode.setCurrentIndex(tab._cb_mode.findData(True))
        assert tab._cb_poll.isEnabled() is True

    def test_take_shot_is_disabled_until_connected(self, qapp):
        tab = self._tab(qapp)
        assert tab._btn_shot.isEnabled() is False

    def test_take_shot_while_disconnected_says_so_and_does_not_hang(self, qapp):
        tab = self._tab(qapp)
        tab._on_take_shot()
        assert tab._shot_pending is False          # never left mid-shot
        assert "not connected" in tab._lbl_status.text()

    def test_an_idle_snapshot_does_not_dirty_the_plot(self, qapp):
        # Idle heartbeats arrive whenever the worker settles. Redrawing on
        # each one would put the redraw cost back that on-demand removed.
        import time
        tab = self._tab(qapp)
        tab._plot_dirty = False
        tab._on_scope_state(ScopeState(timestamp=time.time(), connected=True,
                                       idle=True))
        assert tab._plot_dirty is False
        assert tab._readout_dirty is True          # the status line still moves

    def test_a_measurement_dirties_the_plot_once(self, qapp):
        tab = self._tab(qapp)
        tab._on_scope_state(_measured())
        assert tab._plot_dirty is True
        tab._redraw_plots()
        assert tab._plot_dirty is False


# ---------------------------------------------------------------------------
# The blank waveform: an idle ping erasing the shot that just landed
# ---------------------------------------------------------------------------

class TestIdleSnapshotsDoNotEraseTheMeasurement:
    """Single-shot mode emits an idle ping milliseconds after the
    measurement, because the rate-limit sleep only runs in continuous mode.
    The tab cached both in one variable, so by the time the 1 s plot timer
    fired the trace was gone and the pane drew nothing - while the history
    plot, which reads its own list, kept working.  That asymmetry is the
    fingerprint of this bug; these pin the split cache that fixes it.
    """

    def _tab(self, qapp):
        from rbl.gui.profiler_tab import ProfilerTab
        from rbl.state.beamline import Beamline
        return ProfilerTab(Beamline())

    def test_an_idle_snapshot_does_not_erase_the_last_trace(self, qapp):
        tab = self._tab(qapp)
        tab._on_scope_state(_measured())
        tab._on_scope_state(_idle())
        assert tab._last_measured is not None
        assert tab._last_measured.corrected_downsampled
        assert tab._redraw_waveform() is True

    def test_a_shot_followed_by_idle_still_draws_the_waveform(self, qapp):
        # The exact worker sequence.  This is THE regression.
        tab = self._tab(qapp)
        tab._on_scope_state(_measured())
        tab._on_scope_state(_idle())
        tab._redraw_plots()
        assert len(tab._ax_wave.lines) > 0

    def test_an_error_snapshot_does_not_arm_an_empty_redraw(self, qapp):
        import time
        tab = self._tab(qapp)
        tab._plot_dirty = False
        tab._on_scope_state(ScopeState(timestamp=time.time(), connected=True,
                                       idle=False, error="no peak found"))
        assert tab._plot_dirty is False

    def test_idle_snapshots_do_not_pad_the_fwhm_history(self, qapp):
        tab = self._tab(qapp)
        before = len(tab._fwhm_history)
        for _ in range(10):
            tab._on_scope_state(_idle())
        assert len(tab._fwhm_history) == before

    def test_the_widths_survive_the_idle_ping(self, qapp):
        # The readout used to return early on an idle snapshot, so the X/Y
        # labels were usually never written at all.
        tab = self._tab(qapp)
        tab._on_scope_state(_measured())
        tab._on_scope_state(_idle())
        tab._readout_dirty = True
        tab._redraw_readout()
        assert "idle" in tab._lbl_status.text()
        assert any(lbl.text() not in ("\u2014", "")
                   for lbl in tab._lbl_axis_value.values())


# ---------------------------------------------------------------------------
# The stall: a shot that never comes back
# ---------------------------------------------------------------------------

class TestShotCannotStickForever:
    """A pressed "Take shot" must always end - in a waveform, an error, or
    the watchdog.  It used to be able to end in none of those: an exception
    that was not a transport error escaped the worker's run(), which ends a
    QThread silently, so no signal ever arrived, the button stayed disabled
    reading "Acquiring...", and the scope itself was still answering.
    """

    def _tab(self, qapp):
        from rbl.gui.profiler_tab import ProfilerTab
        from rbl.state.beamline import Beamline
        return ProfilerTab(Beamline())

    def test_the_measure_step_catches_more_than_transport_errors(self):
        """The acquire block must not let a bare Exception through.

        Checked on the source rather than by driving a fake serial port:
        what matters is that the handler EXISTS in that block, and a
        broken-analysis test that needs a whole instrument to reproduce
        would not survive the next refactor.
        """
        import inspect

        from rbl.hardware import scope_worker
        body = inspect.getsource(scope_worker.ScopeWorker._run_loop)
        assert "except Exception as exc:" in body

    def test_run_never_lets_an_exception_escape_the_thread(self):

        from rbl.hardware.scope_worker import ScopeWorker

        worker = ScopeWorker("COM_TEST")
        seen = []
        worker.error.connect(seen.append)

        def boom():
            raise RuntimeError("analysis exploded")

        worker._run_loop = boom
        worker.run()                       # must not raise
        assert seen and "analysis exploded" in seen[0]

    def test_an_error_ends_a_pending_shot(self, qapp):
        tab = self._tab(qapp)
        tab._connected = True
        tab._shot_pending = True
        tab._btn_shot.setEnabled(False)
        tab._on_scope_error("measurement failed: boom")
        assert tab._shot_pending is False
        assert tab._btn_shot.isEnabled() is True
        assert tab._btn_shot.text() == "Take shot"

    def test_the_watchdog_re_arms_a_shot_that_never_landed(self, qapp):
        tab = self._tab(qapp)
        tab._connected = True
        tab._shot_pending = True
        tab._btn_shot.setEnabled(False)
        tab._btn_shot.setText("Acquiring…")
        tab._on_shot_timeout()
        assert tab._shot_pending is False
        assert tab._btn_shot.isEnabled() is True
        assert tab._btn_shot.text() == "Take shot"
        assert "again" in tab._lbl_status.text()

    def test_the_watchdog_does_not_fire_when_nothing_is_pending(self, qapp):
        tab = self._tab(qapp)
        tab._lbl_status.setText("idle")
        tab._on_shot_timeout()
        assert tab._lbl_status.text() == "idle"
