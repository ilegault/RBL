"""
amp_monitor.py
Convert EEL5000.20.100 front-panel monitor voltages -> physical units.

From the EEL5000 manual (Specifications, p. 1-3):
    VOLTAGE MONITOR : 1000:1 representation of the HV output.
                      1 V at the BNC == 1000 V == 1 kV at the output.
                      Accuracy 0.1% of full scale.
    CURRENT MONITOR : 1 V at the BNC == 10 mA drawn from the amplifier.
                      Accuracy 1% of full scale.

Both monitors are ground-referenced BNCs with >11 kHz bandwidth. We sample at
10 Hz, so what we record is effectively a time-average of the deflection
waveform, NOT its instantaneous value. On a raster scan driven at kHz rates the
voltage monitor will read near zero mean with the RMS buried inside it. This is
expected and is not a fault: the tab is a health/DC-bias monitor, not a
waveform capture.

Pure math. No hardware, no Qt.
"""
import math

from rbl.config import hardware_config as SC
# Reuse the existing thread-safe rolling buffer. Do not reimplement it.
from rbl.hardware.current_monitor import RollingBuffer  # noqa: F401


# --- Conversion --------------------------------------------------------------

def monitor_to_kv(voltage: float) -> float:
    """VOLTAGE MONITOR volts -> amplifier output in kV.

    1000:1, so the BNC reading in volts IS the output in kV.
    Returns NaN for an out-of-plausible-range reading (open input / bad wiring).
    The amplifier is rated +/-5 kV; we allow 10% headroom before flagging.
    """
    if voltage is None or math.isnan(voltage):
        return float("nan")
    kv = voltage * SC.VOLTAGE_MONITOR_KV_PER_VOLT
    if abs(kv) > SC.AMP_MAX_KV * 1.1:
        return float("nan")
    return kv


def monitor_to_ma(voltage: float) -> float:
    """CURRENT MONITOR volts -> amplifier current draw in mA.

    1 V == 10 mA. Returns NaN beyond the 100 mA peak rating (+10% headroom),
    which on this scale is 11 V — past the T7's +/-10 V range anyway, so a
    reading there means something is wrong.
    """
    if voltage is None or math.isnan(voltage):
        return float("nan")
    ma = voltage * SC.CURRENT_MONITOR_MA_PER_VOLT
    if abs(ma) > SC.AMP_MAX_MA_PK * 1.1:
        return float("nan")
    return ma


def format_kv(kv: float) -> str:
    """Auto-scale kV for display. Sub-kV values shown in volts."""
    if kv is None or (isinstance(kv, float) and math.isnan(kv)):
        return "  —      "
    if abs(kv) < 1.0:
        return f"{kv * 1000.0:8.1f} V "
    return f"{kv:8.3f} kV"


def format_ma(ma: float) -> str:
    """Auto-scale mA for display. Sub-mA values shown in µA."""
    if ma is None or (isinstance(ma, float) and math.isnan(ma)):
        return "  —      "
    if abs(ma) < 1.0:
        return f"{ma * 1000.0:8.1f} µA"
    return f"{ma:8.3f} mA"


# --- Status classification ---------------------------------------------------

def current_status(ma: float) -> str:
    """Classify a current reading against the EEL5000's ratings.

    'ok'      : within the +/-20 mA continuous DC rating
    'peak'    : above 20 mA — only legal as a <4 ms transient. Sustained
                readings here mean the amplifier is being over-driven.
    'over'    : beyond the 100 mA peak rating, or a NaN/garbage reading.
    """
    if ma is None or (isinstance(ma, float) and math.isnan(ma)):
        return "over"
    a = abs(ma)
    if a <= SC.AMP_MAX_MA_DC:
        return "ok"
    if a <= SC.AMP_MAX_MA_PK:
        return "peak"
    return "over"


def voltage_status(kv: float) -> str:
    """'ok' within +/-5 kV, 'over' outside it (or NaN)."""
    if kv is None or (isinstance(kv, float) and math.isnan(kv)):
        return "over"
    return "ok" if abs(kv) <= SC.AMP_MAX_KV else "over"


# --- Self-test ---------------------------------------------------------------

if __name__ == "__main__":
    # Voltage monitor: 1 V == 1 kV
    assert abs(monitor_to_kv(0.0) - 0.0) < 1e-12
    assert abs(monitor_to_kv(1.0) - 1.0) < 1e-12
    assert abs(monitor_to_kv(4.0) - 4.0) < 1e-12
    assert abs(monitor_to_kv(-5.0) - (-5.0)) < 1e-12
    assert math.isnan(monitor_to_kv(9.0))       # 9 kV — impossible, flag it
    assert math.isnan(monitor_to_kv(float("nan")))

    # Current monitor: 1 V == 10 mA
    assert abs(monitor_to_ma(0.0) - 0.0) < 1e-12
    assert abs(monitor_to_ma(1.0) - 10.0) < 1e-9
    assert abs(monitor_to_ma(2.0) - 20.0) < 1e-9     # DC rating
    assert abs(monitor_to_ma(-2.0) - (-20.0)) < 1e-9
    assert abs(monitor_to_ma(10.0) - 100.0) < 1e-9   # 4 ms peak rating
    assert math.isnan(monitor_to_ma(float("nan")))

    # Status
    assert voltage_status(4.0)  == "ok"
    assert voltage_status(-5.0) == "ok"
    assert voltage_status(6.0)  == "over"
    assert current_status(10.0) == "ok"
    assert current_status(-20.0) == "ok"
    assert current_status(50.0) == "peak"
    assert current_status(150.0) == "over"
    assert current_status(float("nan")) == "over"

    # Formatting
    assert "kV" in format_kv(3.5)
    assert "V"  in format_kv(0.25)
    assert "mA" in format_ma(15.0)
    assert "µA" in format_ma(0.5)
    assert "—"  in format_kv(float("nan"))
    assert "—"  in format_ma(float("nan"))

    # Buffer is the shared one, not a copy
    from rbl.hardware.current_monitor import RollingBuffer as _RB
    assert RollingBuffer is _RB

    print("[OK] amp_monitor self-test passed")
    print(f"    1.000 V mon -> {format_kv(monitor_to_kv(1.0))}")
    print(f"    2.000 V mon -> {format_ma(monitor_to_ma(2.0))}")
