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

WHY LISTING IS NOT ENOUGH (THE 2026-09-21 FALSE PASS)
-----------------------------------------------------
Listing a GPIB resource proves nothing: Keysight VISA lists remembered addresses from
its saved instrument table whether or not anything is currently powered or attached,
and GPIB sessions can open successfully with no listener on the bus. On 2026-09-21 the
probe printed "USING: default ... The default backend works. Nothing to configure." and
"GPIB  3 found" in two runs where no backend got an `*IDN?` reply: once with
VI_ERROR_RSRC_NFOUND on open, and once with VI_ERROR_TMO on the query. The only reliable
success criterion is a non-empty `*IDN?` reply; listing alone proves nothing.
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
    answered = 0
    for res in resources:
        try:
            inst = rm.open_resource(res)
        except Exception as exc:
            print(f"    {res}: LISTED BUT DID NOT ANSWER ({exc})")
            continue
        try:
            inst.timeout = 5000
            try:
                inst.read_termination = "\n"
                inst.write_termination = "\n"
            except Exception:
                pass
            try:
                idn = inst.query("*IDN?").strip()
                if not idn:
                    raise RuntimeError("empty *IDN? response")
                print(f"    {FOUND_MARKER} {res} {idn}")
                answered += 1
            except Exception as exc:
                print(f"    {res}: LISTED BUT DID NOT ANSWER ({exc})")
                continue

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
    return 0 if answered > 0 else 1


def parse_child_found(out: str) -> list[str]:
    """Parse child probe stdout, returning a list of resource names that answered.

    Takes only the first whitespace-separated token after FOUND_MARKER so the appended
    IDN string does not become part of the resource name.
    """
    found = []
    for ln in out.splitlines():
        if FOUND_MARKER in ln:
            tokens = ln.split(FOUND_MARKER, 1)[1].split()
            if tokens:
                found.append(tokens[0])
    return found


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
        found = parse_child_found(out)
        if found:
            working.append((label, spec, found))

    print("\n" + "=" * 70)
    if not working:
        print("NO BACKEND GOT AN ANSWER. Listed resources did not respond to *IDN?.")
        print("Check: instrument powered, GPIB selected (not RS-232), GPIB address matches, "
              "cable seated, instrument not inside a front-panel menu.")
        print("=" * 70)
        return 2

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
    all_found = sorted({r for _, _, f in working for r in f})
    for prefix, what in EXPECTED.items():
        hits = [r for r in all_found if r.upper().startswith(prefix)]
        count_str = f"{len(hits)} answered" if hits else "NONE ANSWERED"
        print(f"  {prefix:5s} {count_str:14s} ({what})")
    print("\nRecord these lines in ticket 01's Comments.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
