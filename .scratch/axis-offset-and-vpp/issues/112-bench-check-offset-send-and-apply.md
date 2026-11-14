# 112: Bench check — send a plan with an offset, apply, read it on the generators

**Status:** ready-for-developer

**Runner:** developer

**Auto-merge:** no

**Blocked by:** 106, 108, 110, 111

**Spec:** `.scratch/axis-offset-and-vpp/spec.md`
**Binding:** `docs/adr/0001-tests-first-and-no-muted-failures.md`

## What to build

A person at the beamline confirms the whole path on real hardware: Raster Planner → Send
to drive → Overview → Apply → both DG1022Z units → beam. An agent must not claim this
ticket.

## Acceptance criteria

- [ ] In the Raster Planner, a plan with a non-zero sweep offset ("Centre the SWEEP on the
      window" ticked, asymmetric jaws), then **Send to drive**: the Overview X and Y panels
      show the plan's Vpp, frequency and offset with Link ± ticked. (by hand)
- [ ] After Apply All on the Overview, the RIGOL front panels read: A1 offset = +plan,
      A2 offset = −plan, the same for B1/B2; amplitude in Vpp equal to the planner's line;
      Triangle shape. Record the four offsets under `## Comments`. (by hand)
- [ ] Entering an amplitude and offset whose input peak is above 5 V greys out Apply All
      on both tabs, and the tooltip names the channel. No command reaches the generator
      (the SCPI log on the FG tab shows nothing new). (by hand)
- [ ] Editing one channel's offset on the FG tab flips that Overview axis to its two-box
      view. (by hand)
- [ ] The beam's centre on the alumina or camera moves in the direction and by roughly
      the amount the planner's patch predicts. Note the observed shift under `## Comments`. (by hand)

## Gate

None (bench). Record findings under `## Comments` and set `Status: done`.

## Comments
