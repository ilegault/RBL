"""
current_monitor.py
Convert NEC log-amp output voltages (V) -> beam current (A).

The log amp produces 1 V per decade of input current, 1 nA -> 1 mA.
Variants:
  - 0-6V models: 0 V = 1 nA, 6 V = 1 mA  (V ascends with current)
  - 9-3V models: 9 V = 1 nA, 3 V = 1 mA  (V descends with current)

Pure math + a thread-safe rolling buffer. No hardware.
"""
import math
import threading

import numpy as np


# --- Conversion --------------------------------------------------------------

def voltage_to_current(voltage: float, v_at_1nA: float, v_at_1mA: float) -> float:
    """Log-amp voltage -> current in Amps.

    Returns NaN if the voltage is more than 0.5 V outside the calibrated range
    (likely an open input or wiring problem)."""
    v_min = min(v_at_1nA, v_at_1mA)
    v_max = max(v_at_1nA, v_at_1mA)
    if voltage < v_min - 0.5 or voltage > v_max + 0.5:
        return float("nan")
    # log10(I_nA) ranges 0 -> 6 over the 6 V span between v_at_1nA and v_at_1mA
    log10_I_nA = (voltage - v_at_1nA) / (v_at_1mA - v_at_1nA) * 6.0
    return 10.0 ** (log10_I_nA - 9.0)


def format_current(current_A: float) -> str:
    """Auto-scale a current value to nA / µA / mA for display."""
    if current_A is None or (isinstance(current_A, float) and math.isnan(current_A)):
        return "  —    "
    abs_I = abs(current_A)
    if abs_I < 1e-6:
        return f"{current_A * 1e9:7.2f} nA"
    elif abs_I < 1e-3:
        return f"{current_A * 1e6:7.2f} µA"
    else:
        return f"{current_A * 1e3:7.2f} mA"


def beam_centering(i_plus: float, i_minus: float) -> float:
    """Beam-centering metric: (I+ - I-) / (I+ + I-).

    0   -> beam perfectly centered between the two jaws.
    >0  -> beam toward the '+' jaw.
    <0  -> beam toward the '-' jaw.
    NaN -> not enough signal to be meaningful."""
    if (i_plus is None or i_minus is None
        or math.isnan(i_plus) or math.isnan(i_minus)):
        return float("nan")
    denom = i_plus + i_minus
    if denom <= 0:
        return float("nan")
    return (i_plus - i_minus) / denom


# --- Rolling buffer (one channel) -------------------------------------------

class RollingBuffer:
    """Fixed-capacity rolling buffer for live plotting, thread-safe."""

    def __init__(self, capacity: int):
        self.capacity = capacity
        self._buf     = np.full(capacity, np.nan, dtype=float)
        self._t       = np.full(capacity, np.nan, dtype=float)
        self._n       = 0
        self._lock    = threading.Lock()

    def append(self, t: float, value: float):
        with self._lock:
            idx = self._n % self.capacity
            self._t[idx]   = t
            self._buf[idx] = value
            self._n += 1

    def snapshot(self):
        """Sorted (t_arr, value_arr) with NaN slots removed."""
        with self._lock:
            t = self._t.copy()
            v = self._buf.copy()
        mask  = ~np.isnan(t)
        t, v  = t[mask], v[mask]
        order = np.argsort(t)
        return t[order], v[order]

    def latest(self):
        with self._lock:
            if self._n == 0:
                return float("nan"), float("nan")
            idx = (self._n - 1) % self.capacity
            return self._t[idx], self._buf[idx]


# --- Self-test ---------------------------------------------------------------

if __name__ == "__main__":
    # 0-6V model
    assert abs(voltage_to_current(0.0, 0.0, 6.0) - 1e-9) < 1e-12
    assert abs(voltage_to_current(3.0, 0.0, 6.0) - 1e-6) < 1e-9
    assert abs(voltage_to_current(6.0, 0.0, 6.0) - 1e-3) < 1e-6
    # 9-3V model
    assert abs(voltage_to_current(9.0, 9.0, 3.0) - 1e-9) < 1e-12
    assert abs(voltage_to_current(6.0, 9.0, 3.0) - 1e-6) < 1e-9
    assert abs(voltage_to_current(3.0, 9.0, 3.0) - 1e-3) < 1e-6
    # Out of range
    assert math.isnan(voltage_to_current(-10.0, 0.0, 6.0))
    assert math.isnan(voltage_to_current( 20.0, 0.0, 6.0))
    # Format
    assert "nA" in format_current(1e-9)
    assert "µA" in format_current(1e-6)
    assert "mA" in format_current(1e-3)
    assert "—"  in format_current(float("nan"))
    # Centering
    assert abs(beam_centering(1e-6, 1e-6))      < 1e-9
    assert beam_centering(2e-6, 1e-6)           > 0
    assert beam_centering(1e-6, 2e-6)           < 0
    assert math.isnan(beam_centering(0.0, 0.0))
    # Rolling buffer
    buf = RollingBuffer(100)
    for i in range(150):
        buf.append(float(i), float(i * 2))
    t, v = buf.snapshot()
    assert len(t) == 100
    assert t[-1] == 149.0
    print("[OK] current_monitor self-test passed")
