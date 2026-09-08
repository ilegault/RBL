"""
tests/test_vgc083_parse.py
Offline parser tests for the VGC083 driver.

No serial port, no hardware.  A FakeTransport feeds canned responses.

CRITICAL TEST: the sentinel 1.10E+03 must NEVER produce a float.
Every test that exercises the sentinel asserts this explicitly.
"""
import pytest

from rbl.hardware.serial_transport import SerialTimeout
from rbl.hardware.vgc083_driver import (
    _SENTINEL,
    Vgc083,
    VgcProtocolError,
    VgcTimeoutError,
    _parse_reading,
)

# ---------------------------------------------------------------------------
# FakeTransport
# ---------------------------------------------------------------------------

class FakeTransport:
    """Same contract as the XGS-600 tests — plays back a response queue."""

    def __init__(self, responses: list):
        self._responses = list(responses)
        self.sent: list[bytes] = []

    def query_line(self, cmd: bytes, terminator: bytes = b"\r") -> bytes:
        self.sent.append(cmd)
        if not self._responses:
            raise SerialTimeout("FakeTransport: queue empty")
        reply = self._responses.pop(0)
        if reply is None:
            raise SerialTimeout("FakeTransport: simulated timeout")
        return reply

    def write(self, data): pass
    def read_until(self, *a, **kw): return b""
    def read_exact(self, n): return b"\x00" * n
    def reset_buffers(self): pass
    connected = True


def _make_vgc(responses: list) -> Vgc083:
    return Vgc083(FakeTransport(responses))


def _make_response(data: str) -> bytes:
    """Build a correctly-framed 13-char VGC083 response: *{2sp}{sp}{data}<CR>"""
    # Format: * + two-space address + one space + data, padded to 12 chars + CR
    body = f"*   {data}"
    # Pad or truncate to exactly 13 chars (including the CR)
    body = body[:12].ljust(12)
    return (body + "\r").encode("ascii")


# ---------------------------------------------------------------------------
# _parse_reading unit tests — sentinel is the most important
# ---------------------------------------------------------------------------

class TestParseReading:
    def test_normal_ig_reading(self):
        r = _parse_reading("IG", "1.53E-06")
        assert r.state == "OK"
        assert r.pressure == pytest.approx(1.53e-6)
        assert r.pressure is not None

    def test_normal_cg_reading(self):
        r = _parse_reading("CG1", "7.60E+02")
        assert r.state == "OK"
        assert r.pressure == pytest.approx(760.0)

    # ---- SENTINEL TESTS: every single one asserts pressure is None --------

    def test_sentinel_ig_never_float(self):
        """THE most important test: sentinel on IG -> None, not 1100."""
        r = _parse_reading("IG", _SENTINEL)
        assert r.pressure is None, (
            f"SENTINEL LEAKED: IG channel returned float {r.pressure!r} "
            f"instead of None.  This would corrupt the pressure log."
        )
        assert r.state == "OFF_OR_OVERRANGE"

    def test_sentinel_cg1_never_float(self):
        r = _parse_reading("CG1", _SENTINEL)
        assert r.pressure is None
        assert r.state == "OFF_OR_OVERRANGE"

    def test_sentinel_cg2_never_float(self):
        r = _parse_reading("CG2", _SENTINEL)
        assert r.pressure is None
        assert r.state == "OFF_OR_OVERRANGE"

    def test_sentinel_ai_never_float(self):
        r = _parse_reading("AI", _SENTINEL)
        assert r.pressure is None
        assert r.state == "OFF_OR_OVERRANGE"

    def test_sentinel_raw_preserved(self):
        """raw field must carry the exact sentinel string for the log guard."""
        r = _parse_reading("IG", _SENTINEL)
        assert r.raw == _SENTINEL

    # ---- Error and unknown states ----------------------------------------

    def test_unknown_text(self):
        r = _parse_reading("IG", "XYZZY")
        assert r.pressure is None
        assert r.state == "ERROR"


# ---------------------------------------------------------------------------
# Vgc083 driver-level tests via FakeTransport
# ---------------------------------------------------------------------------

class TestVgc083Driver:
    def test_normal_reading(self):
        """Normal IG reading round-trip through the driver."""
        resp = _make_response("1.53E-06")
        vgc = _make_vgc([resp])
        reading = vgc.read_channel("IG")
        assert reading.state == "OK"
        assert reading.pressure == pytest.approx(1.53e-6)

    def test_sentinel_via_driver(self):
        """Sentinel coming through a live driver call must still be None."""
        resp = _make_response(_SENTINEL)
        vgc = _make_vgc([resp])
        reading = vgc.read_channel("IG")
        assert reading.pressure is None
        assert reading.state == "OFF_OR_OVERRANGE"

    def test_invalid_reply(self):
        """'?' prefix -> VgcProtocolError."""
        resp = b"?  _INVALID_\r"
        vgc = _make_vgc([resp])
        with pytest.raises(VgcProtocolError):
            vgc.read_channel("IG")

    def test_timeout_raises(self):
        """None in queue -> VgcTimeoutError."""
        vgc = _make_vgc([None])
        with pytest.raises(VgcTimeoutError):
            vgc.read_channel("IG")

    def test_wrong_length_response_logs_warning(self, caplog):
        """Short response emits a warning mentioning RS-485."""
        import logging
        short_resp = b"*  1.53\r"   # only 8 chars, not 13
        vgc = _make_vgc([short_resp])
        with caplog.at_level(logging.WARNING, logger="rbl.hardware.vgc083_driver"):
            try:
                vgc.read_channel("IG")
            except Exception:
                pass
        assert any("RS-485" in r.message or "RS485" in r.message
                   or "RS-232" in r.message
                   for r in caplog.records), \
            "Short-response warning must mention RS-485 / RS-232 mode"

    def test_ig_status_on(self):
        resp = _make_response("1 IG ON ")
        vgc = _make_vgc([resp])
        on, raw = vgc.ig_status()
        assert on is True

    def test_ig_status_off(self):
        resp = _make_response("0 IG OFF")
        vgc = _make_vgc([resp])
        on, raw = vgc.ig_status()
        assert on is False

    def test_ig_fault_ok(self):
        resp = _make_response("00 ST_OK")
        vgc = _make_vgc([resp])
        fault = vgc.ig_fault()
        assert fault == "ST_OK"

    def test_ig_fault_codes(self):
        """Each documented fault code maps to the right name."""
        fault_cases = [
            ("01", "OVPRS"),
            ("02", "EMISS"),
            ("04", "FLVLO"),
            ("08", "FLOPN"),
            ("10", "DEGAS"),
            ("20", "ICLOW"),
            ("40", "FLIHI"),
            ("80", "OVTMP"),
        ]
        for hex_code, expected_name in fault_cases:
            resp = _make_response(f"{hex_code} ...")
            vgc = _make_vgc([resp])
            fault = vgc.ig_fault()
            assert expected_name in fault, \
                f"Fault code {hex_code} should map to {expected_name}, got {fault!r}"

    def test_degas_on(self):
        resp = _make_response("1 DG ON ")
        vgc = _make_vgc([resp])
        assert vgc.degas_status() is True

    def test_degas_off(self):
        resp = _make_response("0 DG OFF")
        vgc = _make_vgc([resp])
        assert vgc.degas_status() is False


# ---------------------------------------------------------------------------
# Sentinel invariant: exhaustive check across all channel types
# ---------------------------------------------------------------------------

class TestSentinelNeverFloat:
    """The sentinel 1.10E+03 must NEVER produce a float on any channel."""

    @pytest.mark.parametrize("channel", ["IG", "CG1", "CG2", "AI"])
    def test_sentinel_all_channels(self, channel):
        r = _parse_reading(channel, _SENTINEL)
        assert r.pressure is None, (
            f"SENTINEL LEAKED on channel {channel}: "
            f"got float {r.pressure!r} — this must never happen"
        )
        assert r.state == "OFF_OR_OVERRANGE"
        # Also confirm the raw field preserves the original string
        assert r.raw == _SENTINEL


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    # Sentinel — the single most critical assertion
    for ch in ("IG", "CG1", "CG2", "AI"):
        r = _parse_reading(ch, _SENTINEL)
        assert r.pressure is None, \
            f"SENTINEL LEAKED on {ch}: got {r.pressure!r}"
        assert r.state == "OFF_OR_OVERRANGE"

    # Normal reading
    r = _parse_reading("IG", "1.53E-06")
    assert r.state == "OK"
    assert r.pressure == pytest.approx(1.53e-6)

    print("[OK] Phase 3 VGC083 parse")
    sys.exit(0)
