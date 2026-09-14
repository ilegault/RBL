"""
test_picoammeter_link.py
Tests for Beamline's PicoammeterLinkMixin, CupFeed test helper, and CupState snapshots.
"""
from __future__ import annotations

import math
from unittest.mock import MagicMock, patch

import pytest

from rbl.snapshots import CupState
from rbl.state.beamline import Beamline
from tests.payloads import CupFeed


class TestPicoammeterLink:
    def test_initial_state_disconnected(self):
        beamline = Beamline()
        assert beamline.picoammeter_connected is False
        state = beamline.last_cup_state
        assert state.connected is False
        assert state.current is None
        assert math.isnan(state.timestamp)
        assert state.status_word == 0
        assert state.over_range is False
        assert state.unavailable is False
        assert state.valid is False

    def test_cup_feed_normal_reading(self):
        beamline = Beamline()
        published: list[CupState] = []
        beamline.cup_changed.connect(published.append)

        feed = CupFeed(beamline=beamline)
        state = feed.send_raw("+2.500000E-06,+1.500000,+00000000", protocol_mode=1, t_host=10.0)

        assert state.connected is True
        assert state.current == pytest.approx(2.5e-6)
        assert state.timestamp == pytest.approx(1.5)
        assert state.status_word == 0
        assert state.over_range is False
        assert state.unavailable is False
        assert state.valid is True
        assert state.t_host == 10.0
        assert len(published) == 1
        assert published[0] == state

    def test_cup_feed_send_reading_helper(self):
        beamline = Beamline()
        published: list[CupState] = []
        beamline.cup_changed.connect(published.append)

        feed = CupFeed(beamline=beamline)
        state = feed.send_reading(current=4.2e-7, timestamp=2.4, status_word=0, t_host=12.0)

        assert state.connected is True
        assert state.current == pytest.approx(4.2e-7)
        assert state.timestamp == pytest.approx(2.4)
        assert state.status_word == 0
        assert state.over_range is False
        assert len(published) == 1

    def test_cup_feed_over_range_status_word(self):
        beamline = Beamline()
        published: list[CupState] = []
        beamline.cup_changed.connect(published.append)

        feed = CupFeed(beamline=beamline)
        # Bit 6 (0x40 / 64) set in status word
        state = feed.send_raw("+1.000000E-03,+3.000000,+00000064", protocol_mode=1)

        assert state.connected is True
        assert state.current == pytest.approx(1.0e-3)
        assert state.status_word == 64
        assert state.over_range is True
        assert state.valid is True
        assert len(published) == 1

    def test_cup_feed_over_range_sentinel(self):
        beamline = Beamline()
        published: list[CupState] = []
        beamline.cup_changed.connect(published.append)

        feed = CupFeed(beamline=beamline)
        state = feed.send_raw("+9.910000E+37,+4.000000,+00000000", protocol_mode=1)

        assert state.connected is True
        assert state.current is None
        assert state.over_range is True
        assert state.valid is True
        assert len(published) == 1

    def test_cup_feed_unavailable_sentinel(self):
        beamline = Beamline()
        published: list[CupState] = []
        beamline.cup_changed.connect(published.append)

        feed = CupFeed(beamline=beamline)
        state = feed.send_raw("+9.900000E+37,+5.000000,+00000000", protocol_mode=1)

        assert state.connected is True
        assert state.current is None
        assert state.unavailable is True
        assert state.valid is False
        assert len(published) == 1

    def test_cup_feed_malformed_string(self):
        beamline = Beamline()
        published: list[CupState] = []
        beamline.cup_changed.connect(published.append)

        feed = CupFeed(beamline=beamline)
        state = feed.send_raw("UNPARSEABLE_JUNK_DATA")

        assert state.connected is True
        assert state.current is None
        assert state.valid is False
        assert math.isnan(state.timestamp)
        assert state.raw == "UNPARSEABLE_JUNK_DATA"
        assert len(published) == 1

    def test_disconnect_picoammeter_emits_disconnected_snapshot(self):
        beamline = Beamline()
        published: list[CupState] = []
        beamline.cup_changed.connect(published.append)

        # Ingest reading first so it is connected
        feed = CupFeed(beamline=beamline)
        feed.send_reading(1.0e-6)
        assert beamline.picoammeter_connected is True

        # Now disconnect
        beamline.disconnect_picoammeter()
        assert beamline.picoammeter_connected is False
        assert beamline.last_cup_state.connected is False
        assert beamline.last_cup_state.current is None
        assert published[-1].connected is False

    @patch("rbl.state.picoammeter_link.PicoammeterWorker")
    def test_connect_and_disconnect_picoammeter(self, mock_worker_cls):
        mock_worker = MagicMock()
        mock_worker_cls.return_value = mock_worker

        beamline = Beamline()
        beamline.connect_picoammeter("GPIB0::14::INSTR")

        mock_worker_cls.assert_called_once_with(resource_name="GPIB0::14::INSTR")
        mock_worker.start.assert_called_once()

        # Idempotent second call
        beamline.connect_picoammeter("GPIB0::14::INSTR")
        assert mock_worker.start.call_count == 1

        # Disconnect
        beamline.disconnect_picoammeter()
        mock_worker.stop.assert_called_once()

    @patch("rbl.state.picoammeter_link.PicoammeterWorker")
    def test_set_cup_acquiring_delegates_to_worker(self, mock_worker_cls):
        mock_worker = MagicMock()
        mock_worker_cls.return_value = mock_worker

        beamline = Beamline()
        beamline.connect_picoammeter("GPIB0::14::INSTR")

        beamline.set_cup_acquiring(True)
        mock_worker.set_acquiring.assert_called_with(True)

        beamline.set_cup_acquiring(False)
        mock_worker.set_acquiring.assert_called_with(False)

    @patch("rbl.state.picoammeter_link.PicoammeterWorker")
    def test_shutdown_picoammeter_stops_worker_safely(self, mock_worker_cls):
        mock_worker = MagicMock()
        mock_worker.wait.return_value = True
        mock_worker_cls.return_value = mock_worker

        beamline = Beamline()
        beamline.connect_picoammeter("GPIB0::14::INSTR")
        beamline.shutdown()

        mock_worker.stop.assert_called_once()
        mock_worker.wait.assert_called_once_with(5000)

