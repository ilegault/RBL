# 78: Bench B1: the ZWO camera's frame rate and bit depth on the bench PC

**Status:** done

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

2026-11-20 (bench PC, ASI585MC; `tools/zwo_bench.py` defaults: exposure 10 ms, gain 0,
10 s per case; USB port and cable not recorded):

    3840x2160 RAW16: frames=236 fps=23.53 dropped=0
    3840x2160 RAW8:  frames=465 fps=46.45 dropped=0
    640x400 RAW16:   frames=988 fps=98.77 dropped=0
    640x400 RAW8:    frames=987 fps=98.67 dropped=0
    RAW16 full frame (lens capped): distinct_levels=270
    RAW16 low nibble (img % 16) first 20 distinct: [0..15], all sixteen values

Reading: `img % 16` shows all sixteen values, not only 0, so the SDK's RAW16 is not 12-bit
data left-shifted by 4. Nothing in the application assumes the shift (only the fake
camera's test pattern uses `<< 4`), so this does not contradict the spec.

Target for ticket 79: **98.77 fps at 640 × 400 RAW16**. Both 640 × 400 cases sit at
~98.7 fps because the tool's 10 ms exposure caps them at 100 fps, so ticket 79 must compare
`acquired_fps` at 10 ms exposure, gain 0. The full-sensor RAW16/RAW8 pair (23.5 / 46.5 fps,
2:1) is bandwidth-limited.

The `WARNING:root:ASI SDK library not found` line printed first is zwoasi's own
import-time check of `ZWO_ASI_LIB`. It is harmless: `zwoasi.init(path)` follows.
