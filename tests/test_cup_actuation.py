"""
Tests for Faraday cup actuation and confirmed position feedback (Ticket 04).

ADR 0003 & Ticket 04:
- Commanded position and confirmed position are separate fields and never collapsed.
- Beamline owns cup actuation via CupActuationLinkMixin.
- A frozen connected-carrying snapshot (CupActuationState) is published on every
  window and on every commanded change.
- Contact debounce from cup_config.py (0.05 s) is applied to confirmed transitions
  using per-scan transition times.
- Last-confirmed-transition timestamp resolves to the exact sample rather than window edge.
- Relay 1 (enable) closes on connect and opens on shutdown/disconnect; Relay 2 is de-asserted.
- Beamline.shutdown() releases drive without issuing a positional command.
- Non-FULL profiles mark status stale without marking disconnected.
"""
from unittest.mock import MagicMock

from rbl.config.cup_config import (
    CUP_COMMAND_OUT_LINE,
    CUP_ENABLE_LINE,
    CUP_STATUS_BIT_IN,
    CUP_STATUS_BIT_OUT,
)
from rbl.hardware.cup_status import CupPosition
from rbl.snapshots import CupActuationState
from rbl.state.beamline import Beamline
from tests.payloads import CupActuationFeed

# Raw FIO_STATE words helper (closed contact = 0, open contact = 1)
# Bit 2 = IN, Bit 3 = OUT, Bit 4 = AUTO
RAW_STATUS_IN = (1 << CUP_STATUS_BIT_OUT)  # IN closed (0), OUT open (1), AUTO closed (0)
RAW_STATUS_OUT = (1 << CUP_STATUS_BIT_IN)  # IN open (1), OUT closed (0), AUTO closed (0)
# Both contacts open (neither asserted), AUTO closed (0)
RAW_STATUS_TRANSIT = (1 << CUP_STATUS_BIT_IN) | (1 << CUP_STATUS_BIT_OUT)
RAW_STATUS_INDETERMINATE = 0  # Both closed (0), AUTO closed (0)


class MockCupTab:
    """Mock tab capturing cup actuation snapshots."""

    def __init__(self):
        self.snapshots: list[CupActuationState] = []

    def on_cup_actuation_state(self, snapshot: CupActuationState):
        self.snapshots.append(snapshot)


class TestCupActuationSnapshotStructure:
    """Verify CupActuationState fields and invariants."""

    def test_snapshot_separate_commanded_and_confirmed(self):
        snap = CupActuationState(
            connected=True,
            commanded=CupPosition.OUT,
            confirmed=CupPosition.IN,
            auto_mode=True,
            stale=False,
            last_transition_t=123.456,
        )
        assert snap.connected is True
        assert snap.commanded == CupPosition.OUT
        assert snap.confirmed == CupPosition.IN
        assert snap.auto_mode is True
        assert snap.stale is False
        assert snap.last_transition_t == 123.456
        assert snap.commanded != snap.confirmed

    def test_snapshot_aliases(self):
        snap = CupActuationState(
            connected=True,
            commanded=CupPosition.IN,
            confirmed=CupPosition.OUT,
            auto_mode=False,
            stale=True,
        )
        assert snap.commanded_position == CupPosition.IN
        assert snap.confirmed_position == CupPosition.OUT


class TestCupActuationFeedAndTransitions:
    """Verify cup actuation through the production path via CupActuationFeed."""

    def test_commanded_out_followed_by_confirming_status_word(self):
        tab = MockCupTab()
        beamline = Beamline()
        beamline.lj.handle = 1  # Mock connection state
        feed = CupActuationFeed(tab, beamline=beamline)

        # Initially commanded IN, confirmed IN
        feed.send_fio_state(RAW_STATUS_IN, t=1.0)
        assert len(tab.snapshots) == 1
        assert tab.snapshots[-1].confirmed == CupPosition.IN
        assert tab.snapshots[-1].commanded == CupPosition.IN

        # Command OUT
        feed.command_cup_out()
        # Immediately emits snapshot reflecting commanded change
        assert len(tab.snapshots) == 2
        assert tab.snapshots[-1].commanded == CupPosition.OUT
        assert tab.snapshots[-1].confirmed == CupPosition.IN  # Not confirmed yet!

        # Now send confirming status word OUT (held for full window > 50 ms debounce)
        feed.send_fio_state(RAW_STATUS_OUT, t=1.1)
        assert len(tab.snapshots) == 3
        latest = tab.snapshots[-1]
        assert latest.commanded == CupPosition.OUT
        assert latest.confirmed == CupPosition.OUT
        assert latest.auto_mode is True
        assert latest.stale is False

    def test_commanded_out_with_no_confirming_word_leaves_confirmed_unchanged(self):
        tab = MockCupTab()
        beamline = Beamline()
        beamline.lj.handle = 1
        feed = CupActuationFeed(tab, beamline=beamline)

        # Confirm initial IN position
        feed.send_fio_state(RAW_STATUS_IN, t=1.0)
        assert tab.snapshots[-1].confirmed == CupPosition.IN

        # Command OUT
        feed.command_cup_out()
        assert tab.snapshots[-1].commanded == CupPosition.OUT
        assert tab.snapshots[-1].confirmed == CupPosition.IN

        # Receive another window with RAW_STATUS_IN (controller did not move, e.g. in LOCAL)
        feed.send_fio_state(RAW_STATUS_IN, t=1.1)
        latest = tab.snapshots[-1]
        assert latest.commanded == CupPosition.OUT
        assert latest.confirmed == CupPosition.IN  # Still IN, unchanged!

    def test_non_full_profile_marks_snapshot_stale_without_disconnecting(self):
        tab = MockCupTab()
        beamline = Beamline()
        beamline.lj.handle = 1
        feed = CupActuationFeed(tab, beamline=beamline)

        # Initial FULL window
        feed.send_fio_state(RAW_STATUS_IN, t=1.0, profile="FULL")
        assert tab.snapshots[-1].stale is False
        assert tab.snapshots[-1].connected is True

        # Diagnostic profile window (WAVEFORM) where FIO_STATE is None
        feed.send_fio_state(None, t=1.1, profile="WAVEFORM")
        latest = tab.snapshots[-1]
        assert latest.stale is True
        assert latest.connected is True  # Distinguishable from disconnected!

    def test_indeterminate_contacts_reaches_snapshot(self):
        tab = MockCupTab()
        beamline = Beamline()
        beamline.lj.handle = 1
        feed = CupActuationFeed(tab, beamline=beamline)

        feed.send_fio_state(RAW_STATUS_INDETERMINATE, t=1.0)
        latest = tab.snapshots[-1]
        assert latest.confirmed == CupPosition.INDETERMINATE
        assert latest.auto_mode is True


class TestContactDebounceAndTimestampResolution:
    """Verify per-scan transition debounce and sample-accurate transition timestamp."""

    def test_contact_bounce_shorter_than_debounce_is_rejected(self):
        tab = MockCupTab()
        beamline = Beamline()
        beamline.lj.handle = 1
        feed = CupActuationFeed(tab, beamline=beamline)

        # Initial state confirmed IN
        feed.send_fio_state(RAW_STATUS_IN, t=1.0)
        assert tab.snapshots[-1].confirmed == CupPosition.IN

        # Send a window with a brief bounce: OUT for only 10 ms, then back to IN
        # At 7500 Hz, 10 ms = 75 scans.
        sample_period = 1.0 / 7500.0
        samples = 750
        transitions = [
            (100, RAW_STATUS_OUT),  # scan 100 switches to OUT
            (175, RAW_STATUS_IN),   # scan 175 (10 ms later) bounces back to IN
        ]
        feed.send_fio_state(
            {"first": RAW_STATUS_IN, "last": RAW_STATUS_IN, "transitions": transitions},
            t=1.1,
            samples=samples,
            sample_period=sample_period,
        )
        latest = tab.snapshots[-1]
        # Bounced state was never sustained for >= 0.05 s, so OUT was rejected!
        assert latest.confirmed == CupPosition.IN

    def test_transition_timestamp_resolves_to_exact_sample(self):
        tab = MockCupTab()
        beamline = Beamline()
        beamline.lj.handle = 1
        feed = CupActuationFeed(tab, beamline=beamline)

        # Initial state IN
        feed.send_fio_state(RAW_STATUS_IN, t=1.0)

        # Window with transition at scan 200 out of 750.
        # From scan 200 to 749 is 550 scans = 73.3 ms > 50 ms debounce, so it confirms!
        sample_period = 1.0 / 7500.0
        samples = 750
        t_window = 2.0
        trans_scan = 200
        expected_t = t_window - (samples - 1 - trans_scan) * sample_period

        transitions = [(trans_scan, RAW_STATUS_OUT)]
        feed.send_fio_state(
            {"first": RAW_STATUS_IN, "last": RAW_STATUS_OUT, "transitions": transitions},
            t=t_window,
            samples=samples,
            sample_period=sample_period,
        )
        latest = tab.snapshots[-1]
        assert latest.confirmed == CupPosition.OUT
        assert abs(latest.last_transition_t - expected_t) < 1e-9

    def test_transition_spanning_across_consecutive_windows(self):
        """A transition occurring near the end of window 1 completes debounce in window 2."""
        tab = MockCupTab()
        beamline = Beamline()
        beamline.lj.handle = 1
        feed = CupActuationFeed(tab, beamline=beamline)

        # Baseline IN
        feed.send_fio_state(RAW_STATUS_IN, t=1.0)
        assert tab.snapshots[-1].confirmed == CupPosition.IN

        sample_period = 1.0 / 7500.0
        samples = 750

        # Window 1: at scan 600 out of 750, switches to OUT.
        # Duration in window 1 = 150 scans = 20 ms < 50 ms.
        trans_scan_w1 = 600
        t_w1 = 1.1
        expected_t = t_w1 - (samples - 1 - trans_scan_w1) * sample_period
        feed.send_fio_state(
            {
                "first": RAW_STATUS_IN,
                "last": RAW_STATUS_OUT,
                "transitions": [(trans_scan_w1, RAW_STATUS_OUT)],
            },
            t=t_w1,
            samples=samples,
            sample_period=sample_period,
        )
        # Not confirmed yet in window 1!
        assert tab.snapshots[-1].confirmed == CupPosition.IN

        # Window 2: continues holding OUT for whole window (100 ms)
        t_w2 = t_w1 + 0.1
        feed.send_fio_state(
            RAW_STATUS_OUT,
            t=t_w2,
            samples=samples,
            sample_period=sample_period,
        )
        # Now confirmed! And timestamp is the exact sample from window 1!
        latest = tab.snapshots[-1]
        assert latest.confirmed == CupPosition.OUT
        assert abs(latest.last_transition_t - expected_t) < 1e-9


class TestCupActuationTeardownAndShutdown:
    """Verify Relay 1 / Relay 2 drive control and shutdown safety."""

    def test_relays_on_connect_and_disconnect(self):
        beamline = Beamline()
        beamline.lj = MagicMock()
        beamline.lj.connected = True

        # On connect: Relay 1 closes (1), Relay 2 de-asserts (0)
        beamline._on_labjack_connect_cup()
        written = [call.args for call in beamline.lj.write_digital.call_args_list]
        assert (CUP_ENABLE_LINE, 1) in written
        assert (CUP_COMMAND_OUT_LINE, 0) in written

        beamline.lj.write_digital.reset_mock()

        # On disconnect: Relay 1 opens (0), Relay 2 de-asserts (0)
        beamline._on_labjack_disconnect_cup()
        written_dc = [call.args for call in beamline.lj.write_digital.call_args_list]
        assert (CUP_ENABLE_LINE, 0) in written_dc
        assert (CUP_COMMAND_OUT_LINE, 0) in written_dc

    def test_shutdown_leaves_both_outputs_deasserted_no_positional_command(self):
        beamline = Beamline()
        beamline.lj = MagicMock()
        beamline.lj.connected = True
        mock_worker = MagicMock()
        mock_worker.isRunning.return_value = False
        beamline._lj_worker = mock_worker

        # Track any calls to positional commands
        command_spy = MagicMock()
        beamline.command_cup = command_spy

        # Execute shutdown
        beamline.shutdown()

        # Both output lines must be de-asserted (set to 0)
        written_lines = {
            call.args[0]: call.args[1]
            for call in beamline.lj.write_digital.call_args_list
        }
        assert written_lines.get(CUP_ENABLE_LINE) == 0
        assert written_lines.get(CUP_COMMAND_OUT_LINE) == 0

        # Positional command must NEVER be issued on shutdown
        assert command_spy.call_count == 0


class TestStreamWorkerPendingWrites:
    """Verify LabJackStreamWorker thread-safe pending writes slot."""

    def test_queue_and_drain_pending_writes(self):
        import threading
        from unittest.mock import patch

        from rbl.hardware.labjack_stream_worker import LabJackStreamWorker

        worker = LabJackStreamWorker.__new__(LabJackStreamWorker)
        worker._handle = 42
        worker._pending_writes = []
        worker._write_lock = threading.Lock()
        worker._output_state = {}

        # Signals
        written_signals = []
        state_signals = []
        worker.digital_output_written = MagicMock()
        worker.digital_output_written.emit.side_effect = (
            lambda line, val: written_signals.append((line, val))
        )
        worker.output_state_changed = MagicMock()
        worker.output_state_changed.emit.side_effect = (
            lambda state: state_signals.append(dict(state))
        )

        # Queue writes
        worker.queue_digital_write(CUP_ENABLE_LINE, 1)
        worker.queue_digital_write(CUP_COMMAND_OUT_LINE, 0)

        with patch("rbl.hardware.labjack_stream_worker._ljm") as mock_ljm, \
             patch("rbl.hardware.labjack_stream_worker._LJM_AVAILABLE", True):
            worker._drain_pending_writes()

            # eWriteName called for each queued line
            mock_ljm.eWriteName.assert_any_call(42, CUP_ENABLE_LINE, 1)
            mock_ljm.eWriteName.assert_any_call(42, CUP_COMMAND_OUT_LINE, 0)

        # Signals emitted
        assert (CUP_ENABLE_LINE, 1) in written_signals
        assert (CUP_COMMAND_OUT_LINE, 0) in written_signals
        assert worker._output_state[CUP_ENABLE_LINE] == 1
        assert worker._output_state[CUP_COMMAND_OUT_LINE] == 0
        assert len(worker._pending_writes) == 0

