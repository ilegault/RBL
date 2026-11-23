# Spec: What the ZWO bench run found: readable SER files, honest feedback, easier settings

**Status:** ready-for-agent

**Binding:** `docs/adr/0008-zwo-camera-owned-by-a-capture-process.md`,
`docs/adr/0001-tests-first-and-no-muted-failures.md`
**Builds on:** `.scratch/zwo-capture/spec.md` (the Capture Process contract, D1-D12),
`.scratch/zwo-open-diagnostics/`, `.scratch/camera-layout-and-draft-roi/`
**Evidence:** the 2026-11-20 comments on bench tickets 78, 79, 97 and 104.

## Problem Statement

The first bench run of the ZWO ASI585MC, on 2026-11-20, showed that the camera opens,
streams and records, but that an operator cannot yet trust or comfortably use what it
produces:

- Every 16-bit **Segment** opens in SER Player as coloured noise. The pixels on disk are
  right: decoded little-endian, the same frame is a clear picture. Only the header's
  byte-order flag is wrong for the programs people actually open SER files with.
- **Take Darks** saves **Dark Frames** and says nothing: no message on screen, no line in
  `rbl.log`. The operator cannot tell that it worked or where the files went. **Take
  Photo** is the same.
- The Camera tab never says how to start an ROI. The operator's first instinct was to
  press **Apply ROI** before drawing anything.
- Exposure and gain are only number boxes. Finding a usable exposure means typing values
  and waiting for a Preview. There is no quick way to land near a good exposure, and the
  **Preview** of this colour camera is green, because raw Bayer data has no white
  balance.
- The camera's identity is recorded wrong: every **Settings record** and the Dark Frame
  library folder say serial `0` (the camera's index), and `sdk_version` is the Python
  wrapper's version, not ZWO's library's.
- `rbl.log` carries `WARNING [root] ASI SDK library not found` on every Open that
  succeeds. Behind that warning is a real hazard: on import, the zwoasi wrapper loads the
  first `ASICamera2.dll` it finds on `PATH`, and after that, RBL's own `init(path)` call
  does nothing. On a PC with ZWO's ASIStudio on `PATH`, RBL would silently run a
  different ZWO library from the one it ships, breaking ADR 0008 decision 2.

## Solution

From the operator's side:

- SER segments open correctly in SER Player, SER Viewer and Siril, with nothing to set
  by hand. Pixel data and file layout are unchanged.
- After Take Darks the camera message says `Taking N Dark Frames…`, then
  `Dark Frame saved: <full path>`, and `rbl.log` records the same. A refusal or failure
  says why. Take Photo reports `Photo saved: <names>` the same way.
- The Camera tab's Aspect row carries a one-line hint that changes with the state:
  `Draw a box on the Preview to start an ROI.` with no Draft;
  `Drag inside to move, drag a corner to resize, then Apply ROI.` with a Draft; and
  `ROI is locked while video records.` while recording.
- Exposure and gain each get a slider beside their number box. Exposure's slider is
  logarithmic over the camera's own range. While dragging, the number box follows the
  slider and nothing is sent to the camera. On release, the setting is sent once, so a
  drag starts at most one new Segment. The mouse wheel never changes either slider.
- **Auto exposure** is one button. It measures the current frame once, chooses one
  exposure and applies it as an ordinary setting change: recorded, starting a new
  Segment like a typed value, and never adjusted again on its own. The camera's own
  automatic exposure and gain stay off, always.
- **Auto white balance** is one button. It computes red and blue Preview gains once
  from the current frame (grey world) and applies them to the Preview only. **Reset
  colour** returns both to 1.0. The values persist across launches like brightness. The
  Master Recording, photos and Dark Frames are never colour-corrected.
- Settings records and the Dark Frame library use the camera's real serial number and
  ZWO's library version. RBL always runs exactly the `ASICamera2.dll` it was pointed at,
  whatever is on `PATH`, and the import warning is gone.

## User Stories

1. As an operator, I want a ZWO Segment to open correctly in SER Player, so that I can
   review a run without converting anything.
2. As an operator, I want the same Segment to open correctly in SER Viewer and Siril,
   so that my analysis tools agree with my viewer.
3. As an analyst, I want the SER pixel data to stay exactly as the camera delivered it,
   so that a header fix never changes my measurements.
4. As an operator, I want a message when I press Take Darks, so that I know the
   Capture Process is working on it.
5. As an operator, I want the message to give the saved Dark Frame's full path, so that
   I can find it without knowing the library layout.
6. As an operator, I want a refused or failed Take Darks to say why, so that I do not
   assume a Dark Frame exists when it does not.
7. As an operator, I want Take Photo to confirm the saved file names the same way, so
   that both still-image actions behave alike.
8. As a developer reading `rbl.log`, I want every saved Dark Frame and photo logged with
   its path, so that a bench report can quote the log instead of a screenshot.
9. As a new operator on the Camera tab, I want a hint telling me to draw on the Preview
   first, so that I do not press Apply ROI expecting it to start a Draft.
10. As an operator with a Draft on screen, I want the hint to tell me how to move,
    resize and apply it, so that I learn the interaction without documentation.
11. As an operator recording video, I want the hint to say the ROI is locked, so that
    disabled ROI controls do not look broken.
12. As an operator, I want an exposure slider, so that I can sweep from microseconds to
    seconds without typing.
13. As an operator, I want the exposure slider to be logarithmic, so that short and long
    exposures both get usable resolution.
14. As an operator, I want a gain slider over the camera's own gain range, so that I can
    try gains quickly.
15. As an operator, I want the number box to follow the slider while I drag, so that I
    see the value before I commit it.
16. As an operator, I want the camera to change only when I release the slider, so that
    one drag never creates dozens of Segments.
17. As an operator, I want the mouse wheel never to change exposure or gain, so that
    scrolling the tab cannot alter a recording.
18. As an operator, I want the sliders to show the value the camera confirmed after
    snapping, so that what I see is what is recorded.
19. As an operator, I want one click to pick a usable exposure from the current scene,
    so that I start near the right value.
20. As an analyst, I want that automatic choice to be an ordinary, recorded setting
    change, so that every Segment still has one known exposure.
21. As an analyst, I want the camera's own continuous auto exposure and auto gain never
    used, so that intensity stays comparable within and between Segments.
22. As an operator, I want Auto exposure to back off hard when the scene is saturated,
    so that one click never leaves a blown-out image.
23. As an operator, I want the chosen exposure announced, so that I know what changed.
24. As an operator, I want one click to remove the green tint from the Preview, so that
    the picture looks natural.
25. As an analyst, I want white balance to affect the Preview only, so that the Master
    Recording, photos and Dark Frames stay raw.
26. As an operator, I want Reset colour, so that I can return to the raw-looking Preview.
27. As an operator, I want my white balance remembered across launches, so that I do not
    redo it every session.
28. As an analyst, I want each Settings record to carry the camera's real serial, so
    that a second camera can never be mistaken for the first.
29. As an analyst, I want each Settings record to carry ZWO's library version, so that a
    change in library behaviour can be traced to the files it affected.
30. As a developer, I want RBL to load exactly the `ASICamera2.dll` it ships, even when
    another copy is on `PATH`, so that ADR 0008's byte-for-byte promise holds on every
    PC.
31. As a developer, I want no false "library not found" warning in `rbl.log`, so that a
    real warning stands out.
32. As a developer, I want the Capture Process to log which DLL it actually loaded, so
    that a bench report can confirm it.
33. As an operator, I want the Auto buttons disabled while the camera is not open, so
    that a click can never do nothing silently.
34. As a developer, I want the Capture Process to refuse the Auto commands with a named
    error when no frame has arrived yet, so that the application can say why nothing
    happened.

## Implementation Decisions

### D1. SER byte-order flag follows the readers, not the written spec

The SER v3 document says the `LittleEndian` header field is 1 for little-endian data.
Nearly every program that reads SER files treats it the other way round: 0 means
little-endian. SER Player, Siril, FireCapture and Lynkeos all do, for compatibility with
the first writers. RBL writes little-endian pixels and sets the field to **0**. Pixel
bytes, the 178-byte header layout and the trailer are unchanged. The serialisation
module's docstring records why, so a later agent does not "fix" it back to the spec.
Existing files are not migrated.

### D2. The ZWO library is loaded explicitly and quietly

- The zwoasi wrapper is imported with its import-time warning suppressed: logging is
  disabled at WARNING level for the duration of the import only.
- Before `init`, if the wrapper already holds a library loaded from a different file
  (compared by normalised absolute path), that handle is dropped, so `init(<explicit
  path>)` really loads the explicit path. The Capture Process logs
  `ZWO library loaded: <path>` at INFO.
- One helper does both, used by the Capture Process entry point and by the ZWO camera
  class. The standalone bench tool, which imports nothing from `rbl`, carries its own
  copy of the same two steps.

### D3. Real serial and library version

- The serial comes from ZWO's `ASIGetSerialNumber` (8 bytes, rendered as 16 uppercase
  hex characters), called through the wrapper's loaded library handle. zwoasi 0.2.0 does
  not wrap it. If the function is missing or returns an error, the serial falls back to
  the camera ID as today, and a WARNING names the fallback.
- `sdk_version` comes from `ASIGetSDKVersion` (a C string). If it is unavailable, the
  value is `unknown (zwoasi <wrapper version>)`.
- Dark Frames already saved under the old `0` folder stay where they are. New ones go
  under the real serial.

### D4. Saved-file feedback comes from the replies

- `SessionRecorder.take_darks` sets the camera message `Taking N Dark Frames…`
  (neutral) and remembers the command id. The `ok` reply's `file` gives
  `Dark Frame saved: <file>` (ok level), and an `error` reply gives
  `Dark Frames not saved: <message>` (warn level). Each is logged to `rbl.log` (INFO,
  WARNING).
- Photo works the same way from the photo command's `ok` reply (`files`):
  `Photo saved: <names>`.
- The `darks_saved` and `photo` events keep feeding `events.csv` exactly as now.

### D5. ROI hint

A label in the Camera tab's Aspect row shows one of the three texts in the Solution,
chosen from the same state that enables the Draft buttons. It is hidden when the kind is
USB or the ZWO camera is closed. Apply ROI and Discard stay disabled with no Draft, as
today.

### D6. Exposure and gain sliders

- Each slider is the repo's no-scroll slider variant. Wheel events never change it.
- The exposure slider has `ZWO_EXPOSURE_SLIDER_STEPS` positions mapped logarithmically
  between the camera's Exposure control minimum and maximum. Gain's slider is linear,
  one position per gain unit, over the Gain control range. The mapping functions are
  pure module-level functions, so they are tested without a widget.
- Slider tracking is off: dragging updates only the number box (signals blocked), and
  release (or a keyboard step) sends one `set` through the same recorder method the
  number box uses. Rendering from confirmed settings repositions the slider with its
  signals blocked.

### D7. Auto exposure and auto white balance are one-shot commands in the Capture Process

- New pure functions in a new hardware-layer module, `auto_adjust`:
  - `suggest_exposure_us(raw, bit_depth, exposure_us, min_us, max_us) -> int`.
    Full scale is 2^bit_depth − 1. If more than `AUTO_EXPOSURE_SATURATED_FRACTION` of
    pixels are at or above `AUTO_EXPOSURE_SATURATION_LEVEL` × full scale, return
    exposure × `AUTO_EXPOSURE_SATURATED_STEP`. Otherwise the black level is the
    `PREVIEW_AUTO_LOW_PCT` percentile and the signal is the
    `AUTO_EXPOSURE_PERCENTILE` percentile minus black. The suggestion scales exposure so
    that signal reaches `AUTO_EXPOSURE_TARGET_FRACTION` × full scale minus black. The
    ratio is clamped to [1/`AUTO_EXPOSURE_MAX_RATIO`, `AUTO_EXPOSURE_MAX_RATIO`] (a
    signal ≤ 0 counts as the maximum ratio), then the result is rounded and clamped to
    [min_us, max_us].
  - `grey_world_gains(raw, bayer_pattern) -> tuple[float, float]`: subtract the black
    level (the same percentile) from the mosaic, take the mean of the red, both green and
    blue sites (located from the Bayer pattern), and return
    (mean G / mean R, mean G / mean B), each clamped to
    [`PREVIEW_WB_MIN`, `PREVIEW_WB_MAX`]. A non-positive mean gives 1.0 for that channel.
- The display-stretch function gains `wb_r` and `wb_b` (default 1.0). On a 3-channel
  image it multiplies the red and blue channels of its private copy before stretching,
  so auto-stretch percentiles see the balanced image. A 2-D image ignores them. The
  input is never modified, as now.
- Constants, in the capture config module: `AUTO_EXPOSURE_TARGET_FRACTION = 0.5`
  (headroom for a glow that brightens during a run), `AUTO_EXPOSURE_PERCENTILE = 99.5`,
  `AUTO_EXPOSURE_SATURATION_LEVEL = 0.98`, `AUTO_EXPOSURE_SATURATED_FRACTION = 0.001`,
  `AUTO_EXPOSURE_SATURATED_STEP = 0.25`, `AUTO_EXPOSURE_MAX_RATIO = 16.0`,
  `PREVIEW_WB_MIN = 0.25`, `PREVIEW_WB_MAX = 4.0`, `PREVIEW_WB_DEFAULT = 1.0`,
  `ZWO_EXPOSURE_SLIDER_STEPS = 1000`.
- **Protocol version 2.** The `preview` command gains optional `wb_r` and `wb_b`
  (finite, within the WB limits). Two new commands with no fields:
  - `auto_exposure` → `ok` with `exposure_us` (the suggestion; the Capture Process does
    **not** apply it);
  - `auto_white_balance` → `ok` with `wb_r` and `wb_b` (not applied either).

  Both are refused with `not_open` when the camera is closed, and with a new error code
  `no_frame` when no frame has arrived since open. They read the latest frame under the
  existing latest-frame lock and compute outside it. The Exposure control range comes
  from the opened camera's controls. `CAPTURE_PROTOCOL_VERSION` becomes 2: the
  application and the Capture Process always ship together, and the hello check already
  refuses a mismatch.
- **The application applies the results through the existing paths**, so there is one
  source of truth. On the `auto_exposure` reply, the recorder calls its own
  `set_zwo_settings(exposure_us=…)` and announces `Auto exposure: <ms> ms`. On the
  `auto_white_balance` reply, it calls `set_zwo_preview` with the returned gains and
  announces `Auto white balance: R ×<r>, B ×<b>`. `ZwoSettings` gains `wb_r` and
  `wb_b`, appended, persisted and validated by the settings store's rules like
  brightness.
- The ZWO controls' Preview row gains **Auto exposure**, **Auto white balance** and
  **Reset colour** buttons, enabled only while the camera is open. Auto exposure is
  allowed while recording, as a typed exposure is.

### D8. Glossary

`CONTEXT.md` gains **Preview white balance** and **Auto exposure**, edited by the planner
and committed with the tickets.

## Testing Decisions

A good test fails if the behaviour is wrong, whatever the implementation. Nothing
asserts on private attributes. Files are compared by their bytes and decoded values.

- **SER header (pure).** The header round trip in the SER format tests is rewritten in
  place to expect 0, and a frame written by the real writer, decoded little-endian,
  equals the frame that went in. Prior art: the SER format test file's own decoder.
- **Library loading (fake wrapper).** The ZWO camera tests already install a stand-in
  zwoasi module. It gains a stand-in library handle (a `_name`, `ASIGetSerialNumber`,
  `ASIGetSDKVersion`), so tests prove a pre-loaded foreign handle is replaced by the
  explicit path, that the serial and version are read, and that the fallbacks apply.
  The quiet-import helper is tested by importing a throwaway module, written to
  `tmp_path`, whose import logs a WARNING, and asserting that `caplog` saw none.
- **Pure auto-adjust functions.** Synthetic NumPy mosaics with known levels: a scene at
  a quarter of the target gives four times the exposure, a saturated scene gives a
  quarter, a black scene gives the maximum ratio, and clamping holds at both ends. A
  mosaic with red sites at half the green level gives `wb_r == 2.0`. Stretch tests prove
  the input array is unchanged and the red channel scales.
- **Capture Process end to end (fake camera, real child).** Through the existing child
  harness: `auto_exposure` and `auto_white_balance` reply with values computed from the
  fake camera's known frame, are refused `not_open` before open, and `no_frame` is
  reachable through a fake seam. A preview command with WB gains changes the Preview's
  colour balance. Prior art: the capture process and capture client test files.
- **Recorder and widgets (fake child, real Qt events).** Take Darks sets the camera
  message to the saved path and `rbl.log` gets the line (`caplog`). Slider drags use real
  `QTest` mouse press, move and release, and the test counts calls to the recorder's
  public `set_zwo_settings` with a wrapper. A synthetic wheel event changes nothing. The
  ROI hint text is read from the label after real state changes. Prior art: the ZWO
  controls and Camera tab test files, and the ZWO session recorder tests.

## Out of Scope

- The camera's own (continuous) auto exposure, auto gain or auto white balance.
- Colour correction or debayering of anything saved: SER, photo FITS, Dark Frame FITS.
- Manual red and blue white-balance sliders (only the one-click button and Reset).
- Repairing SER files already recorded with flag 1, and moving existing Dark Frames out
  of the `0` folder.
- Dark Frame subtraction or any analysis.
- Mouse-wheel adjustment of any setpoint.
- The CI hello timeout (`.scratch/capture-hello-on-ci/`, tickets 152-153).
- Upgrading the zwoasi wrapper.

## Further Notes

- **What can be changed after recording.** White balance and brightness can be applied
  to the raw SER and FITS files later, in SER Player, Siril, PIPP or Fiji, because the
  files keep the raw Bayer data. Exposure and gain cannot: they are physical capture
  settings. Scaling brightness afterwards cannot recover saturated pixels or remove the
  noise a short exposure adds. That is why Auto exposure exists and why its target
  leaves headroom.
- ADR 0008 is unchanged. Decision 6 already keeps display adjustments in the Preview,
  and a one-shot exposure choice is an ordinary recorded setting.
- A bench ticket closes the set: SER Player shows the new segment correctly, the serial
  is real, the darks message appears, the sliders and Auto buttons behave on the real
  camera, and `rbl.log` shows `ZWO library loaded:` with the vendor path.
