"""
tests/test_tds_waveform.py
Offline tests for rbl/hardware/tds2012_driver.py.

All tests use FakeTransport — no real serial port is touched.
"""
import struct

import pytest

from rbl.hardware.serial_transport import SerialTimeout
from rbl.hardware.tds2012_driver import (
    Tds2012,
    TdsProtocolError,
    TdsTimeoutError,
    _decode_ieee488_block,
    _parse_preamble,
    samples_to_volts,
)

# ---------------------------------------------------------------------------
# FakeTransport
# ---------------------------------------------------------------------------

class FakeTransport:
    """Plays back a fixed response queue; None entries raise SerialTimeout."""

    def __init__(self, responses: list):
        self._q    = list(responses)
        self._sent = []         # record of written bytes for assertions

    def _next(self) -> bytes:
        if not self._q:
            raise SerialTimeout("FakeTransport: queue exhausted")
        item = self._q.pop(0)
        if item is None:
            raise SerialTimeout("FakeTransport: simulated timeout")
        return item

    def write(self, data: bytes) -> None:
        self._sent.append(data)

    def read_until(self, terminator: bytes, max_bytes: int = 4096) -> bytes:
        return self._next()

    def read_exact(self, n: int) -> bytes:
        item = self._next()
        if len(item) < n:
            raise SerialTimeout(
                f"FakeTransport: read_exact({n}) got only {len(item)} bytes"
            )
        return item[:n]

    def query_line(self, cmd: bytes, terminator: bytes = b"\n") -> bytes:
        self._sent.append(cmd)
        return self._next()

    def reset_buffers(self) -> None:
        pass


# ---------------------------------------------------------------------------
# _decode_ieee488_block
# ---------------------------------------------------------------------------

class TestDecodeIeee488Block:

    def _make_block(self, payload: bytes) -> bytes:
        n = len(payload)
        digits = str(n).encode()
        header = b"#" + str(len(digits)).encode() + digits
        return header + payload + b"\n"

    def test_single_digit_length(self):
        payload = b"\x01\x02\x03"
        block   = self._make_block(payload)
        assert _decode_ieee488_block(block) == payload

    def test_four_digit_length(self):
        payload = bytes(i % 256 for i in range(1000))
        block   = self._make_block(payload)
        assert _decode_ieee488_block(block) == payload

    def test_leading_whitespace_ignored(self):
        payload = b"ABCD"
        block   = b"  \r\n" + self._make_block(payload)
        assert _decode_ieee488_block(block) == payload

    def test_missing_hash_raises(self):
        with pytest.raises(TdsProtocolError, match="missing '#'"):
            _decode_ieee488_block(b"no hash here\n")

    def test_indefinite_block_raises(self):
        # #0 = indefinite-length block — not supported
        with pytest.raises(TdsProtocolError, match="Indefinite"):
            _decode_ieee488_block(b"#0\n")

    def test_truncated_header_raises(self):
        # '#' only, no N digit
        with pytest.raises(TdsProtocolError):
            _decode_ieee488_block(b"#")

    def test_non_numeric_length_raises(self):
        with pytest.raises(TdsProtocolError, match="non-numeric"):
            _decode_ieee488_block(b"#2AB" + b"\x00" * 20)

    def test_truncated_payload_raises(self):
        # Claims 100 bytes but only supplies 10
        with pytest.raises(TdsProtocolError, match="truncated"):
            _decode_ieee488_block(b"#3100" + b"\x00" * 10)

    def test_extra_bytes_after_payload_ignored(self):
        payload = b"\xDE\xAD\xBE\xEF"
        block   = self._make_block(payload) + b"garbage"
        result  = _decode_ieee488_block(block)
        assert result == payload


# ---------------------------------------------------------------------------
# _parse_preamble
# ---------------------------------------------------------------------------

class TestParsePreamble:

    _TYPICAL = (
        "BYT_NR:1,BIT_NR:8,ENCDG:BIN,BN_FMT:RI,BYT_OR:MSB,"
        "NR_PT:2500,WFID:\"CH1, DC coupling\",PT_FMT:Y,"
        "XINCR:4E-09,PT_OFF:0,XZERO:-4.98E-06,XUNIT:s,"
        "YMULT:4E-04,YOFF:-50,YZERO:0,YUNIT:V"
    )

    def test_typical_preamble(self):
        pre = _parse_preamble(self._TYPICAL)
        assert pre["BYT_NR"] == 1
        assert pre["NR_PT"]  == 2500
        assert pre["ENCDG"]  == "BIN"
        assert pytest.approx(pre["YMULT"],  rel=1e-6) == 4e-4
        assert pytest.approx(pre["YOFF"],   rel=1e-6) == -50.0
        assert pytest.approx(pre["YZERO"],  rel=1e-6) == 0.0
        assert pytest.approx(pre["XINCR"],  rel=1e-6) == 4e-9
        assert pytest.approx(pre["XZERO"],  rel=1e-6) == -4.98e-6

    def test_float_fields_are_float(self):
        pre = _parse_preamble(self._TYPICAL)
        for field in ("YMULT", "YOFF", "YZERO", "XINCR", "XZERO"):
            assert isinstance(pre[field], float), f"{field} should be float"

    def test_int_fields_are_int(self):
        pre = _parse_preamble(self._TYPICAL)
        for field in ("BYT_NR", "BIT_NR", "NR_PT"):
            assert isinstance(pre[field], int), f"{field} should be int"

    def test_empty_string_raises(self):
        with pytest.raises(TdsProtocolError):
            _parse_preamble("")

    def test_no_colon_tokens_skipped(self):
        # A token without ':' should be silently skipped, not raise
        pre = _parse_preamble("BYT_NR:2,GARBAGE,YMULT:1.0E-3")
        assert pre["BYT_NR"] == 2
        assert pytest.approx(pre["YMULT"]) == 1e-3


# ---------------------------------------------------------------------------
# samples_to_volts
# ---------------------------------------------------------------------------

class TestSamplesToVolts:

    def _preamble(self, *, byt_nr=1, bn_fmt="RI", byt_or="MSB",
                  ymult=1.0, yoff=0.0, yzero=0.0):
        return {
            "BYT_NR": byt_nr, "BN_FMT": bn_fmt, "BYT_OR": byt_or,
            "YMULT": ymult, "YOFF": yoff, "YZERO": yzero,
        }

    def test_zero_raw_gives_yzero(self):
        pre  = self._preamble(ymult=4e-4, yoff=0.0, yzero=0.0)
        data = struct.pack("b", 0)
        assert samples_to_volts(data, pre) == [0.0]

    def test_positive_raw_byte(self):
        # (100 - 0) * 4e-4 + 0 = 0.04 V
        pre  = self._preamble(ymult=4e-4, yoff=0.0, yzero=0.0)
        data = struct.pack("b", 100)
        volts = samples_to_volts(data, pre)
        assert pytest.approx(volts[0], rel=1e-6) == 0.04

    def test_yoff_subtracted(self):
        # (0 - (-50)) * 4e-4 + 0 = 50 * 4e-4 = 0.02 V
        pre  = self._preamble(ymult=4e-4, yoff=-50.0, yzero=0.0)
        data = struct.pack("b", 0)
        volts = samples_to_volts(data, pre)
        assert pytest.approx(volts[0], rel=1e-6) == 0.02

    def test_yzero_offset(self):
        # (0 - 0) * 1.0 + 1.5 = 1.5 V
        pre  = self._preamble(ymult=1.0, yoff=0.0, yzero=1.5)
        data = struct.pack("b", 0)
        assert pytest.approx(samples_to_volts(data, pre)[0]) == 1.5

    def test_negative_signed_byte(self):
        pre  = self._preamble(ymult=1.0, yoff=0.0, yzero=0.0)
        data = struct.pack("b", -50)
        assert samples_to_volts(data, pre) == [-50.0]

    def test_unsigned_byte(self):
        # BN_FMT=RP means unsigned
        pre  = self._preamble(bn_fmt="RP", ymult=1.0, yoff=0.0, yzero=0.0)
        data = struct.pack("B", 200)
        assert samples_to_volts(data, pre) == [200.0]

    def test_two_byte_msb(self):
        pre  = self._preamble(byt_nr=2, byt_or="MSB", ymult=1.0, yoff=0.0, yzero=0.0)
        data = struct.pack(">h", 1000)
        assert pytest.approx(samples_to_volts(data, pre)[0]) == 1000.0

    def test_two_byte_lsb(self):
        pre  = self._preamble(byt_nr=2, byt_or="LSB", ymult=1.0, yoff=0.0, yzero=0.0)
        data = struct.pack("<h", -2000)
        assert pytest.approx(samples_to_volts(data, pre)[0]) == -2000.0

    def test_multiple_samples(self):
        pre  = self._preamble(ymult=1.0, yoff=0.0, yzero=0.0)
        data = struct.pack("4b", 1, 2, 3, 4)
        assert samples_to_volts(data, pre) == [1.0, 2.0, 3.0, 4.0]

    def test_unsupported_byt_nr_raises(self):
        pre  = self._preamble(byt_nr=4)
        with pytest.raises(TdsProtocolError, match="BYT_NR"):
            samples_to_volts(b"\x00\x00\x00\x00", pre)

    def test_typical_scope_preamble_scaling(self):
        """Reproduce the exact scaling the TDS 2012 manual example gives."""
        # YMULT=4E-4, YOFF=-50, YZERO=0; raw=127 → (127-(-50))*4e-4+0=0.0708
        pre = {"BYT_NR": 1, "BN_FMT": "RI", "BYT_OR": "MSB",
               "YMULT": 4e-4, "YOFF": -50.0, "YZERO": 0.0}
        data  = struct.pack("b", 127)
        volts = samples_to_volts(data, pre)
        assert pytest.approx(volts[0], rel=1e-6) == (127 - (-50)) * 4e-4


# ---------------------------------------------------------------------------
# Tds2012 driver (FakeTransport)
# ---------------------------------------------------------------------------

class TestTds2012Driver:

    _IDENT = b"ID TEK/TDS 2012,B010001,CF:91.1CT,A\n"
    _PREAMBLE_RESP = (
        b"BYT_NR:1,BIT_NR:8,ENCDG:BIN,BN_FMT:RI,BYT_OR:MSB,"
        b"NR_PT:4,WFID:\"CH1\",PT_FMT:Y,XINCR:1E-6,PT_OFF:0,"
        b"XZERO:0,XUNIT:s,YMULT:1.0,YOFF:0,YZERO:0,YUNIT:V\n"
    )

    def _make_curve_response(self, payload: bytes) -> list:
        """Return the FakeTransport response sequence for a CURVE? query."""
        n      = len(payload)
        digits = str(n).encode()
        # First read_until('#') returns up to and including '#'
        header_part = b"#" + str(len(digits)).encode() + digits
        # After '#' + N char + length digits, payload read via read_exact
        return [
            header_part,    # read_until(b'#') — returns '#NDD...'
            payload,        # read_exact(n_bytes)
            b"\n",          # trailing LF consume
        ]

    def test_identify_ok(self):
        t   = FakeTransport([self._IDENT])
        drv = Tds2012(t)
        ident = drv.identify()
        assert "TEK" in ident or "TDS" in ident

    def test_identify_timeout(self):
        t   = FakeTransport([None])
        drv = Tds2012(t)
        with pytest.raises(TdsTimeoutError):
            drv.identify()

    def test_identify_not_tektronix_raises(self):
        t   = FakeTransport([b"ID MADE_UP_VENDOR,serial\n"])
        drv = Tds2012(t)
        with pytest.raises(TdsProtocolError, match="not look like a Tektronix"):
            drv.identify()

    def test_query_error_response_raises(self):
        # Scope returns '?' on unrecognised command
        t   = FakeTransport([b"? Unrecognized command\n"])
        drv = Tds2012(t)
        with pytest.raises(TdsProtocolError):
            drv._query("BADCMD?")

    def test_read_preamble_ok(self):
        t   = FakeTransport([self._PREAMBLE_RESP])
        drv = Tds2012(t)
        pre = drv.read_preamble()
        assert pre["NR_PT"]  == 4
        assert pre["BYT_NR"] == 1

    def test_acquire_waveform_returns_correct_payload(self):
        payload = struct.pack("4b", 10, 20, 30, -10)
        t = FakeTransport([
            self._PREAMBLE_RESP,           # WFMPRE? response
            *self._make_curve_response(payload),
        ])
        drv = Tds2012(t)
        # set_data_encoding sends 3 commands with no responses; pre-load
        # the transport by queuing the preamble after the 3 write() calls
        # Actually set_data_encoding uses _cmd (write, no read), so no
        # queue entries needed for those.  Only read_preamble and CURVE? do.
        raw, pre = drv.acquire_waveform("CH1")
        assert raw == payload
        assert pre["NR_PT"] == 4

    def test_acquire_waveform_voltage_conversion(self):
        """End-to-end: raw bytes → voltages via preamble scaling."""
        payload = struct.pack("4b", 0, 50, -50, 100)
        t = FakeTransport([
            self._PREAMBLE_RESP,
            *self._make_curve_response(payload),
        ])
        drv   = Tds2012(t)
        raw, pre = drv.acquire_waveform("CH1")
        volts = samples_to_volts(raw, pre)
        # YMULT=1.0, YOFF=0, YZERO=0 → volts == raw floats
        assert pytest.approx(volts) == [0.0, 50.0, -50.0, 100.0]

    def test_acquire_waveform_timeout_raises(self):
        t = FakeTransport([
            self._PREAMBLE_RESP,
            None,    # timeout on read_until('#')
        ])
        drv = Tds2012(t)
        with pytest.raises(TdsTimeoutError):
            drv.acquire_waveform("CH1")

    def test_curve_missing_hash_raises(self):
        # read_until returns bytes without '#'
        t = FakeTransport([
            self._PREAMBLE_RESP,
            b"garbage no hash\n",
        ])
        drv = Tds2012(t)
        with pytest.raises(TdsProtocolError, match="missing '#'"):
            drv.acquire_waveform("CH1")

    def test_zero_nr_pt_raises(self):
        # Preamble with NR_PT:0 should raise before CURVE?
        bad_preamble = (
            b"BYT_NR:1,BIT_NR:8,ENCDG:BIN,BN_FMT:RI,BYT_OR:MSB,"
            b"NR_PT:0,WFID:\"CH1\",PT_FMT:Y,XINCR:1E-6,PT_OFF:0,"
            b"XZERO:0,XUNIT:s,YMULT:1.0,YOFF:0,YZERO:0,YUNIT:V\n"
        )
        t   = FakeTransport([bad_preamble])
        drv = Tds2012(t)
        with pytest.raises(TdsProtocolError, match="NR_PT"):
            drv.acquire_waveform("CH1")

    def test_single_acquisition_sends_correct_commands(self):
        t   = FakeTransport([])
        drv = Tds2012(t)
        drv.single_acquisition()
        assert b"ACQUIRE:STOPAFTER SEQUENCE\n" in t._sent
        assert b"ACQUIRE:STATE RUN\n" in t._sent

    def test_run_continuous_sends_correct_commands(self):
        t   = FakeTransport([])
        drv = Tds2012(t)
        drv.run_continuous()
        assert b"ACQUIRE:STOPAFTER RUNSTOP\n" in t._sent
        assert b"ACQUIRE:STATE RUN\n" in t._sent


# ---------------------------------------------------------------------------
# The idle keepalive must not shout
# ---------------------------------------------------------------------------

class TestIdentifyIsQuiet:
    """`identify()` is called by the idle keepalive on a timer.  At INFO it
    printed a line every few seconds forever and buried everything that
    mattered.  The connect path already reports the identity once, at INFO,
    where it is news."""

    class _FakeTransport:
        def __init__(self, reply=b"ID TEK/TDS 2012,CF:91.1CT FV:v1.16\n"):
            self._reply = reply
        def write(self, data): pass
        def query_line(self, data, terminator=b"\n"): return self._reply
        def read_until(self, *a, **k): return b""
        def read_exact(self, n): return b"\x00" * n
        def reset_buffers(self): pass

    def test_identify_does_not_log_at_info(self, caplog):
        import logging

        from rbl.hardware.tds2012_driver import Tds2012
        with caplog.at_level(logging.INFO, logger="rbl.hardware.tds2012_driver"):
            Tds2012(self._FakeTransport()).identify()
        assert [r for r in caplog.records
                if r.name == "rbl.hardware.tds2012_driver"] == []

    def test_the_keepalive_interval_is_not_walked_back(self):
        from rbl.hardware import scope_worker
        assert scope_worker._KEEPALIVE_S >= 15.0
