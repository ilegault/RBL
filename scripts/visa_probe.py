"""
visa_probe.py
Bench verification for ticket 01: which VISA implementation can actually see the
82357B GPIB adapter, and can Python open a session through it.

WHY THIS EXISTS
---------------
Two VISA implementations can be installed on one Windows box, and only one of them
owns the default `visa32.dll`. On this control PC that owner was National Instruments,
whose VISA has no driver for the Keysight 82357B. The failure mode is silent and
convincing: `pyvisa.ResourceManager()` constructs fine, `list_resources()` returns an
empty tuple, and a naive script reports success having enumerated nothing — while
Keysight Connection Expert, talking through Keysight's own VISA, shows the picoammeter
sitting at GPIB0::14::INSTR.

WHY EACH BACKEND IS PROBED IN A SEPARATE PROCESS
------------------------------------------------
Loading two different VISA implementations into ONE process appears to work — both
answer `list_resources()` — and then throws a C++ exception (Windows error
0xE06D7363) the moment you actually open a session. An earlier version of this script
probed every backend in-process and produced exactly that: the Keysight backend listed
GPIB0::14::INSTR, then `*IDN?` died with 0xE06D7363, which reads like a broken
instrument and is nothing of the sort.

So the parent process spawns one child per candidate. Each child loads exactly one
VISA library, and a crash in one cannot contaminate another.

DEPENDENCY PATHS
----------------
C:\\Windows\\System32\\ktvisa32.dll is a shim whose implementation lives in the IVI
Foundation ktbin folder. Python 3.8 stopped searching PATH for the dependencies of a
DLL loaded through ctypes, so loading the shim fails with "Could not find module (or
one of its dependencies)" — which reads like the file is missing when it is right
there. os.add_dll_directory puts those folders back on the search path.
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

KEYSIGHT_DLL_DIRS = [
    r"C:\Program Files\IVI Foundation\VISA\Win64\ktvisa\ktbin",
    r"C:\Program Files\Keysight\IO Libraries Suite\bin",
]

# ("" means PyVISA's own default resolution, which follows whichever implementation
# currently owns visa32.dll — or whatever a .pyvisarc points at.)
BACKEND_CANDIDATES = [
    ("default (PyVISA's own resolution)", ""),
    ("Keysight (IVI Foundation ktbin, 64-bit)",
     r"C:\Program Files\IVI Foundation\VISA\Win64\ktvisa\ktbin\ktvisa32.dll"),
    ("Keysight (System32 shim)", r"C:\Windows\System32\ktvisa32.dll"),
    ("Keysight (IO Libraries bin)",
     r"C:\Program Files\Keysight\IO Libraries Suite\bin\ktvisa32.dll"),
    ("Agilent/Keysight (IVI Foundation)",
     r"C:\Program Files\IVI Foundation\VISA\Win64\agvisa\agbin\visa32.dll"),
]

EXPECTED = {
    "GPIB": "Keithley picoammeter via the 82357B adapter",
    "USB": "DG1022Z function generators / TDS 2012 scope",
}

FOUND_MARKER = "RESOURCE:"


def add_dependency_dirs(quiet=False):
    """Put Keysight's DLL folders on the search path. The returned handles must be
    kept alive for the lifetime of the process."""
    handles = []
    for d in KEYSIGHT_DLL_DIRS:
        if not Path(d).is_dir():
            continue
        try:
            handles.append(os.add_dll_directory(d))
            if not quiet:
                print(f"  dependency path added: {d}")
        except (OSError, AttributeError) as exc:
            print(f"  could not add dependency path {d}: {exc}")
    return handles


def run_child(spec):
    """Child mode: load exactly ONE VISA library and report everything about it."""
    import pyvisa

    _handles = add_dependency_dirs(quiet=True)  # noqa: F841 — must stay alive
    try:
        rm = pyvisa.ResourceManager(spec) if spec else pyvisa.ResourceManager()
    except Exception as exc:
        print(f"  could not open: {exc}")
        if "dependencies" in str(exc):
            print("  ^ the file exists but a DLL it needs was not found. That is a")
            print("    search-path problem, not a missing install.")
        return 1

    print(f"  library: {getattr(rm, 'visalib', '?')}")
    try:
        resources = list(rm.list_resources())
    except Exception as exc:
        print(f"  list_resources failed: {exc}")
        return 1

    if not resources:
        print("  0 resources — this backend cannot see the hardware.")
        print("  That is a fact about this backend, not about the instruments.")
        return 1

    print(f"  {len(resources)} resource(s):")
    for res in resources:
        print(f"    {FOUND_MARKER} {res}")

    for res in resources:
        try:
            inst = rm.open_resource(res)
        except Exception as exc:
            print(f"    {res}: could not open — {exc}")
            continue
        try:
            inst.timeout = 5000
            try:
                inst.read_termination = "\n"
                inst.write_termination = "\n"
            except Exception:
                pass
            try:
                print(f"    {res} *IDN?: {inst.query('*IDN?').strip()}")
            except Exception as exc:
                print(f"    {res} *IDN? failed: {exc}")
            if res.upper().startswith("GPIB"):
                try:
                    mep = inst.query(":SYSTem:MEP:STATe?").strip()
                    if mep.startswith("1"):
                        print(f"    {res} protocol: SCPI (MEP enabled)")
                    elif mep.startswith("0"):
                        print(f"    {res} protocol: 488.1 — NO compound queries")
                    else:
                        print(f"    {res} protocol: unexpected response {mep!r}")
                except Exception as exc:
                    print(f"    {res} protocol query failed: {exc}")
        finally:
            try:
                inst.close()
            except Exception:
                pass
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--backend", default=None,
                    help="internal: probe exactly this VISA library in this process")
    args = ap.parse_args()

    if args.backend is not None:
        return run_child(args.backend)

    print("=" * 70)
    print("RBL bench VISA verification — ticket 01")
    print("=" * 70)

    try:
        import pyvisa.util
    except ImportError:
        print("pyvisa is not installed in this environment.")
        return 1

    print("\n--- Environment ---")
    print(pyvisa.util.get_debug_info())
    print("--- Dependency search paths ---")
    if not add_dependency_dirs():
        print("  none found — Keysight VISA may not be installed where expected")

    print("\nEach backend below is probed in its OWN process. Mixing two VISA")
    print("implementations in one process throws 0xE06D7363 on the first real")
    print("session, after list_resources() has already appeared to succeed.")

    working = []
    for label, spec in BACKEND_CANDIDATES:
        print(f"\n--- Backend: {label} ---")
        if spec and not Path(spec).exists():
            print(f"  not installed ({spec})")
            continue
        proc = subprocess.run(
            [sys.executable, os.path.abspath(__file__), "--backend", spec],
            capture_output=True, text=True, timeout=120,
        )
        out = proc.stdout.rstrip()
        if out:
            print(out)
        if proc.stderr.strip():
            print(f"  stderr: {proc.stderr.strip()[:500]}")
        found = [ln.split(FOUND_MARKER, 1)[1].strip()
                 for ln in out.splitlines() if FOUND_MARKER in ln]
        if found:
            working.append((label, spec, found))

    print("\n" + "=" * 70)
    if not working:
        print("NO BACKEND SAW ANY INSTRUMENT.")
        for prefix, what in EXPECTED.items():
            print(f"  {prefix}*  {what}")
        print("\nFind ktvisa32.dll with:")
        print("  Get-ChildItem -Path C:\\ -Filter ktvisa32.dll -Recurse "
              "-ErrorAction SilentlyContinue")
        print("then add its full path to BACKEND_CANDIDATES and its FOLDER to")
        print("KEYSIGHT_DLL_DIRS. A 'could not find module (or one of its")
        print("dependencies)' error means the path is right and the dependency")
        print("folder is what is missing.")
        print("=" * 70)
        return 1

    label, spec, found = working[0]
    print(f"USING: {label}")
    if spec:
        print(f"       {spec}")
        print("\n       A non-default backend worked, so a bare ResourceManager()")
        print("       still resolves to the wrong VISA. Fix that on the MACHINE, not")
        print("       in application code — either Keysight's VISA Conflict Manager")
        print("       (a standalone utility, 32- and 64-bit versions are separate), or")
        print("       a .pyvisarc in your home directory:")
        print("\n         [Paths]")
        print(f"         VISA library: {spec}")
        print(f"         dll_extra_paths: {Path(spec).parent}")
    else:
        print("       The default backend works. Nothing to configure.")
    print("=" * 70)

    print("\n--- Summary ---")
    all_found = [r for _, _, f in working for r in f]
    for prefix, what in EXPECTED.items():
        hits = [r for r in all_found if r.upper().startswith(prefix)]
        print(f"  {prefix:5s} {('%d found' % len(hits)) if hits else 'NONE FOUND':12s}"
              f" ({what})")
    print("\nRecord these lines in ticket 01's Comments.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
