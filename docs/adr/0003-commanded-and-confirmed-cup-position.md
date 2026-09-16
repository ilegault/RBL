# ADR 0003 — Cup position is commanded and confirmed, and the cup fails into the beam

- **Status:** Accepted
- **Date:** 2026-09-15
- **Supersedes:** ADR 0002 decision 6. The rest of ADR 0002 stands.

## Context

ADR 0002 decided that acquisition is triggered by the cup current itself, because the
cup was moved by hand and reported no position. Its decision 6 anticipated this
moment:

> The run logic reads a single "is the cup in the beam?" value. Today that value is
> inferred from current. When an actuator and position feedback exist, that value is
> replaced at its source and the run logic does not change.

The actuator and the position feedback now exist. The Faraday Cup Controller has an
ESM remote connector that accepts dry-contact closures to enable and command cup-out,
and returns isolated contact closures reporting cup IN, cup OUT, and whether the
controller is in AUTO mode. A LabJack T7 with a relay driver can operate both ends.

Two things forced the decision rather than merely allowing it.

**Dose over an eight-hour irradiation cannot be measured by hand.** The quantity that
matters at the end of an irradiation is the integral of transmitted current over the
whole run. The cup is the only absolute current measurement on the beamline — the
four slit currents are a relative centring signal read through log amps and cannot be
integrated into a charge. Measuring the integral therefore requires the cup to enter
the beam on a schedule, for a few seconds, for hours, which is not something a person
does.

**Inference cannot bound a short insertion.** The thresholds in ADR 0002 were sized
for a hand insertion lasting minutes: a one-second debounce before a run opens, three
seconds below the release threshold before it closes. A one-second sampling insertion
never satisfies the debounce, so no run opens at all; a three-second one opens a
second late and closes three seconds after the cup has gone. The current-based
detector is not merely less precise than position for this duty cycle — it does not
work at all.

A third option existed and was rejected: keep inference and lengthen the dwell until
the debounce fits. That trades specimen exposure for nothing. The point of a sampling
insertion is to interrupt the irradiation as briefly as possible, and lengthening it
to accommodate a detector the hardware has made unnecessary is backwards.

## Decision

**1. Confirmed position replaces inferred current as the source of "is the cup in the
beam?"** The value comes from the controller's own status contacts, which report
where the cup is rather than where it was told to go.

**2. Commanded position and confirmed position are distinct, and only confirmed
position opens or closes a run.** There is a real mechanical lag from relay closure
through the controller and solenoid to the cup moving, and its magnitude is not yet
measured. Both timestamps are recorded on every insertion, which measures that lag
continuously as a side effect of normal operation.

**3. A commanded move that does not confirm within a timeout is a fault, not a
retry.** It disarms the sampling cycle and is raised visibly. The controller only
honours remote commands in AUTO mode, so a controller switched to LOCAL accepts a
closure and does nothing — the failure mode this decision exists to catch is an
unattended cycle that appears to run for hours while the cup never moves.

**4. Current inference is retained as fallback and as cross-check.** Position is
authoritative when the status contacts are readable and the controller is in AUTO.
Otherwise inference governs, so a hand insertion still records. Where both are
available and they disagree, the disagreement is written to the record as a fault and
is not resolved in favour of either.

**5. The detector seam is preserved exactly.** The position detector satisfies the
same one-value contract as the current detector, and
`CupAcquisitionStateMachine` does not change. A state machine that chose its
behaviour according to which detector it held would destroy the seam that made this
ADR cheap to write.

**6. The cup fails into the beam.** Cup-out requires two contact closures, so any
loss of drive — crash, device reset, disconnection, loss of coil power — returns the
cup to the beam path, where it intercepts the beam ahead of the specimen. This is a
property of the wiring and no software path is load-bearing for it.

**7. Dose is computed by zero-order hold, and every stage is recorded separately.**
Each insertion's current is held constant across the beam-on interval it represents.
Accumulated charge, fluence and dpa are written as separate columns, and the
displacement coefficient is operator-entered and recorded with its depth, its SRIM
version and its entry date.

## Consequences

The application now moves hardware in the beam path on its own schedule, which ADR
0002 explicitly did not do. That is a genuine increase in what a defect in this code
can cost, and it is why the fail-safe is wiring rather than software and why an
unconfirmed move stops the cycle instead of retrying.

Decision 6 has an uncomfortable edge worth stating: the safe state is also a state
that silently stops the irradiation. A power failure to the relay coils parks the cup
in the beam and the specimen quietly stops receiving dose. That is the right trade —
an interrupted irradiation is recoverable and an unrecorded one is not — but it means
"cup IN" is not by itself evidence that anything is working.

Decision 7 bakes an approximation into the archive. About one percent of an
irradiation is measured and ninety-nine percent is assumed constant between samples.
The error is whatever the beam drifted, it is not bounded by anything the application
can observe, and no downstream analysis can recover what was never sampled. The only
control is the cycle period, which is why it is an operator-editable field rather
than a constant. Every stage of the dose chain is recorded separately so that a later
reader can recompute any of it, and so that a better integration method can be
applied to the same archive if one is ever found.

Retaining inference as a cross-check (decision 4) means the application can report a
disagreement it cannot resolve. That is deliberate. The threshold values in
`cup_config.py` have never been validated against a real insertion, and the
disagreement rows produced over one irradiation are the first evidence anyone will
have about whether they are right.

Decision 5 costs something now to save something later: the detectors' input shapes
differ, and keeping one contract means widening it rather than branching inside the
state machine. The alternative is two run-logic paths that drift apart.
