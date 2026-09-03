"""
funcgen_driver.py
PyVISA driver for RIGOL DG1022Z dual-channel function generators.

Two instruments are distinguished by USB serial number. Never hardcode a
serial number: enumerate at runtime via *IDN? and let the user assign A/B.

Connection: USB-TMC via VISA (IVI/VISA driver already installed on the host).
Resource strings: USB0::0x1AB1::0x0642::<SERIAL>::INSTR
"""
import logging
import re
import time

log = logging.getLogger(__name__)

try:
    import pyvisa
    PYVISA_AVAILABLE = True
except ImportError:
    PYVISA_AVAILABLE = False

# These constants are defined in rbl.config.funcgen_limits and re-exported here
# so existing callers of `from rbl.hardware.funcgen_driver import MAX_GEN_VOLTS`
# continue to work without change.
from rbl.config.funcgen_limits import MAX_AMP_VPP, MAX_GEN_VOLTS  # noqa: E402

# Verified by tools/probe_clock_scpi.py against DG1022Z firmware
# 03.01.12. The plain :ROSCillator:SOURce and :SYSTem:CLKSource forms
# are BOTH rejected with -113 "Undefined header" — the write returns
# cleanly at the pyvisa layer and is then discarded by the
# instrument. Only this hybrid form is accepted.
CLOCK_SCPI = ":SYSTem:ROSCillator:SOURce"

_RIGOL_VENDOR_ID = "0x1AB1"


def discover() -> list:
    """Scan USB-TMC for RIGOL DG1022Z instruments.

    Returns a list of dicts: [{"resource": str, "idn": str, "serial": str}, ...]
    Never raises; returns [] when pyvisa is unavailable or no instruments found.
    """
    if not PYVISA_AVAILABLE:
        return []
    try:
        rm = pyvisa.ResourceManager()
        resources = rm.list_resources()
    except Exception:
        log.exception("discover: ResourceManager / list_resources failed")
        return []

    found = []
    for res in resources:
        if not res.upper().startswith("USB"):
            continue
        try:
            inst = rm.open_resource(res)
            inst.timeout = 3000
            inst.read_termination = "\n"
            inst.write_termination = "\n"
            idn = inst.query("*IDN?").strip()
            inst.close()
            if "DG1022Z" not in idn:
                continue
            # Serial is the 4th field in USB0::6833::1602::<SERIAL>::INSTR
            m = re.search(r"USB\d+::[^:]+::[^:]+::([^:]+)::INSTR", res, re.IGNORECASE)
            serial = m.group(1) if m else res
            found.append({"resource": res, "idn": idn, "serial": serial})
        except Exception:
            log.exception("discover: error querying resource %s", res)
            continue
    return found


class DG1022Z:
    """PyVISA driver for one RIGOL DG1022Z function generator (2 channels).

    Lifecycle:
        gen = DG1022Z("USB0::0x1AB1::0x0642::DG1ZA123456::INSTR")
        gen.set_output_load(1, "INFinity")    # match EEL5000 high-Z input
        gen.set_waveform(1, "Sine", 1000, 1.0, 0.0, 0.0)
        gen.output_on(1)
        gen.close()   # session only; instrument keeps its state

    Safety:
        Offset is clamped to ±MAX_GEN_VOLTS and amplitude (peak-to-peak) to
        ±MAX_AMP_VPP before any SCPI command is issued.  set_waveform() returns
        a warning string when clamping occurs.  The combined "true peak"
        interlock (|offset| + amplitude/2 ≤ MAX_GEN_VOLTS) is enforced by the
        GUI, not here — this per-field clamp is only the coarse backstop.
    """

    def __init__(self, resource: str):
        if not PYVISA_AVAILABLE:
            raise ImportError("pyvisa is not installed; install it with: pip install pyvisa")
        rm = pyvisa.ResourceManager()
        self._inst = rm.open_resource(resource)
        self._inst.timeout = 5000
        self._inst.read_termination = "\n"
        self._inst.write_termination = "\n"
        self._resource = resource
        self._idn = self.query("*IDN?")

    # ---- Generic passthroughs ------------------------------------------------

    def write(self, cmd: str):
        self._write_checked(cmd)

    def query(self, cmd: str) -> str:
        log.debug("QUERY %s", cmd)
        resp = self._inst.query(cmd).strip()
        log.debug("RESP  <- %s", resp)
        return resp

    def _write_checked(self, cmd: str):
        """Write a SCPI command, then poll the instrument error queue.
        The DG1022Z accepts a write at the transport layer and then
        discards it internally if malformed, so a clean pyvisa return
        does NOT mean the command took effect."""
        log.debug("WRITE %s", cmd)
        self._inst.write(cmd)
        err = self._inst.query(":SYSTem:ERRor?").strip()
        log.debug("ERR?  <- %s", err)
        if not (err.startswith("0,") or err.startswith("+0,")):
            raise RuntimeError(f"SCPI error after {cmd!r}: {err}")

    def idn(self) -> str:
        return self._idn

    # ---- Session lifecycle ---------------------------------------------------

    def close(self):
        # Does not send *RST and does not disable outputs; the instrument
        # retains its state after the VISA session is closed and after the
        # app exits.
        try:
            self._inst.close()
        except Exception:
            log.exception("close() failed for %s", self._resource)

    # ---- Per-channel waveform ------------------------------------------------

    def set_waveform(self, channel: int, shape: str, freq_hz: float,
                     amp_vpp: float, offset_v: float, phase_deg: float) -> str:
        """Push waveform parameters for one channel.

        Clamps offset_v to ±MAX_GEN_VOLTS and amp_vpp to ±MAX_AMP_VPP BEFORE
        sending any command.  Returns a non-empty warning string when clamping
        occurred, otherwise returns "".

        Shape mapping:
          "Sine"     -> APPLy:SINusoid
          "Triangle" -> APPLy:RAMP + RAMP:SYMMetry 50
                        RIGOL has no TRIANGLE keyword; a 50%-symmetry RAMP
                        is mathematically a triangle wave.
          "Square"   -> APPLy:SQUare
          "Pulse"    -> APPLy:PULSe
          "DC"       -> APPLy:DC 1,1,{offset_v}
                        In DC mode offset_v IS the held voltage; freq and amp
                        are ignored by the instrument but the SCPI syntax still
                        requires two placeholder arguments before the voltage.
        """
        ch = int(channel)
        warnings = []

        orig_amp = amp_vpp
        orig_off = offset_v
        amp_vpp  = max(-MAX_AMP_VPP, min(MAX_AMP_VPP, amp_vpp))
        offset_v = max(-MAX_GEN_VOLTS, min(MAX_GEN_VOLTS, offset_v))
        if amp_vpp != orig_amp:
            warnings.append(f"clamped amplitude {orig_amp:.4g}->{amp_vpp:.4g} V")
        if offset_v != orig_off:
            warnings.append(f"clamped offset {orig_off:.4g}->{offset_v:.4g} V")

        if shape == "Sine":
            self._write_checked(
                f":SOURce{ch}:APPLy:SINusoid {freq_hz},{amp_vpp},{offset_v},{phase_deg}"
            )
        elif shape == "Triangle":
            # RIGOL has no TRIANGLE keyword; 50%-symmetry RAMP is equivalent.
            self._write_checked(
                f":SOURce{ch}:APPLy:RAMP {freq_hz},{amp_vpp},{offset_v},{phase_deg}"
            )
            self._write_checked(f":SOURce{ch}:FUNCtion:RAMP:SYMMetry 50")
        elif shape == "Square":
            self._write_checked(
                f":SOURce{ch}:APPLy:SQUare {freq_hz},{amp_vpp},{offset_v},{phase_deg}"
            )
        elif shape == "Pulse":
            self._write_checked(
                f":SOURce{ch}:APPLy:PULSe {freq_hz},{amp_vpp},{offset_v},{phase_deg}"
            )
        elif shape == "DC":
            # DC mode: offset_v is the held voltage.  The first two positional
            # arguments are required by SCPI syntax but are ignored by the
            # instrument in DC mode.
            self._write_checked(f":SOURce{ch}:APPLy:DC 1,1,{offset_v}")
        else:
            raise ValueError(f"Unknown waveform shape: {shape!r}")

        return ", ".join(warnings)

    def set_amplitude(self, channel: int, vpp: float) -> str:
        """Change amplitude in place WITHOUT restarting the phase generator.

        `:SOURce{ch}:VOLTage` scales the DAC output. Unlike `:APPLy:` it does
        not reconfigure the channel or reset the phase generator, so it is
        safe to call repeatedly on a running waveform — the ramp engine
        (`rbl/services/ramp_engine.py`) exists entirely because `set_waveform`
        cannot be called mid-ramp without producing exactly the discontinuity
        a ramp is meant to avoid. Using `:APPLy:` for this is the single most
        likely way to get that feature wrong.

        Clamped to ±MAX_AMP_VPP exactly as `set_waveform` clamps amplitude —
        the coarse backstop must not be bypassed by this path. Returns a
        warning string on clamp, "" otherwise.
        """
        ch = int(channel)
        orig = vpp
        vpp = max(-MAX_AMP_VPP, min(MAX_AMP_VPP, vpp))
        self._write_checked(f":SOURce{ch}:VOLTage {vpp}")
        return f"clamped amplitude {orig:.4g}->{vpp:.4g} V" if vpp != orig else ""

    def set_offset(self, channel: int, volts: float) -> str:
        """Change DC offset in place WITHOUT restarting the phase generator.

        See `set_amplitude` — same `:APPLy:`-avoidance reasoning, same clamp
        discipline, this time against ±MAX_GEN_VOLTS as `set_waveform` does.
        """
        ch = int(channel)
        orig = volts
        volts = max(-MAX_GEN_VOLTS, min(MAX_GEN_VOLTS, volts))
        self._write_checked(f":SOURce{ch}:VOLTage:OFFSet {volts}")
        return f"clamped offset {orig:.4g}->{volts:.4g} V" if volts != orig else ""

    def write_fast(self, cmd: str):
        """Write WITHOUT the per-command `:SYSTem:ERRor?` poll.

        `_write_checked` costs two USB round trips. A 25-step ramp on four
        channels is 200 round trips — several seconds of wall time, which
        defeats the point of a ramp meant to complete in about a second. Ramp
        steps use this; the caller MUST call `get_error()` once when the ramp
        completes, so errors are still detected, just not per-step.

        Do not use this outside a ramp.
        """
        log.debug("WRITE (fast) %s", cmd)
        self._inst.write(cmd)

    # ---- Output control ------------------------------------------------------

    def output_on(self, channel: int):
        self._write_checked(f":OUTPut{int(channel)} ON")

    def output_off(self, channel: int):
        self._write_checked(f":OUTPut{int(channel)} OFF")

    def set_output_load(self, channel: int, value="INFinity"):
        # EEL5000 input is DC-coupled high-Z BNC.  Wrong load setting (e.g.
        # 50 Ω) would silently halve the real delivered voltage. Default: INFinity.
        self._write_checked(f":OUTPut{int(channel)}:LOAD {value}")

    # ---- State readback ------------------------------------------------------

    def get_state(self, channel: int) -> dict:
        """Query and parse the current state of one channel.

        Returns: shape, shape_raw, freq, amp, offset, phase, output, load,
        load_ohms.  On any VISA error returns {"error": <message>}.

        PARSING, AND WHY IT IS FUSSY
        ----------------------------
        `:APPLy?` comes back in two different layouts depending on firmware
        and shape:

            "SIN 1000.000000,1.000000,0.000000,0.000000"     head + space
            "RAMP,5.170000E+02,2.000000E+00,0.0E+00,0.0E+00" all commas

        and the whole thing may arrive wrapped in double quotes with a
        trailing newline.  Getting this wrong does not raise — it silently
        yields shape = the entire response string and freq/amp/offset/phase =
        0.0, which is exactly what the sidecars from the Ni runs recorded: the
        generator settings were right on the bench and zero in the log, so
        every drive parameter for that campaign has to be read back out of the
        shape string by hand.

        So: strip quotes and whitespace FIRST (a trailing "\n" is what stops
        .strip('"') from finding the closing quote), then split on the first
        run of whitespace OR the first comma, whichever comes first.  The four
        numbers are always freq, amplitude (Vpp), offset (V), phase (deg).

        `shape_raw` keeps the untouched response.  It costs a few bytes in the
        log and it is the only thing that can settle "did the parser get this
        right?" a year from now.
        """
        ch = int(channel)
        try:
            apply_resp  = self.query(f":SOURce{ch}:APPLy?")
            output_resp = self.query(f":OUTPut{ch}?")
            load_resp   = self.query(f":OUTPut{ch}:LOAD?")
        except Exception as e:
            log.exception("get_state ch%s failed", ch)
            return {"error": str(e)}

        raw = (apply_resp or "").strip().strip('"').strip()

        # Head is everything up to the first whitespace or comma.
        head, _sep, tail = "", "", ""
        for i, c in enumerate(raw):
            if c.isspace() or c == ",":
                head, _sep, tail = raw[:i], c, raw[i + 1:]
                break
        else:
            head = raw

        shape = head.strip().upper() or "?"
        nums = []
        for tok in tail.replace(",", " ").split():
            try:
                nums.append(float(tok))
            except ValueError:
                nums.append(float("nan"))

        def _n(i):
            v = nums[i] if len(nums) > i else 0.0
            return 0.0 if v != v else v      # NaN -> 0.0

        load_txt = (load_resp or "").strip().strip('"').strip()
        try:
            load_ohms = float(load_txt)
        except (TypeError, ValueError):
            # "INF" / "INFinity" / "9.9E37" all mean high-Z, which is what the
            # EEL5000 input actually is.  A 50 ohm reading here means the
            # generator is halving every voltage this rig commands.
            #
            # High-Z is reported as 9.9E37 — the DG1022Z's own sentinel, and a
            # FINITE number.  math.inf would be correct in Python and then
            # become `null` in the log, which reads the same as "could not be
            # read" — the one thing this field exists to distinguish.
            load_ohms = 9.9e37 if load_txt.upper().startswith("INF") \
                else float("nan")

        return {
            "shape":     shape,
            "shape_raw": raw,
            "freq":      _n(0),
            "amp":       _n(1),
            "offset":    _n(2),
            "phase":     _n(3),
            "output":    (output_resp or "").strip().upper() in ("ON", "1"),
            "load":      load_txt,
            "load_ohms": load_ohms,
        }

    # ---- Utility / verbose methods -------------------------------------------

    def set_phase(self, channel: int, deg: float):
        self._write_checked(f":SOURce{int(channel)}:PHASe {deg}")

    def set_start_phase(self, channel: int, degrees: float):
        """Set the waveform start phase. Only takes effect on the next
        :PHASe:SYNChronize — it does not reposition a running waveform."""
        self._write_checked(f":SOURce{int(channel)}:PHASe {degrees}")

    def set_duty(self, channel: int, pct: float):
        self._write_checked(f":SOURce{int(channel)}:FUNCtion:SQUare:DCYCle {pct}")

    def set_ramp_symmetry(self, channel: int, pct: float):
        self._write_checked(f":SOURce{int(channel)}:FUNCtion:RAMP:SYMMetry {pct}")

    def sweep_on(self, channel: int):
        self._write_checked(f":SOURce{int(channel)}:SWEep:STATe ON")

    def sweep_off(self, channel: int):
        self._write_checked(f":SOURce{int(channel)}:SWEep:STATe OFF")

    def set_sweep(self, channel: int, start_hz: float, stop_hz: float, time_s: float):
        ch = int(channel)
        self._write_checked(f":SOURce{ch}:SWEep:STARt {start_hz}")
        self._write_checked(f":SOURce{ch}:SWEep:STOP {stop_hz}")
        self._write_checked(f":SOURce{ch}:SWEep:TIME {time_s}")

    def burst_on(self, channel: int):
        self._write_checked(f":SOURce{int(channel)}:BURSt:STATe ON")

    def burst_off(self, channel: int):
        self._write_checked(f":SOURce{int(channel)}:BURSt:STATe OFF")

    def set_burst(self, channel: int, ncycles: int, period_s: float):
        ch = int(channel)
        self._write_checked(f":SOURce{ch}:BURSt:NCYCles {ncycles}")
        self._write_checked(f":SOURce{ch}:BURSt:INTernal:PERiod {period_s}")

    def align_phase(self, channel: int):
        # Aligns the two channels of THIS instrument only (the front-panel
        # "Align Phase" / channel Sync function): it resets the phase generators
        # of BOTH channels at once so they restart phase-coherent.
        # Does NOT synchronise across two separate DG1022Z units — for that the
        # two units must share a timebase (see set_reference_clock).
        self._write_checked(f":SOURce{int(channel)}:PHASe:SYNChronize")

    def set_reference_clock(self, source: str = "INTernal"):
        """Select the instrument timebase: internal crystal or external 10 MHz.

        Two SEPARATE DG1022Z units cannot hold a stable phase relationship on
        their independent internal clocks — they drift.  To phase-lock across
        units, cable one unit's rear-panel [10MHz Out] to the other's
        [10MHz In] and set the second unit to EXTernal here.  With a shared
        reference the two units' frequencies are locked, so a phase relationship
        established at output-enable time is held rather than drifting away.

        SCPI: CLOCK_SCPI {INTernal|EXTernal}
        Verified against firmware 03.01.12; see module-level CLOCK_SCPI constant.
        The timeout is raised to 5000 ms for the duration (datasheet lock < 2 s).
        """
        s = source.strip().upper()
        if s.startswith("EXT"):
            arg = "EXTernal"
        elif s.startswith("INT"):
            arg = "INTernal"
        else:
            raise ValueError(
                f"reference clock source must be INTernal or EXTernal, got {source!r}"
            )
        prev_timeout = self._inst.timeout
        self._inst.timeout = 5000
        try:
            self._write_checked(f"{CLOCK_SCPI} {arg}")
        finally:
            self._inst.timeout = prev_timeout

    def get_reference_clock(self) -> str:
        """Read back the active timebase. Returns exactly 'INT' or 'EXT'.

        The DG1022Z silently falls back to INT if no valid 10 MHz is
        detected on the rear connector, so this MUST be checked after
        setting EXT — a failed lock is invisible otherwise.

        Raises RuntimeError on timeout or an unexpected response; never
        returns None silently.
        """
        prev_timeout = self._inst.timeout
        self._inst.timeout = 5000
        try:
            resp = self.query(f"{CLOCK_SCPI}?").upper()
        except Exception as e:
            raise RuntimeError(
                f"get_reference_clock: query timed out or failed: {e}"
            ) from e
        finally:
            self._inst.timeout = prev_timeout
        if resp.startswith("EXT"):
            return "EXT"
        if resp.startswith("INT"):
            return "INT"
        raise RuntimeError(f"get_reference_clock: unexpected response {resp!r}")

    def verify_external_lock(self, settle_s: float = 3.0) -> tuple[bool, str]:
        """Set EXT, wait for the PLL to settle, read back.

        The DG1022Z falls back to INT when no valid 10 MHz is present,
        so this readback is the only software-visible confirmation that
        the rear-panel reference is live.

        Returns (True, "EXT") on success or (False, actual_value) on fallback.
        Logs the write and readback at DEBUG.
        """
        log.debug("verify_external_lock: writing EXTernal via %s", CLOCK_SCPI)
        self.set_reference_clock("EXTernal")
        log.debug("verify_external_lock: sleeping %.1f s for PLL to settle", settle_s)
        time.sleep(settle_s)
        actual = self.get_reference_clock()
        log.debug("verify_external_lock: readback = %r", actual)
        if actual == "EXT":
            log.debug("verify_external_lock: LOCKED")
            return True, "EXT"
        log.debug("verify_external_lock: NOT LOCKED (fell back to %r)", actual)
        return False, actual

    def beep(self):
        self._write_checked(":SYSTem:BEEPer:IMMediate")

    def get_error(self) -> str:
        """Query the instrument error queue. Returns the raw error string."""
        return self.query(":SYSTem:ERRor?")


# ---- Self-test (no hardware required) ----------------------------------------

if __name__ == "__main__":
    instruments = discover()
    for inst in instruments:
        print(f"  Found: {inst['idn']}  "
              f"(serial={inst['serial']}, resource={inst['resource']})")

    n = len(instruments)
    print(f"[OK] funcgen_driver: discovered {n} instrument(s)")

    if instruments:
        gen = DG1022Z(instruments[0]["resource"])
        print(f"  IDN: {gen.idn()}")
        state = gen.get_state(1)
        print(f"  Ch1 state: {state}")
        # Phase 2 self-test: get_reference_clock must return INT or EXT cleanly.
        clk = gen.get_reference_clock()
        print(f"  {CLOCK_SCPI}? -> {clk!r}  (raw)")
        assert clk in ("INT", "EXT"), f"unexpected clock readback: {clk!r}"
        err = gen.get_error()
        assert err.startswith("0,") or err.startswith("+0,"), f"error queue not clean: {err}"
        gen.close()
        print("  Live I/O test passed")
        print("[OK] Phase 2: clock SCPI corrected")
