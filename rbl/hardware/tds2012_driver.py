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
import time

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


def strip_scpi_header(text: str) -> str:
    """':MEASUREMENT:IMMED:VALUE 1.23E-3'  ->  '1.23E-3'.

    With HEADer ON the scope prefixes every reply with the command that
    produced it.  Scalar queries need that prefix removed before float().
    """
    t = text.strip()
    if t.startswith(":") and " " in t:
        return t.split(" ", 1)[1].strip()
    return t


def _split_top_level(text: str, sep: str) -> list[str]:
    """Split on *sep*, but never inside a double-quoted string.

    Required because WFID is a quoted string full of commas and spaces:
        WFID "Ch1, DC coupling, 1.0E-1 V/div, 5.0E-3 s/div, 2500 points"
    Splitting naively on ',' tears it into fragments that then parse as
    garbage keys.
    """
    out, buf, in_quote = [], [], False
    for ch in text:
        if ch == '"':
            in_quote = not in_quote
            buf.append(ch)
        elif ch == sep and not in_quote:
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    out.append("".join(buf))
    return [tok.strip() for tok in out if tok.strip()]


def _parse_preamble(text: str) -> dict:
    """Parse a WFMPRE? response string into a dict.

    TWO WIRE FORMATS ARE ACCEPTED, because TDS firmware revisions differ and
    the answer also depends on whether HEADer is ON:

      headers on   :WFMPRE:BYT_NR 1;BIT_NR 8;ENCDG BIN;...;XINCR 2.0E-5;...
                   -> ':WFMPRE:' leader, ';' between pairs, SPACE between
                      key and value.  This is what the TDS 2012 with the
                      TDS2CM module actually sends.

      headers off  BYT_NR:1,BIT_NR:8,ENCDG:BIN,...,XINCR:4E-09,...
                   -> ',' between pairs, ':' between key and value.

    Known numeric fields are converted to float or int.  Everything else is
    kept as a stripped, unquoted string.  Tokens that are neither
    'KEY VALUE' nor 'KEY:VALUE' are skipped with a debug log.

    Raises
    ------
    TdsProtocolError
        If the text yields no usable pairs at all.
    """
    t = text.strip()
    if not t:
        raise TdsProtocolError("WFMPRE? returned an empty preamble")

    # Drop the ':WFMPRE:' leader while keeping the key it is glued to:
    #   ':WFMPRE:BYT_NR 1;...'  ->  'BYT_NR 1;...'
    if t.startswith(":"):
        head, sep, rest = t.partition(" ")
        if sep:
            t = f"{head.rsplit(':', 1)[-1]} {rest}"

    tokens = _split_top_level(t, ";")
    if len(tokens) < 3:
        tokens = _split_top_level(t, ",")

    result: dict = {}
    for tok in tokens:
        before_space = tok.split(" ", 1)[0]
        if ":" in before_space:
            key, _, val = tok.partition(":")
        elif " " in tok:
            key, _, val = tok.partition(" ")
        else:
            log.debug("tds2012: preamble token without ':' or ' ' — skipped: %r",
                      tok)
            continue
        key = key.strip().upper()
        val = val.strip().strip('"')
        if key in _FLOAT_FIELDS:
            try:
                result[key] = float(val)
            except ValueError:
                log.warning("tds2012: could not convert preamble field %s=%r to float",
                            key, val)
                result[key] = val
        elif key in _INT_FIELDS:
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

    def _query(self, command: str, retries: int = 1) -> str:
        """Send a query and return the response as a stripped string.

        ONE RETRY BY DEFAULT.  Observed on the real instrument: the first
        query issued after a burst of setup commands reliably times out -
        the TDS2CM module is still digesting the writes and simply does not
        answer.  A single retry turns that into a non-event; without it the
        worker tears the connection down and reconnects every time it
        reconfigures.

        Raises TdsTimeoutError on persistent silence, TdsProtocolError if
        the scope replies with an error indicator (response starting '?').
        """
        raw = None
        last_exc = None
        for _attempt in range(retries + 1):
            try:
                raw = self._t.query_line(
                    command.encode() + self._LF,
                    terminator=self._LF,
                )
                break
            except SerialTimeout as exc:
                last_exc = exc
                try:
                    self._t.reset_buffers()
                except Exception:
                    pass
                time.sleep(0.25)
        if raw is None:
            raise TdsTimeoutError(
                f"TDS 2012 did not reply to {command!r}: {last_exc}"
            ) from last_exc

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
        # DEBUG, not INFO: the idle keepalive calls this on a timer, and at
        # INFO it printed a line every few seconds forever, burying every
        # message that mattered.  Nothing is lost - ScopeWorker._connect()
        # already reports the identity at INFO, once, where it is news.
        log.debug("tds2012: identified — %s", ident)
        return ident

    def set_data_encoding(self, channel: str = "CH1", *,
                          encoding: str = "BIN", width: int = 1,
                          start: int = None, stop: int = None) -> None:
        """Configure the waveform transfer to binary, 1-byte-per-sample.

        Parameters
        ----------
        channel  : "CH1" or "CH2"
        encoding : "BIN" (binary, default) or "ASC" (ASCII)
        width    : bytes per sample — 1 or 2
        start    : first record point to transfer (1-based), or None to leave
        stop     : last record point to transfer, or None to leave

        START/STOP select a CONTIGUOUS SLICE of the 2500-point record — they
        do not decimate.  Transferring 1000 points means 1000 consecutive
        samples at full resolution and the remaining 1500 discarded, i.e.
        40 % of the time window, not a coarser view of all of it.
        """
        # HEADer ON is deliberate: with headers off the scope answers
        # WFMPRE? with bare values in a fixed order, and the parser would be
        # trusting firmware to keep that order.  With headers on every value
        # arrives labelled, so a reordering can never silently line up the
        # wrong number against the wrong field.
        self._cmd("HEADER ON")
        self._cmd("VERBOSE OFF")
        self._cmd(f"DATA SOURCE {channel}")
        self._cmd(f"DATA ENCDG {encoding}")
        self._cmd(f"DATA WIDTH {width}")
        if start is not None:
            self._cmd(f"DATA START {int(start)}")
        if stop is not None:
            self._cmd(f"DATA STOP {int(stop)}")
        log.debug("tds2012: data source=%s encdg=%s width=%d start=%s stop=%s",
                  channel, encoding, width, start, stop)

    def read_preamble(self) -> dict:
        """Query WFMPRE? and return the parsed preamble dict.

        Raises TdsTimeoutError or TdsProtocolError on failure.
        """
        text = self._query("WFMPRE?")
        pre  = _parse_preamble(text)
        log.debug("tds2012: preamble — %s", pre)
        return pre

    def acquire_waveform(self, channel: str = "CH1", *,
                         start: int = None,
                         stop: int = None) -> tuple[bytes, dict]:
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
        start   : first record point to transfer, or None for the scope's
                  current setting
        stop    : last record point — the transfer is a SLICE of the record,
                  not a decimation of it

        Returns
        -------
        (bytes, dict)
            payload  — raw ADC bytes
            preamble — parsed WFMPRE? dict

        Raises TdsTimeoutError or TdsProtocolError on any failure.
        """
        self.set_data_encoding(channel, start=start, stop=stop)
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

    def set_average(self, sweeps: int | None) -> None:
        """Put the scope in average mode over *sweeps* sweeps.

        Averaging on the INSTRUMENT beats smoothing on the host: it cleans
        the trace before it is digitised into the transfer, so the half
        maximum level is computed from a cleaner peak.  Valid sweep counts
        are 4, 16, 64 and 128; 1 restores Sample mode; None leaves the
        scope's acquisition mode untouched.
        """
        if sweeps is None:
            return
        if sweeps <= 1:
            self._cmd("ACQUIRE:MODE SAMPLE")
        else:
            self._cmd("ACQUIRE:MODE AVERAGE")
            self._cmd(f"ACQUIRE:NUMAVG {int(sweeps)}")
        self._cmd("ACQUIRE:STATE RUN")
        log.debug("tds2012: acquisition averaging set to %s", sweeps)

    def measure_immediate(self, meas_type: str = "PWIDTH",
                          channel: str = "CH1") -> float:
        """Read one of the scope's OWN automatic measurements, in seconds.

        PWIDTH is Tektronix's positive pulse width, defined as the time
        between the rising and falling edge at the 50 % level - which for a
        single clean peak IS the FWHM.  It costs one short query instead of
        a 2500-point transfer, and it is an independent check on our own
        arithmetic: if the scope and this code disagree, one of them is
        wrong and it is worth knowing which.

        Raises TdsProtocolError when the scope reports the measurement as
        unavailable (it returns ~9.9E37 when the signal is clipped or the
        edges are off screen).
        """
        self._cmd(f"MEASUREMENT:IMMED:SOURCE {channel}")
        self._cmd(f"MEASUREMENT:IMMED:TYPE {meas_type}")
        time.sleep(0.15)
        text = strip_scpi_header(self._query("MEASUREMENT:IMMED:VALUE?"))
        try:
            val = float(text)
        except ValueError as exc:
            raise TdsProtocolError(
                f"{meas_type} value not numeric: {text!r}"
            ) from exc
        if abs(val) > 1e30:
            raise TdsProtocolError(
                f"scope reports {meas_type} unavailable (clipped, or the "
                f"edges are off screen)"
            )
        return val

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
