"""
tests/test_xgs600_parse.py
Offline parser tests for the XGS-600 driver.

No serial port, no hardware.  A FakeTransport feeds canned byte strings
to Xgs600 and we assert the parsing outcomes.
"""
import pytest

from rbl.hardware.serial_transport import SerialTimeout
from rbl.hardware.xgs600_driver import (
    Xgs600,
    XgsChannel,
    XgsFieldError,
    XgsProtocolError,
    XgsTimeoutError,
    _parse_pressure,
)

# ---------------------------------------------------------------------------
# FakeTransport
# ---------------------------------------------------------------------------

class FakeTransport:
    """Plays back a list of (bytes_sent -> bytes_received) pairs.

    Each call to query_line() pops the next response from the queue.
    Raises SerialTimeout if the queue entry is None (simulates silence).
    """

    def __init__(self, responses: list):
        """
        responses : list of bytes | None
            bytes -> returned verbatim
            None  -> raises SerialTimeout (zero-byte timeout)
        """
        self._responses = list(responses)
        self.sent: list[bytes] = []

    def query_line(self, cmd: bytes, terminator: bytes = b"\r") -> bytes:
        self.sent.append(cmd)
        if not self._responses:
            raise SerialTimeout("FakeTransport: response queue empty")
        reply = self._responses.pop(0)
        if reply is None:
            raise SerialTimeout("FakeTransport: simulated timeout (None)")
        return reply

    # SerialTransport also exposes these; unused in these tests but present
    # so Xgs600.__init__ doesn't blow up if it checks attributes.
    def write(self, data): pass
    def read_until(self, *a, **kw): return b""
    def read_exact(self, n): return b"\x00" * n
    def reset_buffers(self): pass
    connected = True


# ---------------------------------------------------------------------------
# Helpers to build XgsChannel fixtures
# ---------------------------------------------------------------------------

def _ch(index=0, slot=0, board="HFIG", label="CH0", sensor_code="I1"):
    return XgsChannel(
        index=index, slot=slot, board=board,
        label=label, sensor_code=sensor_code,
    )


def _make_xgs(responses: list, channels: list = None) -> Xgs600:
    """Build an Xgs600 backed by a FakeTransport with pre-set channels."""
    xgs = Xgs600(FakeTransport(responses))
    if channels is not None:
        xgs._channels = channels
    return xgs


# ---------------------------------------------------------------------------
# _parse_pressure unit tests
# ---------------------------------------------------------------------------

class TestParsePressure:
    def test_ok_scientific(self):
        p, s = _parse_pressure("1.234E-06")
        assert abs(p - 1.234e-6) < 1e-15
        assert s == "OK"

    def test_ok_negative_exponent(self):
        p, s = _parse_pressure("9.999E-10")
        assert s == "OK"
        assert p == pytest.approx(9.999e-10)

    def test_off_state(self):
        p, s = _parse_pressure("OFF")
        assert p is None
        assert s == "OFF"

    def test_under_state(self):
        p, s = _parse_pressure("UNDER")
        assert p is None
        assert s == "UNDER"

    def test_over_state(self):
        p, s = _parse_pressure("OVER")
        assert p is None
        assert s == "OVER"

    def test_no_cable(self):
        p, s = _parse_pressure("NO CABLE")
        assert p is None
        assert s == "NO_CABLE"

    def test_error_code(self):
        p, s = _parse_pressure("E01")
        assert p is None
        assert s == "ERROR"

    def test_never_coerce_text_to_zero(self):
        """Ensure no text state ever becomes 0.0."""
        for text in ("OFF", "UNDER", "NO CABLE", "OVER", "E01"):
            p, _ = _parse_pressure(text)
            assert p != 0.0
            assert p is None


# ---------------------------------------------------------------------------
# Xgs600.read_all parsing
# ---------------------------------------------------------------------------

class TestReadAll:
    def test_three_board_dump(self):
        """Three channels, all numeric pressures."""
        chs = [
            _ch(0, 0, "HFIG", "IG1", "I1"),
            _ch(1, 2, "CNV",  "CG1", "T1"),
            _ch(2, 2, "CNV",  "CG2", "T2"),
        ]
        # #000F response: >1.000E-06,7.600E+02,7.601E+02<CR>
        resp = b">1.000E-06,7.600E+02,7.601E+02\r"
        xgs = _make_xgs([resp], channels=chs)

        readings = xgs.read_all()

        assert len(readings) == 3
        assert readings[0].state == "OK"
        assert readings[0].pressure == pytest.approx(1.0e-6)
        assert readings[1].pressure == pytest.approx(7.600e2)
        assert readings[2].pressure == pytest.approx(7.601e2)

    def test_dump_with_off_and_under(self):
        """Mixed: one numeric, one OFF, one UNDER — never returns 0.0."""
        chs = [
            _ch(0, 0, "HFIG", "IG1", "I1"),
            _ch(1, 2, "CNV",  "CG1", "T1"),
            _ch(2, 2, "CNV",  "CG2", "T2"),
        ]
        resp = b">1.500E-07,OFF,UNDER\r"
        xgs = _make_xgs([resp], channels=chs)

        readings = xgs.read_all()

        assert readings[0].pressure == pytest.approx(1.5e-7)
        assert readings[0].state == "OK"

        assert readings[1].pressure is None
        assert readings[1].state == "OFF"

        assert readings[2].pressure is None
        assert readings[2].state == "UNDER"

    def test_protocol_error_ff(self):
        """>?FF reply must raise XgsProtocolError."""
        chs = [_ch()]
        resp = b"?FF\r"
        xgs = _make_xgs([resp], channels=chs)

        with pytest.raises(XgsProtocolError):
            xgs.read_all()

    def test_timeout_raises_xgs_timeout(self):
        """Zero-byte response must raise XgsTimeoutError."""
        chs = [_ch()]
        xgs = _make_xgs([None], channels=chs)   # None -> SerialTimeout

        with pytest.raises(XgsTimeoutError):
            xgs.read_all()

    def test_field_count_mismatch_raises(self):
        """If field count != channel count after re-discovery, raise XgsFieldError.

        We pre-set _channels to 2 entries but return 3 fields.  The driver
        tries discover_channels() once; since FakeTransport still has the
        same number of channels, it will raise on the second check.
        """
        chs = [
            _ch(0, 0, "HFIG", "IG1", "I1"),
            _ch(1, 2, "CNV",  "CG1", "T1"),
        ]
        # First call: 3 fields (mismatch)
        # discover_channels() re-runs: needs #0001 + one #0015 per channel.
        # Supply stub responses for re-discovery that return only 2 channels.
        rediscover_responses = [
            b">104000FEFEFE\r",   # #0001: slot 0=HFIG, slot 1=CNV, rest empty
            b">IG1\r",            # #0015 I1
            b">CG1\r",            # #0015 T1
            b">CG2\r",            # #0015 T2 (second CNV sensor)
        ]
        # After re-discovery we now have 3 channels but still 3 fields: match.
        # To force a mismatch error, make re-discovery return 2 channels:
        rediscover_responses = [
            b">10FEFEFEFEFE\r",   # only slot 0=HFIG, rest empty -> 1 channel
            b">IG1\r",            # #0015 I1
        ]
        first_read = b">1.000E-06,2.000E-06,3.000E-06\r"  # 3 fields, 1 ch after rediscover
        xgs = _make_xgs([first_read] + rediscover_responses, channels=chs)

        with pytest.raises(XgsFieldError):
            xgs.read_all()


# ---------------------------------------------------------------------------
# Self-test runner (also callable by pytest)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    # Run a subset of critical assertions inline for the [OK] printout.
    # Pytest runs the full class-based suite above; this block is a quick
    # standalone smoke-test without pytest installed.

    p, s = _parse_pressure("1.000E-06")
    assert s == "OK" and abs(p - 1e-6) < 1e-15

    for text in ("OFF", "UNDER", "NO CABLE", "OVER", "E01"):
        pv, sv = _parse_pressure(text)
        assert pv is None, f"Expected None for {text!r}, got {pv!r}"

    chs = [
        _ch(0, 0, "HFIG", "IG1", "I1"),
        _ch(1, 2, "CNV",  "CG1", "T1"),
        _ch(2, 2, "CNV",  "CG2", "T2"),
    ]
    readings = _make_xgs([b">1.000E-06,OFF,UNDER\r"], channels=chs).read_all()
    assert readings[0].pressure == pytest.approx(1e-6)
    assert readings[1].pressure is None and readings[1].state == "OFF"
    assert readings[2].pressure is None and readings[2].state == "UNDER"

    print("[OK] Phase 2 XGS-600 parse")
    sys.exit(0)
