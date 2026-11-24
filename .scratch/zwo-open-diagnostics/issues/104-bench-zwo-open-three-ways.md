# 104: Bench: click Open three ways on the lab PC and record what RBL says

**Status:** ready-for-developer

**Runner:** developer

**Auto-merge:** no

**Blocked by:** 101, 103

**Spec:** `.scratch/zwo-open-diagnostics/spec.md` (Solution 7; D19)
**Binding:** `docs/adr/0008-zwo-camera-owned-by-a-capture-process.md`

## What to build

Done by the developer on the lab PC with the ASI585MC, running a build that contains 98–103.
An agent must not claim this ticket. Cases 2 and 3 need `ASICamera2.dll` beside `RBL.exe`
(bench ticket 80 deploys it); case 1 does not. Blocks nothing.

For each case, pick **ZWO camera** in the Overview's Session Recorder, click **Open**, then
paste under `## Comments` the camera message line's text and the `rbl.capture` lines that
`rbl.log` gained.

1. `ASICamera2.dll` absent and `RBL_ZWO_SDK_DLL` unset.
2. DLL present, camera unplugged.
3. DLL present, camera plugged in.

## Acceptance criteria

- [ ] Case 1: the line reads `ZWO SDK library not found: <path>. Copy ASICamera2.dll there or set RBL_ZWO_SDK_DLL.` and `rbl.log` has the `ZWO SDK DLL: … exists: False` INFO line and the same text at ERROR. Comment whether this matches the first silent Open on the lab PC.
- [ ] Case 2: the line reads `No ZWO camera detected on USB`; `rbl.log` shows the hello with `cameras []` and the exit marked expected.
- [ ] Case 3: the line reads `Opened <model> <serial>` naming the ASI585MC, the Preview shows the camera, and `rbl.log` shows the first five Open-sequence INFO lines in order (DLL, starting, started pid, hello, sent open, open reply ok). After **Close**, the line is empty and `rbl.log` has the sixth, `Capture Process exited: … expected True`.
- [ ] Any `[capture pid N]` line in `rbl.log` at WARNING or above is pasted with what preceded it.

## Gate

None: bench ticket. The developer records results under `## Comments` and sets `Status: done`.

## Comments

2026-11-20, from source on the bench PC (`RBL dev (8c168cf)`), not yet on the lab PC.

- Case 1: **not a valid run, redo.** The DLL was renamed to `ASICamera2.dll.off` inside
  PyCharm, whose rename refactoring also rewrote the path string in the source (the log shows
  `RBL dev (8c168cf+dirty)` and
  `ZWO SDK DLL: ...\vendor\zwo\ASICamera2.dll.off (from RBL_ZWO_SDK_DLL: False, exists: True)`),
  so RBL loaded the renamed file and opened the camera. Redo by renaming in Explorer, or
  launch with `$env:RBL_ZWO_SDK_DLL="C:\nope\ASICamera2.dll"`.
- Case 2 (camera unplugged): passes. `Capture Process hello: protocol 1, cameras []`, then
  `ERROR rbl.capture: No ZWO camera detected on USB`, then
  `Capture Process exited: code 0, status NormalExit, expected True`.
- Case 3 (plugged in): passes. `hello ... cameras ['ZWO ASI585MC']`, `Sent open`,
  `Open reply ok (id 1): ZWO ASI585MC 0`; after Close,
  `Capture Process exited: code 0, status NormalExit, expected True`. The serial shows as
  `0`: that is the camera index, which `ZwoCamera.info()` falls back to because zwoasi 0.2.0
  does not read the serial.
- WARNING lines: only `[capture pid N] ... WARNING [root] ASI SDK library not found`, from
  zwoasi's import before `init(path)`. Harmless, but it looks like a failure in the log.
- Also: the `[capture pid N]` prefix changes between hello and `camera opened` (e.g. 16568,
  then 26372) because the venv's `python.exe` is a launcher that starts the real interpreter
  as a second process.

Remaining: case 1 redo, and cases 1-3 on the lab PC with the build (ticket 80).
