# CONTEXT

The vocabulary of the Right Beamline (RBL) control application: what each word
means in this project. Terms only — no implementation detail, no design notes,
no plans. Those live in `docs/adr/` and `docs/`.

If a term here and the code disagree, one of them is wrong. Say which, and ask.
Do not silently pick a side.

---

## Beam optics

**Steerer** — the pair of electrostatic deflection plates that bend the beam on
one axis.

**Differential voltage** — the plate-to-plate voltage across a steerer axis.
**Per-plate voltage** is exactly half of it. Voltages are quoted differentially
unless a figure is explicitly labelled per-plate. The two happen to coincide
numerically at a generator gain of 1000, which is why per-plate figures are
always labelled rather than left to context.

**Deflection** — the *angle* the steerer imparts to the beam. Plate length
affects the angle and only the angle.

**Displacement** — the *distance* that angle produces at the point of interest.
It is the deflection angle times the drift distance, and nothing else: no pivot
correction, no geometric fudge.

**Drift distance** — the free flight path between the steerer and the point of
interest. See *Known collisions* below.

**Turnaround** — the region at each end of a raster sweep where the beam
reverses direction and therefore lingers. Sizing a sweep so that the turnaround
falls off the sample (or off the slit jaws) is a deliberate goal, not an
accident of the geometry.

**Slit reconstruction** — inferring where the beam is from the currents landing
on four slit jaws. A centred beam is solvable without knowing the beam width; an
off-centre one is not, and the reported interval widens to say so rather than
returning a confident number.

---

## Instruments and readings

**Window** — one block of samples off the stream, covering a short span of time,
carrying every channel that the active profile scans. The unit in which readings
arrive.

**Profile** — a named scan list. It determines which channels appear in a window
and at what rate. A channel absent from the active profile reads as absent, not
as zero.

**Snapshot** — a typed, converted reading derived from a window. Raw volts become
amps, kilovolts and milliamps exactly once, before any screen sees them. Screens
render snapshots; they do not convert.

**Device view** — one device's state as rendered on one tab. A single device
usually has several views, on different tabs.

**Cross-tab agreement** — the invariant that every view of one device shows the
same value for that device. An operator reading a number off one screen and
acting on it must not be able to get a different number from another screen.

---

## Calibration

**Ladder** — the ordered set of setpoints a calibration sweep visits.
A **rung** is one setpoint on it.

**Pass type** — the order in which one pass traverses the ladder. `up` and
`down` each visit every rung twice, out and back on each polarity; `random`
visits each rung once, shuffled. The three pass types therefore produce
sequences of *different lengths*, by design. Any code or test that assumes they
are the same length is wrong.

**Hard trip** — an immediate, unconditional abort on an over-current reading.
Checked in every state, using the unclamped conversion so that a railed monitor
reads as a huge number rather than as no number at all. It exists to catch a
dead short.

**Soft trip** — an abort raised only once the current has stayed above the
continuous limit for a minimum duration across consecutive windows. It exists to
catch a sustained overload without firing on a transient.

---

## Beam interception and collection

**Slit current** — the current a single slit jaw intercepts. There are four, one
per jaw.

**Cup current** — the current collected by the Faraday cup, which sits
downstream of the slits. It is the part of the beam that passed through the
aperture rather than landing on a jaw. One number, measured in a different place
by a different instrument. It is not a fifth slit current.

**Transmitted current** — a synonym for cup current, preferred where the point
being made is the physics (what got through) rather than which instrument read
it.

**Insertion** — one period during which the Faraday cup is in the beam path. The
cup is moved by hand and reports no position, so the application infers an
insertion from the current it sees rather than being told about it.

**Acquisition run** — the samples recorded across one insertion. One insertion
produces one run.

**Idle ping** — a reading taken at low rate while no run is active. Its only job
is to notice an insertion beginning. It is evidence that the application was
watching; it is not measurement data.

**Arm threshold** and **release threshold** — the cup currents at which a run
starts and ends. The release threshold is deliberately the lower of the two, so
a current sitting near the boundary cannot start and stop runs repeatedly.

---

## Known collisions

**"Beam current" means two things.** The four slit currents and the one cup
current are both current from the beam, read in different places, and they do
not agree with each other — nor should they. Prefer *slit current* and *cup
current*. Do not write *beam current* unqualified.

**"Drift" means two unrelated things.** In optics it is the flight distance
between the steerer and the sample. In calibration it is a long-running pass
that holds a setpoint and watches it wander over hours. Prefer *drift distance*
and *drift pass* wherever both could be meant.
