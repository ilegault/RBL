# 79: Bench B2: a real session with the real ZWO camera, end to end

**Status:** ready-for-developer

**Runner:** developer

**Auto-merge:** no

**Blocked by:** 74, 76

**Spec:** `.scratch/zwo-capture/spec.md`, "Real hardware" → B2; user stories 1-12
**Binding:** `docs/adr/0008-zwo-camera-owned-by-a-capture-process.md`,
`docs/adr/0004-monitoring-log-and-sessions.md`

## What to build

Done by the developer at the bench, from source (`python -m rbl.main`) with the DLL in
`vendor/zwo/`, then once more from a `pyinstaller rbl.spec --clean` one-folder build.

1. Overview: pick **ZWO camera**, press Open. A Preview appears; model, serial and sensor
   temperature show.
2. Set ROI and exposure; start a session; Start Video; change exposure once mid-recording;
   Take Photo once (burst 1); Stop Video; stop the session.
3. Cap the lens; Take Darks; confirm the dialog.
4. Open each `zwo/segment_*.ser` in **SER Player** and in **SER Viewer**: colours right, not
   byte-swapped, timestamps present. Open the photo FITS and the Dark Frame FITS in **Fiji**.
5. Compare the `acquired_fps` on the status line at 640 × 400 RAW16 with ticket 78's figure.
6. Switch back to **USB camera** and check it records exactly as before.
7. Kill RBL from Task Manager mid-recording; confirm the last segment still opens and that a
   restarted RBL can Open the camera within about ten seconds.

## Acceptance criteria

- [ ] Steps 1-7 done; anything unexpected written under `## Comments`.
- [ ] SER Player and SER Viewer both show correct colours and unswapped values. If they show
  swapped bytes, record it: `SER_LITTLE_ENDIAN_FLAG` in `src/rbl/hardware/ser_format.py` is
  then the one value to change, in a follow-up ticket.
- [ ] Two segments exist from step 2, each with a settings record whose `exposure_us` matches.
- [ ] `acquired_fps` is within 10 % of ticket 78's 640 × 400 RAW16 figure, or the shortfall is
  recorded here with the drop counts.

## Gate

None (bench work). A failure here becomes a new ticket; set `Status: blocked` only if it stops
ticket 80.

## Comments
