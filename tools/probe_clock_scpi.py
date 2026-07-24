"""
tools/probe_clock_scpi.py
Standalone SCPI communication probe for the RIGOL DG1022Z.

Identifies which reference-clock mnemonic the connected firmware accepts,
and checks whether the phase / align commands are being rejected.

Run from the repo root:
    python tools/probe_clock_scpi.py

NOT imported by the application.

Safety constraints:
  - Never writes EXT (clock source stays INT throughout).
  - Never enables any output.
  - Never changes frequency, amplitude, or offset.
"""
import sys
import os

_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.abspath(os.path.join(_here, ".."))
if _root not in sys.path:
    sys.path.insert(0, _root)

try:
    import pyvisa
    import pyvisa.errors
except ImportError:
    print("ERROR: pyvisa is not installed.  pip install pyvisa")
    sys.exit(1)

from rbl.hardware.funcgen_driver import discover

PROBE_TIMEOUT_MS = 800


# ── Helpers ───────────────────────────────────────────────────────────────────

def drain_errors(inst, max_iter=20) -> list[str]:
    """Drain the instrument error queue; return list of raw error strings."""
    errors = []
    saved = inst.timeout
    inst.timeout = 1500
    try:
        for _ in range(max_iter):
            try:
                e = inst.query(":SYSTem:ERRor?").strip()
            except Exception as ex:
                errors.append(f"<ERRor? query failed: {ex}>")
                break
            errors.append(e)
            if e.startswith("0,") or e.startswith("+0,") or "No error" in e:
                break
    finally:
        inst.timeout = saved
    return errors


def probe(inst, mnemonic: str, form: str) -> dict:
    """Probe one mnemonic safely.

    form: 'query'  → sends "<mnemonic>?" and captures the response.
          'write'  → sends "<mnemonic> INT" (clock) or "<mnemonic> 0"
                     (phase) — both are no-ops that leave the instrument
                     in its current state.

    Always leaves the instrument in a clean state regardless of outcome so
    the next probe is not contaminated by a stale buffer.
    """
    saved_timeout = inst.timeout
    inst.timeout = PROBE_TIMEOUT_MS

    # Drain error queue BEFORE the probe so a stale error from any prior
    # command cannot contaminate this result's attribution.
    drain_errors(inst)

    response = None
    exc_type = None
    exc_msg  = None

    # Decide what to send
    if form == "query":
        cmd = f"{mnemonic}?"
    elif "PHASe:SYNChronize" in mnemonic:
        cmd = mnemonic          # no-argument write-only command
    elif "PHASe" in mnemonic:
        cmd = f"{mnemonic} 0"   # set phase to 0 — safe no-op
    else:
        cmd = f"{mnemonic} INT" # clock-source write — INT is the default, no-op

    try:
        if form == "query":
            response = inst.query(cmd).strip()
        else:
            inst.write(cmd)
    except pyvisa.errors.VisaIOError as e:
        exc_type = "VisaIOError"
        exc_msg  = f"{e.abbreviation}: {e}"
        # CRITICAL: flush stale I/O after a timeout so the next probe
        # does not read a partial response left in the buffer.
        try:
            inst.clear()
        except Exception:
            pass
    except Exception as e:
        exc_type = type(e).__name__
        exc_msg  = str(e)
        try:
            inst.clear()
        except Exception:
            pass
    finally:
        inst.timeout = saved_timeout

    # Drain error queue regardless of success/failure
    errors = drain_errors(inst)

    return {
        "mnemonic":   mnemonic,
        "form":       form,
        "cmd":        cmd,
        "response":   response,
        "exc_type":   exc_type,
        "exc_msg":    exc_msg,
        "errors":     errors,
    }


def status_of(r: dict) -> str:
    if r["exc_type"] is not None:
        return "TIMEOUT" if r["exc_type"] == "VisaIOError" else "ERROR"
    if r["form"] == "write":
        # A write is OK only when the FIRST entry drained from the error queue
        # is zero.  drain_errors stops on the first zero-error, so errs[-1] is
        # always "0,..." regardless of whether the command was accepted — using
        # errs[-1] produced four false-positive OKs in the Phase 1 run.
        errs = r["errors"]
        first = errs[0] if errs else ""
        if not (first.startswith("0,") or first.startswith("+0,")):
            return "REJECTED"
        return "OK"
    return "OK" if r["response"] is not None else "ERROR"


def err_summary(r: dict) -> str:
    errs = r["errors"]
    if not errs:
        return "—"
    # Show the first non-zero error, or the last entry if all zero
    for e in errs:
        if not (e.startswith("0,") or e.startswith("+0,")):
            return e
    return errs[-1]


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    instruments = discover()
    if not instruments:
        print("No DG1022Z found. Check USB connection and VISA driver.")
        return 1

    print(f"Found {len(instruments)} instrument(s):")
    for d in instruments:
        print(f"  resource : {d['resource']}")
        print(f"  serial   : {d['serial']}")
        print(f"  *IDN?    : {d['idn']}")
    print()

    resource = instruments[0]["resource"]
    rm = pyvisa.ResourceManager()
    inst = rm.open_resource(resource)
    inst.read_termination  = "\n"
    inst.write_termination = "\n"
    inst.timeout = 3000

    results = []

    try:
        # ── 1. Full IDN ───────────────────────────────────────────────────
        idn = inst.query("*IDN?").strip()
        print(f"Full *IDN? response: {idn!r}")
        print()

        # ── 2. Baseline error-queue drain ─────────────────────────────────
        print("Draining error queue before probing …")
        baseline = drain_errors(inst)
        for e in baseline:
            print(f"  drained: {e!r}")
        last = baseline[-1] if baseline else ""
        if not (last.startswith("0,") or last.startswith("+0,")):
            print("ERROR: error queue did not clear. "
                  "Something is wrong before probing begins. Aborting.")
            return 1
        print("  Queue clear — proceeding.\n")

        # ── 3. Reference-clock candidates ─────────────────────────────────
        clock_candidates = [
            ":SYSTem:CLKSource",
            ":ROSCillator:SOURce",
            ":SYSTem:ROSCillator:SOURce",
            ":SOURce:ROSCillator:SOURce",
        ]

        print("─" * 64)
        print("REFERENCE CLOCK CANDIDATES")
        print("─" * 64)
        for mnemonic in clock_candidates:
            for form in ("query", "write"):
                r = probe(inst, mnemonic, form)
                results.append(r)
                st = status_of(r)
                resp_str = repr(r["response"]) if r["response"] is not None else "—"
                exc_str  = f"  [{r['exc_type']}]" if r["exc_type"] else ""
                print(f"  {st:<8}  {form:<6}  {r['cmd']!r:<48}  "
                      f"resp={resp_str}{exc_str}")
        print()

        # ── 4. Phase / align commands (suspected rejection source) ─────────
        phase_probes = [
            (":SOURce1:PHASe",              "query"),
            (":SOURce1:PHASe",              "write"),
            (":SOURce1:PHASe:SYNChronize",  "write"),
        ]

        print("─" * 64)
        print("PHASE / ALIGN COMMANDS")
        print("─" * 64)
        for mnemonic, form in phase_probes:
            r = probe(inst, mnemonic, form)
            results.append(r)
            st = status_of(r)
            resp_str = repr(r["response"]) if r["response"] is not None else "—"
            exc_str  = f"  [{r['exc_type']}]" if r["exc_type"] else ""
            print(f"  {st:<8}  {form:<6}  {r['cmd']!r:<48}  "
                  f"resp={resp_str}{exc_str}")
        print()

    finally:
        try:
            inst.close()
        except Exception:
            pass

    # ── 5. Summary table ──────────────────────────────────────────────────
    print("=" * 78)
    print(f"{'MNEMONIC':<42} {'FORM':<7} {'STATUS':<9} {'RESPONSE / ERR QUEUE'}")
    print("=" * 78)
    for r in results:
        st = status_of(r)
        resp = (repr(r["response"]) if r["response"] is not None
                else (r["exc_type"] or "—"))
        eq   = err_summary(r)
        print(f"{r['mnemonic']:<42} {r['form']:<7} {st:<9} {resp}  |  {eq}")
    print("=" * 78)
    print()

    # ── 6. Verdict ────────────────────────────────────────────────────────
    clock_results = [r for r in results
                     if any(c in r["mnemonic"] for c in
                            [":SYSTem:CLKSource", ":ROSCillator:", ":SOURce:ROSCillator"])]

    query_wins = [r for r in clock_results
                  if r["form"] == "query" and status_of(r) == "OK"
                  and r["response"] is not None
                  and (r["response"].upper().startswith("INT")
                       or r["response"].upper().startswith("EXT"))]
    write_wins = [r for r in clock_results
                  if r["form"] == "write" and status_of(r) == "OK"]

    print("VERDICT")
    print("-------")
    if query_wins:
        for r in query_wins:
            print(f"[OK] Phase 1: clock SCPI identified (query): {r['mnemonic']!r}"
                  f"  returned {r['response']!r}")
    else:
        print("[FAIL] No candidate returned INT or EXT on query.")

    if write_wins:
        for r in write_wins:
            print(f"[OK] Phase 1: clock SCPI identified (write): {r['mnemonic']!r}")
    else:
        print("[FAIL] No candidate accepted a write without error-queue rejection.")

    phase_r = [r for r in results if "PHASe" in r["mnemonic"]]
    if phase_r:
        print()
        print("PHASE/ALIGN:")
        for r in phase_r:
            st = status_of(r)
            eq = err_summary(r)
            print(f"  {st:<9}  {r['form']:<6}  {r['cmd']!r}  |  {eq}")

    return 0 if (query_wins or write_wins) else 1


if __name__ == "__main__":
    sys.exit(main())
