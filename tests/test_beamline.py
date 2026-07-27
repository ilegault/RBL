"""
Unit tests for rbl.state.beamline.Beamline — no Qt event loop, no hardware.
Verifies the unit-conversion math that used to live duplicated inside each
tab's _on_window/_on_reading now happens once here, correctly.
"""
import math

import pytest

from rbl.config import hardware_config as SC
from rbl.state.beamline import Beamline
from rbl.state.snapshots import MotorState, LogAmpState, AmpState, FuncGenState


@pytest.fixture
def beamline():
    return Beamline()


class TestMotorIngestion:
    def _snapshot(self, pos=0, moving=False, enabled=True, **switches):
        sw = {"forward_switch": False, "reverse_switch": False, "home_switch": False}
        sw.update(switches)
        return {"pos": pos, "moving": moving, "switches": sw, "enabled": enabled}

    def test_emits_motor_state_keyed_by_jaw_label(self, beamline):
        received = []
        beamline.motors_changed.connect(received.append)
        snapshot = {axis: self._snapshot(pos=1000) for axis in SC.AXIS_LETTERS}
        beamline.ingest_motor_poll(snapshot, zeroed=True)

        assert len(received) == 1
        state = received[0]
        assert isinstance(state, MotorState)
        assert state.connected is True
        assert state.zeroed is True
        assert set(state.axes.keys()) == set(SC.AXIS_LABELS)

    def test_pos_mm_matches_counts_to_mm(self, beamline):
        received = []
        beamline.motors_changed.connect(received.append)
        snapshot = {"A": self._snapshot(pos=6300)}
        beamline.ingest_motor_poll(snapshot, zeroed=False)
        axis_snap = received[0].axes[SC.AXIS_NAMES["A"]]
        assert axis_snap.pos_counts == 6300
        assert axis_snap.pos_mm == pytest.approx(SC.counts_to_mm("A", 6300))

    def test_disconnected_state_is_empty(self, beamline):
        received = []
        beamline.motors_changed.connect(received.append)
        beamline.motors_disconnected()
        state = received[0]
        assert state.connected is False
        assert state.axes == {}


class TestLabjackIngestion:
    def _payload(self, logamp_volts=None, amp_channels=None):
        channels = {}
        for ain, jaw in SC.LABJACK_CHANNEL_MAP.items():
            v = (logamp_volts or {}).get(jaw)
            channels[ain] = None if v is None else {"mean": v}
        for amp in SC.AMP_LABELS:
            v_ain = SC.AMP_CHANNEL_MAP[amp]["voltage"]
            i_ain = SC.AMP_CHANNEL_MAP[amp]["current"]
            data = (amp_channels or {}).get(amp)
            if data is None:
                channels[v_ain] = None
                channels[i_ain] = None
            else:
                channels[v_ain] = {"peak": data["v_peak"], "pk_pk": data["v_pkpk"], "rms": data["v_rms"]}
                channels[i_ain] = {"rms": data["i_rms"]}
        return {"channels": channels, "t": 1.0}

    def test_logamp_current_conversion(self, beamline):
        received = []
        beamline.logamps_changed.connect(received.append)
        # 3.0 V is the midpoint of the 0-6V log-amp range -> 1 uA (10^-6 A).
        payload = self._payload(logamp_volts={"X+": 3.0, "X-": 3.0, "Y+": 3.0, "Y-": 3.0})
        beamline.ingest_labjack_window(payload)
        state = received[0]
        assert isinstance(state, LogAmpState)
        assert state.connected is True
        assert state.currents["X+"] == pytest.approx(1e-6, rel=1e-6)

    def test_logamp_paused_channel_is_nan(self, beamline):
        received = []
        beamline.logamps_changed.connect(received.append)
        payload = self._payload(logamp_volts={"X+": 3.0})   # others None (WAVEFORM mode)
        beamline.ingest_labjack_window(payload)
        state = received[0]
        assert math.isnan(state.currents["X-"])

    def test_amp_voltage_and_current_conversion(self, beamline):
        received = []
        beamline.amps_changed.connect(received.append)
        payload = self._payload(amp_channels={
            "X+": {"v_peak": 2.0, "v_pkpk": 4.0, "v_rms": 1.5, "i_rms": 0.5},
        })
        beamline.ingest_labjack_window(payload, active_profile="FULL")
        state = received[0]
        assert isinstance(state, AmpState)
        assert state.connected is True
        assert state.active_profile == "FULL"
        ch = state.channels["X+"]
        assert ch.peak_kv == pytest.approx(2.0)          # 1000:1 -> V is kV
        assert ch.pkpk_kv == pytest.approx(4.0)
        assert ch.rms_kv == pytest.approx(1.5)
        assert ch.rms_ma == pytest.approx(5.0)            # 1 V == 10 mA

    def test_amp_missing_channel_is_nan(self, beamline):
        received = []
        beamline.amps_changed.connect(received.append)
        payload = self._payload(amp_channels={})   # nothing streaming
        beamline.ingest_labjack_window(payload)
        ch = received[0].channels["X+"]
        assert math.isnan(ch.peak_kv)
        assert math.isnan(ch.rms_ma)

    def test_disconnected_marks_both_subsystems(self, beamline):
        log_received, amp_received = [], []
        beamline.logamps_changed.connect(log_received.append)
        beamline.amps_changed.connect(amp_received.append)
        # disconnect_labjack() is safe to call even when never connected
        # (LabJackT7.disconnect() no-ops without a handle).
        beamline.disconnect_labjack()
        assert log_received[0].connected is False
        assert amp_received[0].connected is False


class TestBeamReconstruction:
    def test_no_beam_without_all_four_edges(self, beamline):
        assert beamline.reconstruct_beam(sigma_mm=1.0) is None

    def test_centred_beam_from_symmetric_currents(self, beamline):
        beamline.ingest_motor_poll(
            {axis: {"pos": SC.mm_to_counts(axis, 10.0), "moving": False,
                    "switches": {"forward_switch": False, "reverse_switch": False,
                                 "home_switch": False}, "enabled": True}
             for axis in SC.AXIS_LETTERS},
            zeroed=True,
        )
        payload = self._payload_equal_currents(3.0)
        beamline.ingest_labjack_window(payload)
        est = beamline.reconstruct_beam(sigma_mm=1.0)
        assert est is not None
        assert est.ok
        assert abs(est.x) < 1e-6
        assert abs(est.y) < 1e-6

    @staticmethod
    def _payload_equal_currents(volts):
        channels = {}
        for ain in SC.LABJACK_CHANNEL_MAP:
            channels[ain] = {"mean": volts}
        for amp in SC.AMP_LABELS:
            v_ain = SC.AMP_CHANNEL_MAP[amp]["voltage"]
            i_ain = SC.AMP_CHANNEL_MAP[amp]["current"]
            channels[v_ain] = None
            channels[i_ain] = None
        return {"channels": channels, "t": 1.0}


class TestFuncgenIngestion:
    def test_readback_converts_to_channel_snapshots(self, beamline):
        received = []
        beamline.funcgens_changed.connect(received.append)
        beamline.ingest_funcgen_readback(
            connected={"A": True, "B": False},
            timebase={"A": "INT", "B": "INT"},
            readback={"A1": {"shape": "SIN", "freq": 1000.0, "amp": 2.0,
                              "offset": 0.0, "phase": 0.0, "output": True}},
        )
        state = received[0]
        assert isinstance(state, FuncGenState)
        assert state.connected == {"A": True, "B": False}
        ch = state.channels["A1"]
        assert ch.shape == "SIN"
        assert ch.freq_hz == 1000.0
        assert ch.amp_vpp == 2.0
        assert ch.output_on is True

    def test_error_channel_is_skipped(self, beamline):
        received = []
        beamline.funcgens_changed.connect(received.append)
        beamline.ingest_funcgen_readback(
            connected={"A": True, "B": True},
            timebase={},
            readback={"A1": {"error": "VISA timeout"}},
        )
        assert "A1" not in received[0].channels

    def test_disconnected_clears_channels(self, beamline):
        received = []
        beamline.funcgens_changed.connect(received.append)
        beamline.funcgens_disconnected()
        state = received[0]
        assert state.connected == {"A": False, "B": False}
        assert state.channels == {}


class TestDeviceOwnership:
    """Beamline is the single owner of every instrument (Phase 7): no QWidget
    constructs or is responsible for tearing down a driver instance."""

    def test_constructs_galil_and_labjack(self, beamline):
        from rbl.hardware.galil_driver import GalilController
        from rbl.hardware.labjack_driver import LabJackT7
        assert isinstance(beamline.galil, GalilController)
        assert isinstance(beamline.lj, LabJackT7)

    def test_funcgens_start_unconnected(self, beamline):
        assert beamline.dg_a is None
        assert beamline.dg_b is None

    def test_shutdown_is_safe_with_nothing_connected(self, beamline):
        beamline.shutdown()   # must not raise

    def test_shutdown_disconnects_galil(self, beamline):
        from unittest.mock import MagicMock
        beamline.galil = MagicMock(connected=True)
        beamline.shutdown()
        beamline.galil.abort.assert_called_once()
        beamline.galil.disconnect.assert_called_once()

    def test_shutdown_never_disables_funcgen_outputs(self, beamline):
        """Deliberate: instruments retain state after the app exits."""
        from unittest.mock import MagicMock
        beamline.dg_a = MagicMock()
        beamline.dg_b = MagicMock()
        beamline.shutdown()
        beamline.dg_a.close.assert_called_once()
        beamline.dg_b.close.assert_called_once()
        beamline.dg_a.output_off.assert_not_called()
        beamline.dg_b.output_off.assert_not_called()

    def test_shutdown_survives_a_broken_generator(self, beamline):
        """One generator raising on close() must not stop the other's teardown
        or the Galil/LabJack teardown that follows in shutdown()."""
        from unittest.mock import MagicMock
        beamline.dg_a = MagicMock()
        beamline.dg_a.close.side_effect = RuntimeError("VISA timeout")
        beamline.dg_b = MagicMock()
        beamline.shutdown()   # must not raise
        beamline.dg_b.close.assert_called_once()

    def test_disconnect_labjack_stops_stream_before_closing_handle(self, beamline):
        from unittest.mock import MagicMock
        worker = MagicMock()
        worker.wait.return_value = True
        beamline._lj_worker = worker
        beamline.lj = MagicMock(connected=True)
        beamline.disconnect_labjack()
        worker.stop.assert_called_once()
        beamline.lj.disconnect.assert_called_once()
        assert beamline._lj_worker is None

    def test_labjack_connected_signal_carries_serial(self, beamline):
        from unittest.mock import MagicMock
        received = []
        beamline.labjack_connected.connect(received.append)
        beamline.lj = MagicMock(connected=False)
        beamline.lj.serial_number.return_value = "T7-12345"
        beamline._start_stream_worker = lambda profile: None   # no real worker
        beamline.connect_labjack("USB", "ANY")
        assert received == ["T7-12345"]
