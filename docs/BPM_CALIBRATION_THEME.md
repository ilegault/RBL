# Getting millimetres out of a shot, every shot

A working note on what to build, not how to build it. No file names, no
function names — those come later, once the idea is settled.

## The problem, stated honestly

Every width the profiler produces is a time. Turning it into a distance needs
one number: how much real space the wire covers per millisecond of trace. Call
it the **scale**.

Two things make a stored scale uncomfortable:

1. The scale is set by how fast the wire is turning. Motors drift, belts age,
   and nothing in the app would notice.
2. Shots are taken by hand, minutes or hours apart, with the beam changed in
   between. A scale measured on Tuesday is being applied to Friday's shot with
   no evidence it still holds.

So the instinct is right: **something in every shot should re-establish the
scale.** The question is only what that something is allowed to be.

## The one rule

> A calibration measures the **instrument**. The moment it starts measuring the
> **subject**, it is not a calibration any more.

The test is simple and worth applying to any candidate: *if I change the beam
and the number changes, it is not a ruler.*

That rule is the whole content of this note. Everything below is applying it.

## Three things you can measure in milliseconds

### 1. Mark to mark — the ground truth

The head generates two calibration marks, 60 mm apart. They are fixed points on
the hardware. Nothing about the beam moves them, so the time between them is a
true measurement of the sweep. This is the ground truth and it stays the ground
truth.

Its only fault is the workflow: it needs a shot with the controller's output
selector on fiducials, so it cannot happen on the same trigger as the beam.

### 2. Peak to peak, X apex to Y apex — **not** a ruler

This is the tempting one, because both peaks are already in every shot. It
fails the rule.

The X apex sits where the beam is horizontally; the Y apex sits where it is
vertically. That is what a profile monitor is *for* — the apex position is the
beam position readout. So the gap between them is 60 mm only in the single case
where the beam happens to sit on both reference positions at once. Move the
beam and the gap moves.

Across the six shots of one tuning session:

| shot | X to Y | implied scale |
|---|---|---|
| 1 | 26.1 ms | 2.299 mm/ms |
| 2 | 23.8 ms | 2.521 |
| 3 | 22.3 ms | 2.691 |
| 4 | 22.2 ms | 2.703 |
| 5 | 27.5 ms | 2.182 |
| 6 | 27.4 ms | 2.190 |

**Widest over narrowest: 24 %.** The wire's rotation did not change by 24 %
inside half an hour. The beam moved.

The damage is worse than a 24 % error, because the error is *anti-correlated
with the measurement*. Steer the beam and the ruler changes length, so a beam
that got no wider reads wider in millimetres. There is no residual to inspect
and no shot to compare against — every shot yields one plausible number. That is
the same silent-wrongness the fiducial trigger-peak trap has, and it is why this
one is worth writing down rather than just avoiding.

### 2b. Why re-measuring it every shot does not rescue it

The natural objection: *the beam moves, and quads move it further, so surely
that is exactly why the gap must be re-measured every shot rather than stored.*

That is right about the gap and wrong about what re-measuring buys. Write down
what the gap actually contains:

```
X-to-Y gap  =  ( D + x_beam - y_beam )  /  v

   D       fixed mechanical offset between the two crossings
   x, y    where the beam sits in each plane
   v       sweep speed  <- the only thing you wanted
```

One equation, three unknowns. Re-measuring it on the next shot gives you the
same equation again with different values of x and y, so you still cannot
separate them. Frequency does not create information. A ruler has to be an
equation with **one** unknown, and the rotation period is one: `T = 2π/ω`, no
beam terms in it at all.

**Quads make this worse, not better.** A quad focuses one plane and defocuses
the other, so tuning one tends to move `x` and `y` in *opposite* directions —
and the gap depends on `x − y`, which then gets the sum of both movements. The
gap is at its most sensitive precisely when you are doing the thing you are
trying to measure the effect of.

Here is what that costs, on this run, in the units you care about:

| shot | X FWHM (ms) | change | X FWHM (mm), gap ruler | change |
|---|---|---|---|---|
| 2 | 1.90 | — | 4.79 | — |
| 3 | 2.50 | +31.6 % | 6.73 | +40.4 % |
| 4 | 2.80 | +12.0 % | 7.57 | +12.5 % |
| 5 | 2.30 | **−17.9 %** | 5.02 | **−33.7 %** |
| 6 | 2.30 | +0.0 % | 5.04 | +0.4 % |

Shot 4 to shot 5: the beam narrowed 18 % in milliseconds and 34 % in
millimetres. Same two traces. The extra 16 points are the ruler changing
length, and nothing in the millimetre number says which part was the beam.

Note the asymmetry that makes a *fixed* scale — even a wrong one — safer than
this: multiplying every shot by the same number preserves every ratio exactly.
A wrong fixed scale gives you wrong sizes but correct comparisons, and one later
division fixes the whole run. A per-shot scale taken from the beam gives you
wrong sizes **and** wrong comparisons, and nothing recovers it.

### 3. X apex to the NEXT X apex — a ruler that renews itself every shot

This is the answer to the original question.

The wire goes round. One full revolution sweeps a **fixed distance through the
aperture** — a property of the metal, not of the beam and not of the motor. So:

```
scale  =  mm per revolution  /  ms per revolution
          ^^^^^^^^^^^^^^^^^     ^^^^^^^^^^^^^^^^^
          geometric constant    measured in THIS shot
          measured once         from the beam trace
```

Both X apexes move together when the beam moves, so the beam cancels out of the
subtraction. It passes the rule.

**Getting the constant.** Take one fiducial shot. It gives the scale the honest
way (60 mm ÷ mark separation) *and*, if the record is long enough, the period in
the same trace. Multiply them: that product is millimetres per revolution, and
it is the thing you store instead of the scale. It is immune to the motor
drifting, because the drift shows up in the period, which you re-measure every
shot.

**What has to change on the bench.** The record has to be longer than one
revolution. It currently is not. At 5 ms/div the record is 50 ms, and the
evidence says the period is close to 45–55 ms: the X and Y peaks sit about half
a revolution apart, and the next X peak lands just past the end of the record —
in all six shots the last 4 ms is still flat, no second rise. Going to 10 ms/div
gives a 100 ms record, which holds two full revolutions with room to spare.

The cost of that is resolution: 2500 points over 100 ms is 40 µs per sample
instead of 20. A 2 ms FWHM is still about 50 samples wide, which is fine. Push
to 20 ms/div and it gets thin for the 10 % crossings, which live on the steep
flanks where samples are worth the most.

**A free cross-check.** Y-apex to Y-apex measures the same period. Two
independent numbers from one trace that must agree. If they do not, something
about the trace is wrong, and it is better to say so than to publish a scale.

## The shape of the per-shot flow is right

Worth saying plainly, because only one piece of it changes: *new shot → measure
the ruler in that shot → apply it to the widths from that same shot* is the
correct design. Self-contained, nothing remembered between clicks, no stale
constant. Swap the ruler from the X-to-Y gap to the rotation period and the
rest of that flow stands exactly as it is.

## What it looks like in the workflow you actually use

Snapshot-on-click suits this well. Each shot is self-contained: it carries the
beam, the period, and therefore its own scale. Nothing has to be remembered
between clicks except the one geometric constant.

Rastering is the complication and needs stating clearly. With the raster on, the
trace is chopped into a train of teeth and there are two periods present — the
raster ripple, which is fast, and the wire revolution, which is slow. **The
apex search for the period must run on the envelope, not the raw trace**, or it
will lock onto the ripple and report a period tens of times too short. This is
the same reason the widths already need the envelope; it is one more consumer of
it, not a new mechanism.

## The rules that keep it honest

- **Milliseconds are the measurement. Millimetres are a conversion.** Log both,
  always, in every row. Then a run taken under a wrong scale is still a good run
  and can be rescaled later. Log only millimetres and the run is gone.
- **Uncalibrated reads as absent, never as zero.** Blank, not 0.0 mm. A zero is a
  number, and numbers get believed.
- **Every scaled number carries which ruler produced it and what it measured.**
  A row that says `period, T = 51.4 ms` is auditable a month later. A bare
  `4.57 mm` is not.
- **Never silently substitute one ruler for another.** If the period cannot be
  found in a shot, that shot is uncalibrated and says so. Quietly falling back
  to a stored scale, or to the peak gap, produces the failure this whole note is
  about.
- **Show the disagreement.** When both the stored fiducial scale and the
  per-shot period scale exist, put the difference between them on screen. That
  difference *is* the motor-drift measurement, and it costs nothing.

## The peak gap still earns its place

Not as a ruler — as a **position readout**. It is trigger-independent (both
apexes are measured from the start of the record, so a trigger shift cancels),
it costs nothing, and once a real scale exists it converts straight to
millimetres of beam movement.

It is also the cheapest tripwire in the system: if that number jumps between two
shots, you steered, and any width comparison across the jump needs a second
look. Worth logging every shot from now on, whether or not any of the rest of
this gets built.

## One assumption to check before trusting any of it

All of this — including the existing fiducial calibration — assumes the wire's
position sweeps **linearly in time**. A helical wire does that by design, which
is presumably why it is helical. A plain rotating arm does not: position goes as
sin θ, so the middle of the sweep is faster than the edges, and a single
mm-per-ms is then only a local approximation, biased by wherever the marks sit.

Worth confirming from the head's geometry. If the sweep turns out to be
non-linear, none of the structure above changes — the constant just becomes a
curve instead of a number.

## Order of work

1. **Log the X-to-Y peak gap every shot** as a position channel. Independent of
   everything else, useful immediately, near-zero effort.
2. **Lengthen the timebase and take one look.** Confirm two revolutions fit in
   the record and that X-to-X and Y-to-Y agree. This is a bench measurement, not
   a code change, and it decides whether step 3 is worth building.
3. **Take one fiducial shot at that timebase** and get millimetres per
   revolution. Store that instead of the scale.
4. **Then** make the per-shot period the active ruler, with the fiducial scale
   still stored beside it as the cross-check.

Steps 1 and 2 are worth doing regardless. Step 4 is only worth building once
step 2 says the period is there and stable.

---

*On the shape of this note, since it may be useful as a pattern: it states the
problem before any solution, names the one rule that decides everything, walks
the candidates in order and says plainly why two of the three fail, keeps every
claim next to the number that supports it, and ends with what to do first rather
than everything that could be done. The rejected options are kept rather than
deleted — a reader who has the same idea next month needs to find the reason it
was dropped, or they will just have it again.*
