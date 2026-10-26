# 78: Bench B1: the ZWO camera's frame rate and bit depth on the bench PC

**Status:** ready-for-developer

**Runner:** developer

**Auto-merge:** no

**Blocked by:** 76

**Spec:** `.scratch/zwo-capture/spec.md`, "Real hardware" → B1
**Binding:** `docs/adr/0008-zwo-camera-owned-by-a-capture-process.md`

## What to build

Done by the developer at the bench with the ASI585MC on USB 3, not by an agent.

1. Put ZWO's `ASICamera2.dll` in `vendor/zwo/` (see `vendor/zwo/README.md`).
2. Run `python tools/zwo_bench.py --sdk-dll vendor\zwo\ASICamera2.dll` with the lens capped
   or pointed at a static scene.
3. Record below, under `## Comments`: for each of full sensor RAW16, full sensor RAW8,
   640 × 400 RAW16, 640 × 400 RAW8, the measured fps and dropped frames; the distinct-levels
   count and the `img % 16` values; the USB port and cable used.

## Acceptance criteria

- [ ] The four fps / dropped-frame lines are recorded here.
- [ ] The distinct-levels result is recorded, with a one-line reading: does `img % 16` show
  only 0 (12-bit data left-shifted in 16-bit pixels) or more?
- [ ] The 640 × 400 RAW16 fps is named as the target that bench ticket 79 compares the Capture
  Process's `acquired_fps` against.

## Gate

None (bench work). If a finding contradicts the spec (for example RAW16 delivering 8 distinct
low bits), set `Status: blocked` and say what needs deciding.

## Comments
