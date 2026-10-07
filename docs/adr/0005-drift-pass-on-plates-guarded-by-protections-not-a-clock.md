# ADR 0005 - A drift pass on the plates is guarded by running protections, not by a time cap

- **Status:** Accepted
- **Date:** 2026-10-06

## Context

A drift pass holds one setpoint on one or more amplifiers and records how the output
wanders over hours. Until now its length depended on the load condition: up to 12 h with
the amplifier disconnected, but at most 2 h on the plates.

Irradiations run on the plates for 12 h or more. The drift pass exists to show that the
amplifiers hold the exact waveform of a real experiment for that long. A 2 h cap on the
plates means the one test that matches real operation cannot be run. The cap stood in for
something else: not leaving high voltage on the plates for hours with nothing watching.

## Decision

1. **A drift pass on the plates has no time limit.** The operator enters the duration.

2. **It may start, and keeps running, only while two protections are running:** the
   vacuum HV interlock has a known pressure and permits the commanded voltage, and the
   current spike recorder is recording every driven amplifier. If either stops, the pass
   ends and says which one stopped it.

3. **A drift pass drives the experiment's own waveform.** The operator enters the shape,
   frequency and amplitude of the irradiation it stands for; the pass adds nothing and
   changes nothing about that drive.

## Consequences

- The time-based guard and the attended/unattended split for plate runs go away. The
  disconnected 12 h limit is unaffected.
- A long plate run depends on the spike recorder existing. Until it does, this pass cannot
  start on the plates at all, which is stricter than today, not looser.
- What the pass does when a spike or a regulation fault occurs is decided in ADR 0006: it
  records and warns, and does not stop.
