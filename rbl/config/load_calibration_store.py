"""
load_calibration_store.py
Per-channel measured load capacitance/conductance, persisted across
sessions — the measurement Phase 1 (rbl/services/load_characterizer.py)
produces, replacing `calibration_config.CAL_LOAD_CAP_PF`'s single global
guess with a number the app can reproduce and refresh on demand, per amp
channel.

WHY A SEPARATE FILE FROM rbl/config/persistence.py
-----------------------------------------------------
`persistence.py` is scoped to funcgen SCPI config, keyed by instrument
serial. This is a different kind of fact (a MEASUREMENT, not a setting) with
a different key (amp label, not serial), so it gets its own small on-disk
store rather than growing persistence.py's schema into two unrelated shapes
under one file.

WHAT THIS DOES NOT DO
----------------------
This store must never let a per-channel measurement retroactively alter a
recording already taken. `calibration_config.CAL_LOAD_CAP_PF`'s docstring
makes that promise for the global constant; this file keeps the same
promise for the per-channel case. `ac_peak_current_ma()`/`ac_max_peak_kv()`
consult this store ONLY to size a ladder BEFORE a run starts — nothing
downstream re-derives an already-recorded value from a later, different
stored capacitance.
"""
import json
import time
from pathlib import Path

STORE_PATH = Path.home() / ".config" / "rbl" / "load_calibration.json"


def load_all() -> dict:
    """{amp_label: {"c_pf", "g_us", "measured_at", "load_condition", "method"}}.

    Returns {} on any failure (missing file, corrupt JSON) — a bad or absent
    store must never prevent the app from starting, and callers fall back to
    CAL_LOAD_CAP_PF exactly as if no measurement had ever been taken.
    """
    try:
        with open(STORE_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def save_measurement(amp_label: str, c_pf: float, g_us: float,
                      load_condition: str, method: str,
                      measured_at: float = None) -> None:
    """Persist one channel's measurement, overwriting any prior one for that
    label. Never raises — a failure to persist must not fail a measurement
    run that already completed successfully."""
    data = load_all()
    data[amp_label] = {
        "c_pf": c_pf,
        "g_us": g_us,
        "measured_at": measured_at if measured_at is not None else time.time(),
        "load_condition": load_condition,
        "method": method,
    }
    try:
        STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(STORE_PATH, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def capacitance_pf_for(amp_label: str):
    """Stored c_pf for `amp_label`, or None if this channel has never been
    measured (the "fall back to CAL_LOAD_CAP_PF" case)."""
    entry = load_all().get(amp_label)
    return entry["c_pf"] if entry else None


def measurement_for(amp_label: str):
    """The full stored record for `amp_label`, or None."""
    return load_all().get(amp_label)


if __name__ == "__main__":
    import tempfile
    import rbl.config.load_calibration_store as mod

    with tempfile.TemporaryDirectory() as d:
        mod.STORE_PATH = Path(d) / "sub" / "load_calibration.json"

        assert load_all() == {}
        assert capacitance_pf_for("X+") is None
        print("[OK] missing store reads back empty, falls back cleanly")

        save_measurement("X+", c_pf=1180.0, g_us=0.02,
                          load_condition="ON_PLATES", method="impedance_sweep")
        assert capacitance_pf_for("X+") == 1180.0
        assert capacitance_pf_for("X-") is None
        print("[OK] save_measurement/capacitance_pf_for round-trip, per-channel")

        record = measurement_for("X+")
        assert record["method"] == "impedance_sweep"
        assert record["load_condition"] == "ON_PLATES"
        assert "measured_at" in record
        print("[OK] full record carries method/load_condition/measured_at")

        # Overwriting a channel does not disturb another channel's record.
        save_measurement("Y+", c_pf=1250.0, g_us=0.0,
                          load_condition="DISCONNECTED", method="charge_integral")
        save_measurement("X+", c_pf=1195.0, g_us=0.05,
                          load_condition="ON_PLATES", method="impedance_sweep")
        assert capacitance_pf_for("X+") == 1195.0
        assert capacitance_pf_for("Y+") == 1250.0
        print("[OK] per-channel overwrite does not disturb other channels")

    print("\n[OK] load_calibration_store self-test passed")
