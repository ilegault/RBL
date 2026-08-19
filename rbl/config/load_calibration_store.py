"""
load_calibration_store.py
Per-channel measured load capacitance/conductance, persisted across
sessions — the measurement Phase 1 (rbl/services/load_characterizer.py)
produces, replacing `calibration_config.CAL_LOAD_CAP_PF`'s single global
guess with a number the app can reproduce and refresh on demand, per amp
channel.

WHY KEYED BY (amp_label, load_condition)
-------------------------------------------
Section 2.4 of docs/AMP_ENVELOPE_AND_HV_SAFETY_PLAN.md makes answering the
~1 nF capacitance discrepancy a first-class feature: run Mode A once with
`LoadCondition.DISCONNECTED` (amplifier + internal network only) and once
with `LoadCondition.ON_PLATES` (everything), then let the app show the two
side by side and their difference. A store that overwrote one condition's
measurement with the other's would make that comparison impossible the
moment the second sweep finished — so both are kept, per channel.

WHY A SEPARATE FILE FROM rbl/config/persistence.py
-----------------------------------------------------
`persistence.py` is scoped to funcgen SCPI config, keyed by instrument
serial. This is a different kind of fact (a MEASUREMENT, not a setting) with
a different key, so it gets its own small on-disk store rather than growing
persistence.py's schema into two unrelated shapes under one file.

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
    """{amp_label: {load_condition: {"c_pf", "g_us", "measured_at", "method"}}}.

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
    """Persist one channel's measurement under its load_condition, leaving
    any OTHER load_condition already recorded for this channel untouched —
    see the module docstring. Never raises: a failure to persist must not
    fail a measurement run that already completed successfully."""
    data = load_all()
    per_channel = data.setdefault(amp_label, {})
    per_channel[load_condition] = {
        "c_pf": c_pf,
        "g_us": g_us,
        "measured_at": measured_at if measured_at is not None else time.time(),
        "method": method,
    }
    try:
        STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(STORE_PATH, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def measurement_for(amp_label: str, load_condition: str = None):
    """The stored record for `amp_label`.

    With `load_condition` given, returns that specific record or None.
    Without it, returns the MOST RECENTLY measured record across whichever
    condition(s) exist for this channel — the convenient default for
    ladder-sizing, which does not care which condition produced the estimate.
    """
    per_channel = load_all().get(amp_label)
    if not per_channel:
        return None
    if load_condition is not None:
        return per_channel.get(load_condition)
    return max(per_channel.values(), key=lambda r: r.get("measured_at", 0.0))


def capacitance_pf_for(amp_label: str, load_condition: str = None):
    """Stored c_pf for `amp_label` (see `measurement_for`), or None if never
    measured under the requested condition (or at all)."""
    record = measurement_for(amp_label, load_condition)
    return record["c_pf"] if record else None


def comparison_for(amp_label: str) -> dict:
    """Both load conditions' records for `amp_label`, plus their difference —
    the Section 2.4 "DISCONNECTED vs ON_PLATES" view.

    Returns {"DISCONNECTED": record|None, "ON_PLATES": record|None,
             "diff_c_pf": float|None}. `diff_c_pf` is ON_PLATES.c_pf minus
    DISCONNECTED.c_pf (the external load's own capacitance, per Section 2.4:
    "the difference is the external load"), or None if either side is
    missing.
    """
    per_channel = load_all().get(amp_label, {})
    disconnected = per_channel.get("DISCONNECTED")
    on_plates = per_channel.get("ON_PLATES")
    diff = None
    if disconnected and on_plates:
        diff = on_plates["c_pf"] - disconnected["c_pf"]
    return {"DISCONNECTED": disconnected, "ON_PLATES": on_plates, "diff_c_pf": diff}


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        STORE_PATH = Path(d) / "sub" / "load_calibration.json"

        assert load_all() == {}
        assert capacitance_pf_for("X+") is None
        print("[OK] missing store reads back empty, falls back cleanly")

        save_measurement("X+", c_pf=1180.0, g_us=0.02,
                          load_condition="ON_PLATES", method="impedance_sweep")
        assert capacitance_pf_for("X+") == 1180.0
        assert capacitance_pf_for("X+", "ON_PLATES") == 1180.0
        assert capacitance_pf_for("X+", "DISCONNECTED") is None
        assert capacitance_pf_for("X-") is None
        print("[OK] save_measurement/capacitance_pf_for round-trip, per-channel and per-condition")

        record = measurement_for("X+", "ON_PLATES")
        assert record["method"] == "impedance_sweep"
        assert "measured_at" in record
        print("[OK] full record carries method/measured_at")

        # Both conditions coexist for the same channel — the whole point.
        save_measurement("X+", c_pf=125.0, g_us=0.0,
                          load_condition="DISCONNECTED", method="impedance_sweep")
        assert capacitance_pf_for("X+", "ON_PLATES") == 1180.0
        assert capacitance_pf_for("X+", "DISCONNECTED") == 125.0
        cmp = comparison_for("X+")
        assert cmp["diff_c_pf"] == 1180.0 - 125.0
        print(f"[OK] DISCONNECTED and ON_PLATES coexist: diff_c_pf={cmp['diff_c_pf']:.1f} pF "
              "(the external load)")

        # Overwriting a channel's condition does not disturb another channel.
        save_measurement("Y+", c_pf=1250.0, g_us=0.0,
                          load_condition="DISCONNECTED", method="charge_integral")
        assert capacitance_pf_for("X+", "ON_PLATES") == 1180.0
        assert capacitance_pf_for("Y+", "DISCONNECTED") == 1250.0
        print("[OK] per-channel writes do not disturb other channels")

    print("\n[OK] load_calibration_store self-test passed")
