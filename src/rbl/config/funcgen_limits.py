"""
funcgen_limits.py
Generator output limits for the RIGOL DG1022Z driving the EEL5000.

WHY THIS EXISTS
---------------
These are bare numeric constants, not instrument protocol — they belong in
config, not in the driver.  calibration_config imported MAX_GEN_VOLTS from
rbl.hardware.funcgen_driver, which in turn imports pyvisa; that caused every
config-layer consumer of calibration constants to drag pyvisa into its import
chain even on machines without VISA installed.

Moving the constants here breaks that dependency: funcgen_driver now imports
from here and re-exports for callers that already point at the driver, so
there is no change in runtime values or call sites.
"""

# ES5 plate rating = 5 kV/plate; EEL5000 gain = 1000x, range +/-5 kV.
# The amplifier input tolerates up to +/-5 V (= +/-5 kV/plate). That +/-5 V
# rail is the hard ceiling for the DC OFFSET and for the instantaneous voltage
# the amplifier sees.
MAX_GEN_VOLTS: float = 5.0

# Amplitude is entered peak-to-peak (RIGOL's native unit). A centred sine
# swings +/-amplitude/2 about the offset, so a 10 Vpp wave at 0 offset
# reaches the full +/-5 V (= +/-5 kV) rail.  Hence amplitude alone is allowed
# up to 2 x the rail.  This per-field cap does NOT by itself bound the
# instantaneous voltage: an offset plus half the peak-to-peak amplitude can
# still exceed 5 V. That combined "true peak" limit
# (|offset| + amplitude/2 <= 5 V, warn above 4 V) is enforced in the GUI at
# apply time (funcgen_tab.py). Adjust these here only.
MAX_AMP_VPP: float = 2.0 * MAX_GEN_VOLTS   # 10 Vpp -> +/-5 V peak at 0 offset
