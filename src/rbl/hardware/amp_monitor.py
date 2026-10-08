"""
amp_monitor.py
Convert EEL5000.20.100 front-panel monitor voltages -> physical units.

From the EEL5000 manual (Specifications, p. 1-3):
    VOLTAGE MONITOR : 1000:1 representation of the HV output.
                      1 V at the BNC == 1000 V == 1 kV at the output.
                      Accuracy 0.1% of full scale.
    CURRENT MONITOR : 1 V at the BNC == 2 mA drawn from the amplifier (ADR 0007).
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
from typing import Union, overload

import numpy as np

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

    1 V == 2 mA (ADR 0007). Returns NaN only for NaN input or |voltage| > 11.0 V
    (beyond the LabJack T7's +/-10 V range plus 10% headroom, which is garbage).
    """
    if voltage is None or math.isnan(voltage):
        return float("nan")
    if abs(voltage) > 11.0:
        return float("nan")
    return voltage * SC.CURRENT_MONITOR_MA_PER_VOLT


def ma_to_monitor(ma: float) -> float:
    """Inverse of monitor_to_ma: mA -> CURRENT MONITOR volts.

    Deliberately unclamped, unlike monitor_to_ma.  Interlocks compare raw
    monitor samples against a threshold, and it is cheaper to convert the one
    threshold into volts than to convert every sample in a 5000-point window
    into mA.  There is also no plausibility range to enforce here: the caller
    supplies the limit, and a limit is by definition in range.
    """
    return ma / SC.CURRENT_MONITOR_MA_PER_VOLT


def ma_unclamped(voltage: float) -> float:
    """CURRENT MONITOR volts -> mA, with no plausibility clamp.

    monitor_to_ma returns NaN above ~110 mA to flag a nonsense reading. That
    is right for display but wrong for an interlock: NaN loses every numeric
    comparison, so the single largest reading the hardware can produce — a
    dead short railing the monitor — would silently fail an `if peak > limit`
    test. Safety code needs the number, however implausible it looks.
    """
    if voltage is None or math.isnan(voltage):
        return float("nan")
    return voltage * SC.CURRENT_MONITOR_MA_PER_VOLT


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
 
CURRENT_STATUSES = ("ok", "at_limit", "over")


def current_status(ma: float) -> str:
    """Classify a current reading against the EEL5000's ratings and monitor rail.

    'ok'       : below the monitor rail
    'at_limit' : at or beyond the current-monitor rail (|ma| >= 19.8 mA),
                 true current is at least this high but unknown
    'over'     : NaN or invalid reading
    """
    if ma is None or (isinstance(ma, float) and math.isnan(ma)):
        return "over"
    if abs(ma) >= SC.CURRENT_MONITOR_RAIL_VOLTS * SC.CURRENT_MONITOR_MA_PER_VOLT:
        return "at_limit"
    return "ok"


def voltage_status(kv: float) -> str:
    """'ok' within +/-5 kV, 'over' outside it (or NaN)."""
    if kv is None or (isinstance(kv, float) and math.isnan(kv)):
        return "over"
    return "ok" if abs(kv) <= SC.AMP_MAX_KV else "over"


# --- Push-pull pair check ----------------------------------------------------

def pair_correlation(a, b) -> float:
    """Pearson correlation between two same-window waveforms, or NaN.

    A push-pull pair driven correctly sits at -1: every volt one plate rises,
    the other falls by the same amount. It stays at -1 for any amplitude and
    any frequency, which is what makes it a phase check rather than a
    disguised amplitude comparison.

    It leaves -1 when the two channels stop being mirror images — which, for
    an X or Y pair on ONE generator, means a misapplied start-phase, and for
    the X pair against the Y pair means the two units' clocks have drifted.

    Both series must come from the same stream window; comparing across
    windows would measure the sampling, not the signals.
    """
    n = min(len(a), len(b))
    if n < 2:
        return float("nan")
    a, b = list(a[:n]), list(b[:n])
    if any(math.isnan(v) for v in a) or any(math.isnan(v) for v in b):
        return float("nan")

    mean_a = sum(a) / n
    mean_b = sum(b) / n
    da = [v - mean_a for v in a]
    db = [v - mean_b for v in b]
    cov = sum(x * y for x, y in zip(da, db))
    var_a = sum(x * x for x in da)
    var_b = sum(y * y for y in db)
    if var_a <= 0.0 or var_b <= 0.0:
        # One channel is flat: a dead plate has no phase, so there is no
        # relationship to report. Saying "0" would read as "out of phase".
        return float("nan")
    return cov / math.sqrt(var_a * var_b)


# --- Rail tracking -----------------------------------------------------------

@overload
def is_at_rail(volts: float) -> bool: ...

@overload
def is_at_rail(volts: np.ndarray) -> np.ndarray: ...

def is_at_rail(volts: Union[float, np.ndarray]) -> Union[bool, np.ndarray]:
    """Return True (or bool array) where |volts| >= CURRENT_MONITOR_RAIL_VOLTS.

    NaN gives False. Pure function accepting a float or numpy array.
    """
    if isinstance(volts, np.ndarray):
        abs_v = np.abs(np.where(np.isnan(volts), 0.0, volts))
        return abs_v >= SC.CURRENT_MONITOR_RAIL_VOLTS
    if volts is None or (isinstance(volts, (float, int, np.floating)) and math.isnan(volts)):
        return False
    return abs(volts) >= SC.CURRENT_MONITOR_RAIL_VOLTS


class RailTracker:
    """Track contiguous at-the-rail runs across stream windows.

    Pure: numpy only, no Qt, no clock, no hardware. That is what makes it
    testable with synthetic arrays.
    """

    def __init__(self) -> None:
        self._open_run: int = 0

    def feed(self, volts: np.ndarray, dt_s: float) -> float:
        """Feed a window of raw current-monitor volts.

        Returns the longest contiguous at-the-rail run, in seconds, among
        the runs that include at least one sample of this window.
        A run still at the rail at the end of the previous feed continues
        into this one, and its earlier samples count.
        Duration is sample count * dt_s.
        A NaN sample or a sample below the rail ends a run.
        """
        arr = np.asarray(volts, dtype=float).ravel()
        if len(arr) == 0:
            return 0.0

        mask = is_at_rail(arr)
        max_run = 0
        current_run = self._open_run

        for m in mask:
            if m:
                current_run += 1
                if current_run > max_run:
                    max_run = current_run
            else:
                current_run = 0

        self._open_run = current_run
        return max_run * dt_s

    def reset(self) -> None:
        """Forget any open run."""
        self._open_run = 0


# --- Self-test ---------------------------------------------------------------

if __name__ == "__main__":
    # Voltage monitor: 1 V == 1 kV
    assert abs(monitor_to_kv(0.0) - 0.0) < 1e-12
    assert abs(monitor_to_kv(1.0) - 1.0) < 1e-12
    assert abs(monitor_to_kv(4.0) - 4.0) < 1e-12
    assert abs(monitor_to_kv(-5.0) - (-5.0)) < 1e-12
    assert math.isnan(monitor_to_kv(9.0))       # 9 kV — impossible, flag it
    assert math.isnan(monitor_to_kv(float("nan")))

    # Current monitor: 1 V == 2 mA (ADR 0007)
    assert abs(monitor_to_ma(0.0) - 0.0) < 1e-12
    assert abs(monitor_to_ma(1.0) - 2.0) < 1e-9
    assert abs(monitor_to_ma(5.0) - 10.0) < 1e-9
    assert abs(monitor_to_ma(10.0) - 20.0) < 1e-9     # DC rating
    assert abs(monitor_to_ma(-10.0) - (-20.0)) < 1e-9
    assert math.isnan(monitor_to_ma(float("nan")))

    # Status
    assert voltage_status(4.0)  == "ok"
    assert voltage_status(-5.0) == "ok"
    assert voltage_status(6.0)  == "over"
    assert current_status(10.0) == "ok"
    assert current_status(19.7) == "ok"
    assert current_status(-19.7) == "ok"
    assert current_status(19.8) == "at_limit"
    assert current_status(-20.0) == "at_limit"
    assert current_status(50.0) == "at_limit"
    assert current_status(150.0) == "at_limit"
    assert current_status(float("nan")) == "over"

    # Formatting
    assert "kV" in format_kv(3.5)
    assert "V"  in format_kv(0.25)
    assert "mA" in format_ma(15.0)
    assert "µA" in format_ma(0.5)
    assert "—"  in format_kv(float("nan"))
    assert "—"  in format_ma(float("nan"))

    # Rail tracking
    assert is_at_rail(9.9) is True
    assert is_at_rail(-9.95) is True
    assert is_at_rail(9.89) is False
    assert is_at_rail(float("nan")) is False
    assert np.array_equal(
        is_at_rail(np.array([0.0, 10.0, -10.0, np.nan])),
        [False, True, True, False],
    )
    _rt = RailTracker()
    assert abs(_rt.feed(np.array([0.0] * 5 + [10.0] * 10 + [0.0] * 5), 1e-4) - 0.001) < 1e-12

    # Buffer is the shared one, not a copy
    from rbl.hardware.current_monitor import RollingBuffer as _RB
    assert RollingBuffer is _RB

    print("[OK] amp_monitor self-test passed")
    print(f"    1.000 V mon -> {format_kv(monitor_to_kv(1.0))}")
    print(f"    2.000 V mon -> {format_ma(monitor_to_ma(2.0))}")
