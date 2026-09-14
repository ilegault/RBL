"""
keithley6482_driver.py
PyVISA driver for the Keithley 6482 dual-channel picoammeter over GPIB.

WHY THIS EXISTS
---------------
The Right Beam Line uses a Faraday cup downstream of the four slit jaws to measure
transmitted beam current landing on the sample position. The Keithley 6482 dual-channel
picoammeter measures this current with high dynamic range (fA to mA).

SAFETY REQUIREMENT: NEVER SEND THE CONFIGURE COMMAND
---------------------------------------------------
The Keithley 6482 includes two independent +/-30 V bias voltage sources. The SCPI
configure command (':CONFigure' or 'CONF') resets the instrument into a default state
that turns BOTH voltage source outputs ON. In this beamline, the picoammeter is wired
directly to the Faraday cup collector, so an active voltage source would drive potential
directly onto the cup.

Therefore:
1. The configure command is NEVER sent anywhere in this driver.
2. The measurement function (':SENSe1:FUNCtion'), range, filters, and formats are
   configured explicitly with individual SCPI commands.
3. Both voltage source outputs are explicitly turned OFF (':SOURce1:STATe OFF',
   ':SOURce2:STATe OFF') and asserted OFF upon connection.

MEASUREMENT & PARSING ARCHITECTURE
----------------------------------
- Autoranging is enabled on channel 1 (':SENSe1:CURRent:DC:RANGe:AUTO ON').
- Median filter and averaging filter are disabled (':SENSe1:MEDian:STATe OFF',
  ':SENSe1:AVERage:STATe OFF'). The Keithley manual explicitly notes that the median
  filter significantly slows down autoranging.
- Integration time is set to 1 power-line cycle (NPLC = 1 via
  ':SENSe1:CURRent:DC:NPLCycles 1').
- Data elements are configured to ':FORMat:ELEMents READing,TIME,STATus'.
- Reading responses are parsed by a pure, standalone function 'parse_reading' outside
  any polling thread or transport.
- The instrument status word (specifically bit 6, value 0x40 / 64) is the authority on
  over-range condition. Over-range is NEVER inferred from reading magnitude.
- Out-of-band sentinels (+/-9.91e37 for overflow/over-range, +/-9.90e37 for unavailable)
  are recognised and flagged rather than recorded as physical currents.
- Channel 2 is declared as absent (None) in Keithley6482Reading to prepare for future
  dual-cup support without redesign.
- Protocol mode is queried at connect via ':SYSTem:MEP:STATe?' (1 = SCPI/MEP enabled,
  0 = 488.1). Under 488.1, compound queries fail on the GPIB bus.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

try:
    import pyvisa
    PYVISA_AVAILABLE = True
except ImportError:
    PYVISA_AVAILABLE = False


OVER_RANGE_STATUS_BIT = 1 << 6  # Bit 6 (0x40 / 64) in Keithley status word
OVER_RANGE_SENTINEL_MAGNITUDE = 9.91e37
UNAVAILABLE_SENTINEL_MAGNITUDE = 9.90e37
SENTINEL_TOLERANCE = 1.0e35


@dataclass(frozen=True)
class Keithley6482Reading:
    """Parsed reading from a Keithley 6482 picoammeter query.

    Attributes:
        current: Measured current in Amperes on Channel 1, or None if over-range/unavailable.
        timestamp: Instrument relative timestamp in seconds from start of timer.
        status_word: Raw integer status word reported by the instrument.
        over_range: True if status word bit 6 is set OR the reading is the over-range sentinel.
        unavailable: True if the reading is the unavailable sentinel (e.g. zero-check).
        valid: True if reading was successfully parsed and is not unavailable.
        channel_2: Absent on single-cup configuration (always None).
        raw: The raw response string from the instrument.
        protocol_mode: 1 for SCPI (MEP enabled), 0 for 488.1, or None if unqueried.
    """
    current: float | None
    timestamp: float
    status_word: int
    over_range: bool
    unavailable: bool = False
    valid: bool = True
    channel_2: None = None
    raw: str = ""
    protocol_mode: int | None = None


def is_over_range_sentinel(val: float) -> bool:
    """Check if a numeric float matches the Keithley +9.91E+37 overflow sentinel."""
    return math.isfinite(val) and abs(abs(val) - OVER_RANGE_SENTINEL_MAGNITUDE) < SENTINEL_TOLERANCE


def is_unavailable_sentinel(val: float) -> bool:
    """Check if a numeric float matches the Keithley +9.90E+37 unavailable sentinel."""
    return (
        math.isfinite(val)
        and abs(abs(val) - UNAVAILABLE_SENTINEL_MAGNITUDE) < SENTINEL_TOLERANCE
    )


def parse_reading(raw: str, protocol_mode: int | None = None) -> Keithley6482Reading:
    """Pure parser converting raw Keithley 6482 SCPI response into Keithley6482Reading.

    Expected format from ':FORMat:ELEMents READing,TIME,STATus' query:
      '<reading_amps>,<timestamp_s>,<status_word>'
      e.g. '+1.234567E-06,+12.345678,+00000000'

    Raises:
        ValueError: If input is empty, has fewer than 3 elements, or elements cannot be converted.
    """
    if not isinstance(raw, str):
        raise ValueError(f"Expected string response, got {type(raw).__name__}")

    cleaned = raw.strip().strip('"').strip()
    if not cleaned:
        raise ValueError("Cannot parse empty response from Keithley 6482")

    tokens = [t.strip() for t in cleaned.split(",") if t.strip()]
    if len(tokens) < 3:
        raise ValueError(
            f"Keithley 6482 response expected at least 3 elements (reading, time, status), "
            f"got {len(tokens)} from {raw!r}"
        )

    try:
        raw_val = float(tokens[0])
    except ValueError as exc:
        raise ValueError(f"Invalid current reading {tokens[0]!r} in response {raw!r}") from exc

    try:
        timestamp = float(tokens[1])
    except ValueError as exc:
        raise ValueError(f"Invalid timestamp {tokens[1]!r} in response {raw!r}") from exc

    try:
        # Status word is formatted as integer or integer float string (e.g. '+00000000' or '64')
        status_word = int(float(tokens[2]))
    except ValueError as exc:
        raise ValueError(f"Invalid status word {tokens[2]!r} in response {raw!r}") from exc

    # Status word bit 6 is the authority on over-range
    over_range_from_status = bool(status_word & OVER_RANGE_STATUS_BIT)
    over_range_sentinel = is_over_range_sentinel(raw_val)
    unavailable_sentinel = is_unavailable_sentinel(raw_val)

    over_range = over_range_from_status or over_range_sentinel

    if unavailable_sentinel:
        return Keithley6482Reading(
            current=None,
            timestamp=timestamp,
            status_word=status_word,
            over_range=over_range,
            unavailable=True,
            valid=False,
            channel_2=None,
            raw=raw,
            protocol_mode=protocol_mode,
        )

    if over_range_sentinel:
        return Keithley6482Reading(
            current=None,
            timestamp=timestamp,
            status_word=status_word,
            over_range=True,
            unavailable=False,
            valid=True,
            channel_2=None,
            raw=raw,
            protocol_mode=protocol_mode,
        )

    # Valid numeric reading (if status word has over-range bit set on a finite reading,
    # we record the current value while setting over_range=True)
    return Keithley6482Reading(
        current=raw_val,
        timestamp=timestamp,
        status_word=status_word,
        over_range=over_range,
        unavailable=False,
        valid=True,
        channel_2=None,
        raw=raw,
        protocol_mode=protocol_mode,
    )


def discover() -> list[dict[str, str]]:
    """Scan GPIB for Keithley 6482 instruments.

    Returns a list of dicts: [{"resource": str, "idn": str, "model": str}, ...]
    Never raises; returns [] when pyvisa is unavailable or no instruments found.
    """
    if not PYVISA_AVAILABLE:
        return []
    try:
        rm = pyvisa.ResourceManager()
        resources = rm.list_resources()
    except Exception:
        log.exception("Keithley 6482 discover: ResourceManager / list_resources failed")
        return []

    found = []
    for res in resources:
        if not res.upper().startswith("GPIB"):
            continue
        try:
            inst: Any = rm.open_resource(res)
            inst.timeout = 3000
            inst.read_termination = "\n"
            inst.write_termination = "\n"
            idn = inst.query("*IDN?").strip()
            inst.close()
            if "6482" in idn or "KEITHLEY" in idn.upper():
                found.append({"resource": res, "idn": idn, "model": "6482"})
        except Exception:
            log.exception("Keithley 6482 discover: error querying resource %s", res)
            continue
    return found


class Keithley6482:
    """PyVISA driver for the Keithley 6482 dual-channel picoammeter.

    Lifecycle:
        pico = Keithley6482("GPIB0::14::INSTR")
        reading = pico.read_reading()
        pico.close()

    Safety:
        The configure command is never sent. Bias voltage sources (Source 1 & 2)
        are explicitly commanded OFF and verified OFF during initialisation.
    """
    _inst: Any
    _resource: str
    _idn: str
    _protocol_mode: int | None

    def __init__(self, resource: str):
        if not PYVISA_AVAILABLE:
            raise ImportError("pyvisa is not installed; install it with: pip install pyvisa")
        rm = pyvisa.ResourceManager()
        inst: Any = rm.open_resource(resource)
        inst.timeout = 5000
        inst.read_termination = "\n"
        inst.write_termination = "\n"
        self._inst: Any = inst
        self._resource = resource

        self._idn = self.query("*IDN?")
        self._protocol_mode = self._query_protocol_mode()
        self._init_instrument()

    def _query_protocol_mode(self) -> int | None:
        """Query the GPIB protocol mode (:SYSTem:MEP:STATe?).

        1 = SCPI (MEP enabled, compound queries allowed)
        0 = 488.1 protocol (single command per line only)
        """
        try:
            resp = self.query(":SYSTem:MEP:STATe?")
            if resp.startswith("1"):
                log.info("Keithley 6482 on %s: protocol mode SCPI (MEP enabled)", self._resource)
                return 1
            if resp.startswith("0"):
                log.warning(
                    "Keithley 6482 on %s: protocol mode 488.1 (compound queries NOT permitted)",
                    self._resource,
                )
                return 0
            log.warning("Keithley 6482 on %s: unexpected MEP response %r", self._resource, resp)
            return None
        except Exception:
            log.exception("Keithley 6482 on %s: could not query MEP protocol mode", self._resource)
            return None

    def _init_instrument(self) -> None:
        """Configure channel 1 for autoranged DC current with filters off, and sources off.

        Never sends the configure command.
        """
        # Safety: Turn off voltage bias sources 1 and 2
        self.write(":SOURce1:STATe OFF")
        self.write(":SOURce2:STATe OFF")
        self.assert_sources_off()

        # Channel 1 measurement function: DC current
        self.write(":SENSe1:FUNCtion 'CURRent:DC'")

        # Autorange enabled
        self.write(":SENSe1:CURRent:DC:RANGe:AUTO ON")

        # Integration time: 1 PLC
        self.write(":SENSe1:CURRent:DC:NPLCycles 1")

        # Disable median filter (slows autoranging) and digital averaging filter
        self.write(":SENSe1:MEDian:STATe OFF")
        self.write(":SENSe1:AVERage:STATe OFF")

        # Reading elements: reading, timestamp, status word
        self.write(":FORMat:ELEMents READing,TIME,STATus")

    def assert_sources_off(self) -> None:
        """Assert that both voltage bias source outputs are OFF.

        Raises RuntimeError if either voltage source output is active.
        """
        s1 = self.query(":SOURce1:STATe?").strip()
        s2 = self.query(":SOURce2:STATe?").strip()
        s1_on = s1 in ("1", "ON", "+1")
        s2_on = s2 in ("1", "ON", "+1")
        if s1_on or s2_on:
            raise RuntimeError(
                f"Keithley 6482 voltage source safety assertion failed: "
                f"Source1={s1!r}, Source2={s2!r}. Both must be OFF."
            )

    def write(self, cmd: str) -> None:
        """Send a SCPI command to the instrument."""
        log.debug("WRITE %s", cmd)
        self._inst.write(cmd)

    def query(self, cmd: str) -> str:
        """Send a SCPI query and return the stripped string response."""
        log.debug("QUERY %s", cmd)
        resp = str(self._inst.query(cmd)).strip()
        log.debug("RESP  <- %s", resp)
        return resp

    def idn(self) -> str:
        """Return the identification string queried at connect."""
        return self._idn

    @property
    def protocol_mode(self) -> int | None:
        """Return protocol mode (1=SCPI, 0=488.1, or None)."""
        return self._protocol_mode

    def read_raw(self) -> str:
        """Query :READ? and return the raw response string."""
        return self.query(":READ?")

    def read_reading(self) -> Keithley6482Reading:
        """Acquire a reading from Channel 1 and parse into Keithley6482Reading."""
        raw = self.read_raw()
        return parse_reading(raw, protocol_mode=self._protocol_mode)

    def close(self) -> None:
        """Close the VISA session cleanly."""
        try:
            self._inst.close()
        except Exception:
            log.exception("close() failed for Keithley 6482 on %s", self._resource)
