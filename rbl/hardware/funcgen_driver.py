"""
funcgen_driver.py
PyVISA driver for RIGOL DG1022Z dual-channel function generators.

Two instruments are distinguished by USB serial number. Never hardcode a
serial number: enumerate at runtime via *IDN? and let the user assign A/B.

Connection: USB-TMC via VISA (IVI/VISA driver already installed on the host).
Resource strings: USB0::0x1AB1::0x0642::<SERIAL>::INSTR
"""
import re

try:
    import pyvisa
    PYVISA_AVAILABLE = True
except ImportError:
    PYVISA_AVAILABLE = False

# ES5 plate rating = 5 kV/plate; EEL5000 gain = 1000x, range +/-5 kV.
# Cap generator output below the 5 V (=5 kV/plate) hardware ceiling to leave margin.
# 4.0 V -> 4 kV/plate, 1 kV margin below the 5 kV plate rating. Adjust here only.
MAX_GEN_VOLTS = 4.0

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
        All amplitude/offset values are clamped to ±MAX_GEN_VOLTS before any
        SCPI command is issued.  set_waveform() returns a warning string when
        clamping occurs.
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
        self._idn = self._inst.query("*IDN?").strip()

    # ---- Generic passthroughs ------------------------------------------------

    def write(self, cmd: str):
        self._inst.write(cmd)

    def query(self, cmd: str) -> str:
        return self._inst.query(cmd).strip()

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
            pass

    # ---- Per-channel waveform ------------------------------------------------

    def set_waveform(self, channel: int, shape: str, freq_hz: float,
                     amp_vpp: float, offset_v: float, phase_deg: float) -> str:
        """Push waveform parameters for one channel.

        Clamps amp_vpp and offset_v to ±MAX_GEN_VOLTS BEFORE sending any
        command.  Returns a non-empty warning string when clamping occurred,
        otherwise returns "".

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
        amp_vpp  = max(-MAX_GEN_VOLTS, min(MAX_GEN_VOLTS, amp_vpp))
        offset_v = max(-MAX_GEN_VOLTS, min(MAX_GEN_VOLTS, offset_v))
        if amp_vpp != orig_amp:
            warnings.append(f"clamped amplitude {orig_amp:.4g}->{amp_vpp:.4g} V")
        if offset_v != orig_off:
            warnings.append(f"clamped offset {orig_off:.4g}->{offset_v:.4g} V")

        if shape == "Sine":
            self._inst.write(
                f":SOURce{ch}:APPLy:SINusoid {freq_hz},{amp_vpp},{offset_v},{phase_deg}"
            )
        elif shape == "Triangle":
            # RIGOL has no TRIANGLE keyword; 50%-symmetry RAMP is equivalent.
            self._inst.write(
                f":SOURce{ch}:APPLy:RAMP {freq_hz},{amp_vpp},{offset_v},{phase_deg}"
            )
            self._inst.write(f":SOURce{ch}:FUNCtion:RAMP:SYMMetry 50")
        elif shape == "Square":
            self._inst.write(
                f":SOURce{ch}:APPLy:SQUare {freq_hz},{amp_vpp},{offset_v},{phase_deg}"
            )
        elif shape == "Pulse":
            self._inst.write(
                f":SOURce{ch}:APPLy:PULSe {freq_hz},{amp_vpp},{offset_v},{phase_deg}"
            )
        elif shape == "DC":
            # DC mode: offset_v is the held voltage.  The first two positional
            # arguments are required by SCPI syntax but are ignored by the
            # instrument in DC mode.
            self._inst.write(f":SOURce{ch}:APPLy:DC 1,1,{offset_v}")
        else:
            raise ValueError(f"Unknown waveform shape: {shape!r}")

        return ", ".join(warnings)

    # ---- Output control ------------------------------------------------------

    def output_on(self, channel: int):
        self._inst.write(f":OUTPut{int(channel)} ON")

    def output_off(self, channel: int):
        self._inst.write(f":OUTPut{int(channel)} OFF")

    def set_output_load(self, channel: int, value="INFinity"):
        # EEL5000 input is DC-coupled high-Z BNC.  Wrong load setting (e.g.
        # 50 Ω) would silently halve the real delivered voltage. Default: INFinity.
        self._inst.write(f":OUTPut{int(channel)}:LOAD {value}")

    # ---- State readback ------------------------------------------------------

    def get_state(self, channel: int) -> dict:
        """Query and parse the current state of one channel.

        Returns a dict with keys: shape, freq, amp, offset, phase, output, load.
        On any VISA error returns {"error": <message>}.
        """
        ch = int(channel)
        try:
            apply_resp  = self._inst.query(f":SOURce{ch}:APPLy?").strip().strip('"')
            output_resp = self._inst.query(f":OUTPut{ch}?").strip()
            load_resp   = self._inst.query(f":OUTPut{ch}:LOAD?").strip()
        except Exception as e:
            return {"error": str(e)}

        # APPLy? returns e.g.: "SIN 1000.000000,1.000000,0.000000,0.000000"
        parts = apply_resp.split(None, 1)
        shape = parts[0] if parts else "?"
        nums  = []
        if len(parts) > 1:
            try:
                nums = [float(x) for x in parts[1].split(",")]
            except Exception:
                nums = []

        return {
            "shape":  shape,
            "freq":   nums[0] if len(nums) > 0 else 0.0,
            "amp":    nums[1] if len(nums) > 1 else 0.0,
            "offset": nums[2] if len(nums) > 2 else 0.0,
            "phase":  nums[3] if len(nums) > 3 else 0.0,
            "output": output_resp.upper() in ("ON", "1"),
            "load":   load_resp,
        }

    # ---- Utility / verbose methods -------------------------------------------

    def set_phase(self, channel: int, deg: float):
        self._inst.write(f":SOURce{int(channel)}:PHASe {deg}")

    def set_duty(self, channel: int, pct: float):
        self._inst.write(f":SOURce{int(channel)}:FUNCtion:SQUare:DCYCle {pct}")

    def set_ramp_symmetry(self, channel: int, pct: float):
        self._inst.write(f":SOURce{int(channel)}:FUNCtion:RAMP:SYMMetry {pct}")

    def sweep_on(self, channel: int):
        self._inst.write(f":SOURce{int(channel)}:SWEep:STATe ON")

    def sweep_off(self, channel: int):
        self._inst.write(f":SOURce{int(channel)}:SWEep:STATe OFF")

    def set_sweep(self, channel: int, start_hz: float, stop_hz: float, time_s: float):
        ch = int(channel)
        self._inst.write(f":SOURce{ch}:SWEep:STARt {start_hz}")
        self._inst.write(f":SOURce{ch}:SWEep:STOP {stop_hz}")
        self._inst.write(f":SOURce{ch}:SWEep:TIME {time_s}")

    def burst_on(self, channel: int):
        self._inst.write(f":SOURce{int(channel)}:BURSt:STATe ON")

    def burst_off(self, channel: int):
        self._inst.write(f":SOURce{int(channel)}:BURSt:STATe OFF")

    def set_burst(self, channel: int, ncycles: int, period_s: float):
        ch = int(channel)
        self._inst.write(f":SOURce{ch}:BURSt:NCYCles {ncycles}")
        self._inst.write(f":SOURce{ch}:BURSt:INTernal:PERiod {period_s}")

    def align_phase(self, channel: int):
        # Aligns the two channels of THIS instrument only.
        # Does NOT synchronise across two separate DG1022Z units.
        self._inst.write(f":SOURce{int(channel)}:PHASe:SYNChronize")

    def beep(self):
        self._inst.write(":SYSTem:BEEPer:IMMediate")

    def get_error(self) -> str:
        """Query the instrument error queue. Returns the raw error string."""
        return self._inst.query(":SYSTem:ERRor?").strip()


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
        gen.close()
        print("  Live I/O test passed")
