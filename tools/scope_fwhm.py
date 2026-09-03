#!/usr/bin/env python3
"""
scope_fwhm.py  -  standalone bench tool: read a beam profile off the
Tektronix TDS 2012 over RS-232 and print/plot its FWHM.

This file deliberately depends on NOTHING inside the RBL package.  It is a
single-file diagnostic you can run on the control PC while the beam is on,
so we can learn exactly how the scope behaves before folding that knowledge
back into rbl/hardware/scope_worker.py.

--------------------------------------------------------------------------
FLOW OF OPERATIONS
--------------------------------------------------------------------------
    1. find the scope        open each COM port, ask "ID?", keep the one
                             that answers with TEK/TDS
    2. configure transfer    DATA:SOURCE / ENCDG / WIDTH / START / STOP
    3. per acquisition:
         a. WFMPRE?          -> scaling numbers (volts-per-count, s/sample)
         b. CURVE?           -> 2500 raw ADC bytes in an IEEE-488.2 block
         c. rescale          volts[i] = (raw[i] - YOFF) * YMULT + YZERO
                             time[i]  = XZERO + i * XINCR
         d. FWHM             half-max crossings + optional Gaussian fit
         e. cross-check      ask the scope for its own PWIDTH measurement
                             (positive pulse width at the 50 % level, which
                             for a single clean peak IS the FWHM)
         f. report           one line to the terminal, one row to CSV,
                             optional live plot
    4. repeat until Ctrl-C

--------------------------------------------------------------------------
SCOPE FRONT PANEL (do this first, once)
--------------------------------------------------------------------------
    UTILITY -> Options -> RS232 Setup
        Baud          19200        (must match --baud)
        Flow control  Hard Flagging  (RTS/CTS - the default, and what this
                                      script uses unless you pass --flow)
        EOL String    LF
        Parity        None
    The TDS2CMA communication module must be seated in the back of the scope.

--------------------------------------------------------------------------
TYPICAL USE
--------------------------------------------------------------------------
    python scope_fwhm.py --list                 # what COM ports exist
    python scope_fwhm.py --simulate --plot      # dry run, no hardware
    python scope_fwhm.py --plot                 # auto-find scope, live
    python scope_fwhm.py --port COM5 --channel CH1 --plot --csv fwhm_log.csv
    python scope_fwhm.py --once --save-trace shot1.csv

    # a RASTERED beam - measure the envelope of the raster teeth, not one
    # tooth (read the ripple period off the scope and pass it here):
    python scope_fwhm.py --plot --envelope-ms 1.1

    # convert the time-axis FWHM into millimetres of beam width, when the
    # beam is being swept by a triangle wave:
    python scope_fwhm.py --plot --sweep-hz 100 --sweep-mm-pp 40
    # (or give the conversion directly:  --mm-per-s 8000)
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import struct
import sys
import time

# ---------------------------------------------------------------------------
# Optional imports.  Everything degrades gracefully so the script still runs
# on a machine that is missing matplotlib or scipy.
# ---------------------------------------------------------------------------
try:
    import serial  # pyserial
    from serial.tools import list_ports
except ImportError:                     # pragma: no cover
    serial = None
    list_ports = None

try:
    import numpy as np
except ImportError:                     # pragma: no cover
    np = None


# ===========================================================================
# SECTION 1 - talking to the scope
# ===========================================================================

class ScopeError(RuntimeError):
    """Anything that means 'the scope did not do what we asked'."""

# ---------------------------------------------------------------------------
# Reply parsing helpers
#
# This scope answers with SCPI headers switched ON, e.g.
#
#   :WFMPRE:BYT_NR 1;BIT_NR 8;ENCDG BIN;BN_FMT RI;BYT_OR MSB;NR_PT 2500;
#   WFID "Ch1, DC coupling, 1.0E-1 V/div, 5.0E-3 s/div, 2500 points, Sample
#   mode";PT_FMT Y;XINCR 2.0E-5;PT_OFF 0;XZERO -2.5E-2;XUNIT "s";YMULT 4.0E-3;
#   YZERO 0.0E0;YOFF -3.0E1;YUNIT "V"
#
# So: one ':COMMAND:' leader, then 'KEY VALUE' pairs separated by ';', and one
# of those values (WFID) is a quoted string full of commas and spaces.  The
# splitter below therefore has to respect quotes.
# ---------------------------------------------------------------------------

def strip_scpi_header(text: str) -> str:
    """':MEASUREMENT:IMMED:VALUE 1.23E-3'  ->  '1.23E-3'."""
    t = text.strip()
    if t.startswith(":") and " " in t:
        return t.split(" ", 1)[1].strip()
    return t


def _split_top_level(text: str, sep: str = ";") -> list[str]:
    """Split on *sep*, but never inside a double-quoted string."""
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


_PRE_FLOATS = {"YMULT", "YOFF", "YZERO", "XINCR", "XZERO", "PT_OFF"}
_PRE_INTS = {"BYT_NR", "BIT_NR", "NR_PT"}


def parse_preamble(text: str) -> dict:
    """WFMPRE? reply -> {'XINCR': 2e-05, 'YMULT': 0.004, ...}.

    Tolerates headers on or off-ish, ';' or ',' separators, and 'KEY VALUE'
    or 'KEY:VALUE' pairs, because different TDS firmware revisions disagree.
    """
    t = text.strip()

    # Drop the ':WFMPRE:' leader while keeping the first key it is glued to:
    # ':WFMPRE:BYT_NR 1;...'  ->  'BYT_NR 1;...'
    if t.startswith(":"):
        head, sep, rest = t.partition(" ")
        if sep:
            t = f"{head.rsplit(':', 1)[-1]} {rest}"

    tokens = _split_top_level(t, ";")
    if len(tokens) < 3:                      # some firmware uses commas
        tokens = _split_top_level(t, ",")

    out: dict = {}
    for tok in tokens:
        if ":" in tok and " " not in tok.split(":", 1)[0]:
            key, _, val = tok.partition(":")
        elif " " in tok:
            key, _, val = tok.partition(" ")
        else:
            continue
        key = key.strip().upper()
        val = val.strip().strip('"')
        try:
            if key in _PRE_FLOATS:
                out[key] = float(val)
            elif key in _PRE_INTS:
                out[key] = int(float(val))
            else:
                out[key] = val
        except ValueError:
            out[key] = val
    return out



class Tds:
    """Thin RS-232 wrapper around the TDS 2012 + TDS2CMA module.

    Only four primitives are needed: write a line, read a line, read exactly
    N bytes, and flush.  Everything else in this file is built on those.
    """

    LF = b"\n"

    def __init__(self, port: str, baud: int = 19200, flow: str = "hard",
                 timeout: float = 5.0):
        if serial is None:
            raise ScopeError("pyserial is not installed:  pip install pyserial")
        self.port = port
        self._ser = serial.Serial(
            port=port,
            baudrate=baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=timeout,
            write_timeout=timeout,
            rtscts=(flow == "hard"),      # scope default is Hard Flagging
            xonxoff=(flow == "soft"),
        )
        # Give the module a moment; some USB-serial adapters drop the first
        # bytes right after the port opens.
        time.sleep(0.2)
        self.flush()

    # ---- primitives -------------------------------------------------------

    def flush(self) -> None:
        self._ser.reset_input_buffer()
        self._ser.reset_output_buffer()

    def write(self, cmd: str) -> None:
        self._ser.write(cmd.encode() + self.LF)

    def read_line(self) -> str:
        raw = self._ser.read_until(self.LF)
        if not raw:
            raise ScopeError("timeout: no reply from the scope")
        return raw.decode(errors="replace").strip()

    def read_exact(self, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = self._ser.read(n - len(buf))
            if not chunk:
                raise ScopeError(
                    f"timeout after {len(buf)} of {n} expected bytes"
                )
            buf += chunk
        return bytes(buf)

    def query(self, cmd: str, retries: int = 1) -> str:
        """Ask a question, get one line back.

        One retry by default: the first query after a burst of setup commands
        reliably times out on this scope/adapter pair - the TDS2CM module is
        still chewing on the previous writes and simply does not answer.
        """
        last = None
        for attempt in range(retries + 1):
            self.flush()
            self.write(cmd)
            try:
                text = self.read_line()
            except ScopeError as exc:
                last = exc
                time.sleep(0.25)
                continue
            if text.startswith("?"):
                raise ScopeError(f"scope rejected {cmd!r}: {text!r}")
            return text
        raise ScopeError(f"no reply to {cmd!r} after {retries + 1} tries ({last})")

    def close(self) -> None:
        try:
            self._ser.close()
        except Exception:
            pass

    # ---- identity ---------------------------------------------------------

    def identify(self) -> str:
        """ID? first (Tek native), *IDN? as a fallback."""
        for cmd in ("ID?", "*IDN?"):
            try:
                text = self.query(cmd)
            except ScopeError:
                continue
            if "TEK" in text.upper() or "TDS" in text.upper():
                return text
        raise ScopeError("no Tektronix identification string on this port")

    # ---- acquisition ------------------------------------------------------

    def configure(self, channel: str = "CH1", width: int = 1,
                  start: int = 1, stop: int = 2500,
                  average: int | None = None) -> None:
        """Set up the waveform transfer.

        HEADER ON is deliberate.  With headers off the scope answers WFMPRE?
        with bare values in a fixed order and you are trusting the firmware
        to keep that order; with headers on every value arrives labelled, so
        the parser can never silently line up the wrong number.

        width=1 -> one byte per sample (8-bit).  That is the scope's real
        vertical resolution, so width=2 buys nothing but doubles the transfer
        time on a 19200-baud link.
        """
        if average and average > 1:
            self.write("ACQUIRE:MODE AVERAGE")
            self.write(f"ACQUIRE:NUMAVG {average}")
        elif average == 1:
            self.write("ACQUIRE:MODE SAMPLE")
        if average is not None:
            self.write("ACQUIRE:STATE RUN")
            time.sleep(0.2)

        for cmd in (
            "HEADER ON",
            "VERBOSE OFF",
            f"DATA:SOURCE {channel}",
            "DATA:ENCDG RIBINARY",     # signed binary, MSB-first
            f"DATA:WIDTH {width}",
            f"DATA:START {start}",
            f"DATA:STOP {stop}",
        ):
            self.write(cmd)
            time.sleep(0.08)
        time.sleep(0.3)                # let the module settle before we ask

    def preamble(self, debug: bool = False) -> dict:
        """WFMPRE? -> dict of the scaling constants."""
        text = self.query("WFMPRE?")
        if debug:
            print(f"    [raw WFMPRE?] {text}")
        pre = parse_preamble(text)
        if "YMULT" not in pre or "XINCR" not in pre:
            raise ScopeError(
                f"preamble has no YMULT/XINCR - got keys {sorted(pre)} "
                f"from {text[:200]!r}"
            )
        return pre

    def curve(self, n_bytes: int) -> bytes:
        """CURVE? -> the raw ADC payload, unwrapped from its #NDDD header."""
        self.flush()
        self.write("CURVE?")

        # With HEADER ON the reply starts ':CURVE #42500<binary>', so skip
        # everything up to the '#' that opens the IEEE-488.2 block.
        seen = bytearray()
        for _ in range(80):
            b = self.read_exact(1)
            seen += b
            if b == b"#":
                break
        else:
            raise ScopeError(f"CURVE? header never showed a '#': {bytes(seen)!r}")
        head = b"#" + self.read_exact(1)
        n_digits = head[1] - ord("0")
        if not 1 <= n_digits <= 9:
            raise ScopeError(f"bad IEEE-488.2 digit count: {head!r}")
        payload_len = int(self.read_exact(n_digits))
        if payload_len <= 0 or payload_len > 4 * n_bytes + 64:
            raise ScopeError(f"implausible CURVE? length {payload_len}")
        payload = self.read_exact(payload_len)
        self._ser.read(1)               # trailing LF, best effort
        return payload

    def immediate_pwidth(self, channel: str = "CH1") -> float:
        """The scope's own positive-pulse-width measurement, in seconds.

        Tektronix defines Pos Width as the time between the rising and
        falling edge at the 50 % level - i.e. the FWHM of a single positive
        pulse.  Free cross-check on our own arithmetic.
        """
        self.write(f"MEASUREMENT:IMMED:SOURCE {channel}")
        self.write("MEASUREMENT:IMMED:TYPE PWIDTH")
        time.sleep(0.15)
        text = strip_scpi_header(self.query("MEASUREMENT:IMMED:VALUE?"))
        try:
            val = float(text)
        except ValueError:
            raise ScopeError(f"PWIDTH value not numeric: {text!r}")
        # The scope returns ~9.9e37 when the measurement is not available.
        if abs(val) > 1e30:
            raise ScopeError("scope reports PWIDTH unavailable (clipping?)")
        return val


def find_scope(baud: int, flow: str, timeout: float, verbose: bool = True):
    """Open every COM port in turn and keep the one that says TEK."""
    if list_ports is None:
        raise ScopeError("pyserial is not installed:  pip install pyserial")
    ports = list(list_ports.comports())
    if not ports:
        raise ScopeError("no serial ports found - is the USB adapter plugged in?")
    for info in ports:
        if verbose:
            print(f"  probing {info.device:<8} ({info.description}) ... ",
                  end="", flush=True)
        try:
            scope = Tds(info.device, baud=baud, flow=flow, timeout=min(timeout, 2.0))
        except Exception as exc:
            if verbose:
                print(f"cannot open ({exc})")
            continue
        try:
            ident = scope.identify()
        except Exception as exc:
            if verbose:
                print(f"no ({exc})")
            scope.close()
            continue
        if verbose:
            print(f"YES -> {ident}")
        scope.close()
        return Tds(info.device, baud=baud, flow=flow, timeout=timeout), ident
    raise ScopeError(
        "no scope answered on any port.  Check: TDS2CMA module seated, "
        "baud matches UTILITY->RS232 Setup, Flow control = Hard Flagging, "
        "EOL String = LF, and that the cable is a null-modem/straight type "
        "the adapter likes."
    )


# ===========================================================================
# SECTION 2 - turning bytes into a profile
# ===========================================================================

def samples_to_volts(payload: bytes, pre: dict) -> list[float]:
    """raw ADC counts -> volts, using the preamble's scaling numbers."""
    byt_nr = int(pre.get("BYT_NR", 1))
    signed = str(pre.get("BN_FMT", "RI")).upper() == "RI"
    msb_first = str(pre.get("BYT_OR", "MSB")).upper() == "MSB"
    ymult = float(pre.get("YMULT", 1.0))
    yoff = float(pre.get("YOFF", 0.0))
    yzero = float(pre.get("YZERO", 0.0))

    if byt_nr == 1:
        fmt = f"{len(payload)}{'b' if signed else 'B'}"
        counts = struct.unpack(fmt, payload)
    elif byt_nr == 2:
        n = len(payload) // 2
        fmt = f"{'>' if msb_first else '<'}{n}{'h' if signed else 'H'}"
        counts = struct.unpack(fmt, payload[: 2 * n])
    else:
        raise ScopeError(f"unsupported BYT_NR={byt_nr}")

    return [(c - yoff) * ymult + yzero for c in counts]


def time_axis(n: int, pre: dict) -> list[float]:
    xincr = float(pre.get("XINCR", 1.0))
    xzero = float(pre.get("XZERO", 0.0))
    return [xzero + i * xincr for i in range(n)]


# ---------------------------------------------------------------------------
# Profile analysis
#
# WHY THIS IS MULTI-PEAK
# ----------------------
# One channel shows TWO beam peaks per axis, because the sweep crosses the
# aperture twice per period - once going out, once coming back.  Both peaks
# are the same beam, so:
#
#   * each peak gets its OWN half-maximum measurement (its own peak height,
#     its own two crossings), never a shared one;
#   * the crossing search for a peak is fenced in by the valleys either side
#     of it, so it can never wander into its neighbour;
#   * the Gaussian fit is a SUM of N Gaussians.  Fitting one Gaussian across
#     two peaks is what produced r2 = 0.14 - the model was wrong, not the
#     data;
#   * the two FWHMs should agree.  Their spread is a free quality metric: if
#     they disagree by more than a few percent, something is off (sweep not
#     symmetric, one peak clipped, wrong timebase).
# ---------------------------------------------------------------------------

MAX_PEAKS = 4          # CSV columns are laid out for this many


def _median(vals):
    s = sorted(vals)
    m = len(s) // 2
    return float(s[m]) if len(s) % 2 else (s[m - 1] + s[m]) / 2.0


def moving_average(volts: list[float], window: int) -> list[float]:
    """Boxcar smoother.  Rejects single-sample noise spikes that would
    otherwise be picked as 'the peak'.

    A boxcar of width w broadens a true Gaussian only slightly - the widths
    add in quadrature - so keep w well under the FWHM you expect.
    """
    if window <= 1:
        return list(volts)
    n = len(volts)
    half = window // 2
    out = []
    for i in range(n):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        out.append(sum(volts[lo:hi]) / (hi - lo))
    return out


def upper_envelope(volts: list[float], ripple_samples: int) -> list[float]:
    """Envelope through the tops of a RASTERED burst.

    A rastered beam is chopped by the fast raster axis into a train of
    teeth; the beam width lives in their envelope, and half maximum on the
    raw trace measures one tooth instead.  Block maxima plus interpolation,
    not a rolling maximum - a rolling max widens every feature by the window
    and reads the FWHM high by about that much.
    """
    n = len(volts)
    if ripple_samples <= 1 or n < 4:
        return list(volts)
    block = max(2, int(ripple_samples))
    nodes = []
    for start in range(0, n, block):
        stop = min(n, start + block)
        best = max(range(start, stop), key=lambda i: volts[i])
        nodes.append((best, volts[best]))
    if len(nodes) < 2:
        return list(volts)
    out = [0.0] * n
    for i in range(nodes[0][0] + 1):
        out[i] = nodes[0][1]
    for i in range(nodes[-1][0], n):
        out[i] = nodes[-1][1]
    for (i0, v0), (i1, v1) in zip(nodes, nodes[1:]):
        span = i1 - i0
        if span <= 0:
            continue
        for i in range(i0, i1 + 1):
            out[i] = v0 + (v1 - v0) * (i - i0) / span
    return out


def detect_clipping(volts: list[float], tolerance: float = 0.02) -> dict:
    """Flag a trace that ran off the top or bottom of the screen."""
    n = len(volts)
    if not n:
        return {"low": False, "high": False, "low_fraction": 0.0,
                "high_fraction": 0.0}
    lo, hi = min(volts), max(volts)
    n_lo = sum(1 for v in volts if v == lo)
    n_hi = sum(1 for v in volts if v == hi)
    return {"low": n_lo / n > tolerance, "high": n_hi / n > tolerance,
            "low_fraction": n_lo / n, "high_fraction": n_hi / n}


def _crossing(v, peak_i: int, half: float, lo: int, hi: int, direction: int):
    """Interpolated index where v crosses *half*, walking from the peak.

    Confined to [lo, hi] - the valleys either side of this peak.  Returns
    None if the trace never gets down to half maximum inside that fence,
    which means the two peaks are not resolved at half height.
    """
    i = peak_i
    while lo <= i + direction <= hi:
        j = i + direction
        if (v[i] >= half >= v[j]) or (v[i] <= half <= v[j]):
            if v[i] == v[j]:
                return float(i)
            frac = (half - v[i]) / (v[j] - v[i])      # 0..1 between i and j
            return i + direction * frac
        i = j
    return None


def find_peaks(v: list[float], threshold: float, min_sep: int,
               max_peaks: int) -> list[int]:
    """Indices of the tallest local maxima above *threshold*, left to right."""
    n = len(v)
    cands = [i for i in range(1, n - 1)
             if v[i] >= v[i - 1] and v[i] >= v[i + 1] and v[i] >= threshold]
    if not cands and v:
        cands = [max(range(n), key=lambda i: v[i])]

    # Tallest first, then drop anything too close to an already-kept peak.
    kept: list[int] = []
    for i in sorted(cands, key=lambda i: v[i], reverse=True):
        if all(abs(i - k) >= min_sep for k in kept):
            kept.append(i)
        if len(kept) >= max_peaks:
            break
    return sorted(kept)


def analyse(volts: list[float], xincr: float, *, polarity: str = "auto",
            smooth: int = 1, max_peaks: int = 2, peak_threshold: float = 0.30,
            min_sep_frac: float = 0.02, baseline_frac: float = 0.10,
            envelope_samples: int = 0, quantum: float = 0.0,
            axis_labels: tuple = ("X", "Y")) -> dict:
    """Measure every beam peak in the trace.

    Raises ValueError when there is nothing measurable at all - a flat or
    all-noise trace should say so loudly, not quietly return a number.
    """
    n = len(volts)
    if n < 16:
        raise ValueError(f"only {n} samples")

    raw = list(volts)
    clipping = detect_clipping(raw)
    work = moving_average(raw, smooth)
    if envelope_samples > 1:
        work = upper_envelope(work, envelope_samples)

    # 1. baseline = median of the outer 10 % (5 % each end)
    wing = max(1, int(n * baseline_frac / 2))
    baseline = _median(work[:wing] + work[-wing:])
    v = [x - baseline for x in work]

    # 2. polarity - a negative-going cup signal is flipped so everything
    #    downstream only ever deals with positive bumps
    if polarity == "neg":
        flip = True
    elif polarity == "pos":
        flip = False
    else:
        flip = abs(min(v)) > abs(max(v))
    if flip:
        v = [-x for x in v]

    # Robust sigma: median absolute deviation of the quiet edges, scaled by
    # 1.4826 so it means the same as a standard deviation for Gaussian
    # noise.  A 3x bar on the plain median of |edge| lets pure noise through
    # - the largest of 2500 Gaussian samples sits near 3.5 sigma - so the
    # gate is 5 sigma.  (Same metric as the app's profile_fwhm.py, so the
    # two report the same S/N.)
    edge = v[:wing] + v[-wing:]
    edge_med = _median(edge)
    noise = 1.4826 * _median([abs(x - edge_med) for x in edge])
    # Floor at half an ADC count: a quiet stretch sitting on one code has
    # zero apparent noise, and S/N then runs to 1e12.
    noise = max(noise, 0.5 * quantum, 1e-12)
    tallest = max(v)
    if tallest <= 0:
        raise ValueError("no positive peak after baseline subtraction")
    if tallest < 5 * noise:
        raise ValueError(f"tallest peak {tallest:.3g} V is under 5x the edge "
                         f"noise sigma ({noise:.3g} V) - nothing to measure")

    # 3. locate the peaks
    threshold = max(peak_threshold * tallest, 5 * noise)
    min_sep = max(3, int(n * min_sep_frac))
    idx = find_peaks(v, threshold, min_sep, max_peaks)

    # 4. valleys between neighbours become each peak's fence
    fences = []
    for k, i in enumerate(idx):
        lo = 0 if k == 0 else min(range(idx[k - 1], i),
                                  key=lambda j: v[j])
        hi = n - 1 if k == len(idx) - 1 else min(range(i, idx[k + 1]),
                                                 key=lambda j: v[j])
        fences.append((lo, hi))

    peaks = []
    for k, (i, (lo, hi)) in enumerate(zip(idx, fences)):
        half = v[i] / 2.0
        left = _crossing(v, i, half, lo, hi, -1)
        right = _crossing(v, i, half, lo, hi, +1)
        entry = {
            "axis": (axis_labels[k] if k < len(axis_labels) else f"#{k + 1}"),
            "index": i,
            "time_index": float(i),
            "volts": v[i],
            "half_volts": half,
            "left_index": left,
            "right_index": right,
            "fence": (lo, hi),
            "resolved": left is not None and right is not None,
            "note": "",
        }
        if entry["resolved"]:
            width = right - left
            if width <= 0:
                entry["resolved"] = False
                entry["note"] = "degenerate width"
            else:
                entry["fwhm_samples"] = width
                entry["fwhm_seconds"] = width * xincr
                entry["centre_index"] = (left + right) / 2.0
        if not entry["resolved"]:
            entry.setdefault("fwhm_samples", float("nan"))
            entry.setdefault("fwhm_seconds", float("nan"))
            entry.setdefault("centre_index", float(i))
            if not entry["note"]:
                side = "left" if left is None else "right"
                entry["note"] = (f"never falls to half maximum on the {side} "
                                 f"before the neighbouring peak")
        peaks.append(entry)

    good = [p["fwhm_seconds"] for p in peaks if p["resolved"]]
    if not peaks:
        raise ValueError("no peaks found above the threshold")

    # Note: peaks that never come back down to half maximum are reported as
    # unresolved rather than raising, because the Gaussian fit can still
    # measure them - overlapping peaks are a modelling problem, not a
    # missing-signal problem.
    mean_fwhm = sum(good) / len(good) if good else float("nan")
    spread = ((max(good) - min(good)) / mean_fwhm
              if len(good) > 1 else (0.0 if good else float("nan")))
    separations = [(peaks[k + 1]["centre_index"] - peaks[k]["centre_index"]) * xincr
                   for k in range(len(peaks) - 1)]

    return {
        "baseline": baseline,
        "flipped": flip,
        "smooth": smooth,
        "noise": noise,
        "tallest_volts": tallest,
        "signal_to_noise": tallest / noise,
        "corrected": v,          # smoothed, baseline-subtracted, sign-fixed
        "raw": raw,              # untouched, for the plot underlay
        "peaks": peaks,
        "n_peaks": len(peaks),
        "n_resolved": len(good),
        "mean_fwhm_seconds": mean_fwhm,
        "fwhm_spread": spread,   # 0.03 means the two peaks differ by 3 %
        "separations_seconds": separations,
        "envelope_samples": envelope_samples,
        "clipping": clipping,
    }


def gaussian_fit(result: dict, xincr: float) -> dict | None:
    """Least-squares fit of a SUM of Gaussians, one per detected peak.

    The half-max method only ever looks at four samples per peak, so noise
    moves it around.  This uses every sample.  Trust it when r2 is close to
    1; if r2 is poor the model is wrong (missed a peak, or the profile is
    not Gaussian), not the data.
    """
    try:
        from scipy.optimize import curve_fit
    except ImportError:
        return None
    if np is None:
        return None

    y = np.asarray(result["corrected"], dtype=float)
    x = np.arange(y.size, dtype=float)
    peaks = result["peaks"]
    k = len(peaks)

    def model(xx, *p):
        out = np.full_like(xx, p[-1])
        for m in range(k):
            a, mu, sigma = p[3 * m: 3 * m + 3]
            out = out + a * np.exp(-0.5 * ((xx - mu) / sigma) ** 2)
        return out

    p0, lo, hi = [], [], []
    for pk in peaks:
        w = pk["fwhm_samples"]
        sigma0 = (w / 2.3548) if (w == w and w > 0) else max(y.size / 20.0, 2.0)
        p0 += [pk["volts"], pk["centre_index"], sigma0]
        lo += [0.0, 0.0, 0.5]
        hi += [np.inf, float(y.size), float(y.size)]
    p0 += [0.0]
    lo += [-np.inf]
    hi += [np.inf]

    try:
        popt, _ = curve_fit(model, x, y, p0=p0, bounds=(lo, hi), maxfev=20000)
    except Exception:
        return None

    pred = model(x, *popt)
    ss_res = float(np.sum((y - pred) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")

    per_peak = []
    for m in range(k):
        a, mu, sigma = popt[3 * m: 3 * m + 3]
        fw = 2.0 * math.sqrt(2.0 * math.log(2.0)) * abs(sigma)
        per_peak.append({
            "amplitude": float(a),
            "centre_index": float(mu),
            "sigma_samples": float(abs(sigma)),
            "fwhm_samples": fw,
            "fwhm_seconds": fw * xincr,
        })
    widths = [p["fwhm_seconds"] for p in per_peak]
    return {
        "peaks": per_peak,
        "offset": float(popt[-1]),
        "r_squared": r2,
        "curve": pred.tolist(),
        "mean_fwhm_seconds": sum(widths) / len(widths) if widths else float("nan"),
    }


def eng(value, unit: str = "s") -> str:
    """Format a number the way the scope's own readout would."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "   --   "
    a = abs(value)
    for scale, prefix in ((1e-9, "n"), (1e-6, "u"), (1e-3, "m"), (1.0, "")):
        if a < scale * 1000:
            return f"{value / scale:7.3f} {prefix}{unit}"
    return f"{value:.4g} {unit}"


# ===========================================================================
# SECTION 3 - simulation, so the tool can be proven without beam
# ===========================================================================

def simulated_capture(seed_counter: int = 0, n_peaks: int = 2):
    """Fake trace: *n_peaks* Gaussian bumps, as the sweep crossing the
    aperture on the way out and on the way back."""
    import random
    rng = random.Random(1234 + seed_counter)
    n = 2500
    xincr = 2e-5                       # 50 ms record, like 5 ms/div
    sigma = 30 + rng.uniform(-2, 2)
    amp = 0.22
    centres = ([n * 0.32 + rng.uniform(-15, 15), n * 0.68 + rng.uniform(-15, 15)]
               if n_peaks == 2 else [n / 2 + rng.uniform(-40, 40)])
    volts = []
    for i in range(n):
        y = 0.03
        for c in centres:
            y += amp * math.exp(-0.5 * ((i - c) / sigma) ** 2)
        volts.append(y + rng.gauss(0, 0.018))     # S/N ~ 12, like the beam
    pre = {
        "BYT_NR": 1, "BIT_NR": 8, "ENCDG": "BIN", "BN_FMT": "RI",
        "BYT_OR": "MSB", "NR_PT": n, "YMULT": 4e-3, "YOFF": 0.0,
        "YZERO": 0.0, "XINCR": xincr, "XZERO": -n * xincr / 2,
        "WFID": "SIMULATED", "_TRUE_FWHM_S": 2.3548 * sigma * xincr,
    }
    return volts, pre


# ===========================================================================
# SECTION 4 - the run loop
# ===========================================================================

def mm_per_second(args):
    """Sweep speed that converts seconds of trace into mm of beam width."""
    if args.mm_per_s:
        return args.mm_per_s
    if args.sweep_hz and args.sweep_mm_pp:
        # A triangle wave crosses the full peak-to-peak span twice per
        # period, so one traverse takes 1/(2f) seconds.
        return 2.0 * args.sweep_mm_pp * args.sweep_hz
    return None


def one_shot(scope, args, counter: int):
    """Acquire once -> (volts, preamble, result, fit, scope_pwidth)."""
    if args.simulate:
        volts, pre = simulated_capture(counter, n_peaks=max(1, args.peaks))
    else:
        pre = scope.preamble(debug=args.debug)
        n_bytes = int(pre.get("NR_PT", 2500)) * int(pre.get("BYT_NR", 1))
        payload = scope.curve(n_bytes)
        volts = samples_to_volts(payload, pre)

    xincr = float(pre.get("XINCR", 1.0))
    result = analyse(volts, xincr, polarity=args.polarity, smooth=args.smooth,
                     max_peaks=args.peaks, peak_threshold=args.peak_threshold)
    fit = None if args.no_fit else gaussian_fit(result, xincr)

    pwidth = float("nan")
    if not args.simulate and not args.no_scope_measure:
        try:
            pwidth = scope.immediate_pwidth(args.channel)
        except ScopeError:
            pass
    return volts, pre, result, fit, pwidth


def format_line(result, fit, pwidth, mmps) -> str:
    widths = "  ".join(
        f"{p.get('axis', '?')} " + (eng(p["fwhm_seconds"]).strip()
                                    if p["resolved"] else "unresolved")
        for p in result["peaks"]
    )
    parts = [f"FWHM {widths}"]
    if result.get("envelope_samples", 0) > 1:
        parts.append(f"env {result['envelope_samples']}sa")
    if result["n_resolved"] > 1:
        # The peaks are X and Y - different directions - so their ratio is
        # the beam's ASPECT, not a disagreement to worry about.
        widths_ok = [p["fwhm_seconds"] for p in result["peaks"] if p["resolved"]]
        parts.append(f"X/Y {widths_ok[0] / widths_ok[1]:.2f}")
    if result["separations_seconds"]:
        parts.append("sep " + " ".join(eng(s).strip()
                                       for s in result["separations_seconds"]))
    if fit:
        parts.append(f"fit {eng(fit['mean_fwhm_seconds']).strip()} "
                     f"(r2 {fit['r_squared']:.3f})")
    if not math.isnan(pwidth):
        parts.append(f"PWIDTH {eng(pwidth).strip()}")
    parts.append(f"S/N {result['signal_to_noise']:.0f}")
    clip = result.get("clipping", {})
    if clip.get("low") or clip.get("high"):
        where = "bottom" if clip.get("low") else "top"
        parts.append(f"CLIPPED at the {where}")
    if mmps and not math.isnan(result["mean_fwhm_seconds"]):
        parts.append(f"= {result['mean_fwhm_seconds'] * mmps:.2f} mm")
    elif mmps and fit:
        parts.append(f"= {fit['mean_fwhm_seconds'] * mmps:.2f} mm (fit)")
    return " | ".join(parts)


def csv_header() -> list[str]:
    cols = ["iso_time", "unix_time", "channel", "n_peaks", "n_resolved"]
    cols += [f"fwhm_s_{k + 1}" for k in range(MAX_PEAKS)]
    cols += ["fwhm_mean_s", "fwhm_spread"]
    cols += [f"fit_fwhm_s_{k + 1}" for k in range(MAX_PEAKS)]
    cols += ["fit_r2", "scope_pwidth_s"]
    cols += [f"sep_s_{k + 1}" for k in range(MAX_PEAKS - 1)]
    cols += [f"fwhm_mm_{k + 1}" for k in range(MAX_PEAKS)]
    cols += ["peak_v", "baseline_v", "snr", "xincr_s"]
    return cols


def csv_row(t0, args, result, fit, pwidth, mmps, xincr) -> list:
    def pad(values, k=MAX_PEAKS):
        out = list(values[:k]) + [""] * max(0, k - len(values))
        return out

    fw = [f"{p['fwhm_seconds']:.9g}" if p["resolved"] else ""
          for p in result["peaks"]]
    fitw = [f"{p['fwhm_seconds']:.9g}" for p in fit["peaks"]] if fit else []
    mm = [f"{p['fwhm_seconds'] * mmps:.6g}" if (mmps and p["resolved"]) else ""
          for p in result["peaks"]]
    sep = [f"{s:.9g}" for s in result["separations_seconds"]]

    row = [time.strftime("%Y-%m-%dT%H:%M:%S"), f"{t0:.3f}", args.channel,
           result["n_peaks"], result["n_resolved"]]
    row += pad(fw)
    mean_s = result["mean_fwhm_seconds"]
    spread = result["fwhm_spread"]
    row += ["" if math.isnan(mean_s) else f"{mean_s:.9g}",
            "" if math.isnan(spread) else f"{spread:.5f}"]
    row += pad(fitw)
    row += [f"{fit['r_squared']:.5f}" if fit else "",
            "" if math.isnan(pwidth) else f"{pwidth:.9g}"]
    row += pad(sep, MAX_PEAKS - 1)
    row += pad(mm)
    row += [f"{result['tallest_volts']:.6g}", f"{result['baseline']:.6g}",
            f"{result['signal_to_noise']:.3f}", f"{xincr:.9g}"]
    return row


def draw(ax, volts, pre, result, fit, args, mmps):
    """Live plot: raw trace, smoothed trace, fit, and one labelled
    half-max span per peak."""
    xincr = float(pre.get("XINCR", 1.0))
    xzero = float(pre.get("XZERO", 0.0))
    n = len(volts)
    to_ms = lambda i: (xzero + i * xincr) * 1e3
    xs = [to_ms(i) for i in range(n)]
    corrected = result["corrected"]

    ax.clear()
    sign = -1.0 if result["flipped"] else 1.0
    base = result["baseline"]
    if result["smooth"] > 1:
        ax.plot(xs, [sign * (v - base) for v in result["raw"]],
                lw=0.6, color="0.78", label=f"{args.channel} raw")
        main_label = f"smoothed x{result['smooth']}"
    else:
        main_label = f"{args.channel} (baseline removed)"
    ax.plot(xs, corrected, lw=1.0, color="#1f77b4", label=main_label)
    if fit:
        ax.plot(xs, fit["curve"], lw=1.2, ls="--", color="#ff7f0e",
                label=f"{len(fit['peaks'])}-Gaussian fit  r2={fit['r_squared']:.3f}")

    for k, p in enumerate(result["peaks"]):
        if not p["resolved"]:
            ax.axvline(to_ms(p["index"]), lw=0.8, ls=":", color="crimson")
            ax.text(to_ms(p["index"]), p["volts"], f" peak {k + 1}\n unresolved",
                    fontsize=8, color="crimson", va="bottom")
            continue
        half = p["half_volts"]
        lx, rx = to_ms(p["left_index"]), to_ms(p["right_index"])
        ax.annotate("", xy=(lx, half), xytext=(rx, half),
                    arrowprops=dict(arrowstyle="<->", lw=1.3))
        label = eng(p["fwhm_seconds"]).strip()
        if mmps:
            label += f"\n{p['fwhm_seconds'] * mmps:.2f} mm"
        ax.text((lx + rx) / 2, half + 0.06 * result["tallest_volts"], label,
                ha="center", va="bottom", fontsize=9)

    ax.set_ylim(min(corrected) - 0.08 * result["tallest_volts"],
                result["tallest_volts"] * 1.30)
    ax.set_xlabel("time (ms)")
    ax.set_ylabel("volts")
    title = f"TDS 2012 beam profile - {result['n_peaks']} peaks"
    if result["n_resolved"] > 1 and not math.isnan(result["mean_fwhm_seconds"]):
        title += (f"  |  mean FWHM {eng(result['mean_fwhm_seconds']).strip()}"
                  f"  spread {result['fwhm_spread'] * 100:.1f}%")
    ax.set_title(title + ("  [SIMULATED]" if args.simulate else ""))
    ax.legend(loc="upper right", fontsize=9)
    ax.grid(alpha=0.25)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Read beam-profile FWHM off a Tektronix TDS 2012.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--list", action="store_true", help="list serial ports and exit")
    p.add_argument("--port", help="COM port (default: probe every port)")
    p.add_argument("--baud", type=int, default=19200,
                   help="must match UTILITY->RS232 Setup (default 19200)")
    p.add_argument("--flow", choices=["hard", "soft", "none"], default="hard",
                   help="flow control; scope default is Hard Flagging")
    p.add_argument("--timeout", type=float, default=5.0)
    p.add_argument("--channel", default="CH1")
    p.add_argument("--points", type=int, default=2500,
                   help="samples to transfer, 1..2500. This CROPS the record "
                        "- DATA:START/STOP take a contiguous slice, they do "
                        "not decimate - so 1000 points is 40 %% of the time "
                        "window at full resolution, not a coarser view of all "
                        "of it")
    p.add_argument("--points-anchor", choices=["centre", "start"],
                   default="centre",
                   help="which part of the record a shortened transfer keeps")
    p.add_argument("--peaks", type=int, default=2, metavar="N",
                   help="how many beam peaks per trace (default 2 - the sweep "
                        "crosses the aperture on the way out and back)")
    p.add_argument("--peak-threshold", type=float, default=0.30, metavar="F",
                   help="ignore peaks shorter than F x the tallest (default 0.30)")
    p.add_argument("--polarity", choices=["auto", "pos", "neg"], default="auto",
                   help="peak direction; auto picks the larger excursion")
    p.add_argument("--envelope-ms", type=float, default=0.0, metavar="MS",
                   help="raster period in ms. Set this when the beam is "
                        "RASTERED: the analysis then measures the envelope of "
                        "the raster teeth instead of one tooth. 0 = off")
    p.add_argument("--smooth", type=int, default=1, metavar="N",
                   help="boxcar-average N samples before measuring; try 9-25 "
                        "on a noisy trace (default 1 = off)")
    p.add_argument("--avg", type=int, metavar="N",
                   help="put the SCOPE in average mode over N sweeps "
                        "(4/16/64/128); N=1 restores Sample mode")
    p.add_argument("--interval", type=float, default=1.0,
                   help="seconds between acquisitions")
    p.add_argument("--once", action="store_true", help="single acquisition")
    p.add_argument("--plot", action="store_true", help="live matplotlib window")
    p.add_argument("--csv", help="append one row per acquisition here")
    p.add_argument("--save-trace", help="write the LAST full trace to this CSV")
    p.add_argument("--mm-per-s", type=float,
                   help="sweep speed, to convert seconds -> mm")
    p.add_argument("--sweep-hz", type=float,
                   help="triangle sweep frequency (with --sweep-mm-pp)")
    p.add_argument("--sweep-mm-pp", type=float,
                   help="peak-to-peak sweep span in mm (with --sweep-hz)")
    p.add_argument("--no-fit", action="store_true", help="skip the Gaussian fit")
    p.add_argument("--no-scope-measure", action="store_true",
                   help="skip the scope's own PWIDTH cross-check")
    p.add_argument("--simulate", action="store_true",
                   help="fabricate waveforms - no hardware needed")
    p.add_argument("--debug", action="store_true",
                   help="print the raw WFMPRE? reply on every acquisition")
    args = p.parse_args(argv)

    args.peaks = max(1, min(args.peaks, MAX_PEAKS))

    if args.list:
        if list_ports is None:
            print("pyserial is not installed:  pip install pyserial")
            return 2
        found = list(list_ports.comports())
        if not found:
            print("no serial ports found")
        for info in found:
            print(f"{info.device:<8} {info.description}  [{info.hwid}]")
        return 0

    mmps = mm_per_second(args)

    scope = None
    if not args.simulate:
        try:
            if args.port:
                scope = Tds(args.port, baud=args.baud, flow=args.flow,
                            timeout=args.timeout)
                print(f"connected on {args.port}: {scope.identify()}")
            else:
                print("searching for the scope ...")
                scope, ident = find_scope(args.baud, args.flow, args.timeout)
                print(f"connected on {scope.port}: {ident}")
            if args.points >= 2500:
                p_start, p_stop = 1, 2500
            elif args.points_anchor == "start":
                p_start, p_stop = 1, args.points
            else:
                p_start = (2500 - args.points) // 2 + 1
                p_stop = p_start + args.points - 1
            scope.configure(args.channel, width=1, start=p_start, stop=p_stop,
                            average=args.avg)
        except ScopeError as exc:
            print(f"\nCONNECTION FAILED: {exc}", file=sys.stderr)
            if scope:
                scope.close()
            return 1
    else:
        print("SIMULATION MODE - no hardware is being read")

    fig = ax = None
    if args.plot:
        try:
            import matplotlib.pyplot as plt
            plt.ion()
            fig, ax = plt.subplots(figsize=(11, 6))
        except ImportError:
            print("matplotlib not available - continuing without the plot")
            args.plot = False

    csv_file = csv_writer = None
    if args.csv:
        new = not os.path.exists(args.csv)
        csv_file = open(args.csv, "a", newline="")
        csv_writer = csv.writer(csv_file)
        if new:
            csv_writer.writerow(csv_header())

    last = None
    counter = 0
    print("\nCtrl-C to stop.\n")
    try:
        while True:
            t0 = time.time()
            try:
                volts, pre, result, fit, pwidth = one_shot(scope, args, counter)
            except ValueError as exc:
                print(f"[{time.strftime('%H:%M:%S')}]  no measurement: {exc}")
                if args.once:
                    break
                counter += 1
                time.sleep(args.interval)
                continue
            except ScopeError as exc:
                print(f"[{time.strftime('%H:%M:%S')}]  scope error: {exc}")
                if args.once:
                    return 1
                counter += 1
                time.sleep(args.interval)
                continue

            last = (volts, pre, result, fit)
            counter += 1
            print(f"[{time.strftime('%H:%M:%S')}]  " +
                  format_line(result, fit, pwidth, mmps))
            for k, pk in enumerate(result["peaks"]):
                if not pk["resolved"]:
                    print(f"              peak {k + 1}: {pk['note']}")

            if csv_writer:
                csv_writer.writerow(csv_row(t0, args, result, fit, pwidth, mmps,
                                            float(pre.get("XINCR", float("nan")))))
                csv_file.flush()

            if args.plot and ax is not None:
                import matplotlib.pyplot as plt
                draw(ax, volts, pre, result, fit, args, mmps)
                plt.pause(0.001)

            if args.once:
                break
            time.sleep(max(0.0, args.interval - (time.time() - t0)))

    except KeyboardInterrupt:
        print("\nstopped.")
    finally:
        if csv_file:
            csv_file.close()
        if scope:
            scope.close()

    if args.save_trace and last:
        volts, pre, result, fit = last
        xs = time_axis(len(volts), pre)
        with open(args.save_trace, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["# n_peaks", result["n_peaks"],
                        "# mean_fwhm_s", f"{result['mean_fwhm_seconds']:.9g}",
                        "# spread", f"{result['fwhm_spread']:.4g}"])
            for k, pk in enumerate(result["peaks"]):
                w.writerow([f"# peak_{k + 1}_fwhm_s",
                            f"{pk['fwhm_seconds']:.9g}" if pk["resolved"] else "unresolved",
                            "# peak_v", f"{pk['volts']:.6g}"])
            if fit:
                w.writerow(["# fit_r2", f"{fit['r_squared']:.5f}"])
            w.writerow(["time_s", "volts_raw", "volts_corrected"])
            for t, v, c in zip(xs, volts, result["corrected"]):
                w.writerow([f"{t:.9g}", f"{v:.6g}", f"{c:.6g}"])
        print(f"trace written to {args.save_trace}")

    if args.plot and fig is not None:
        import matplotlib.pyplot as plt
        plt.ioff()
        print("close the plot window to exit.")
        plt.show()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
