"""
Tests for the RIGOL DG1022Z driver with pyvisa fully mocked, so discovery,
SCPI command formatting, the MAX_GEN_VOLTS safety clamp, and state-readback
parsing are verified without any real USB-TMC instrument attached.
"""
import pytest
from unittest.mock import MagicMock

import rbl.hardware.funcgen_driver as fgd
from rbl.hardware.funcgen_driver import discover, DG1022Z, MAX_GEN_VOLTS, MAX_AMP_VPP


@pytest.fixture
def mock_pyvisa(monkeypatch):
    """Patch the module-level pyvisa binding with a MagicMock and mark it available."""
    m = MagicMock()
    # pyvisa may not be importable in this environment at all, in which case
    # the module-level `pyvisa` name was never bound (the `import pyvisa`
    # inside the try/except failed) -- raising=False lets us inject it.
    monkeypatch.setattr(fgd, "pyvisa", m, raising=False)
    monkeypatch.setattr(fgd, "PYVISA_AVAILABLE", True)
    return m


@pytest.fixture
def mock_inst(mock_pyvisa):
    """A fake instrument resource returned by ResourceManager.open_resource()."""
    inst = MagicMock()
    inst.query.return_value = "RIGOL TECHNOLOGIES,DG1022Z,DG1ZA123456,00.00"
    mock_pyvisa.ResourceManager.return_value.open_resource.return_value = inst
    return inst


# ── discover() ────────────────────────────────────────────────────────────────

class TestDiscover:
    def test_returns_empty_when_pyvisa_unavailable(self, monkeypatch):
        monkeypatch.setattr(fgd, "PYVISA_AVAILABLE", False)
        assert discover() == []

    def test_returns_empty_when_resource_manager_raises(self, mock_pyvisa):
        mock_pyvisa.ResourceManager.side_effect = RuntimeError("no VISA backend")
        assert discover() == []

    def test_skips_non_usb_resources(self, mock_pyvisa):
        mock_pyvisa.ResourceManager.return_value.list_resources.return_value = (
            "ASRL1::INSTR", "GPIB0::1::INSTR",
        )
        assert discover() == []

    def test_skips_instruments_that_fail_to_open(self, mock_pyvisa):
        rm = mock_pyvisa.ResourceManager.return_value
        rm.list_resources.return_value = ("USB0::0x1AB1::0x0642::DG1ZA1::INSTR",)
        rm.open_resource.side_effect = RuntimeError("comm error")
        assert discover() == []

    def test_skips_non_dg1022z_instruments(self, mock_pyvisa):
        rm = mock_pyvisa.ResourceManager.return_value
        rm.list_resources.return_value = ("USB0::0x1AB1::0x0588::DS1ZA1::INSTR",)
        inst = MagicMock()
        inst.query.return_value = "RIGOL TECHNOLOGIES,DS1054Z,DS1ZA1,00.00"
        rm.open_resource.return_value = inst
        assert discover() == []

    def test_finds_dg1022z_and_extracts_serial(self, mock_pyvisa):
        rm = mock_pyvisa.ResourceManager.return_value
        rm.list_resources.return_value = ("USB0::0x1AB1::0x0642::DG1ZA123456::INSTR",)
        inst = MagicMock()
        inst.query.return_value = "RIGOL TECHNOLOGIES,DG1022Z,DG1ZA123456,00.00"
        rm.open_resource.return_value = inst
        found = discover()
        assert len(found) == 1
        assert found[0]["serial"] == "DG1ZA123456"
        assert found[0]["resource"] == "USB0::0x1AB1::0x0642::DG1ZA123456::INSTR"
        assert "DG1022Z" in found[0]["idn"]

    def test_finds_multiple_instruments(self, mock_pyvisa):
        rm = mock_pyvisa.ResourceManager.return_value
        rm.list_resources.return_value = (
            "USB0::0x1AB1::0x0642::DG1ZA111111::INSTR",
            "USB0::0x1AB1::0x0642::DG1ZA222222::INSTR",
        )
        inst = MagicMock()
        inst.query.return_value = "RIGOL TECHNOLOGIES,DG1022Z,SERIAL,00.00"
        rm.open_resource.return_value = inst
        found = discover()
        assert len(found) == 2

    def test_falls_back_to_resource_string_when_serial_unparseable(self, mock_pyvisa):
        rm = mock_pyvisa.ResourceManager.return_value
        rm.list_resources.return_value = ("USB-MALFORMED-RESOURCE",)
        inst = MagicMock()
        inst.query.return_value = "RIGOL TECHNOLOGIES,DG1022Z,X,00.00"
        rm.open_resource.return_value = inst
        found = discover()
        assert found[0]["serial"] == "USB-MALFORMED-RESOURCE"


# ── DG1022Z lifecycle ─────────────────────────────────────────────────────────

class TestLifecycle:
    def test_init_raises_when_pyvisa_unavailable(self, monkeypatch):
        monkeypatch.setattr(fgd, "PYVISA_AVAILABLE", False)
        with pytest.raises(ImportError):
            DG1022Z("USB0::0x1AB1::0x0642::DG1ZA123456::INSTR")

    def test_init_queries_idn(self, mock_inst, mock_pyvisa):
        gen = DG1022Z("USB0::0x1AB1::0x0642::DG1ZA123456::INSTR")
        assert gen.idn() == "RIGOL TECHNOLOGIES,DG1022Z,DG1ZA123456,00.00"

    def test_close_closes_session(self, mock_inst, mock_pyvisa):
        gen = DG1022Z("USB0::...::INSTR")
        gen.close()
        mock_inst.close.assert_called_once()

    def test_close_swallows_errors(self, mock_inst, mock_pyvisa):
        mock_inst.close.side_effect = RuntimeError("already closed")
        gen = DG1022Z("USB0::...::INSTR")
        gen.close()  # must not raise

    def test_write_and_query_passthrough(self, mock_inst, mock_pyvisa):
        gen = DG1022Z("USB0::...::INSTR")
        gen.write(":SOURce1:FREQuency 1000")
        mock_inst.write.assert_called_with(":SOURce1:FREQuency 1000")
        mock_inst.query.return_value = "1000.0"
        assert gen.query(":SOURce1:FREQuency?") == "1000.0"


# ── set_waveform() ────────────────────────────────────────────────────────────

class TestSetWaveform:
    def test_sine_sends_apply_sinusoid(self, mock_inst, mock_pyvisa):
        gen = DG1022Z("USB0::...::INSTR")
        warn = gen.set_waveform(1, "Sine", 1000.0, 1.0, 0.0, 0.0)
        mock_inst.write.assert_any_call(":SOURce1:APPLy:SINusoid 1000.0,1.0,0.0,0.0")
        assert warn == ""

    def test_triangle_sends_ramp_with_50pct_symmetry(self, mock_inst, mock_pyvisa):
        gen = DG1022Z("USB0::...::INSTR")
        gen.set_waveform(2, "Triangle", 500.0, 2.0, 0.0, 0.0)
        mock_inst.write.assert_any_call(":SOURce2:APPLy:RAMP 500.0,2.0,0.0,0.0")
        mock_inst.write.assert_any_call(":SOURce2:FUNCtion:RAMP:SYMMetry 50")

    def test_square_sends_apply_square(self, mock_inst, mock_pyvisa):
        gen = DG1022Z("USB0::...::INSTR")
        gen.set_waveform(1, "Square", 200.0, 1.5, 0.0, 0.0)
        mock_inst.write.assert_any_call(":SOURce1:APPLy:SQUare 200.0,1.5,0.0,0.0")

    def test_pulse_sends_apply_pulse(self, mock_inst, mock_pyvisa):
        gen = DG1022Z("USB0::...::INSTR")
        gen.set_waveform(1, "Pulse", 200.0, 1.5, 0.0, 0.0)
        mock_inst.write.assert_any_call(":SOURce1:APPLy:PULSe 200.0,1.5,0.0,0.0")

    def test_dc_sends_apply_dc_with_offset_only(self, mock_inst, mock_pyvisa):
        gen = DG1022Z("USB0::...::INSTR")
        gen.set_waveform(1, "DC", 0.0, 0.0, 2.5, 0.0)
        mock_inst.write.assert_any_call(":SOURce1:APPLy:DC 1,1,2.5")

    def test_unknown_shape_raises_value_error(self, mock_inst, mock_pyvisa):
        # Regression: set_waveform() previously had no else-clause for an
        # unrecognized shape string, silently sending no SCPI command and
        # returning "" (success) instead of signaling the bad input.
        gen = DG1022Z("USB0::...::INSTR")
        with pytest.raises(ValueError):
            gen.set_waveform(1, "Sawtooth", 100.0, 1.0, 0.0, 0.0)

    def test_amplitude_above_max_is_clamped_with_warning(self, mock_inst, mock_pyvisa):
        # Amplitude (peak-to-peak) clamps to MAX_AMP_VPP (10 Vpp), the level a
        # centred sine needs to reach the ±5 V rail — not MAX_GEN_VOLTS.
        gen = DG1022Z("USB0::...::INSTR")
        warn = gen.set_waveform(1, "Sine", 1000.0, MAX_AMP_VPP + 1.0, 0.0, 0.0)
        assert "clamped amplitude" in warn
        sent = mock_inst.write.call_args_list[0].args[0]
        assert f",{MAX_AMP_VPP},0.0,0.0" in sent

    def test_offset_above_max_is_clamped_with_warning(self, mock_inst, mock_pyvisa):
        gen = DG1022Z("USB0::...::INSTR")
        warn = gen.set_waveform(1, "Sine", 1000.0, 1.0, MAX_GEN_VOLTS + 2.0, 0.0)
        assert "clamped offset" in warn

    def test_amplitude_below_negative_max_is_clamped(self, mock_inst, mock_pyvisa):
        gen = DG1022Z("USB0::...::INSTR")
        warn = gen.set_waveform(1, "Sine", 1000.0, -(MAX_AMP_VPP + 1.0), 0.0, 0.0)
        assert "clamped amplitude" in warn

    def test_within_range_values_are_not_clamped(self, mock_inst, mock_pyvisa):
        gen = DG1022Z("USB0::...::INSTR")
        warn = gen.set_waveform(1, "Sine", 1000.0, 1.0, 0.5, 0.0)
        assert warn == ""


# ── Output control ───────────────────────────────────────────────────────────

class TestOutputControl:
    def test_output_on(self, mock_inst, mock_pyvisa):
        gen = DG1022Z("USB0::...::INSTR")
        gen.output_on(1)
        mock_inst.write.assert_called_with(":OUTPut1 ON")

    def test_output_off(self, mock_inst, mock_pyvisa):
        gen = DG1022Z("USB0::...::INSTR")
        gen.output_off(2)
        mock_inst.write.assert_called_with(":OUTPut2 OFF")

    def test_set_output_load_default_infinity(self, mock_inst, mock_pyvisa):
        gen = DG1022Z("USB0::...::INSTR")
        gen.set_output_load(1)
        mock_inst.write.assert_called_with(":OUTPut1:LOAD INFinity")

    def test_set_output_load_explicit_value(self, mock_inst, mock_pyvisa):
        gen = DG1022Z("USB0::...::INSTR")
        gen.set_output_load(1, "50")
        mock_inst.write.assert_called_with(":OUTPut1:LOAD 50")


# ── get_state() ───────────────────────────────────────────────────────────────

class TestGetState:
    def test_parses_normal_response(self, mock_inst, mock_pyvisa):
        mock_inst.query.side_effect = [
            "RIGOL TECHNOLOGIES,DG1022Z,DG1ZA123456,00.00",  # *IDN? at init
            '"SIN 1000.000000,1.000000,0.000000,0.000000"',   # APPLy?
            "1",                                              # OUTPut?
            "INF",                                             # OUTPut:LOAD?
        ]
        gen = DG1022Z("USB0::...::INSTR")
        state = gen.get_state(1)
        assert state["shape"] == "SIN"
        assert state["freq"] == 1000.0
        assert state["amp"] == 1.0
        assert state["offset"] == 0.0
        assert state["phase"] == 0.0
        assert state["output"] is True
        assert state["load"] == "INF"

    def test_output_off_parsed_as_false(self, mock_inst, mock_pyvisa):
        mock_inst.query.side_effect = [
            "RIGOL TECHNOLOGIES,DG1022Z,DG1ZA123456,00.00",
            '"SIN 1000.000000,1.000000,0.000000,0.000000"',
            "0",
            "INF",
        ]
        gen = DG1022Z("USB0::...::INSTR")
        state = gen.get_state(1)
        assert state["output"] is False

    def test_returns_error_dict_on_visa_failure(self, mock_inst, mock_pyvisa):
        mock_inst.query.side_effect = [
            "RIGOL TECHNOLOGIES,DG1022Z,DG1ZA123456,00.00",  # *IDN? at init
            RuntimeError("VISA timeout"),
        ]
        gen = DG1022Z("USB0::...::INSTR")
        state = gen.get_state(1)
        assert "error" in state

    def test_malformed_numeric_payload_does_not_raise(self, mock_inst, mock_pyvisa):
        mock_inst.query.side_effect = [
            "RIGOL TECHNOLOGIES,DG1022Z,DG1ZA123456,00.00",
            '"SIN not,valid,numbers"',
            "1",
            "INF",
        ]
        gen = DG1022Z("USB0::...::INSTR")
        state = gen.get_state(1)
        assert state["shape"] == "SIN"
        assert state["freq"] == 0.0


# ── Verbose / passthrough SCPI helpers ───────────────────────────────────────

class TestPassthroughMethods:
    def test_set_phase(self, mock_inst, mock_pyvisa):
        gen = DG1022Z("USB0::...::INSTR")
        gen.set_phase(1, 90.0)
        mock_inst.write.assert_called_with(":SOURce1:PHASe 90.0")

    def test_set_duty(self, mock_inst, mock_pyvisa):
        gen = DG1022Z("USB0::...::INSTR")
        gen.set_duty(1, 25.0)
        mock_inst.write.assert_called_with(":SOURce1:FUNCtion:SQUare:DCYCle 25.0")

    def test_set_ramp_symmetry(self, mock_inst, mock_pyvisa):
        gen = DG1022Z("USB0::...::INSTR")
        gen.set_ramp_symmetry(1, 80.0)
        mock_inst.write.assert_called_with(":SOURce1:FUNCtion:RAMP:SYMMetry 80.0")

    def test_sweep_on_off(self, mock_inst, mock_pyvisa):
        gen = DG1022Z("USB0::...::INSTR")
        gen.sweep_on(1)
        mock_inst.write.assert_called_with(":SOURce1:SWEep:STATe ON")
        gen.sweep_off(1)
        mock_inst.write.assert_called_with(":SOURce1:SWEep:STATe OFF")

    def test_set_sweep(self, mock_inst, mock_pyvisa):
        gen = DG1022Z("USB0::...::INSTR")
        gen.set_sweep(1, 100.0, 5000.0, 2.0)
        mock_inst.write.assert_any_call(":SOURce1:SWEep:STARt 100.0")
        mock_inst.write.assert_any_call(":SOURce1:SWEep:STOP 5000.0")
        mock_inst.write.assert_any_call(":SOURce1:SWEep:TIME 2.0")

    def test_burst_on_off(self, mock_inst, mock_pyvisa):
        gen = DG1022Z("USB0::...::INSTR")
        gen.burst_on(1)
        mock_inst.write.assert_called_with(":SOURce1:BURSt:STATe ON")
        gen.burst_off(1)
        mock_inst.write.assert_called_with(":SOURce1:BURSt:STATe OFF")

    def test_set_burst(self, mock_inst, mock_pyvisa):
        gen = DG1022Z("USB0::...::INSTR")
        gen.set_burst(1, 5, 0.01)
        mock_inst.write.assert_any_call(":SOURce1:BURSt:NCYCles 5")
        mock_inst.write.assert_any_call(":SOURce1:BURSt:INTernal:PERiod 0.01")

    def test_align_phase(self, mock_inst, mock_pyvisa):
        gen = DG1022Z("USB0::...::INSTR")
        gen.align_phase(1)
        mock_inst.write.assert_called_with(":SOURce1:PHASe:SYNChronize")

    def test_beep(self, mock_inst, mock_pyvisa):
        gen = DG1022Z("USB0::...::INSTR")
        gen.beep()
        mock_inst.write.assert_called_with(":SYSTem:BEEPer:IMMediate")

    def test_get_error(self, mock_inst, mock_pyvisa):
        mock_inst.query.return_value = '0,"No error"'
        gen = DG1022Z("USB0::...::INSTR")
        assert gen.get_error() == '0,"No error"'
