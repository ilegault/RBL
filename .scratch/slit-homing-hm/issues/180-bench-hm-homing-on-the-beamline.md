# 180: Bench: the encoder check, HM homing on A-D, Auto-Home All, and edge-return with the stop column

**Status:** ready-for-developer

**Runner:** developer

**Auto-merge:** no

**Blocked by:** 175, 176, 177, 178, 179

**Spec:** `.scratch/slit-homing-hm/spec.md` ("Further Notes": bench ticket; user stories 44-46)
**Binding:** `docs/adr/0010-slits-homed-with-hm-as-nec-specifies.md`

## What to build

Nothing to build. The developer does this on the beamline from source (`python -m rbl.main`), on a `master` that contains 175-179. An agent must not claim this ticket. It replaces bench ticket 142, which is retired.

## Acceptance criteria

- [ ] (by hand) Encoder check, in the Stepper Motors tab's Command Console, for each axis: `MO x`, `MG _TPx`, turn the drive a little by hand, `MG _TPx` again, then `SH x`. Paste both numbers per axis, and anything seen on the back of each motor (a cable, a label).
- [ ] (by hand) Home on each axis A-D. Paste each success line (`stops S1, S2 cts`) or the failure message.
- [ ] (by hand) One Auto-Home All. Paste the summary, and the dialog text if it failed (it should list each failed axis with its reason).
- [ ] (by hand) Edge-return on each axis: 10 cycles, out counts 2000, approach speed 900. Name the four CSVs and paste both summary lines (stop and edge) per axis.
- [ ] (by hand) Write down whether the stop column drifts with the edge column (lost steps on the ordinary moves) or stays steady while the edge drifts (the one-count steps), and whether 4 counts is the right agreement limit given the stop spread.

## Gate

None: bench ticket. Paste the results under `## Comments` and set `Status: done`. A failure becomes a new ticket. If `HM` ever drives an axis out toward the forward limit, stop and report it; do not change `CN` in a ticket. Axis A will most likely fail the 4-count agreement (about +167 counts per cycle on 2026-11-20): that is the check working.

## Comments
