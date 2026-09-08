"""
serial_transport.py
Thin pyserial wrapper providing one-port, one-lock, line- and block-oriented
I/O, plus identity-based port discovery for the three RS-232 instruments.

No instrument-protocol knowledge lives here — only bytes, timeouts, and
termination.

THREADING CONTRACT
------------------
Every public I/O method acquires self._lock before touching the serial port.
This mirrors GalilController's approach and allows the worker thread and
diagnostic __main__ invocations to share the same transport safely.

PORT OPEN REGISTRY
------------------
A module-level set (_OPEN_PORTS) tracks every currently-open port path.
SerialTransport.open() asserts the port is not already registered, raising a
clear error naming the collision if it is.  probe_port() opens raw
serial.Serial objects transiently and does NOT register them — probing is
read-only and must not block a later real open.
"""
import logging
import re
import sys
import threading

log = logging.getLogger(__name__)

try:
    import serial
    import serial.tools.list_ports
    _SERIAL_AVAILABLE = True
except ImportError:
    serial = None
    _SERIAL_AVAILABLE = False
    log.warning(
        "pyserial not installed — serial_transport will not function. "
        "Run: pip install pyserial>=3.5"
    )

# Module-level registry of open port paths (upper-cased on Windows).
_OPEN_PORTS: set[str] = set()
_OPEN_PORTS_LOCK = threading.Lock()


def _norm(port: str) -> str:
    return port.upper() if sys.platform == "win32" else port


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class SerialTimeout(OSError):
    """read_until / read_exact received zero bytes before the deadline."""


class SerialTransportError(OSError):
    """Port-registry or configuration error (e.g. port already open)."""


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------

class SerialTransport:
    """One pyserial port, one lock, line- and block-oriented reads.

    Deliberately dumb: it knows about bytes, timeouts and termination,
    and nothing about any instrument's protocol.

    Parameters
    ----------
    port     : COM port name, e.g. "COM4" or "/dev/ttyUSB0"
    baud     : baud rate
    rtscts   : enable RTS/CTS hardware flow control (required for TDS 2012)
    timeout  : per-read timeout in seconds
    bytesize : data bits (default 8)
    parity   : "N" / "E" / "O" (default "N")
    stopbits : 1 or 2 (default 1)
    """

    def __init__(self, port: str, baud: int, *, rtscts: bool = False,
                 timeout: float = 1.0, bytesize: int = 8,
                 parity: str = "N", stopbits: int = 1):
        self._port     = port
        self._baud     = baud
        self._rtscts   = rtscts
        self._timeout  = timeout
        self._bytesize = bytesize
        self._parity   = parity
        self._stopbits = stopbits
        self._serial: "serial.Serial | None" = None
        self._lock     = threading.Lock()

    # ---- Lifecycle --------------------------------------------------------

    def open(self):
        """Open the port and register it in the global open-port registry.

        Raises SerialTransportError if the port is already registered
        (another SerialTransport has it open).
        Raises serial.SerialException if the OS refuses the open.
        """
        if not _SERIAL_AVAILABLE:
            raise SerialTransportError(
                "pyserial is not installed — run: pip install pyserial>=3.5"
            )
        norm = _norm(self._port)
        with _OPEN_PORTS_LOCK:
            if norm in _OPEN_PORTS:
                raise SerialTransportError(
                    f"Port {self._port!r} is already open by another "
                    "SerialTransport instance.  Two instruments may be "
                    "configured for the same COM port."
                )
            self._serial = serial.Serial(
                port     = self._port,
                baudrate = self._baud,
                bytesize = self._bytesize,
                parity   = self._parity,
                stopbits = self._stopbits,
                rtscts   = self._rtscts,
                timeout  = self._timeout,
            )
            _OPEN_PORTS.add(norm)
        log.debug("serial_transport: opened %s @ %d baud rtscts=%s",
                  self._port, self._baud, self._rtscts)

    def close(self):
        """Close the port and remove it from the registry. Idempotent."""
        with _OPEN_PORTS_LOCK:
            if self._serial is not None:
                try:
                    self._serial.close()
                except Exception:
                    pass
                self._serial = None
                _OPEN_PORTS.discard(_norm(self._port))
                log.debug("serial_transport: closed %s", self._port)

    @property
    def connected(self) -> bool:
        return self._serial is not None and self._serial.is_open

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *_):
        self.close()

    # ---- I/O primitives ---------------------------------------------------

    def write(self, data: bytes) -> None:
        """Send bytes. Acquires the lock."""
        with self._lock:
            assert self._serial is not None, "write() called before open()"
            log.debug("serial TX %s: %r", self._port, data)
            self._serial.write(data)

    def read_until(self, terminator: bytes, max_bytes: int = 4096) -> bytes:
        """Read until *terminator* is seen or the timeout expires.

        Returns whatever arrived (which may or may not end with the
        terminator — the caller's parser decides if a partial read is an
        error).  Raises SerialTimeout only if ZERO bytes arrived; a partial
        read is returned, never raised.  This is what makes the XGS-600's
        silent-on-bad-address behaviour tractable.

        Acquires the lock for the full read.
        """
        with self._lock:
            return self._read_until_locked(terminator, max_bytes)

    def _read_until_locked(self, terminator: bytes,
                           max_bytes: int = 4096) -> bytes:
        """read_until without acquiring the lock — for use inside query_line."""
        assert self._serial is not None, "read_until() called before open()"
        buf = b""
        while len(buf) < max_bytes:
            chunk = self._serial.read_until(terminator,
                                            size=max_bytes - len(buf))
            buf += chunk
            if terminator in buf:
                break
            if not chunk:
                break
        if not buf:
            raise SerialTimeout(
                f"read_until on {self._port!r}: "
                f"zero bytes before timeout ({self._timeout:.2f} s)"
            )
        log.debug("serial RX %s: %r", self._port, buf)
        return buf

    def read_exact(self, n: int) -> bytes:
        """Read exactly *n* bytes.  Raises SerialTimeout if fewer arrive.

        Used for IEEE 488.2 binary block payloads where no terminator is
        safe (the payload can contain the terminator byte).  Acquires the
        lock for the full read.
        """
        with self._lock:
            assert self._serial is not None, "read_exact() called before open()"
            buf = b""
            while len(buf) < n:
                chunk = self._serial.read(n - len(buf))
                if not chunk:
                    raise SerialTimeout(
                        f"read_exact({n}) on {self._port!r}: "
                        f"got {len(buf)} bytes then timeout"
                    )
                buf += chunk
            log.debug("serial RX %s (exact %d): %r", self._port, n, buf)
            return buf

    def query_line(self, cmd: bytes,
                   terminator: bytes = b"\r") -> bytes:
        """Write *cmd* then read until *terminator*.

        One atomic transaction — the lock is held for the combined write +
        read so no other thread can interleave a command between them.
        """
        with self._lock:
            assert self._serial is not None, "query_line() called before open()"
            log.debug("serial TX %s: %r", self._port, cmd)
            self._serial.write(cmd)
            return self._read_until_locked(terminator)

    def reset_buffers(self) -> None:
        """Flush in- and out-bound OS buffers.  Call before probing."""
        with self._lock:
            if self._serial is not None and self._serial.is_open:
                self._serial.reset_input_buffer()
                self._serial.reset_output_buffer()


# ---------------------------------------------------------------------------
# Port information
# ---------------------------------------------------------------------------

class PortInfo:
    """Lightweight container for one enumerated serial port."""
    __slots__ = ("device", "description", "hwid")

    def __init__(self, device: str, description: str, hwid: str):
        self.device      = device
        self.description = description
        self.hwid        = hwid

    def __repr__(self):
        return f"PortInfo({self.device!r}, {self.description!r})"


def enumerate_ports() -> list:
    """Return a list of PortInfo for every available serial port."""
    if not _SERIAL_AVAILABLE:
        return []
    result = []
    for p in serial.tools.list_ports.comports():
        result.append(PortInfo(p.device, p.description or "", p.hwid or ""))
    return result


# ---------------------------------------------------------------------------
# Identity probe candidates
# ---------------------------------------------------------------------------
# Each entry:
#   key      — short identifier used as the persistence / dict key
#   bauds    — baud rates to try in order
#   query    — bytes to send (read-only, harmless if sent to a wrong device)
#   match_fn — callable(bytes) -> bool; True if the reply is this instrument
#   rtscts   — whether the probe itself needs hardware flow control

_CANDIDATES = [
    {
        "key":   "xgs600",
        "bauds": [9600, 19200],
        "query": b"#0005\r",
        # >HHHH,HHHH,... — starts with '>' and hex words separated by commas.
        "match_fn": lambda b: (
            b.lstrip().startswith(b">") and
            bool(re.match(
                rb">([0-9A-Fa-f]{4})(,[0-9A-Fa-f]{4})*\r?$",
                b.strip()
            ))
        ),
        "rtscts": False,
    },
    {
        "key":   "vgc083",
        "bauds": [19200, 9600],
        "query": b"#  VER\r",
        # *   01306-11  — starts with '*', response is 13 chars including CR.
        "match_fn": lambda b: (
            b.lstrip().startswith(b"*") and len(b.rstrip(b"\r\n")) >= 12
        ),
        "rtscts": False,
    },
    {
        "key":   "tds2012",
        "bauds": [19200, 9600],
        "query": b"ID?\n",
        # ID TEK/TDS 1002,... — contains both "TEK" and "TDS".
        "match_fn": lambda b: b"TEK" in b and b"TDS" in b,
        "rtscts": True,
    },
]

_PROBE_TIMEOUT_S = 0.3


def candidate_keys() -> list:
    """Every instrument key this module knows how to probe for."""
    return [c["key"] for c in _CANDIDATES]


def candidates_for(keys) -> list:
    """The probe candidates for *keys*, in the table's own order.

    Lets a caller scan for ONE instrument instead of all of them.  Probing
    is not free - every port is opened and waited on at each candidate baud
    - so a tab that only wants its own instrument should say so.
    """
    wanted = set(keys)
    return [c for c in _CANDIDATES if c["key"] in wanted]


# ---------------------------------------------------------------------------
# Probing
# ---------------------------------------------------------------------------

def probe_port(port: str, candidates: list) -> "str | None":
    """Probe *port* transiently with each candidate's identity query.

    Opens a raw serial.Serial (NOT registered in _OPEN_PORTS — probing is
    transient and must not block a subsequent real open).  Always closes
    before returning.  Returns the first matching instrument key, or None.

    Callers must ensure the port is not in _OPEN_PORTS before calling;
    discover() handles this by checking the claimed set.
    """
    if not _SERIAL_AVAILABLE:
        return None

    for candidate in candidates:
        key      = candidate["key"]
        bauds    = candidate["bauds"]
        query    = candidate["query"]
        match_fn = candidate["match_fn"]
        rtscts   = candidate["rtscts"]

        for baud in bauds:
            log.debug("probe_port: %s @ %d baud -> %s ?", port, baud, key)
            ser = None
            try:
                ser = serial.Serial(
                    port     = port,
                    baudrate = baud,
                    bytesize = 8,
                    parity   = "N",
                    stopbits = 1,
                    rtscts   = rtscts,
                    timeout  = _PROBE_TIMEOUT_S,
                )
                ser.reset_input_buffer()
                ser.reset_output_buffer()
                log.debug("probe TX  %s: %r", port, query)
                ser.write(query)
                reply = ser.read_until(b"\r", size=256)
                if not reply:
                    # Some instruments terminate with LF; try a fallback read.
                    reply = ser.read(256)
                log.debug("probe RX  %s: %r", port, reply)
                if reply and match_fn(reply):
                    log.info("probe_port: %s identified as %s @ %d baud",
                             port, key, baud)
                    return key
            except Exception as exc:
                log.debug("probe_port: %s @ %d baud %s -> %s",
                          port, baud, key, exc)
            finally:
                if ser is not None:
                    try:
                        ser.close()
                    except Exception:
                        pass

    return None


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def discover(candidates: list = None, saved: dict = None) -> dict:
    """Identify which COM port each instrument is on.

    Strategy (plan §Phase 1):
    1. For each instrument, try its saved port first (from persistence).
    2. On failure, enumerate all ports and probe each unclaimed one with
       each unmatched instrument's identity query.
    3. Return a dict mapping instrument key -> port string.
    4. Never probe a port already claimed by a matched instrument.

    Parameters
    ----------
    candidates : list of candidate dicts (defaults to _CANDIDATES)
    saved      : {"xgs600": "COM4", ...} from persistence, or None for {}

    Returns
    -------
    Partial dict — keys absent means the instrument was not found.
    """
    if candidates is None:
        candidates = _CANDIDATES
    if saved is None:
        saved = {}

    result:  dict[str, str] = {}
    claimed: set[str]       = set()  # normalised port names already assigned

    # ---- Step 1: try saved ports first ------------------------------------
    for cand in candidates:
        key        = str(cand["key"])
        saved_port = saved.get(key)
        if not saved_port:
            continue
        norm = _norm(saved_port)
        if norm in claimed:
            log.debug("discover: saved port %s for %s already claimed",
                      saved_port, key)
            continue
        log.info("discover: trying saved port %s for %s", saved_port, key)
        found = probe_port(saved_port, [cand])
        if found == key:
            result[key] = saved_port
            claimed.add(norm)
            log.info("discover: %s confirmed on saved port %s", key, saved_port)
        else:
            log.info("discover: %s NOT found on saved port %s (will scan)",
                     key, saved_port)

    # ---- Step 2: scan remaining ports for unmatched instruments -----------
    unmatched = [c for c in candidates if c["key"] not in result]
    if not unmatched:
        return result

    all_ports = enumerate_ports()
    log.info("discover: scanning %d port(s) for %s",
             len(all_ports), [c["key"] for c in unmatched])

    for port_info in all_ports:
        port = port_info.device
        norm = _norm(port)
        if norm in claimed:
            continue
        still_unmatched = [c for c in unmatched if c["key"] not in result]
        if not still_unmatched:
            break
        log.debug("discover: probing %s (%s)", port, port_info.description)
        found = probe_port(port, still_unmatched)
        if found is not None:
            result[found] = port
            claimed.add(norm)
            log.info("discover: found %s on %s", found, port)

    return result


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def load_saved_ports() -> dict:
    """Return the serial_ports sub-dict from the rbl persistence store."""
    try:
        from rbl.config.persistence import load_config
        return load_config().get("serial_ports", {})
    except Exception as exc:
        log.debug("load_saved_ports: %s", exc)
        return {}


def save_ports(ports: dict):
    """Merge discovered port assignments into the rbl persistence store."""
    try:
        from rbl.config.persistence import load_config, save_config
        cfg = load_config()
        existing = cfg.get("serial_ports", {})
        existing.update(ports)
        cfg["serial_ports"] = existing
        save_config(cfg)
    except Exception as exc:
        log.debug("save_ports: %s", exc)


# ---------------------------------------------------------------------------
# Self-test / CLI  (python -m rbl.hardware.serial_transport --discover)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Enumerate and identity-probe serial ports for RBL instruments."
    )
    parser.add_argument(
        "--discover", action="store_true",
        help="Run full instrument discovery (default when invoked directly).",
    )
    parser.add_argument(
        "--port",
        help="Probe a single specific port and print the result.",
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Enable DEBUG logging for probe I/O.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    if not _SERIAL_AVAILABLE:
        print("ERROR: pyserial is not installed.")
        print("       Run: pip install pyserial>=3.5")
        sys.exit(1)

    # ---- Enumerate --------------------------------------------------------
    ports = enumerate_ports()
    print(f"\nEnumerated {len(ports)} port(s):")
    if ports:
        for p in ports:
            print(f"  {p.device:12s}  {p.description}")
    else:
        print("  (none found)")

    # ---- Single-port probe ------------------------------------------------
    if args.port:
        print(f"\nProbing {args.port} with all candidates...")
        key = probe_port(args.port, _CANDIDATES)
        if key:
            print(f"  -> identified as: {key}")
        else:
            print("  -> no instrument recognised on this port")
        print("\n[OK] Phase 1 transport")
        sys.exit(0)

    # ---- Full discovery ---------------------------------------------------
    print("\nLoading saved port assignments from persistence store...")
    saved = load_saved_ports()
    if saved:
        print(f"  Saved: {saved}")
    else:
        print("  (none saved yet)")

    print("\nRunning discovery (trying saved ports first, then scanning)...")
    found = discover(saved=saved)

    if found:
        print(f"\nDiscovered {len(found)} instrument(s):")
        for key, port in found.items():
            print(f"  {key:12s} -> {port}")
        save_ports(found)
        print("  (Saved to persistence store for next launch.)")
    else:
        print("\nNo instruments discovered.")
        print("  (Expected when no hardware is attached — this is not an error.)")

    print("\n[OK] Phase 1 transport")
