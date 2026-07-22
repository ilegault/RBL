"""
Tests for the LabJack T7 driver with the LJM library fully mocked, so the AIN
configuration sequence and batched reads are verified without any hardware or
the native libLabJackM.so being installed.
"""
import pytest
from unittest.mock import MagicMock

import rbl.hardware.labjack_driver as ljd
from rbl.hardware.labjack_driver import LabJackT7, LabJackError


@pytest.fixture
def mock_ljm(monkeypatch):
    """Patch the module-level ljm binding with a MagicMock and mark it available."""
    m = MagicMock()
    m.openS.return_value = 42                       # fake device handle
    m.eReadName.return_value = 470012345.0          # serial number
    m.eReadNames.return_value = [0.10, 0.20, 0.30, 0.40,   # log amps
                                 1.0, 0.5, 1.1, 0.6,       # amp X+, X-
                                 1.2, 0.7, 1.3, 0.8]       # amp Y+, Y-
    monkeypatch.setattr(ljd, "ljm", m)
    monkeypatch.setattr(ljd, "LJM_AVAILABLE", True)
    return m


class TestConnect:
    def test_connect_opens_t7(self, mock_ljm):
        lj = LabJackT7()
        lj.connect("USB", "ANY")
        mock_ljm.openS.assert_called_once_with("T7", "USB", "ANY")
        assert lj.connected
        assert lj.connection_type == "USB"

    def test_connect_configures_fourteen_ains(self, mock_ljm):
        lj = LabJackT7()
        lj.connect("ETHERNET", "192.168.1.5")
        # AIN0..AIN13 each get range, single-ended negative-ch, and resolution.
        # (AIN0-3 log amps, AIN4-5 spare, AIN6-13 amp monitors — all configured.)
        names_written = [call.args[1] for call in mock_ljm.eWriteName.call_args_list]
        for ch in range(14):
            assert f"AIN{ch}_RANGE" in names_written
            assert f"AIN{ch}_NEGATIVE_CH" in names_written
            assert f"AIN{ch}_RESOLUTION_INDEX" in names_written
        assert len(mock_ljm.eWriteName.call_args_list) == 42   # 14 x 3

    def test_connect_sets_pm10v_range(self, mock_ljm):
        lj = LabJackT7()
        lj.connect()
        range_calls = [c.args for c in mock_ljm.eWriteName.call_args_list
                       if c.args[1].endswith("_RANGE")]
        assert all(args[2] == 10.0 for args in range_calls)

    def test_connect_single_ended_negative_channel_is_199(self, mock_ljm):
        lj = LabJackT7()
        lj.connect()
        neg_calls = [c.args for c in mock_ljm.eWriteName.call_args_list
                     if c.args[1].endswith("_NEGATIVE_CH")]
        assert all(args[2] == 199 for args in neg_calls)

    def test_reconnect_closes_previous_handle(self, mock_ljm):
        lj = LabJackT7()
        lj.connect()
        first_handle = lj.handle
        mock_ljm.openS.return_value = 99
        lj.connect()
        mock_ljm.close.assert_called_with(first_handle)
        assert lj.handle == 99

    def test_connect_raises_when_library_unavailable(self, monkeypatch):
        monkeypatch.setattr(ljd, "LJM_AVAILABLE", False)
        monkeypatch.setattr(ljd, "_LJM_IMPORT_ERROR", "no .so")
        lj = LabJackT7()
        with pytest.raises(LabJackError):
            lj.connect()


class TestRead:
    def test_read_channels_returns_named_dict(self, mock_ljm):
        lj = LabJackT7()
        lj.connect()
        readings = lj.read_channels()
        assert len(readings) == 12
        assert readings["AIN0"] == 0.10
        assert readings["AIN3"] == 0.40
        assert readings["AIN4"] == 1.0     # amp X+ voltage monitor
        assert readings["AIN11"] == 0.8    # amp Y- current monitor

    def test_read_channels_is_single_batched_call(self, mock_ljm):
        """All 12 channels in ONE round trip — the whole no-interference design
        rests on this. Two eReadNames calls from two threads would collide."""
        lj = LabJackT7()
        lj.connect()
        lj.read_channels()
        mock_ljm.eReadNames.assert_called_once()
        args = mock_ljm.eReadNames.call_args.args
        assert args[0] == 42                # handle
        assert args[1] == 14                # count
        assert args[2] == [f"AIN{i}" for i in range(14)]

    def test_read_custom_channel_subset(self, mock_ljm):
        mock_ljm.eReadNames.return_value = [1.0, 2.0]
        lj = LabJackT7()
        lj.connect()
        readings = lj.read_channels(("AIN0", "AIN1"))
        assert readings == {"AIN0": 1.0, "AIN1": 2.0}

    def test_read_channels_raises_when_not_connected(self):
        lj = LabJackT7()
        with pytest.raises(LabJackError):
            lj.read_channels()


class TestSerialAndDisconnect:
    def test_serial_number_when_connected(self, mock_ljm):
        lj = LabJackT7()
        lj.connect()
        assert lj.serial_number() == "470012345"

    def test_serial_number_dash_when_disconnected(self):
        lj = LabJackT7()
        assert lj.serial_number() == "—"

    def test_serial_number_handles_read_error(self, mock_ljm):
        lj = LabJackT7()
        lj.connect()
        mock_ljm.eReadName.side_effect = RuntimeError("comm error")
        assert lj.serial_number() == "?"

    def test_disconnect_closes_and_clears_handle(self, mock_ljm):
        lj = LabJackT7()
        lj.connect()
        lj.disconnect()
        mock_ljm.close.assert_called_once_with(42)
        assert lj.handle is None
        assert not lj.connected

    def test_disconnect_when_not_connected_is_safe(self):
        lj = LabJackT7()
        lj.disconnect()           # must not raise
        assert not lj.connected

    def test_disconnect_swallows_close_errors(self, mock_ljm):
        lj = LabJackT7()
        lj.connect()
        mock_ljm.close.side_effect = RuntimeError("already closed")
        lj.disconnect()           # must not raise
        assert lj.handle is None
