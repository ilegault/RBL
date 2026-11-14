# Spec: Compact Session Recorder, compact mode, Last ROI and Draft ROI

Status: ready-for-agent
Date: 2026-11-09
Related: `docs/adr/0008-zwo-camera-owned-by-a-capture-process.md` (decision 6: ROI is a
recorded setting; decision 7: USB path unchanged), `docs/adr/0001-tests-first-and-no-muted-failures.md`,
`CONTEXT.md` ("Camera": ZWO camera, Capture Process, Segment, Region of interest, Preview).

> ADR 0001 is binding on all test work here: a failing test is fixed or escalated, never muted.

## Problem Statement

The Overview tab's Session Recorder panel (`src/rbl/gui/widgets/recording_panel.py`) takes
far more height than it needs:

- The Preview (`VideoView`) has an Expanding size policy and `stretch=1`, so it absorbs all
  spare height and shows it as black letterbox.
- Under the ZWO camera, USB-only widgets are hidden but their rows remain, so **Open**,
  **Start Video** and **CSV every** each occupy a full-width row on their own.
- `ZwoControls` (`src/rbl/gui/widgets/zwo_controls.py`) spends five rows on what fits in
  three, with labels drifting away from their fields because no row has a trailing stretch.

Separately, on the ZWO camera:

- "Full sensor" throws the current ROI away; getting it back means redrawing or retyping it.
- Drawing an ROI on the Camera tab applies it the moment the mouse is released, so a
  rectangle cannot be positioned, held to a shape, or reconsidered before it changes what
  the camera records.
- The ROI number boxes stay live during a ZWO recording, and a change there silently rolls
  the Segment, while drawing is already blocked then. The rules disagree.
- Full screen on the Camera tab exists (double-click, Esc) but nothing on screen says so.

## Solution

1. **Repacked rows** in the Session Recorder (both cameras) and in `ZwoControls` (shared by
   the Overview and the Camera tab, so both change; spec D11 of `zwo-capture` stays intact).
2. **Preview sizing on the Overview**: the Preview's height follows its width at the frame's
   aspect ratio, capped at a 16:9 shape; 16:9 before any frame. The image is always drawn as
   large as the box allows, with black bars where shapes differ. Spare panel height goes
   below the panel's last row, not into the Preview. The Camera tab's Preview is unchanged.
3. **Compact mode** on the Overview Session Recorder only: a checkable **Compact** button.
   Starts off every launch; never saved.
4. **Last ROI**: the most recent applied ROI that is not the full sensor, saved in the ZWO
   settings store. The ROI button swaps **Full sensor** ⇄ **Back to ROI**, on both views.
5. **No ROI change during a ZWO recording**: the recorder refuses every ROI change while ZWO
   video is recording or starting; every ROI control is disabled then.
6. **Draft ROI** on the Camera tab only: the rectangle on screen that is not yet the ROI.
   Held to an aspect preset (Free, 1:1, 4:3, 16:9, Custom W:H). Moved and resized by
   dragging. Becomes the ROI only on **Apply ROI**; **Discard** or Esc throws it away. At the
   full sensor, the Last ROI appears as the Draft. Drawing on top of an applied ROI nests
   inside it, as today.
7. A visible **Full screen** button on the Camera tab.

## Decisions

- D1 One layout for `ZwoControls`; no Overview-only variant of it.
- D2 Every setting stays on the Overview in full mode.
- D3 Compact mode shows only: Open/Close and its resolution text, the Compact button, the
  Preview (and its ZWO caption), START/STOP SESSION, the status line, Take Photo, Add Note.
  Everything else in the panel is hidden.
- D4 Overview rows stay fixed; a column narrower than a row scrolls sideways (existing
  `DragPanel` behaviour). No wrapping layout.
- D5 START SESSION keeps its own row at 36 px minimum height, sharing it only with CSV every.
- D6 The aspect preset is never saved; it starts at Free.
- D7 Draft geometry is pure functions (no Qt) in `src/rbl/hardware/roi_geometry.py`, held in
  sensor pixels, so it is testable with tuples and survives window resizes.
- D8 Drafting is off in the full-screen window and while ZWO video is recording.
- D9 Glossary only, no new ADR.

## Out of scope

ROI drawing on the Overview; a saved compact-mode or preset preference; any change to the
Capture Process, its contract, or the snapping rules (`rbl.hardware.capture_protocol.snap_roi`);
any change to the USB recording path; reattaching to a Capture Process.
