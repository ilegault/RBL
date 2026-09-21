"""
Tests for the Keithley 6482 picoammeter driver and pure response parsing.
Uses mocked pyvisa to verify discovery, SCPI configuration, the voltage-source safety
interlock, and pure response parsing without real GPIB hardware attached.
"""
from inspect import getsource
from unittest.mock import MagicMock

import pytest
import pyvisa.errors

import rbl.hardware.keithley6482_driver as k6482_mod
from rbl.hardware.keithley6482_driver import (
    Keithley6482,
    Keithley6482Reading,
    discover,
    is_over_range_sentinel,
    is_unavailable_sentinel,
    parse_reading,
)

_MOCK_IDN = "KEITHLEY INSTRUMENTS INC.,MODEL 6482,1234567,A01 / 700x"

_ACCEPTED_WRITES: frozenset[str] = frozenset({
    "*CLS",
    ":OUTPut1:STATe OFF",
    ":OUTPut2:STATe OFF",
    ":SENSe1:CURRent:DC:RANGe:AUTO ON",
    ":SENSe1:CURRent:DC:NPLCycles 1",
    ":SENSe1:MEDian:STATe OFF",
    ":SENSe1:AVERage:STATe OFF",
    ":FORMat:ELEMents CURRent1,TIME,STATus",
})

_QUERY_RESPONSES: dict[str, str] = {
    "*IDN?": _MOCK_IDN,
    ":SYSTem:MEP:STATe?": "1",
    ":OUTPut1?": "0",
    ":OUTPut2?": "0",
    ":READ?": "+4.827434E-11,+1.953328E+03,+0.000000E+00",
}


class _Fake6482:
    """Models a real Keithley 6482 on GPIB per bench verification (2026-09-21).

    - Holds an accepted_writes set; unknown writes queue -113,"Undefined header".
    - Holds a responses dict; unknown queries queue -113 and raise VisaIOError(VI_ERROR_TMO).
    - :SYSTem:ERRor? pops the oldest error from the queue, returning 0,"No error" if empty.
    - *CLS clears the error queue.
    """

    def __init__(self) -> None:
        self.accepted_writes: set[str] = set(_ACCEPTED_WRITES)
        self.responses: dict[str, str] = dict(_QUERY_RESPONSES)
        self.error_queue: list[str] = []
        self.write = MagicMock(side_effect=self._write_impl)
        self.query = MagicMock(side_effect=self._query_impl)
        self.close = MagicMock()

    def _write_impl(self, cmd: str) -> None:
        if cmd == "*CLS":
            self.error_queue.clear()
            return
        if cmd not in self.accepted_writes:
            self.error_queue.append('-113,"Undefined header"')

    def _query_impl(self, cmd: str) -> str:
        if cmd == ":SYSTem:ERRor?":
            if self.error_queue:
                return self.error_queue.pop(0)
            return '0,"No error"'
        if cmd in self.responses:
            return self.responses[cmd]
        self.error_queue.append('-113,"Undefined header"')
        raise pyvisa.errors.VisaIOError(-1073807339)


@pytest.fixture
def mock_pyvisa(monkeypatch):
    """Patch module-level pyvisa binding with MagicMock and mark it available."""
    m = MagicMock()
    monkeypatch.setattr(k6482_mod, "pyvisa", m, raising=False)
    monkeypatch.setattr(k6482_mod, "PYVISA_AVAILABLE", True)
    return m


@pytest.fixture
def mock_inst(mock_pyvisa):
    """Fake PyVISA resource returned by ResourceManager.open_resource()."""
    fake = _Fake6482()
    mock_pyvisa.ResourceManager.return_value.open_resource.return_value = fake
    return fake


# ── Pure Response Parsing Tests ───────────────────────────────────────────────

class TestParseReading:
    def test_normal_reading(self):
        raw = "+1.234567E-06,+12.345678,+00000000\n"
        reading = parse_reading(raw)
        assert isinstance(reading, Keithley6482Reading)
        assert reading.current == pytest.approx(1.234567e-6)
        assert reading.timestamp == pytest.approx(12.345678)
        assert reading.status_word == 0
        assert reading.over_range is False
        assert reading.unavailable is False
        assert reading.valid is True
        assert reading.channel_2 is None
        assert reading.raw == raw

    def test_negative_current_reading(self):
        raw = '"-5.432100E-09, 1.5, 0"'
        reading = parse_reading(raw)
        assert reading.current == pytest.approx(-5.4321e-9)
        assert reading.timestamp == pytest.approx(1.5)
        assert reading.over_range is False
        assert reading.valid is True

    def test_over_range_sentinel(self):
        raw = "+9.910000E+37, 10.0, 64"
        reading = parse_reading(raw)
        assert reading.over_range is True
        assert reading.current is None
        assert reading.valid is True
        assert reading.unavailable is False
        assert reading.timestamp == pytest.approx(10.0)
        assert reading.status_word == 64

    def test_negative_over_range_sentinel(self):
        raw = "-9.910000E+37, 5.0, 0"
        reading = parse_reading(raw)
        assert reading.over_range is True
        assert reading.current is None
        assert reading.valid is True

    def test_unavailable_reading_sentinel(self):
        raw = "+9.900000E+37, 10.0, 0"
        reading = parse_reading(raw)
        assert reading.unavailable is True
        assert reading.valid is False
        assert reading.current is None
        assert reading.timestamp == pytest.approx(10.0)

    def test_status_word_over_range_bit_set(self):
        # Bit 6 (value 64 / 0x40) is the over-range bit
        raw = "+2.500000E-06, 3.0, 64"
        reading = parse_reading(raw)
        assert reading.over_range is True
        assert reading.current == pytest.approx(2.5e-6)
        assert reading.status_word == 64

    def test_status_word_multiple_bits_with_over_range(self):
        # Status word with bits 5 and 6 set (32 + 64 = 96)
        raw = "+3.000000E-06, 4.0, 96"
        reading = parse_reading(raw)
        assert reading.over_range is True
        assert reading.current == pytest.approx(3.0e-6)

    def test_status_word_other_bits_without_over_range(self):
        # Bit 5 (zero correct) and bit 4 (zero check) without bit 6: 16 + 32 = 48
        raw = "+1.000000E-06, 2.0, 48"
        reading = parse_reading(raw)
        assert reading.over_range is False
        assert reading.current == pytest.approx(1.0e-6)

    def test_over_range_never_inferred_from_magnitude_alone(self):
        # Even a huge or small physical value with bit 6 cleared is NOT over-range
        raw = "+0.020000E+00, 1.0, 0"  # 20 mA
        reading = parse_reading(raw)
        assert reading.over_range is False
        assert reading.current == pytest.approx(0.02)

    def test_channel_2_is_represented_as_absent(self):
        reading = parse_reading("+1e-6, 1.0, 0")
        assert reading.channel_2 is None

    def test_protocol_mode_passthrough(self):
        reading = parse_reading("+1e-6, 1.0, 0", protocol_mode=1)
        assert reading.protocol_mode == 1

    def test_bench_reading_from_real_6482(self):
        reading = parse_reading("+4.827434E-11,+1.953328E+03,+0.000000E+00")
        assert reading.current == pytest.approx(4.827434e-11)
        assert reading.timestamp == pytest.approx(1953.328)
        assert reading.status_word == 0
        assert reading.over_range is False
        assert reading.valid is True

    @pytest.mark.parametrize("bad_input", [
        "",
        "   ",
        "invalid,text,here",
        "1.0, 2.0",  # Only 2 elements instead of 3
        "1.0, abc, 0",
        "1.0, 2.0, xyz",
    ])
    def test_malformed_input_raises_value_error(self, bad_input):
        with pytest.raises(ValueError):
            parse_reading(bad_input)

    def test_non_string_input_raises_value_error(self):
        with pytest.raises(ValueError):
            parse_reading(12345)  # type: ignore[arg-type]


# ── Sentinel Recognition Pure Functions ───────────────────────────────────────

class TestSentinels:
    def test_over_range_sentinel_recognition(self):
        assert is_over_range_sentinel(9.91e37)
        assert is_over_range_sentinel(-9.91e37)
        assert is_over_range_sentinel(9.910000e37)
        assert not is_over_range_sentinel(9.90e37)
        assert not is_over_range_sentinel(1.0)
        assert not is_over_range_sentinel(float("inf"))

    def test_unavailable_sentinel_recognition(self):
        assert is_unavailable_sentinel(9.90e37)
        assert is_unavailable_sentinel(-9.90e37)
        assert is_unavailable_sentinel(9.900000e37)
        assert not is_unavailable_sentinel(9.91e37)
        assert not is_unavailable_sentinel(1.0)
        assert not is_unavailable_sentinel(float("nan"))


# ── Discovery Tests ───────────────────────────────────────────────────────────

class TestDiscover:
    def test_returns_empty_when_pyvisa_unavailable(self, monkeypatch):
        monkeypatch.setattr(k6482_mod, "PYVISA_AVAILABLE", False)
        assert discover() == []

    def test_returns_empty_when_resource_manager_raises(self, mock_pyvisa):
        mock_pyvisa.ResourceManager.side_effect = RuntimeError("no VISA backend")
        assert discover() == []

    def test_skips_non_gpib_resources(self, mock_pyvisa):
        mock_pyvisa.ResourceManager.return_value.list_resources.return_value = (
            "ASRL1::INSTR", "USB0::0x1AB1::0x0642::DG1ZA123456::INSTR",
        )
        assert discover() == []

    def test_skips_instruments_that_fail_to_open(self, mock_pyvisa):
        rm = mock_pyvisa.ResourceManager.return_value
        rm.list_resources.return_value = ("GPIB0::14::INSTR",)
        rm.open_resource.side_effect = RuntimeError("comm error")
        assert discover() == []

    def test_skips_non_keithley_instruments(self, mock_pyvisa):
        rm = mock_pyvisa.ResourceManager.return_value
        rm.list_resources.return_value = ("GPIB0::14::INSTR",)
        inst = MagicMock()
        inst.query.return_value = "HEWLETT-PACKARD,34401A,0,1"
        rm.open_resource.return_value = inst
        assert discover() == []

    def test_finds_keithley_6482(self, mock_pyvisa):
        rm = mock_pyvisa.ResourceManager.return_value
        rm.list_resources.return_value = ("GPIB0::14::INSTR",)
        inst = MagicMock()
        inst.query.return_value = _MOCK_IDN
        rm.open_resource.return_value = inst
        found = discover()
        assert len(found) == 1
        assert found[0]["resource"] == "GPIB0::14::INSTR"
        assert "6482" in found[0]["idn"]


# ── Keithley 6482 Lifecycle and Configuration ─────────────────────────────────

class TestKeithley6482Driver:
    def test_init_raises_when_pyvisa_unavailable(self, monkeypatch):
        monkeypatch.setattr(k6482_mod, "PYVISA_AVAILABLE", False)
        with pytest.raises(ImportError):
            Keithley6482("GPIB0::14::INSTR")

    def test_init_queries_idn_and_protocol_mode(self, mock_inst, mock_pyvisa):
        pico = Keithley6482("GPIB0::14::INSTR")
        assert pico.idn() == _MOCK_IDN
        assert pico.protocol_mode == 1

    def test_init_handles_488_1_protocol_mode(self, mock_inst, mock_pyvisa):
        mock_inst.responses[":SYSTem:MEP:STATe?"] = "0"
        pico = Keithley6482("GPIB0::14::INSTR")
        assert pico.protocol_mode == 0

    def test_init_sends_required_configuration_commands(self, mock_inst, mock_pyvisa):
        Keithley6482("GPIB0::14::INSTR")
        written = [c.args[0] for c in mock_inst.write.call_args_list]

        assert "*CLS" in written
        assert ":OUTPut1:STATe OFF" in written
        assert ":OUTPut2:STATe OFF" in written
        assert ":SENSe1:CURRent:DC:RANGe:AUTO ON" in written
        assert ":SENSe1:CURRent:DC:NPLCycles 1" in written
        assert ":SENSe1:MEDian:STATe OFF" in written
        assert ":SENSe1:AVERage:STATe OFF" in written
        assert ":FORMat:ELEMents CURRent1,TIME,STATus" in written

        assert ":SOURce1:STATe OFF" not in written
        assert ":SOURce2:STATe OFF" not in written
        assert ":SENSe1:FUNCtion 'CURRent:DC'" not in written
        assert ":FORMat:ELEMents READing,TIME,STATus" not in written

    def test_init_verifies_voltage_sources_are_off(self, mock_inst, mock_pyvisa):
        # Normal case: both return "0", no exception
        pico = Keithley6482("GPIB0::14::INSTR")
        # Assertions ran in __init__
        assert pico.idn() == _MOCK_IDN

    def test_init_raises_if_source1_is_active(self, mock_inst, mock_pyvisa):
        mock_inst.responses[":OUTPut1?"] = "1"
        with pytest.raises(RuntimeError, match="voltage source safety assertion failed"):
            Keithley6482("GPIB0::14::INSTR")

    def test_init_raises_if_source2_is_active(self, mock_inst, mock_pyvisa):
        mock_inst.responses[":OUTPut2?"] = "1"
        with pytest.raises(RuntimeError, match="voltage source safety assertion failed"):
            Keithley6482("GPIB0::14::INSTR")

    def test_init_raises_naming_rejected_command(self, mock_inst, mock_pyvisa):
        cmd = ":SENSe1:CURRent:DC:NPLCycles 1"
        mock_inst.accepted_writes.remove(cmd)
        with pytest.raises(RuntimeError) as exc_info:
            Keithley6482("GPIB0::14::INSTR")
        assert cmd in str(exc_info.value)
        assert "-113" in str(exc_info.value)

    def test_init_clears_stale_errors_first(self, mock_inst, mock_pyvisa):
        mock_inst.error_queue.append('-420,"Query UNTERMINATED"')
        pico = Keithley6482("GPIB0::14::INSTR")
        assert pico.idn() == _MOCK_IDN

    def test_configure_command_is_never_used_in_module(self):
        """CRITICAL SAFETY TEST: Verify ':CONFigure' or 'CONF' is NEVER sent in the driver."""
        src = getsource(k6482_mod)
        # Check executable code lines (excluding comments and docstrings)
        code_lines = []
        in_docstring = False
        for line in src.splitlines():
            s = line.strip()
            if s.startswith('"""') or s.startswith("'''"):
                if s.count('"""') == 2 or s.count("'''") == 2:
                    continue
                in_docstring = not in_docstring
                continue
            if in_docstring or s.startswith("#"):
                continue
            code_lines.append(s)

        code_text = "\n".join(code_lines).upper()
        assert ":CONFIGURE" not in code_text
        assert "CONF:" not in code_text

    def test_read_reading_queries_read_and_parses(self, mock_inst, mock_pyvisa):
        mock_inst.responses[":READ?"] = "+3.456700E-07,+5.123456,+00000000"
        pico = Keithley6482("GPIB0::14::INSTR")
        reading = pico.read_reading()
        assert isinstance(reading, Keithley6482Reading)
        assert reading.current == pytest.approx(3.4567e-7)
        assert reading.timestamp == pytest.approx(5.123456)
        assert reading.status_word == 0
        assert reading.over_range is False
        assert reading.protocol_mode == 1

    def test_close_closes_visa_session(self, mock_inst, mock_pyvisa):
        pico = Keithley6482("GPIB0::14::INSTR")
        pico.close()
        mock_inst.close.assert_called_once()

    def test_close_swallows_errors(self, mock_inst, mock_pyvisa):
        mock_inst.close.side_effect = RuntimeError("already closed")
        pico = Keithley6482("GPIB0::14::INSTR")
        pico.close()  # Must not raise
