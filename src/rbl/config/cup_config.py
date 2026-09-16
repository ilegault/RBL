"""
cup_config.py
Constants for the Keithley 6482 picoammeter and Faraday cup acquisition / actuation.

WHY THIS EXISTS
---------------
No tunable numbers live inside widgets or services. Polling intervals,
acquisition trigger thresholds, debounce intervals, backoff schedules, digital I/O
lines, and timeout parameters are defined here as named constants with their
physical and operational rationale documented.

POLLING RATES
-------------
- Idle polling runs at 2 Hz (0.5 s period): provides responsive detection of cup
  insertion while keeping bus traffic low.
- Acquiring polling runs at 10 Hz (0.1 s period): matches the log-amp stream
  refresh rate so that the slit currents and cup current views tick together.

THRESHOLD-TRIGGERED ACQUISITION (ADR 0002)
------------------------------------------
The Faraday cup can be inserted manually and carries no limit switches or position
encoder. In manual mode, insertion is inferred from current:
- Arm threshold: 0.5 µA (current above this starts the debounce timer).
- Arm debounce: 1.0 s (current must remain above arm threshold continuously to open a run).
- Release threshold: 0.25 µA (must be strictly lower than arm threshold for hysteresis).
- Release interval: 3.0 s (current must remain below release threshold continuously to close a run).

DIGITAL ACTUATION AND STATUS DECODING (ADR 0003)
------------------------------------------------
The Faraday Cup Controller ESM remote connector is operated via the LabJack T7
and an LJTick-RelayDriver (LJTRD) with relay isolation:
- Enable line (FIO0): closes relay 1 across ESM P1 5-13, enabling cup OUT control.
- Command line (FIO1): closes relay 2 across ESM P1 1-9, commanding cup OUT.
- Cup IN status (FIO2): isolated dry contact to GND (bit 2).
- Cup OUT status (FIO3): isolated dry contact to GND (bit 3).
- Controller AUTO mode (FIO4): isolated dry contact to GND (bit 4).

Polarity:
Closed is zero. When a status contact closes, it pulls the FIO line to GND,
reading as bit value 0 in FIO_STATE. The decoding function inverts.
Fails-into-the-beam:
Cup OUT requires both closures (enable and command). Any loss of drive opens both
relays, and the cup pneumatic cylinder returns IN, intercepting the beam ahead
of the specimen.

TIMING AND DEBOUNCE (ADR 0003)
------------------------------
- Move confirmation timeout: 2.0 s. If a commanded move does not confirm within
  this window, a fault is raised and the sampling cycle is disarmed.
- Contact debounce: 0.05 s (50 ms). Sized for mechanical relay and microswitch bounce,
  applied to confirmed transitions.

TWO CONSTANT SETS — DO NOT MERGE (ADR 0003 Decision 4)
-------------------------------------------------------
CUP_ARM_DEBOUNCE_S and CUP_RELEASE_INTERVAL_S own the CURRENT-INFERENCE path.
They are sized for a manual insertion lasting minutes: 1.0 s to confirm the cup is
in the beam, 3.0 s to confirm it has left. Hand insertions still happen and still
need that hysteresis. Do NOT reuse or lower these for the confirmed-position path.

CUP_CONTACT_DEBOUNCE_S owns the CONFIRMED-POSITION path. It is sized for mechanical
relay and microswitch bounce (50 ms), not for insertion duration. CupPositionDetector
uses only this constant; CupDetector uses only the inference constants above. Using
one set of numbers for both mechanisms is how the next person inadvertently breaks
both: a hand insertion debounced at 50 ms would fire on every contact chatter,
while a sampling insertion debounced at 1.0 s would never produce a run at all.
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

# ---- Session Logging & Heartbeat (ADR 0002) -------------------------------
# Periodic idle heartbeat interval in seconds while watching out-of-beam baseline.
CUP_IDLE_HEARTBEAT_INTERVAL_S: float = 10.0

# ---- Display & Stale-data guard -------------------------------------------
# A snapshot older than this many seconds is considered stale.
CUP_STALE_THRESHOLD_S: float = 3.0 * CUP_POLL_INTERVAL_IDLE_S

# ---- Digital I/O lines & status bit positions (ADR 0003) -----------------
# CB37 terminal board assignments for Faraday cup actuation and status contacts
CUP_ENABLE_LINE: str = "FIO0"          # Relay 1 -> ESM P1 5-13 (enable cup OUT control)
CUP_COMMAND_OUT_LINE: str = "FIO1"     # Relay 2 -> ESM P1 1-9 (command cup OUT)
CUP_ENABLE_OUT_LINE: str = CUP_ENABLE_LINE  # Alias for explicit role naming

# Status bit positions in the raw FIO_STATE integer
# Active-low dry contacts to GND: 0 = closed (asserted), 1 = open (not asserted)
CUP_STATUS_BIT_IN: int = 2             # FIO2: cup IN contact
CUP_STATUS_BIT_OUT: int = 3            # FIO3: cup OUT contact
CUP_STATUS_BIT_AUTO: int = 4           # FIO4: controller AUTO mode contact

# ---- Actuation timing & debounce (ADR 0003) ------------------------------
CUP_MOVE_CONFIRMATION_TIMEOUT_S: float = 2.0  # Seconds before unconfirmed move raises a fault
CUP_CONTACT_DEBOUNCE_S: float = 0.05           # Seconds of sustained reading for contact bounce


