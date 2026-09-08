"""
Dependency bootstrapper — are the system-level drivers here, and can we
install them for the user?

The RBL app depends on several system-level libraries and drivers that
PyInstaller cannot bundle.  On a fresh control PC any of them may be missing.
This module answers three questions per dependency:

    check_*()          -> (ok, message)   is it installed AND responding?
    find_*_installer() -> path | None     did we ship an installer alongside us?
    launch_installer() -> bool            run that installer (asks for admin)

Checked dependencies (and which hardware they serve):

    LabJack LJM       T7 analog input (log amps, HV amplifier monitors)
    NI-VISA Runtime   Rigol DG1022Z function generators (USB-TMC via VISA)
    USB-serial driver  VGC083/XGS-600 vacuum gauges, TDS 2012 oscilloscope

Not checked (they work out of the box):
    Galil DMC-4103    pure TCP sockets — no driver needed
    USB camera        Windows UVC driver — built in

Nothing here imports PySide6, so it is safe to call from anywhere.
"""
from __future__ import annotations

import glob
import os
import sys

# =========================================================================
# Shared helpers
# =========================================================================

def _candidate_dirs() -> list[str]:
    """Folders that might hold a bundled installer, frozen or from source."""
    dirs: list[str] = []

    # The folder holding RBL.exe (or the project root from source).
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
    else:
        exe_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dirs += [exe_dir, os.path.join(exe_dir, "vendor")]

    # Where PyInstaller unpacks bundled data files (varies by version).
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        dirs += [meipass, os.path.join(meipass, "vendor")]

    # De-duplicate while keeping order.
    seen: set[str] = set()
    unique: list[str] = []
    for d in dirs:
        if d and d not in seen:
            seen.add(d)
            unique.append(d)
    return unique


def _find_exe(patterns: list[str]) -> str | None:
    """Search candidate dirs for the first file matching any of *patterns*.

    Each pattern is a glob basename, e.g. "LabJack*.exe".
    If several match, the newest (by mtime) wins.
    """
    hits: list[str] = []
    for d in _candidate_dirs():
        for pat in patterns:
            hits += glob.glob(os.path.join(d, pat))
    if not hits:
        return None
    hits.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return hits[0]


def launch_installer(path: str, silent: bool = False) -> bool:
    """Launch an installer with a UAC (administrator) prompt.

    We do NOT install anything ourselves — we hand off to the vendor's own
    installer, which needs admin rights to place the driver system-wide.
    Returns True if the launch was accepted (the install then runs on its own).
    """
    try:
        import ctypes
        params = "/S" if silent else ""
        # "runas" triggers the Windows elevation prompt.
        rc = ctypes.windll.shell32.ShellExecuteW(  # type: ignore[attr-defined]
            None, "runas", path, params, os.path.dirname(path), 1
        )
        return int(rc) > 32  # ShellExecute returns >32 on success
    except Exception:
        return False


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


def find_ljm_installer() -> str | None:
    """Return the path to a bundled LabJack installer, or None."""
    return _find_exe(["LabJack*.exe", "labjack*.exe"])


# =========================================================================
# 2. NI-VISA Runtime  (Rigol DG1022Z function generators)
# =========================================================================

def check_visa() -> tuple[bool, str]:
    """True only if pyvisa can create a ResourceManager (VISA backend present).

    The ``pyvisa`` Python package is bundled by PyInstaller and imports fine,
    but ``ResourceManager()`` fails unless a VISA backend (NI-VISA, Keysight
    IO Libraries, R&S VISA, etc.) is installed system-wide.  Without one the
    Rigol DG1022Z function generators cannot be discovered or controlled.
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
            return False, "No VISA backend installed (needed for function generators)"
        return False, f"VISA backend error ({exc})"


def find_visa_installer() -> str | None:
    """Return the path to a bundled NI-VISA Runtime installer, or None.

    Looks for anything matching  ni-visa*  or  NIVISA*  or  visa*.exe
    (NI's runtime installer is typically named like
    ``NI-VISA-Runtime_xx.x_online.exe``).
    """
    return _find_exe([
        "ni-visa*.exe", "NI-VISA*.exe", "NIVISA*.exe", "ni_visa*.exe",
        "visa_runtime*.exe",
    ])


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


def find_serial_installer() -> str | None:
    """Return the path to a bundled USB-serial driver installer, or None.

    Looks for common USB-serial driver installer names (FTDI, Prolific, CH340).
    """
    return _find_exe([
        "FTDI*.exe", "ftdi*.exe",
        "CDM*.exe",                     # FTDI "Combined Driver Model" installer
        "PL2303*.exe", "pl2303*.exe",   # Prolific PL2303
        "CH34*.exe", "ch34*.exe",       # WCH CH340/CH341
    ])


# =========================================================================
# Combined check
# =========================================================================

def check_all() -> list[dict]:
    """Run every prerequisite check.  Returns a list of failures only.

    Each failure dict:
        name      — human-readable dependency name
        message   — what went wrong
        installer — path to a bundled installer, or None
        key       — short identifier for the GUI (ljm / visa / serial)
    """
    failures: list[dict] = []

    ok, msg = check_ljm()
    if not ok:
        failures.append({
            "name": "LabJack LJM",
            "message": msg,
            "installer": find_ljm_installer(),
            "key": "ljm",
        })

    ok, msg = check_visa()
    if not ok:
        failures.append({
            "name": "NI-VISA Runtime",
            "message": msg,
            "installer": find_visa_installer(),
            "key": "visa",
        })

    ok, msg = check_serial()
    if not ok:
        failures.append({
            "name": "USB-serial driver",
            "message": msg,
            "installer": find_serial_installer(),
            "key": "serial",
        })

    return failures
