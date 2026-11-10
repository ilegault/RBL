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
