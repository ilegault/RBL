# 166: Bench: the ZWO follow-ups on the real camera

**Status:** ready-for-developer

**Runner:** developer

**Auto-merge:** no

**Blocked by:** 158, 159, 160, 161, 162, 163, 164, 165

**Spec:** `.scratch/zwo-bench-followups/spec.md`
**Binding:** `docs/adr/0008-zwo-camera-owned-by-a-capture-process.md`

## What to build

Nothing to build. The developer does this at the bench with the ASI585MC, from source (`python -m rbl.main`) on a `master` that contains 158-165. An agent must not claim this ticket.

## Acceptance criteria

- [ ] (by hand) Record a short ZWO session at RAW16. `zwo/segment_000.ser` opens in SER Player and in SER Viewer as a clear picture, not coloured noise.
- [ ] (by hand) `rbl.log` shows `ZWO library loaded: …\vendor\zwo\ASICamera2.dll` and no `ASI SDK library not found` line. The Open message and the segment's settings record show a 16-character serial (not `0`) and ZWO's SDK version, both pasted here.
- [ ] (by hand) Take Darks shows `Taking N Dark Frames…` then `Dark Frame saved: <path>`, and the file is at that path. Take Photo shows `Photo saved: …`.
- [ ] (by hand) The ROI hint reads correctly with no Draft, with a Draft and while recording. Dragging the exposure and gain sliders changes the camera once per release (one new segment per drag while recording), and the mouse wheel changes nothing.
- [ ] (by hand) Auto exposure lands on a usable exposure in one or two clicks (record the before and after values and the scene). Auto white balance removes the green tint from the Preview. Reset colour brings it back. The SER from after Auto white balance is still raw (green-tinted in SER Player without its own colour controls).

## Gate

None: bench ticket. Paste the results under `## Comments` and set `Status: done`. A failure becomes a new ticket.

## Comments
