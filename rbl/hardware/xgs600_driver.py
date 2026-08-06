"""
xgs600_driver.py
Agilent XGS-600 multi-gauge controller — ASCII serial protocol driver.

No Qt imports.  No file I/O.  Pure protocol translation.

PROTOCOL REFERENCE (from the XGS-600 manual)
---------------------------------------------
Frame  : 8 data bits, no parity, 1 stop bit.  No flow control.
Command: #{aa}{cmd}{optional data}<CR>
         aa = address, literally "00" in RS-232 mode.
Response: >{data}<CR>
Error   : ?FF  (invalid command or bad length)
Silence : wrong address or missing terminator -> no response at all.
          Therefore the driver is timeout-driven, not wait-until-reply.
Timing : latency ~10 ms; DO NOT query faster than 10 per second.
         This driver enforces that limit at the Xgs600 level.

SLOT CODES (from #0001 response, two hex chars each)
-----------------------------------------------------
10 = hot filament ion gauge (HFIG)
3A = inverted magnetron (IMG)
40 = convection gauge board (CNV) — carries TWO sensors
4C = analog input board (AUX)
FE = empty slot

CHANNEL INDEXING
----------------
#000F returns all channels in board-installation (left-to-right) order.
One convection board contributes two entries; all others contribute one.
The mapping from list position to physical gauge is derived from #0001
and #0015 once at connect time, then cached in self._channels.
"""
import logging
import re
import time
from dataclasses import dataclass
from typing import Optional

from rbl.hardware.serial_transport import SerialTransport

log = logging.getLogger(__name__)

# Minimum seconds between successive queries (manual: < 10 queries/sec).
_MIN_QUERY_INTERVAL_S = 0.12   # ~8 Hz ceiling; poll target is 1 Hz

# Slot code -> board type string
_SLOT_CODES = {
    "10": "HFIG",
    "3A": "IMG",
    "40": "CNV",
    "4C": "AUX",
    "FE": "EMPTY",
}

# Text states the XGS-600 returns instead of a numeric pressure
_STATE_MAP = {
    "OFF":     "OFF",
    "UNDER":   "UNDER",
    "NO CABLE":"NO_CABLE",
    "NO_CABLE":"NO_CABLE",  # some firmware variants
    "OVER":    "OVER",
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class XgsProtocolError(RuntimeError):
    """The instrument returned ?FF — command rejected."""


class XgsTimeoutError(OSError):
    """The instrument returned nothing — likely a disconnect."""


class XgsFieldError(ValueError):
    """#000F field count does not match discovered channel count."""


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class XgsChannel:
    index:       int    # position in the #000F dump (0-based)
    slot:        int    # 0-5, physical board slot (left-to-right)
    board:       str    # "HFIG" | "IMG" | "CNV" | "AUX"
    label:       str    # user label from #0015, or generated sensor ID
    sensor_code: str    # "I1", "T1", "T2" ... for single-channel queries


@dataclass(frozen=True)
class XgsReading:
    channel:  XgsChannel
    pressure: Optional[float]   # None when the channel reports a non-numeric state
    raw:      str               # exact text the instrument returned for this field
    state:    str               # "OK" | "OFF" | "UNDER" | "OVER" | "NO_CABLE" |
                                # "ERROR" | "UNKNOWN"


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

class Xgs600:
    """Agilent XGS-600 multi-gauge controller.

    Parameters
    ----------
    transport : SerialTransport (must already be open)
    address   : RS-232 address, literally "00" (factory default)
    """

    def __init__(self, transport: SerialTransport, address: str = "00"):
        self._t     = transport
        self._addr  = address
        self._channels: list[XgsChannel] = []
        self._last_query_time: float = 0.0

    # ---- Rate limiter -----------------------------------------------------

    def _throttle(self):
        """Enforce minimum inter-query interval (manual: <10 queries/sec)."""
        elapsed = time.monotonic() - self._last_query_time
        if elapsed < _MIN_QUERY_INTERVAL_S:
            time.sleep(_MIN_QUERY_INTERVAL_S - elapsed)
        self._last_query_time = time.monotonic()

    # ---- Raw command / response ------------------------------------------

    def _cmd(self, cmd_body: str) -> str:
        """Send #{addr}{cmd_body}<CR>, return stripped response text.

        Raises:
            XgsTimeoutError   — zero bytes received (disconnect)
            XgsProtocolError  — instrument replied with ?FF
        """
        self._throttle()
        raw_cmd = f"#{self._addr}{cmd_body}\r".encode("ascii")
        log.debug("xgs600 CMD: %r", raw_cmd)
        try:
            reply = self._t.query_line(raw_cmd, terminator=b"\r")
        except Exception as exc:
            # SerialTimeout or comms failure
            raise XgsTimeoutError(
                f"XGS-600: no response to #{self._addr}{cmd_body!r}: {exc}"
            ) from exc

        text = reply.decode("ascii", errors="replace").strip()
        log.debug("xgs600 REPLY: %r -> %r", raw_cmd, text)

        if not text:
            raise XgsTimeoutError(
                f"XGS-600: empty response to {cmd_body!r} "
                "(check address, terminator, cable)"
            )
        if "?FF" in text:
            raise XgsProtocolError(
                f"XGS-600: ?FF error in response to {cmd_body!r} — "
                "invalid command or wrong argument length"
            )
        # Strip the leading '>' that every normal response carries.
        if text.startswith(">"):
            text = text[1:]
        return text

    # ---- Identity and configuration --------------------------------------

    def identify(self) -> str:
        """Send #0005, return raw firmware version string."""
        return self._cmd("05")

    def read_units(self) -> str:
        """Send #0013, return 'Torr', 'mbar', or 'Pa'."""
        code = self._cmd("13").strip()
        return {"00": "Torr", "01": "mbar", "02": "Pa"}.get(code, f"unknown({code})")

    # ---- Channel discovery -----------------------------------------------

    def discover_channels(self) -> "list[XgsChannel]":
        """Query slot occupancy (#0001) and user labels (#0015).

        Builds and caches self._channels.  Must be called once after open
        before read_all() or read_one() will work.  Returns the channel list.
        """
        # --- Slot codes ---
        slot_text = self._cmd("01")      # e.g. "104040FEFEFE"
        if len(slot_text) != 12:
            log.warning(
                "xgs600: #0001 returned %d chars (expected 12): %r",
                len(slot_text), slot_text
            )
        slots = [slot_text[i:i+2].upper() for i in range(0, min(12, len(slot_text)), 2)]
        log.debug("xgs600: slot codes = %s", slots)

        channels: list[XgsChannel] = []
        ion_index  = 0   # for sensor codes I1, I2, ...
        conv_index = 0   # for sensor codes T1, T2, ...

        for slot_num, code in enumerate(slots):
            board = _SLOT_CODES.get(code, "UNKNOWN")
            if board == "EMPTY":
                continue

            if board in ("HFIG", "IMG"):
                ion_index += 1
                sensor_code = f"I{ion_index}"
                label = self._get_label(sensor_code)
                channels.append(XgsChannel(
                    index       = len(channels),
                    slot        = slot_num,
                    board       = board,
                    label       = label or sensor_code,
                    sensor_code = sensor_code,
                ))

            elif board == "CNV":
                # Convection board carries two sensors.
                for sub in range(2):
                    conv_index += 1
                    sensor_code = f"T{conv_index}"
                    label = self._get_label(sensor_code)
                    channels.append(XgsChannel(
                        index       = len(channels),
                        slot        = slot_num,
                        board       = board,
                        label       = label or sensor_code,
                        sensor_code = sensor_code,
                    ))

            elif board == "AUX":
                conv_index += 1
                sensor_code = f"T{conv_index}"
                label = self._get_label(sensor_code)
                channels.append(XgsChannel(
                    index       = len(channels),
                    slot        = slot_num,
                    board       = board,
                    label       = label or sensor_code,
                    sensor_code = sensor_code,
                ))

        self._channels = channels
        log.info("xgs600: discovered %d channel(s): %s",
                 len(channels), [ch.label for ch in channels])
        return channels

    def _get_label(self, sensor_code: str) -> str:
        """Read user label for *sensor_code* via #0015. Returns '' on error."""
        try:
            return self._cmd(f"15{sensor_code}").strip()
        except Exception as exc:
            log.debug("xgs600: #0015%s failed: %s", sensor_code, exc)
            return ""

    # ---- Pressure reads --------------------------------------------------

    def read_all(self) -> "list[XgsReading]":
        """Send #000F — one round trip for all channels.

        Returns one XgsReading per discovered channel, in board order.

        Raises XgsFieldError if the number of returned fields does not match
        the number of channels discovered at connect time.
        """
        if not self._channels:
            raise RuntimeError(
                "xgs600.read_all(): call discover_channels() first"
            )
        text = self._cmd("0F")
        fields = [f.strip() for f in text.split(",")]

        if len(fields) != len(self._channels):
            # Try a re-discovery once before raising.
            log.warning(
                "xgs600: #000F returned %d fields but expected %d — "
                "re-running discover_channels()",
                len(fields), len(self._channels),
            )
            self.discover_channels()
            if len(fields) != len(self._channels):
                raise XgsFieldError(
                    f"XGS-600: #000F returned {len(fields)} fields "
                    f"but {len(self._channels)} channels discovered.  "
                    "A board may have been added or removed."
                )

        return [_parse_reading(ch, raw)
                for ch, raw in zip(self._channels, fields)]

    def read_one(self, ch: XgsChannel) -> "XgsReading":
        """Send #0002{sensor_code} — single channel query."""
        text = self._cmd(f"02{ch.sensor_code}").strip()
        return _parse_reading(ch, text)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _parse_pressure(raw: str) -> "tuple[Optional[float], str]":
    """Parse one pressure field from a #000F or #0002 response.

    Returns (pressure_float, state_string).
    pressure_float is None for any non-numeric state.
    state_string is one of: "OK", "OFF", "UNDER", "OVER", "NO_CABLE", "ERROR".

    Never coerces a text state to 0.0.
    """
    raw = raw.strip()

    # Check for known text states (case-insensitive)
    upper = raw.upper().replace(" ", "_")
    for key, state in _STATE_MAP.items():
        if key.upper().replace(" ", "_") == upper:
            return None, state

    # Check for E-prefixed error codes (e.g. "E01", "E02")
    if re.match(r'^E\d+$', raw, re.IGNORECASE):
        return None, "ERROR"

    # Attempt numeric parse: x.xxxE±xx
    try:
        value = float(raw)
        return value, "OK"
    except ValueError:
        pass

    # Unrecognised text state
    log.debug("xgs600: unrecognised field value %r — treating as ERROR", raw)
    return None, "UNKNOWN"


def _parse_reading(ch: XgsChannel, raw: str) -> XgsReading:
    pressure, state = _parse_pressure(raw)
    return XgsReading(channel=ch, pressure=pressure, raw=raw, state=state)


# ---------------------------------------------------------------------------
# CLI self-test  (python -m rbl.hardware.xgs600_driver --port COMn)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="XGS-600 hardware self-test (requires instrument connected)."
    )
    parser.add_argument("--port", required=True,
                        help="COM port the XGS-600 is connected to (e.g. COM4)")
    parser.add_argument("--baud", type=int, default=9600,
                        help="Baud rate (default 9600; try 19200 if unresponsive)")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    from rbl.hardware.serial_transport import SerialTransport

    transport = SerialTransport(args.port, args.baud, timeout=2.0)
    transport.open()
    try:
        xgs = Xgs600(transport)

        print("\n--- Identity ---")
        ident = xgs.identify()
        print(f"  Firmware: {ident}")

        print("\n--- Channels ---")
        channels = xgs.discover_channels()
        for ch in channels:
            print(f"  [{ch.index}] slot={ch.slot} board={ch.board} "
                  f"code={ch.sensor_code} label={ch.label!r}")

        print("\n--- Units ---")
        units = xgs.read_units()
        print(f"  Pressure units: {units}")

        print("\n--- 10 consecutive read_all() polls ---")
        for i in range(10):
            t0 = time.monotonic()
            readings = xgs.read_all()
            elapsed = time.monotonic() - t0
            parts = []
            for r in readings:
                if r.pressure is not None:
                    parts.append(f"{r.channel.label}={r.pressure:.3e}")
                else:
                    parts.append(f"{r.channel.label}={r.state}")
            print(f"  [{i+1:2d}] {elapsed*1000:.0f} ms  {', '.join(parts)}")

    finally:
        transport.close()

    print("\n[OK] Phase 2 XGS-600 hardware")
