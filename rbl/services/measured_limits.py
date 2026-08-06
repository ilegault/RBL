"""
measured_limits.py
Persistent store for two physical quantities that cannot be read back over any
interface and must be measured experimentally:

  trip_ma       — the actual current-pot trip threshold (mA) per amp
  load_cap_pf   — the total load capacitance (pF) per amp

Written automatically by the test runners:
  G1.1 calls record_trip_ma()   on every amp it successfully trips.
  G2.1 calls record_load_cap_pf() on every amp it measures.

Until a value has been measured the functions return a fallback with
is_measured=False and print a warning on every call:

  trip_ma       -> AMP_MAX_MA_DC (20 mA)  — under-protective; warning printed
  load_cap_pf   -> LOAD_CAP_PF_DEFAULT (130 pF) — optimistic; warning printed

File location: data/amp_tests/measured_limits.json
(AMT_MEASURED_LIMITS_JSON constant in amp_test_config)
"""
import hashlib
import json
import logging
import tempfile
import os
from datetime import timezone, datetime
from pathlib import Path

from rbl.config.amp_test_config import (
    AMT_OUTPUT_DIR,
    AMT_MEASURED_LIMITS_JSON,
    AMP_MAX_KV,          # not used here but imported for completeness
    LOAD_CAP_PF_DEFAULT,
    POT_RANGE_MA,
)
from rbl.config.hardware_config import AMP_LABELS, AMP_MAX_MA_DC

log = logging.getLogger(__name__)

_JSON_PATH = AMT_OUTPUT_DIR / AMT_MEASURED_LIMITS_JSON


def _empty_skeleton() -> dict:
    return {
        "updated_iso": None,
        "amps": {
            label: {
                "trip_ma":          None,
                "trip_measured_iso": None,
                "load_cap_pf":       None,
                "cap_measured_iso":  None,
            }
            for label in AMP_LABELS
        },
    }


def load(path: Path = None) -> dict:
    """Return the stored state, or the empty skeleton if the file is absent."""
    p = Path(path) if path else _JSON_PATH
    if not p.exists():
        return _empty_skeleton()
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        # Backfill any amp labels missing from older files.
        for label in AMP_LABELS:
            if label not in data.get("amps", {}):
                data.setdefault("amps", {})[label] = {
                    "trip_ma": None, "trip_measured_iso": None,
                    "load_cap_pf": None, "cap_measured_iso": None,
                }
        return data
    except Exception as exc:
        log.warning("measured_limits: could not read %s: %s", p, exc)
        return _empty_skeleton()


def _save(state: dict, path: Path = None) -> None:
    """Write atomically via a temp file in the same directory."""
    p = Path(path) if path else _JSON_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, p)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def record_trip_ma(amp: str, ma: float, path: Path = None) -> None:
    """Record the measured trip current for one amp and persist."""
    state = load(path)
    state["amps"][amp]["trip_ma"]          = float(ma)
    state["amps"][amp]["trip_measured_iso"] = datetime.now(tz=timezone.utc).isoformat()
    state["updated_iso"] = datetime.now(tz=timezone.utc).isoformat()
    _save(state, path)
    log.info("measured_limits: %s trip_ma = %.3f mA", amp, ma)


def record_load_cap_pf(amp: str, pf: float, path: Path = None) -> None:
    """Record the measured load capacitance for one amp and persist."""
    state = load(path)
    state["amps"][amp]["load_cap_pf"]      = float(pf)
    state["amps"][amp]["cap_measured_iso"] = datetime.now(tz=timezone.utc).isoformat()
    state["updated_iso"] = datetime.now(tz=timezone.utc).isoformat()
    _save(state, path)
    log.info("measured_limits: %s load_cap_pf = %.1f pF", amp, pf)


def trip_ma(amp: str, path: Path = None) -> tuple[float, bool]:
    """Return (value_ma, is_measured).

    Fallback: AMP_MAX_MA_DC (20 mA) — the amplifier's own DC rating.
    This is under-protective (the pot may be set lower), so a warning is
    printed on every call that uses the fallback.
    """
    state = load(path)
    val   = state["amps"].get(amp, {}).get("trip_ma")
    if val is None:
        print(
            f"[AMT] WARNING: trip_ma for {amp} not measured yet; "
            f"using fallback {AMP_MAX_MA_DC} mA (under-protective — run G1 first)"
        )
        log.warning(
            "trip_ma(%s): not measured; falling back to AMP_MAX_MA_DC=%.1f mA",
            amp, AMP_MAX_MA_DC,
        )
        return float(AMP_MAX_MA_DC), False
    return float(val), True


def load_cap_pf(amp: str, path: Path = None) -> tuple[float, bool]:
    """Return (value_pf, is_measured).

    Fallback: LOAD_CAP_PF_DEFAULT (130 pF) — optimistic, so the envelope
    guard is advisory until G2 runs.
    """
    state = load(path)
    val   = state["amps"].get(amp, {}).get("load_cap_pf")
    if val is None:
        print(
            f"[AMT] WARNING: load_cap_pf for {amp} not measured yet; "
            f"using fallback {LOAD_CAP_PF_DEFAULT} pF (optimistic — run G2 first)"
        )
        log.warning(
            "load_cap_pf(%s): not measured; falling back to %.1f pF",
            amp, LOAD_CAP_PF_DEFAULT,
        )
        return float(LOAD_CAP_PF_DEFAULT), False
    return float(val), True


def hash_state(path: Path = None) -> str:
    """Return a short (8-char) sha256 hex prefix of the JSON content.

    Stable: two calls on the same file return the same hash.
    Changes: any recorded measurement changes the hash.
    Returns "00000000" if the file is absent.
    """
    p = Path(path) if path else _JSON_PATH
    if not p.exists():
        return "00000000"
    try:
        raw = p.read_bytes()
        return hashlib.sha256(raw).hexdigest()[:8]
    except Exception as exc:
        log.warning("hash_state: %s", exc)
        return "00000000"


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import tempfile as _tempfile
    from pathlib import Path as _Path

    print("=== measured_limits self-test ===")

    with _tempfile.TemporaryDirectory() as tmp:
        p = _Path(tmp) / "measured_limits.json"

        # empty file -> fallback with is_measured=False
        val, measured = trip_ma("X+", path=p)
        assert val == AMP_MAX_MA_DC and not measured
        print(f"[OK] trip_ma fallback = {val} mA, is_measured=False")

        val, measured = load_cap_pf("Y-", path=p)
        assert val == LOAD_CAP_PF_DEFAULT and not measured
        print(f"[OK] load_cap_pf fallback = {val} pF, is_measured=False")

        # hash of empty/absent file
        h0 = hash_state(path=p)
        assert h0 == "00000000", f"absent file hash should be '00000000', got {h0}"
        print(f"[OK] absent file hash = {h0}")

        # record a trip_ma
        record_trip_ma("X+", 7.5, path=p)
        val, measured = trip_ma("X+", path=p)
        assert abs(val - 7.5) < 1e-9 and measured
        print(f"[OK] trip_ma('X+') = {val} mA, is_measured=True after record")

        # hash changes after record
        h1 = hash_state(path=p)
        assert h1 != "00000000" and len(h1) == 8
        print(f"[OK] hash changed after record: {h1}")

        # record a second value — hash changes again
        record_trip_ma("Y+", 9.0, path=p)
        h2 = hash_state(path=p)
        assert h2 != h1
        print(f"[OK] hash changes again: {h1} -> {h2}")

        # hash is stable (two reads of same file)
        assert hash_state(path=p) == h2
        print("[OK] hash is stable across two reads")

        # record load_cap_pf
        record_load_cap_pf("X+", 128.4, path=p)
        val, measured = load_cap_pf("X+", path=p)
        assert abs(val - 128.4) < 1e-6 and measured
        print(f"[OK] load_cap_pf('X+') = {val:.1f} pF, is_measured=True")

        # unmeasured amp still falls back
        val, measured = trip_ma("X-", path=p)
        assert val == AMP_MAX_MA_DC and not measured
        print("[OK] trip_ma('X-') still falls back (not measured)")

        # round-trip: load the file and check all fields
        state = load(path=p)
        assert state["amps"]["X+"]["trip_ma"] == 7.5
        assert state["amps"]["Y+"]["trip_ma"] == 9.0
        assert state["amps"]["X+"]["load_cap_pf"] == 128.4
        print("[OK] load() round-trip: all recorded values present")

    print("\n[OK] measured_limits self-test passed")
