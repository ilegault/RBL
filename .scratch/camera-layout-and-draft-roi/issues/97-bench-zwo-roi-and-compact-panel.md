# 97: Bench: Draft ROI and Back to ROI on the real ZWO camera; screenshots of the panel

**Status:** ready-for-developer

**Runner:** developer

**Auto-merge:** yes

**Blocked by:** 90, 94, 95

**Spec:** `.scratch/camera-layout-and-draft-roi/spec.md`
**Binding:** `docs/adr/0008-zwo-camera-owned-by-a-capture-process.md`

## What to build

Nothing to build. A bench check on the lab PC with the ZWO ASI585MC, since CI only has the
fake camera (1920 × 1080). An agent must not claim this ticket.

## Acceptance criteria

- [ ] Camera tab, ZWO open: draw a Draft at 16:9, move it, resize a corner, Apply. The
  applied ROI numbers match the outline to within the camera's rounding (width a multiple of
  8, x and y even), and the Preview now shows only that area.
- [ ] Full sensor, then Back to ROI: the four ROI numbers are identical to before. Restart
  RBL: at the full sensor, the Last ROI appears as the Draft outline.
- [ ] Start a session and video: every ROI control (number boxes, the swap button, Apply,
  drawing) is disabled; exposure and gain still work.
- [ ] Overview screenshots under USB and ZWO, full and compact mode, attached under
  `## Comments`; the Preview shows no tall black band at a normal column width.
- [ ] Camera tab: the Full screen button opens full screen; Esc closes it.

## Comments
