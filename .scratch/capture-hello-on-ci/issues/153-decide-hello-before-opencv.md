# 153: Decide whether the Capture Process says hello before importing OpenCV

**Status:** ready-for-developer

**Runner:** any

**Auto-merge:** no

**Blocked by:** None (can start immediately)

## Question

The Capture Process (`src/rbl/capture/process.py`) imports `cv2` and `numpy` at module top, before
it can send `hello`. On a slow machine that start-up can pass the 10 s hello limit
(`CAPTURE_HELLO_TIMEOUT_S`). Ticket 152 gives tests 30 s, but the app still gets 10 s. Moving the heavy
imports after `hello` would start the child faster on the lab PC too, but `hello` carries the camera
list, so whether that list can be built without OpenCV (fake camera, ZWO SDK) needs checking first.

This is a design question for `/grill-with-docs`, not agent work.

## Acceptance criteria

- [ ] (by hand) Decide yes or no, after a `/grill-with-docs` pass against ADR 0008. If yes, `/to-spec`
      and `/ticket-set` produce the work.

## Comments
