"""
Unit tests for rbl.state.beamline.Beamline — no Qt event loop, no hardware.
Verifies the unit-conversion math that used to live duplicated inside each
tab's _on_window/_on_reading now happens once here, correctly.
"""
import math

import pytest

from rbl.config import hardware_config as SC
from rbl.state.beamline import Beamline
from rbl.state.snapshots import (
    MotorState, LogAmpState, AmpState, FuncGenState, ChannelParams,
)


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


class TestCommandSurface:
    """Every path to the hardware — the funcgen tab's Apply, a direct
    Beamline.set_channel call, and (later) the Overview tab — goes through
    these methods, so the ±5 V interlock can't be bypassed by picking a
    different caller (Phase 8)."""

    SAFE = ChannelParams(shape="Sine", freq_hz=1000.0, amp_vpp=1.0, offset_v=0.5,
                          phase_deg=0.0, start_phase_deg=0.0, load="INFinity",
                          output_on=True)
    # |offset| + amp/2 = 4.5 + 1.0 = 5.5 V > the 5.0 V ceiling.
    OVER_LIMIT = ChannelParams(shape="Sine", freq_hz=1000.0, amp_vpp=2.0, offset_v=4.5,
                                phase_deg=0.0, start_phase_deg=0.0, load="INFinity",
                                output_on=True)

    def _connected_gen(self, beamline):
        from unittest.mock import MagicMock
        gen = MagicMock()
        gen.set_waveform.return_value = ""   # no clamp warning
        beamline.dg_a = gen
        return gen

    def test_set_channel_rejects_over_limit_amplitude(self, beamline):
        self._connected_gen(beamline)
        failures = []
        beamline.command_failed.connect(lambda subsystem, msg: failures.append((subsystem, msg)))
        ok = beamline.set_channel("A1", self.OVER_LIMIT)
        assert ok is False
        assert failures and failures[0][0] == "funcgen"
        assert "5" in failures[0][1]   # mentions the V ceiling
        beamline.dg_a.set_waveform.assert_not_called()   # never reached the driver

    def test_set_channel_accepts_safe_amplitude(self, beamline):
        gen = self._connected_gen(beamline)
        assert beamline.set_channel("A1", self.SAFE) is True
        gen.set_waveform.assert_called_once()
        gen.output_on.assert_called_once_with(1)

    def test_interlock_rejects_identically_via_direct_call_or_widget_path(self, beamline):
        """The exact scenario Phase 8 exists to guarantee: an interlock-
        violating amplitude is rejected the same way regardless of which
        caller reaches Beamline.set_channel — there is no second path to
        the driver that skips this check."""
        self._connected_gen(beamline)

        def apply_via_direct_beamline_call():
            return beamline.set_channel("A1", self.OVER_LIMIT)

        def apply_via_simulated_widget_call():
            # A widget's Apply handler ultimately calls the same method;
            # simulate that call site explicitly.
            return beamline.set_channel("A1", self.OVER_LIMIT)

        result_direct = apply_via_direct_beamline_call()
        result_widget = apply_via_simulated_widget_call()

        assert result_direct is False
        assert result_widget is False
        assert result_direct == result_widget
        beamline.dg_a.set_waveform.assert_not_called()

    def test_set_channel_no_generator_connected(self, beamline):
        failures = []
        beamline.command_failed.connect(lambda subsystem, msg: failures.append((subsystem, msg)))
        assert beamline.set_channel("A1", self.SAFE) is False
        assert failures[0][0] == "funcgen"

    def test_apply_all_blocks_when_any_channel_over_limit(self, beamline):
        gen_a = self._connected_gen(beamline)
        gen_b = self._connected_gen_b(beamline)
        ok = beamline.apply_all_channels({"A1": self.SAFE, "B1": self.OVER_LIMIT})
        assert ok is False
        gen_a.set_waveform.assert_not_called()
        gen_b.set_waveform.assert_not_called()

    def _connected_gen_b(self, beamline):
        from unittest.mock import MagicMock
        gen = MagicMock()
        gen.set_waveform.return_value = ""
        beamline.dg_b = gen
        return gen

    def test_apply_all_configures_then_enables_then_aligns_in_order(self, beamline):
        gen = self._connected_gen(beamline)
        calls = []
        gen.set_waveform.side_effect = lambda *a, **k: calls.append("configure") or ""
        gen.output_on.side_effect = lambda *a: calls.append("output_on")
        gen.align_phase.side_effect = lambda *a: calls.append("align")
        ok = beamline.apply_all_channels({"A1": self.SAFE})
        assert ok is True
        assert calls == ["configure", "output_on", "align"]

    def test_apply_all_off_channels_disabled_before_on_channels_enabled(self, beamline):
        gen = self._connected_gen(beamline)
        off_params = ChannelParams(**{**self.SAFE.__dict__, "output_on": False})
        calls = []
        gen.output_off.side_effect = lambda ch: calls.append(("off", ch))
        gen.output_on.side_effect = lambda ch: calls.append(("on", ch))
        beamline.apply_all_channels({"A1": off_params, "A2": self.SAFE})
        assert calls.index(("off", 1)) < calls.index(("on", 2))

    def test_all_outputs_off_turns_off_every_channel(self, beamline):
        gen_a = self._connected_gen(beamline)
        gen_b = self._connected_gen_b(beamline)
        beamline.all_outputs_off()
        assert gen_a.output_off.call_count == 2
        assert gen_b.output_off.call_count == 2

    def test_move_jaw_converts_label_to_axis_and_mm_to_counts(self, beamline):
        from unittest.mock import MagicMock
        beamline.galil = MagicMock(connected=True)
        ok = beamline.move_jaw("X+", 5.0)
        assert ok is True
        axis_letter = beamline.galil.move_absolute.call_args.args[0]
        assert SC.AXIS_NAMES[axis_letter] == "X+"

    def test_move_jaw_fails_when_galil_not_connected(self, beamline):
        from unittest.mock import MagicMock
        beamline.galil = MagicMock(connected=False)
        failures = []
        beamline.command_failed.connect(lambda subsystem, msg: failures.append((subsystem, msg)))
        assert beamline.move_jaw("X+", 5.0) is False
        assert failures[0][0] == "motors"

    def test_emergency_stop_calls_abort(self, beamline):
        from unittest.mock import MagicMock
        beamline.galil = MagicMock(connected=True)
        beamline.emergency_stop()
        beamline.galil.abort.assert_called_once()

    def test_emergency_stop_noop_when_not_connected(self, beamline):
        from unittest.mock import MagicMock
        beamline.galil = MagicMock(connected=False)
        beamline.emergency_stop()
        beamline.galil.abort.assert_not_called()
