# 134: Bench — encoders, the painted-mark rotation test, and the edge-return test

**Status:** ready-for-developer

**Runner:** developer

**Auto-merge:** no

**Blocked by:** None (can start immediately; the last two checklist items need 131 done)

**Spec:** `.scratch/slit-motion/spec.md`

**An agent must not claim this ticket.**

## What this establishes

Whether the "home switch is somewhere else when I come back" observation is switch jitter or lost
steps, and whether the Galil can detect lost steps by itself. Background: a stalled stepper loses
steps in groups of four full steps (8 counts at half-stepping, 7.2° at the motor shaft), and the
position every screen shows is the count the Galil *sent*, so lost steps are invisible in the app.

Numbers for this bench, from the config's 629.92 counts/mm and a 200-step motor at half-step:
**400 counts = one motor-shaft turn** (0.9° per count). Past the motor it depends on the drive:
a 40:1 gearbox on a 1 in/rev screw is 16,000 counts per screw turn; a direct 40-TPI screw is 400.

## Checklist

- [ ] **Encoders.** Galil connected, Stepper Motors tab Command Console: type `MG _TPA`, jog A a short
      way, type `MG _TPA` again. Repeat for B, C, D. Record each pair. A number that changes with the
      motion means an encoder is wired to the Galil for that axis; 0 both times means none is
      (any encoder on the separate drive box does not reach the Galil). Also note whether a cable
      runs from the back of each motor to the Galil's encoder connector.
- [ ] **What the visible dial is on.** Record whether the dial or scale you watch turns with the motor
      shaft, the screw, or something else, and whether there is a gearbox.
- [ ] **Painted-mark rotation test**, one axis, slits clear of the beam: paint a thin mark on the motor
      shaft or coupling. In the console send `PR 4000` (or `PR ,4000` etc. for B–D), wait, then the
      matching `PR -4000`; repeat 20 times at your normal slit speed. Record whether the mark returns
      exactly. A jump that is a multiple of 7.2° is lost steps. Repeat once at a faster slit speed.
- [ ] **Edge-return test** (needs 131): run it on each axis with the default 10 cycles and 2000 counts
      out. Attach or name the four CSVs and paste the twelve console summary lines below.
- [ ] **Pass speed decision** (needs 131): from those lines, write down the fastest pass speeds whose
      spread and lost-step jumps you accept. A follow-up change to
      `HOME_PASS_SPEEDS_COUNTS_PER_SEC` comes from this.

## Comments

**2026-11-18 (planning):** The Edge-return test and Pass speed decision items cannot be
done as written. On these slits the home switch and the reverse limit trip at the same
point, so every home pass ends on stop code 3 (see `docs/adr/0009-slits-homed-on-the-release-edge.md`).
Homing moves to the release edge and the three pass speeds are replaced by one approach
speed, so the pass speed decision no longer exists. The edge-return run moves to the
bench ticket of `.scratch/slit-homing-release-edge/`. The Encoders, dial and
painted-mark items do not need homing and can be done now.
