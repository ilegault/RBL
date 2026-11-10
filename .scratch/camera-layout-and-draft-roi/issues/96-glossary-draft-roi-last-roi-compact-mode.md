# 96: Glossary: Draft ROI, Last ROI, compact mode, and the ROI recording lock

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** no

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/camera-layout-and-draft-roi/spec.md` (D9)
**Binding:** `docs/agents/domain.md` (how `CONTEXT.md` is written)

**Read first:** `CONTEXT.md`, section "## Camera" (the entries for **Region of interest (ROI)**, **Preview**, **Segment**), for tone: one bold term, an em dash, plain sentences, no code identifiers.

## What to build

`CONTEXT.md` changes only; this ticket is held for the developer's review (it changes
`CONTEXT.md`) and nothing depends on it.

1. Append to the **Region of interest (ROI)** entry: "It cannot be changed while the ZWO
   camera is recording video; every ROI control is disabled then."
2. New entries in "## Camera", after **Region of interest (ROI)**:
   - **Draft ROI** — the rectangle drawn over the Preview on the Camera tab that is not yet
     the ROI. It can be held to an aspect ratio, moved and resized; nothing the camera
     records changes until the operator applies it. Discarding it changes nothing.
   - **Last ROI** — the most recent ROI, other than the full sensor, that the camera
     confirmed. It is remembered across launches; **Back to ROI** restores it exactly, and at
     the full sensor it is shown as the Draft ROI.
3. New entry at the end of "## Logging and sessions":
   - **Compact mode** — the Overview's Session Recorder showing only what a run needs:
     open/close the camera, the Preview, start/stop the session, take a photo, add a note,
     and the status line. It changes what is shown, never what is recorded, and is off each
     time the application opens.

## Acceptance criteria

- [ ] `CONTEXT.md` contains the three new bold terms exactly as **Draft ROI**, **Last ROI**,
  **Compact mode**, each once as an entry heading, with the text above.
- [ ] The ROI entry ends with the recording-lock sentence above.
- [ ] No other line of `CONTEXT.md` changes (`git diff --stat` shows only `CONTEXT.md`).

## Gate

Run all four, in CI's order (`.github/workflows/tests.yml`). All must pass.

```
ruff check .
python scripts/check_tests_first.py
python tools/type_gate.py
pytest --tb=short -q -n auto --dist loadfile
```

## Comments
