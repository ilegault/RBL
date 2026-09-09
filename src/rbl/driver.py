"""
Driver and library preflight checks — verify system-level dependencies are present.

WHY THIS EXISTS
---------------
The RBL app depends on system-level libraries and kernel drivers that PyInstaller
cannot bundle into the application distribution (LabJack LJM, VISA backend for
USB-TMC and GPIB, and USB-serial adapter drivers).

Previously, this module attempted to bundle and launch vendor installer executables
with an elevation prompt (ShellExecuteW runas). Bundling third-party installers
beside or inside an unsigned PyInstaller executable caused antivirus false positives
(heuristics flagging the app as a dropper). Furthermore, instruments on this
beamline (such as the 82357B USB/GPIB adapter for the Keithley 6482) require
Keysight IO Libraries Suite specifically. As documented in
docs/ANTIVIRUS_FALSE_POSITIVE.md and ticket 02, the bundled-installer path was
retired: the app no longer ships or launches installers. Instead, this module
performs capability checks and directs the operator to install the required
system software.

Checked dependencies (and which hardware they serve):
    LabJack LJM       T7 analog input (log amps, HV amplifier monitors)
    VISA backend      Rigol DG1022Z function generators, Keithley 6482 (Keysight IO Libraries Suite)
    USB-serial driver VGC083/XGS-600 vacuum gauges, TDS 2012 oscilloscope

Not checked (they work out of the box):
    Galil DMC-4103    pure TCP sockets — no driver needed
    USB camera        Windows UVC driver — built in

Nothing here imports PySide6, so it is safe to call from anywhere.
"""
from __future__ import annotations

# =========================================================================
# 1. LabJack LJM  (T7 analog input)
# =========================================================================

def check_ljm() -> tuple[bool, str]:
    """True only if LJM is importable *and* actually answers.

    A plain ``import`` succeeding is not enough: the DLL can load while its
    constants file is missing, which would let openS work but break the named
    reads (AIN0 -> Modbus address) the app relies on.  Reading the library
    version is a cheap way to confirm LJM is really alive.
    """
    try:
        from labjack import ljm
    except Exception as exc:
        return False, f"LabJack LJM driver not found ({exc})"

    try:
        version = ljm.readLibraryConfigS("LJM_LIBRARY_VERSION")
        return True, f"LJM library {version:.4f} ready"
    except Exception as exc:
        return False, f"LJM present but not responding ({exc})"


# =========================================================================
# 2. VISA Backend  (Rigol DG1022Z function generators, Keithley 6482)
# =========================================================================

def check_visa() -> tuple[bool, str]:
    """True only if pyvisa can create a ResourceManager (VISA backend present).

    The ``pyvisa`` Python package is bundled by PyInstaller and imports fine,
    but ``ResourceManager()`` fails unless a VISA backend (Keysight IO
    Libraries Suite, NI-VISA, R&S VISA, etc.) is installed system-wide.
    Without one, the Rigol DG1022Z function generators and Keithley 6482
    cannot be discovered or controlled.
    """
    try:
        import pyvisa
    except Exception as exc:
        return False, f"pyvisa package not available ({exc})"

    try:
        rm = pyvisa.ResourceManager()
        # If we get here, a backend is present.  Close immediately.
        rm.close()
        return True, "VISA backend ready"
    except Exception as exc:
        msg = str(exc)
        # pyvisa raises a clear message when no backend is found
        if "backend" in msg.lower() or "visa" in msg.lower() or "library" in msg.lower():
            return False, "No VISA backend installed (install Keysight IO Libraries Suite)"
        return False, f"VISA backend error ({exc})"


# =========================================================================
# 3. USB-serial adapter driver  (VGC083, XGS-600, TDS 2012)
# =========================================================================

def check_serial() -> tuple[bool, str]:
    """Check whether pyserial can enumerate COM ports.

    The ``pyserial`` package itself is bundled by PyInstaller.  The system-level
    USB-serial adapter driver (FTDI, Prolific PL2303, CH340, etc.) depends on
    the specific adapter hardware.  Windows 10/11 auto-installs FTDI and CH340
    via Windows Update, but Prolific PL2303 and some others need manual
    installation.

    This check only confirms that pyserial is importable and can enumerate.
    Whether the specific adapter's driver is installed only becomes apparent
    when the adapter is plugged in and a COM port either does or does not
    appear in the enumeration.  So this is a softer check: it returns True
    even if no ports are currently visible (the adapter may simply not be
    plugged in yet).
    """
    try:
        import serial
        import serial.tools.list_ports
    except Exception as exc:
        return False, f"pyserial not available ({exc})"

    try:
        ports = list(serial.tools.list_ports.comports())
        if ports:
            names = ", ".join(p.device for p in ports[:4])
            extra = f" (+{len(ports) - 4} more)" if len(ports) > 4 else ""
            return True, f"Serial ports found: {names}{extra}"
        else:
            # No ports visible — adapter may not be plugged in, or driver missing.
            return True, ("No COM ports detected — plug in a USB-serial adapter; "
                          "if it still does not appear, its driver may need installing")
    except Exception as exc:
        return False, f"Serial port enumeration failed ({exc})"


# =========================================================================
# Combined check
# =========================================================================

def check_all() -> list[dict]:
    """Run every prerequisite check.  Returns a list of failures only.

    Each failure dict:
        name      — human-readable dependency name
        message   — what went wrong
        key       — short identifier for the GUI (ljm / visa / serial)
    """
    failures: list[dict] = []

    ok, msg = check_ljm()
    if not ok:
        failures.append({
            "name": "LabJack LJM",
            "message": msg,
            "key": "ljm",
        })

    ok, msg = check_visa()
    if not ok:
        failures.append({
            "name": "Keysight IO Libraries Suite",
            "message": msg,
            "key": "visa",
        })

    ok, msg = check_serial()
    if not ok:
        failures.append({
            "name": "USB-serial driver",
            "message": msg,
            "key": "serial",
        })

    return failures
