"""
tds2012_driver.py
RS-232 driver for the Tektronix TDS 2012 oscilloscope fitted with the
TDS2CMA RS-232/GPIB communication module.

COMMUNICATION PARAMETERS
------------------------
- Baud: 19 200 (configurable on the scope front panel; 9 600 also supported)
- Data bits: 8, Parity: None, Stop bits: 1
- RTS/CTS hardware flow control: REQUIRED — the scope will not respond
  without it.  SerialTransport must be opened with rtscts=True.
- Command terminator: LF (\\n)
- Response terminator: LF (\\n)

WAVEFORM TRANSFER
-----------------
The scope sends waveform data in IEEE 488.2 Definite-Length Arbitrary Block
format:

    #<N><D…D><payload bytes>\\n

  - '#'      literal hash
  - N        one ASCII digit: the count of decimal digits in the length field
  - D…D      N ASCII decimal digits giving the byte count of the payload
  - payload  exactly that many bytes of binary sample data
  - \\n       trailing newline (consumed but not part of the payload)

_decode_ieee488_block() extracts the payload from this envelope.
The caller (acquire_waveform) reads the '#' + header with read_until('#N...\\n'),
then reads the exact binary payload with read_exact().

PREAMBLE
--------
WFMPRE? returns a comma-separated key:value string.  _parse_preamble() turns
it into a plain dict with float-converted numeric fields.  Relevant fields:

    BYT_NR   bytes per sample (1 or 2)
    BIT_NR   bits per sample (8 or 16)
    ENCDG    "BIN" or "ASC"
    BN_FMT   "RI" (signed int) or "RP" (unsigned)
    BYT_OR   "MSB" or "LSB" — byte order for 2-byte samples
    NR_PT    number of sample points
    YMULT    vertical scale factor (volts per quantisation level)
    YOFF     vertical position offset in quantisation levels
    YZERO    vertical reference voltage
    XINCR    time between samples (seconds)
    XZERO    time of first sample relative to trigger (seconds)

Sample-to-voltage:
    voltage = (raw_adc - YOFF) * YMULT + YZERO

RATE LIMITING
-------------
The scope is slow over RS-232.  A CURVE? transfer at 19 200 baud for 2 500
samples × 2 bytes takes ~3 s.  No artificial rate limiting is applied;
the acquisition itself is the bottleneck.

EXCEPTIONS
----------
TdsProtocolError  — the scope replied with an unexpected format or error code.
TdsTimeoutError   — SerialTimeout propagated from the transport.
"""
import logging
import struct

from rbl.hardware.serial_transport import SerialTimeout

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class TdsProtocolError(OSError):
    """Unexpected reply from the TDS 2012 (bad format, NAK, etc.)."""


class TdsTimeoutError(OSError):
    """The TDS 2012 did not reply before the transport timeout."""


# ---------------------------------------------------------------------------
# IEEE 488.2 block decode
# ---------------------------------------------------------------------------

def _decode_ieee488_block(raw: bytes) -> bytes:
    """Extract the binary payload from an IEEE 488.2 definite-length block.

    Expects *raw* to begin with '#' and to contain at least the full header.
    The trailing '\\n' (or '\\r\\n') may or may not be present in *raw* — it is
    ignored.

    Returns
    -------
    bytes
        The raw payload bytes, not including the '#NDD…D' header.

    Raises
    ------
    TdsProtocolError
        If the block does not start with '#', N is 0 (indefinite-length
        block — not supported), or the header is truncated.
    """
    # Strip leading whitespace / CRLF that sometimes precede the '#'
    idx = raw.find(b"#")
    if idx < 0:
        raise TdsProtocolError(
            f"IEEE 488.2 block missing '#' header: {raw[:32]!r}"
        )
    raw = raw[idx:]

    if len(raw) < 2:
        raise TdsProtocolError(
            f"IEEE 488.2 block too short (< 2 bytes): {raw!r}"
        )

    n_digits = raw[1] - ord("0")   # raw[1] is the ASCII digit N
    if n_digits == 0:
        raise TdsProtocolError(
            "Indefinite-length IEEE 488.2 block (#0...) is not supported."
        )
    if n_digits < 1 or n_digits > 9:
        raise TdsProtocolError(
            f"IEEE 488.2 block: invalid digit count N={n_digits!r} "
            f"in header {raw[:10]!r}"
        )

    header_len = 2 + n_digits          # '#' + digit char + N length digits
    if len(raw) < header_len:
        raise TdsProtocolError(
            f"IEEE 488.2 block header truncated: need {header_len} bytes, "
            f"got {len(raw)}: {raw!r}"
        )

    try:
        payload_len = int(raw[2 : 2 + n_digits])
    except ValueError as exc:
        raise TdsProtocolError(
            f"IEEE 488.2 block: non-numeric length field: "
            f"{raw[2 : 2 + n_digits]!r}"
        ) from exc

    payload = raw[header_len : header_len + payload_len]
    if len(payload) < payload_len:
        raise TdsProtocolError(
            f"IEEE 488.2 block payload truncated: expected {payload_len} "
            f"bytes, got {len(payload)}"
        )

    return payload


# ---------------------------------------------------------------------------
# Preamble parser
# ---------------------------------------------------------------------------

_FLOAT_FIELDS = {"YMULT", "YOFF", "YZERO", "XINCR", "XZERO", "PT_OFF"}
_INT_FIELDS   = {"BYT_NR", "BIT_NR", "NR_PT"}


def _parse_preamble(text: str) -> dict:
    """Parse a WFMPRE? response string into a dict.

    The scope returns comma-separated 'KEY:VALUE' pairs, e.g.::

        BYT_NR:1,BIT_NR:8,ENCDG:BIN,...,YMULT:4E-4,YOFF:-50,...

    Known numeric fields are converted to float (floats) or int (integers).
    All other fields are kept as stripped strings.

    Raises
    ------
    TdsProtocolError
        If the text cannot be parsed as a preamble at all (no ':' separators).
    """
    pairs = [tok.strip() for tok in text.strip().split(",") if tok.strip()]
    if not pairs:
        raise TdsProtocolError(
            f"WFMPRE? returned an empty or unparseable preamble: {text!r}"
        )

    result: dict = {}
    for pair in pairs:
        if ":" not in pair:
            log.debug("tds2012: preamble token without ':' — skipped: %r", pair)
            continue
        key, _, val = pair.partition(":")
        key = key.strip().upper()
        val = val.strip()
        if key in _FLOAT_FIELDS:
            try:
                result[key] = float(val)
            except ValueError:
                log.warning("tds2012: could not convert preamble field %s=%r to float",
                            key, val)
                result[key] = val
        elif key in _INT_FIELDS:
            try:
                result[key] = int(val)
            except ValueError:
                try:
                    result[key] = int(float(val))
                except ValueError:
                    log.warning("tds2012: could not convert preamble field %s=%r to int",
                                key, val)
                    result[key] = val
        else:
            result[key] = val

    if not result:
        raise TdsProtocolError(
            f"WFMPRE? preamble parsed to empty dict: {text!r}"
        )
    return result


# ---------------------------------------------------------------------------
# Sample conversion
# ---------------------------------------------------------------------------

def samples_to_volts(raw_bytes: bytes, preamble: dict) -> list[float]:
    """Convert raw ADC bytes to a list of voltages using preamble scaling.

    Parameters
    ----------
    raw_bytes : payload bytes from acquire_waveform()
    preamble  : dict from _parse_preamble(), must contain YMULT, YOFF, YZERO,
                BYT_NR, BN_FMT, and BYT_OR.

    Returns
    -------
    list[float]
        One voltage (V) per sample point.

    Raises
    ------
    TdsProtocolError
        If BYT_NR is not 1 or 2, or BN_FMT is unrecognised.
    """
    byt_nr = int(preamble.get("BYT_NR", 1))
    bn_fmt = str(preamble.get("BN_FMT", "RI")).upper()
    byt_or = str(preamble.get("BYT_OR", "MSB")).upper()
    ymult  = float(preamble.get("YMULT",  1.0))
    yoff   = float(preamble.get("YOFF",   0.0))
    yzero  = float(preamble.get("YZERO",  0.0))

    signed = bn_fmt == "RI"

    if byt_nr == 1:
        fmt = "b" if signed else "B"
        raw_ints = list(struct.unpack(f"{len(raw_bytes)}{fmt}", raw_bytes))
    elif byt_nr == 2:
        n = len(raw_bytes) // 2
        endian = ">" if byt_or == "MSB" else "<"
        fmt = "h" if signed else "H"
        raw_ints = list(struct.unpack(f"{endian}{n}{fmt}", raw_bytes[:n * 2]))
    else:
        raise TdsProtocolError(
            f"tds2012: unsupported BYT_NR={byt_nr}; only 1 and 2 are supported."
        )

    return [(v - yoff) * ymult + yzero for v in raw_ints]


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

class Tds2012:
    """RS-232 driver for the Tektronix TDS 2012 oscilloscope.

    Parameters
    ----------
    transport : SerialTransport (or compatible — must implement write,
                read_until, read_exact, query_line, reset_buffers).
                Must have been opened with rtscts=True.
    """

    _LF = b"\n"

    def __init__(self, transport):
        self._t = transport

    # ---- Low-level command helpers ----------------------------------------

    def _cmd(self, command: str) -> None:
        """Send a command string (no response expected)."""
        self._t.write(command.encode() + self._LF)

    def _query(self, command: str) -> str:
        """Send a query and return the response as a stripped string.

        Raises TdsTimeoutError on silence, TdsProtocolError if the scope
        replies with an error indicator (response starting with '?').
        """
        try:
            raw = self._t.query_line(
                command.encode() + self._LF,
                terminator=self._LF,
            )
        except SerialTimeout as exc:
            raise TdsTimeoutError(
                f"TDS 2012 did not reply to {command!r}: {exc}"
            ) from exc

        text = raw.decode(errors="replace").strip()
        if text.startswith("?"):
            raise TdsProtocolError(
                f"TDS 2012 returned error for {command!r}: {text!r}"
            )
        return text

    # ---- Public API -------------------------------------------------------

    def identify(self) -> str:
        """Send ID? and return the identification string.

        Expected format: ``ID TEK/TDS 2012,<serial>,CF:<fw>,<opt>``

        Raises TdsTimeoutError or TdsProtocolError on failure.
        """
        ident = self._query("ID?")
        if "TEK" not in ident.upper() and "TDS" not in ident.upper():
            raise TdsProtocolError(
                f"ID? reply does not look like a Tektronix response: {ident!r}"
            )
        log.info("tds2012: identified — %s", ident)
        return ident

    def set_data_encoding(self, channel: str = "CH1", *,
                          encoding: str = "BIN", width: int = 1) -> None:
        """Configure the waveform transfer to binary, 1-byte-per-sample.

        Parameters
        ----------
        channel  : "CH1" or "CH2"
        encoding : "BIN" (binary, default) or "ASC" (ASCII)
        width    : bytes per sample — 1 or 2
        """
        self._cmd(f"DATA SOURCE {channel}")
        self._cmd(f"DATA ENCDG {encoding}")
        self._cmd(f"DATA WIDTH {width}")
        log.debug("tds2012: data source=%s encdg=%s width=%d",
                  channel, encoding, width)

    def read_preamble(self) -> dict:
        """Query WFMPRE? and return the parsed preamble dict.

        Raises TdsTimeoutError or TdsProtocolError on failure.
        """
        text = self._query("WFMPRE?")
        pre  = _parse_preamble(text)
        log.debug("tds2012: preamble — %s", pre)
        return pre

    def acquire_waveform(self, channel: str = "CH1") -> tuple[bytes, dict]:
        """Acquire one waveform from *channel*.

        Steps:
        1. Select the channel and set binary encoding.
        2. Read the preamble.
        3. Send CURVE? and read the IEEE 488.2 binary block.
        4. Return (payload_bytes, preamble_dict).

        The caller converts payload_bytes to voltages with samples_to_volts().

        Parameters
        ----------
        channel : "CH1" or "CH2"

        Returns
        -------
        (bytes, dict)
            payload  — raw ADC bytes
            preamble — parsed WFMPRE? dict

        Raises TdsTimeoutError or TdsProtocolError on any failure.
        """
        self.set_data_encoding(channel)
        preamble = self.read_preamble()

        n_bytes = int(preamble.get("NR_PT", 0)) * int(preamble.get("BYT_NR", 1))
        if n_bytes <= 0:
            raise TdsProtocolError(
                f"tds2012: preamble reports NR_PT={preamble.get('NR_PT')}, "
                f"BYT_NR={preamble.get('BYT_NR')} — cannot determine payload size"
            )

        # Send CURVE? then read the '#NDD…D' header (up to 12 bytes covers
        # '#' + 1 digit N + up to 9 length digits + at least 1 byte) using
        # read_until to get everything up to and including the payload.
        # Because the payload is binary (may contain any byte), we use a
        # two-step approach:
        #   a) write the CURVE? command
        #   b) read the '#NDD…D' header via read_until(b'#', max_bytes=12)
        #      — actually we read until we can parse the header, then
        #   c) read the exact payload with read_exact(n_bytes)
        #   d) consume the trailing LF

        try:
            self._t.write(b"CURVE?" + self._LF)

            # Read until we have the '#' and the N digit
            header_start = self._t.read_until(b"#", max_bytes=64)
            idx = header_start.find(b"#")
            if idx < 0:
                raise TdsProtocolError(
                    f"CURVE? response missing '#': {header_start!r}"
                )
            after_hash = header_start[idx + 1:]  # bytes after '#'

            # We need at least 1 byte for N
            while len(after_hash) < 1:
                chunk = self._t.read_until(b"\n", max_bytes=1)
                after_hash += chunk

            n_digits = after_hash[0] - ord("0")
            if n_digits < 1 or n_digits > 9:
                raise TdsProtocolError(
                    f"CURVE? IEEE 488.2 block: bad N byte {after_hash[0]!r}"
                )

            # Read remaining length digits if not yet buffered
            while len(after_hash) < 1 + n_digits:
                needed = 1 + n_digits - len(after_hash)
                chunk  = self._t.read_exact(needed)
                after_hash += chunk

            payload_len_str = after_hash[1 : 1 + n_digits].decode(errors="replace")
            try:
                payload_len = int(payload_len_str)
            except ValueError as exc:
                raise TdsProtocolError(
                    f"CURVE? IEEE 488.2 length field not numeric: "
                    f"{payload_len_str!r}"
                ) from exc

            # Any bytes that arrived after the length field already count
            extra = after_hash[1 + n_digits:]
            remaining = payload_len - len(extra)
            if remaining > 0:
                payload = extra + self._t.read_exact(remaining)
            else:
                payload = extra[:payload_len]

            # Consume the trailing LF (best effort — don't fail if absent)
            try:
                self._t.read_exact(1)
            except SerialTimeout:
                pass

        except SerialTimeout as exc:
            raise TdsTimeoutError(
                f"TDS 2012 CURVE? timed out: {exc}"
            ) from exc

        log.debug("tds2012: acquired %d bytes from %s", len(payload), channel)
        return bytes(payload), preamble

    def single_acquisition(self) -> None:
        """Arm a single-sequence acquisition (ACQUIRE:STOPAFTER SEQUENCE)."""
        self._cmd("ACQUIRE:STOPAFTER SEQUENCE")
        self._cmd("ACQUIRE:STATE RUN")
        log.debug("tds2012: single acquisition armed")

    def run_continuous(self) -> None:
        """Put the scope back into continuous run mode."""
        self._cmd("ACQUIRE:STOPAFTER RUNSTOP")
        self._cmd("ACQUIRE:STATE RUN")
        log.debug("tds2012: continuous run restored")

    def reset_buffers(self) -> None:
        """Flush the transport buffers (call before any new query sequence)."""
        self._t.reset_buffers()
