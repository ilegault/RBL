# 174: Bench: AUTO/LOCAL, insertions without move faults, raw words, interlock

**Status:** ready-for-developer

**Runner:** developer

**Auto-merge:** no

**Blocked by:** 167, 168, 170, 172, 173

**Spec:** `.scratch/cup-status-fix/spec.md`
**Binding:** `docs/adr/0003-commanded-and-confirmed-cup-position.md`

## What to build

Done by the developer at the beamline with a build containing 167–173. An agent must not
claim this ticket. Open a cup test log first so every step lands in the CSV, and paste the
results and the CSV path under `## Comments`.

## Acceptance criteria

- [ ] (by hand) Connect the T7 from the Faraday Cup tab's own LabJack panel.
- [ ] (by hand) Controller in AUTO: the tab shows "AUTO mode (remote actuation enabled)" and
      "Source" is position, not inference. Switch to LOCAL: the warning appears. Back to AUTO.
- [ ] (by hand) Ten insertions and retractions (some from the Overview panel): no
      "Move Fault", no `fault_impossible_status` rows; the `position_transition` rows'
      `status_word` read `0x00000004` (IN) and `0x00000008` (OUT).
- [ ] (by hand) Interlock: with the cup IN and the controller in LOCAL, Release interlock, then
      press the front-panel OUT button; record whether the cup moves. Close interlock and
      repeat. Expected: no motion with it open, OUT with it closed. Also, with the cup OUT,
      Release interlock and record that the cup returns IN.
- [ ] (by hand) Unplug the status cable at the CB37 briefly: the tab shows in transit and the
      LOCAL/status-cable warning. Replug.

## Comments
