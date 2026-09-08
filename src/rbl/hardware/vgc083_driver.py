"""
vgc083_driver.py
INFICON VGC083 vacuum gauge controller — RS-232 serial protocol driver.

No Qt imports.  No file I/O.  Pure protocol translation.

PROTOCOL REFERENCE (VGC083A/B Operating Manual, tinb29e1-e 2026-01, §9)
------------------------------------------------------------------------
Frame    : 19200 baud, 8 data bits, no parity, 1 stop bit.
           (Manual 9.2 note 1 and 6.5.1 factory defaults.)
           The front panel can set baud to any of 300 / 600 / 1200 / 2400 /
           4800 / 9600 / 19200 / 38400, data bits to 7 or 8, parity to
           None / Even / Odd, and stop bits to 1 or 2 — independently.  Any
           one of those being off 8-N-1 breaks comms, so tools/vgc083_sweep.py
           sweeps all of them.
           NO hardware handshake — open with rtscts=False.  Manual 9.3
           note 3, verbatim: "Hardware handshake controls do not exist on
           VGC083 (e.g., RTS, CTS, DTR)."  The RS232 socket leaves pins 4,
           6, 7 and 8 unconnected.
Identity : #xxVER<CR> -> *xx_mmmmm-vv<CR>, e.g. *01_01306-11<CR>
           (Manual 9.3, READ SW VERSION.)  identify() below relies on this.
Max rate : 38400->38 ms, 19200->46 ms, 9600->61 ms, 4800->93 ms,
           2400->156 ms, 1200->280 ms (manual 9.1 repetition-rate table).
           _MIN_GAP_S below is the 19200 figure plus margin.

WIRING — THE TWO MISTAKES THAT PRODUCE PERFECT SILENCE
-------------------------------------------------------
1. COMM TYPE.  Manual 6.5.1: "COMM TYPE [Factory default = RS485]".
   An unconfigured unit does not talk RS232 at all.  Set it on the front
   panel: MENU -> SETUP UNIT -> SERIAL COMM -> COMM TYPE -> RS232.
2. CONNECTOR.  There are two independent DE9 connectors (manual 4.2.10):
       RS232 -> DE9S (female socket on the instrument)
       RS485 -> DE9P (male pins on the instrument)
   Never connect both at once; the unit serves one or the other.

CABLE: straight-through, pin-to-pin — NOT null-modem.  The RS232 socket is
DCE-wired (manual 4.2.10 table):
       pin 2 = Transmitted Data (OUT, from the VGC083)
       pin 3 = Received Data (IN, to the VGC083)
       pin 5 = Signal Ground
       pins 1, 4, 6, 7, 8, 9 = no connection
Only those three conductors carry anything.
Command  : #{xx}{CMD}<CR>
           In RS232 mode the two address characters are two spaces: "#  CMD<CR>"
Response : *{xx}_{data}<CR>
           Every response is EXACTLY 13 characters including the CR.
           In RS232 mode the address field comes back as two spaces.
           Normal responses start with '*'; error responses start with '?'.
           '_' in this notation means a literal space character.
Error    : ?xx_INVALID_ or ?xx_SYNTX_ER
Timing   : Maximum command repetition rate is 46 ms at 19200 baud.
           This driver enforces a 60 ms minimum gap (conservative margin).

SENTINEL VALUE — THIS IS THE MOST IMPORTANT PARSING RULE
---------------------------------------------------------
The VGC083 returns "1.10E+03" when:
  - the ion gauge is off or faulted
  - a convection gauge is over-ranged
  - the analog input is unpowered or out of range

1.10E+03 IS NOT A PRESSURE.  It must be parsed to pressure=None,
state="OFF_OR_OVERRANGE".  It must NEVER be written to the pressure log
as a number.  A log full of 1100 Torr readings is the specific failure
mode this guard prevents.

RSIG FAULT CODES (bitfield returned by #  RSIG)
-----------------------------------------------
00=ST_OK  01=OVPRS  02=EMISS  04=FLVLO  08=FLOPN
10=DEGAS  20=ICLOW  40=FLIHI  80=OVTMP
"""
import logging
import time
from dataclasses import dataclass
from typing import Optional

from rbl.hardware.serial_transport import SerialTimeout, SerialTransport

log = logging.getLogger(__name__)

# Minimum gap between commands (manual: 46 ms at 19200 baud; we use 60 ms).
_MIN_GAP_S = 0.060

# Response length validation (13 chars including CR, per manual).
_EXPECTED_RESPONSE_LEN = 13

# THE sentinel value.  See module docstring.
_SENTINEL = "1.10E+03"
_SENTINEL_FLOAT = 1100.0   # approx float equivalent — used for detection

# RSIG fault code -> human-readable name
_RSIG_CODES = {
    0x00: "ST_OK",
    0x01: "OVPRS",
    0x02: "EMISS",
    0x04: "FLVLO",
    0x08: "FLOPN",
    0x10: "DEGAS",
    0x20: "ICLOW",
    0x40: "FLIHI",
    0x80: "OVTMP",
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class VgcProtocolError(RuntimeError):
    """Instrument returned a '?'-prefixed error response."""


class VgcTimeoutError(OSError):
    """Instrument returned nothing — likely a disconnect or RS485 mode."""


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class VgcReading:
    channel:  str            # "IG" | "CG1" | "CG2" | "AI"
    pressure: Optional[float]  # None when off, over-range, or error
    raw:      str            # exact 13-char response from the instrument
    state:    str            # "OK" | "OFF_OR_OVERRANGE" | "ERROR"


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

class Vgc083:
    """INFICON VGC083 vacuum gauge controller.

    Parameters
    ----------
    transport : SerialTransport (must already be open, rtscts=False)
    address   : RS-232 address field — two spaces in RS-232 mode (default)
    """

    def __init__(self, transport: SerialTransport, address: str = "  "):
        self._t        = transport
        self._addr     = address          # two spaces in RS-232 mode
        self._last_cmd = 0.0             # monotonic time of last command

    # ---- Rate limiter -----------------------------------------------------

    def _throttle(self):
        """Enforce minimum 60 ms inter-command gap (manual: 46 ms at 19200)."""
        elapsed = time.monotonic() - self._last_cmd
        if elapsed < _MIN_GAP_S:
            time.sleep(_MIN_GAP_S - elapsed)
        self._last_cmd = time.monotonic()

    # ---- Raw command / response ------------------------------------------

    def _cmd(self, cmd_body: str) -> str:
        """Send #{addr}{cmd_body}<CR>, validate and return the response body.

        Validates:
        - Response is exactly 13 chars including CR (RS-232 mode).
          A short read almost always means the instrument is still in RS-485
          mode — the error message says so explicitly.
        - Normal ('*') vs error ('?') prefix.

        Returns the data portion of a normal response (stripped of prefix,
        address, and CR).

        Raises:
            VgcTimeoutError    — zero bytes received
            VgcProtocolError   — instrument returned a '?' error response
        """
        self._throttle()
        raw_cmd = f"#{self._addr}{cmd_body}\r".encode("ascii")
        log.debug("vgc083 CMD: %r", raw_cmd)

        try:
            reply = self._t.query_line(raw_cmd, terminator=b"\r")
        except SerialTimeout as exc:
            raise VgcTimeoutError(
                f"VGC083: no response to {cmd_body!r}: {exc}"
            ) from exc
        except Exception as exc:
            raise VgcTimeoutError(
                f"VGC083: comms error on {cmd_body!r}: {exc}"
            ) from exc

        text = reply.decode("ascii", errors="replace")
        log.debug("vgc083 REPLY: %r -> %r", raw_cmd, text)

        # Length validation.
        if len(text) != _EXPECTED_RESPONSE_LEN:
            log.warning(
                "vgc083: response length %d != %d for %r (reply=%r) — "
                "instrument may still be in RS-485 mode.  "
                "Front panel: SERIAL COMM -> COMM TYPE -> RS232",
                len(text), _EXPECTED_RESPONSE_LEN, cmd_body, text,
            )

        if not text:
            raise VgcTimeoutError(
                f"VGC083: empty response to {cmd_body!r}"
            )

        prefix = text[0] if text else ""

        if prefix == "?":
            raise VgcProtocolError(
                f"VGC083: error response to {cmd_body!r}: {text.strip()!r}"
            )

        if prefix != "*":
            raise VgcProtocolError(
                f"VGC083: unexpected response prefix {prefix!r} to "
                f"{cmd_body!r}: {text.strip()!r}"
            )

        # Response format: *{2-char addr}{space}{data}<CR>
        # Strip prefix (1) + address (2) + space (1) = first 4 chars, then CR.
        body = text[4:].rstrip("\r\n").strip()
        return body

    # ---- Identity --------------------------------------------------------

    def identify(self) -> str:
        """Send #  VER, return firmware version string."""
        return self._cmd("VER")

    # ---- Pressure reads --------------------------------------------------

    def read_channel(self, ch: str) -> VgcReading:
        """Read one channel by name.

        ch : "IG" | "CG1" | "CG2" | "AI"

        Maps channel name to the correct command and calls _cmd().
        """
        cmd_map = {
            "IG":  "RDIG",
            "CG1": "RDCG1",
            "CG2": "RDCG2",
            "AI":  "RDAI",
        }
        if ch not in cmd_map:
            raise ValueError(
                f"VGC083: unknown channel {ch!r}. "
                f"Valid channels: {list(cmd_map.keys())}"
            )
        body = self._cmd(cmd_map[ch])
        return _parse_reading(ch, body)

    def read_all(self, channels: "list[str]") -> "list[VgcReading]":
        """Read each channel in *channels* in order, with enforced gaps.

        Gaps are managed by the throttle inside _cmd().
        """
        return [self.read_channel(ch) for ch in channels]

    # ---- Status reads ----------------------------------------------------

    def ig_status(self) -> "tuple[bool, str]":
        """Return (ig_on: bool, raw_body: str) from #  IGS."""
        body = self._cmd("IGS")
        on = body.startswith("1")
        return on, body

    def ig_fault(self) -> str:
        """Return a human-readable fault string from #  RSIG.

        Returns a comma-separated list of active fault names, or "ST_OK".
        """
        body = self._cmd("RSIG")
        # Response body is two hex chars, e.g. "00" = OK, "08" = FLOPN
        try:
            code = int(body[:2], 16)
        except ValueError:
            return f"PARSE_ERROR({body!r})"
        faults = [name for mask, name in _RSIG_CODES.items()
                  if mask != 0 and (code & mask)]
        return ",".join(faults) if faults else "ST_OK"

    def degas_status(self) -> bool:
        """Return True if degas is active (#  DGS)."""
        body = self._cmd("DGS")
        return body.startswith("1")


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _parse_reading(channel: str, body: str) -> VgcReading:
    """Parse one VGC083 pressure response body into a VgcReading.

    The body is the data portion after stripping prefix, address, and CR —
    typically looks like "1.53E-06" or "1.10E+03".

    THE SENTINEL RULE: "1.10E+03" (and its float equivalent ~1100) maps to
    pressure=None, state="OFF_OR_OVERRANGE".  It must NEVER produce a float.
    """
    raw = body.strip()

    # --- Sentinel check FIRST, before any float conversion ----------------
    # Compare both the exact string and a float-converted value for safety.
    if raw == _SENTINEL:
        # Exact match — the canonical sentinel.
        return VgcReading(channel=channel, pressure=None,
                          raw=raw, state="OFF_OR_OVERRANGE")

    # --- Attempt numeric parse --------------------------------------------
    try:
        value = float(raw)
        # Paranoid float check: if the value is suspiciously close to the
        # sentinel (1100 Torr), treat it as OFF_OR_OVERRANGE.  This catches
        # rounding variants that would otherwise slip through.
        if abs(value - _SENTINEL_FLOAT) < 1.0:
            log.warning(
                "vgc083: channel %s returned value %.4g which is "
                "indistinguishable from the sentinel (1.10E+03) — "
                "treating as OFF_OR_OVERRANGE",
                channel, value,
            )
            return VgcReading(channel=channel, pressure=None,
                              raw=raw, state="OFF_OR_OVERRANGE")
        return VgcReading(channel=channel, pressure=value,
                          raw=raw, state="OK")
    except ValueError:
        pass

    # --- Error / unknown text ---------------------------------------------
    return VgcReading(channel=channel, pressure=None,
                      raw=raw, state="ERROR")


# ---------------------------------------------------------------------------
# CLI self-test  (python -m rbl.hardware.vgc083_driver --port COMn)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="VGC083 hardware self-test (requires instrument connected)."
    )
    parser.add_argument("--port", required=True,
                        help="COM port (e.g. COM5)")
    parser.add_argument("--baud", type=int, default=19200)
    parser.add_argument(
        "--channels", default="IG,CG1,CG2",
        help="Comma-separated list of channels to read (default: IG,CG1,CG2)"
    )
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    transport = SerialTransport(args.port, args.baud, rtscts=False, timeout=2.0)
    transport.open()
    try:
        vgc = Vgc083(transport)

        print("\n--- Identity ---")
        print(f"  Firmware: {vgc.identify()}")

        channels = [ch.strip() for ch in args.channels.split(",")]
        print(f"\n--- Channels: {channels} ---")

        print("\n--- IG status ---")
        on, raw = vgc.ig_status()
        print(f"  IG on={on}  raw={raw!r}")

        print("\n--- IG fault (RSIG) ---")
        print(f"  Fault: {vgc.ig_fault()}")

        print("\n--- Degas status ---")
        print(f"  Degas active: {vgc.degas_status()}")

        print("\n--- Channel readings ---")
        t0 = time.monotonic()
        readings = vgc.read_all(channels)
        elapsed = time.monotonic() - t0
        for r in readings:
            if r.pressure is not None:
                print(f"  {r.channel:5s}: {r.pressure:.3e}  ({r.state})")
            else:
                print(f"  {r.channel:5s}: {r.state}  raw={r.raw!r}")
        print(f"  (total read time: {elapsed*1000:.0f} ms, "
              f"inter-command gap enforced: {_MIN_GAP_S*1000:.0f} ms)")

    finally:
        transport.close()

    print("\n[OK] Phase 3 VGC083 hardware")
