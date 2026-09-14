"""
cup_config.py
Constants for the Keithley 6482 picoammeter and Faraday cup acquisition.

WHY THIS EXISTS
---------------
No tunable numbers live inside widgets or services. Polling intervals,
acquisition trigger thresholds, debounce intervals, and backoff schedules
are defined here as named constants with their physical and operational
rationale documented.

POLLING RATES
-------------
- Idle polling runs at 2 Hz (0.5 s period): provides responsive detection of cup
  insertion while keeping bus traffic low.
- Acquiring polling runs at 10 Hz (0.1 s period): matches the log-amp stream
  refresh rate so that the slit currents and cup current views tick together.

THRESHOLD-TRIGGERED ACQUISITION (ADR 0002)
------------------------------------------
The Faraday cup is inserted manually and carries no limit switches or position
encoder. Insertion is inferred from current:
- Arm threshold: 0.5 µA (current above this starts the debounce timer).
- Arm debounce: 1.0 s (current must remain above arm threshold continuously to open a run).
- Release threshold: 0.25 µA (must be strictly lower than arm threshold for hysteresis).
- Release interval: 3.0 s (current must remain below release threshold continuously to close a run).
"""

# ---- Polling rates --------------------------------------------------------
CUP_POLL_RATE_IDLE_HZ: float = 2.0
CUP_POLL_INTERVAL_IDLE_S: float = 1.0 / CUP_POLL_RATE_IDLE_HZ  # 0.5 s

CUP_POLL_RATE_ACQUIRING_HZ: float = 10.0
CUP_POLL_INTERVAL_ACQUIRING_S: float = 1.0 / CUP_POLL_RATE_ACQUIRING_HZ  # 0.1 s

# ---- Hardware & VISA defaults --------------------------------------------
# Standard GPIB primary address 14 on GPIB interface 0
KEITHLEY_6482_DEFAULT_RESOURCE: str = "GPIB0::14::INSTR"

# ---- Reconnect backoff schedule -------------------------------------------
# Seconds to wait between reconnect attempts after a VISA communication failure.
CUP_RECONNECT_BACKOFF_S: list[float] = [1.0, 2.0, 5.0, 10.0, 30.0]

# ---- Acquisition trigger thresholds (ADR 0002) ---------------------------
CUP_ARM_THRESHOLD_A: float = 0.5e-6        # 0.5 µA to trigger run arming
CUP_RELEASE_THRESHOLD_A: float = 0.25e-6   # 0.25 µA to trigger run release
CUP_ARM_DEBOUNCE_S: float = 1.0            # 1.0 s sustained reading before run opens
CUP_RELEASE_INTERVAL_S: float = 3.0        # 3.0 s below release threshold before run closes

# ---- Display & Stale-data guard -------------------------------------------
# A snapshot older than this many seconds is considered stale.
CUP_STALE_THRESHOLD_S: float = 3.0 * CUP_POLL_INTERVAL_IDLE_S
