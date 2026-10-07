# ADR 0007 - The EEL5000 current monitor reads 1 V = 2 mA, on the manufacturer's word

- **Status:** Accepted
- **Date:** 2026-10-07

## Context

Every current RBL computes from an EEL5000 amplifier passes through one conversion
factor, `CURRENT_MONITOR_MA_PER_VOLT` in `src/rbl/config/hardware_config.py`. It is
10.0, from the operator's manual ("1 V = 10 mA").

On 2026-10-07 the manufacturer said the manual has two errors on current. The current
monitor is scaled **1 V = 2 mA** (±10 V = ±20 mA), and the current limit pot runs
0.5-20 mA, not 0.5-10 mA (`docs/hardware/eel5000-manufacturer-notes.md`).

The usual way to settle this is to drive a known resistor and compare the monitor
with a meter. That needs the amplifier output disconnected from the steerer plates,
and on this beamline it cannot be disconnected.

The manufacturer's figure is consistent with everything else known. The corrected
plate capacitance (~300 pF instead of ~1.5 nF) is close to the geometry estimate
(~125 pF plus an amplifier output network). It is also under the manual's
1 nF factory-adjustment line. And the monitor's ±10 V span then equals the pot's
20 mA maximum.

## Decision

1. **The current monitor scale is 1 V = 2 mA.** `CURRENT_MONITOR_MA_PER_VOLT` becomes
   2.0. The manual's 1 V = 10 mA is wrong for our units.
2. **The source is the manufacturer, not a bench measurement.** Every place this
   factor is documented says so.
3. **A reading at the end of the monitor's ±10 V span (±20 mA) is a lower bound** on
   the current, not a measurement of it.

## Consequences

- Every current, capacitance, noise figure and current-derived threshold recorded
  before this decision is 5x too high. Old results are not silently reinterpreted.
  How they are handled is decided separately.
- Thresholds written in the old units (`CAL_AC_TRIP_MA`, `CAL_TRIP_HARD_MA`, spike
  warning levels) fired at a fifth of their stated true current. Correcting the factor
  moves them to their stated values. The hard trip as defined in `CONTEXT.md` relies
  on a railed monitor reading as a huge number. Under this scale a railed monitor
  reads 20 mA, so the hard trip must be redefined in the same change, not after it.
- The clamp test with the pot at dial 50 (10 mA) is a cross-check that needs no
  disconnection. The monitor should flatten near 5 V.
- **Revisit this record** if the clamp test flattens near 1 V, or if the manufacturer
  says the scale depends on a configuration our units are in. Do not add a second
  conversion factor beside this one.
