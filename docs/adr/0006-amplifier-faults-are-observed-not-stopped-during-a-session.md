# ADR 0006 - During an irradiation the application observes amplifier faults; it does not stop them

- **Status:** Accepted
- **Date:** 2026-10-06

## Context

The EEL5000 amplifiers protect themselves. In LIMIT mode an amplifier clamps its current
and stops following its input; past a limit the manufacturer has not yet described, it
shuts itself down. The application can detect both from the monitors: the regulation
detector compares commanded and measured voltage and tells "current limited" from
"amplifier off", and the spike recorder watches the current.

The regulation detector's existing response turns the channel off and opens a modal
dialog. It was written for calibration runs, where a reading taken while an amplifier is
limiting is invalid data. During an irradiation the trade-off is the opposite: the
amplifier can often keep running, and an application still under development stopping a
multi-hour irradiation because it was over-cautious costs more than the event it reacted
to. The purpose during an irradiation is to understand these events, not to prevent them
in software.

## Decision

1. **During a session and during a drift pass, the application never turns an amplifier
   output off and never opens a blocking dialog because of a spike or a regulation
   fault.** It records the event, saves the raw monitor waveform around it, and shows a
   non-blocking warning on the Overview tab that stays until the operator dismisses it.

2. **Characterization and calibration runs keep their aborts** - the hard trip, the soft
   trip, and the Mode C ladder's abort rules - because those runs push toward the limits
   on purpose and their data is only valid while the amplifier is following its input.

3. **The amplifier's own LIMIT and TRIP remain the protection.** The application is the
   witness.

## Consequences

- The regulation detector gets a second, observe-only response for sessions and drift
  passes. Its stopping response stays for calibration.
- The vacuum HV interlock is not a spike or regulation response and is unaffected.
- If evidence later shows a fault the amplifier does not catch itself, revisit this
  record rather than adding an automatic stop beside it.
