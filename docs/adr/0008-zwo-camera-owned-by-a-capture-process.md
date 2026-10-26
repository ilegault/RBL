# ADR 0008 - The ZWO camera is owned by a Capture Process that is RBL.exe relaunched

- **Status:** Accepted
- **Date:** 2026-10-23

## Context

The ZWO ASI585MC delivers 12-bit data in 16-bit pixels through ZWO's own library
(`ASICamera2.dll`). The application's existing camera path, OpenCV through DirectShow,
gets only 8-bit colour-processed frames from it, with no reliable control of exposure or
gain. The images are for measurement (spot position and size, glow intensity across a
run), so the ZWO path has to keep every bit and every setting fixed and recorded.

A full-resolution 16-bit frame is about 16.6 MB. Recording that inside the GUI process
would put the application's responsiveness, and every other instrument it drives, at
the mercy of disk and USB throughput.

The lab PC's antivirus scores executables by machine learning. `RBL.exe` itself, an
unsigned PyInstaller build, has been flagged before (`docs/ANTIVIRUS_FALSE_POSITIVE.md`);
its bundled DLLs never have. A new executable would be a new, unseen file to score.

Only one program can hold the camera at a time.

## Decision

1. **One program owns the ZWO camera: the Capture Process.** It alone calls the ZWO
   library. The application never opens the camera; it sends the Capture Process
   commands and displays the status and Preview it sends back. The rule is the same one
   that keeps one owner per instrument everywhere else in RBL.

2. **The Capture Process is `RBL.exe` relaunched in a capture mode, never a new
   executable.** In development it is the same interpreter running `rbl.main` with the
   capture flag. The ZWO library ships inside the one-folder build as the DLL ZWO
   publishes, byte for byte. A future agent must not split the Capture Process into its
   own `.exe`.

3. **The Capture Process imports no Qt.** It is plain Python with threads, so its
   behaviour is testable without a GUI and its boundary is only what crosses the pipe.

4. **The boundary is a language-neutral contract.** Commands and replies are UTF-8 JSON,
   one message per line, over the Capture Process's standard input and output. The files
   it writes (SER segments, FITS photos and Dark Frames, the frame log, the settings
   records) are defined by format, not by Python objects. A compiled Capture Process
   could replace this one without the application noticing. Python comes first;
   replacing it is a later decision, and decision 2 would then have to be weighed again.

5. **If the application disappears, the Capture Process finishes cleanly and exits.**
   The application sends a heartbeat every second. After 10 seconds without one, the
   Capture Process closes every open file so it stays readable, records why the last
   segment ended, releases the camera and exits. It does not keep recording on its own,
   and a restarted application does not reattach to it.

6. **The Master Recording is never converted.** Frames are written exactly as the
   library delivered them. Bit depth (16 by default, 8 by deliberate choice), exposure,
   gain and region of interest are recorded for every segment, and a change to any of
   them starts a new segment. Display stretch and brightness exist only in the Preview.

7. **The USB camera path is unchanged.** It keeps recording in-process through OpenCV.
   The operator uses one camera at a time and chooses which.

## Consequences

The GUI never touches a 16 MB frame. The Preview it receives is a few tens of kilobytes,
at most five times a second.

The application and the Capture Process run on one PC and stamp frames from the same
wall clock, so "which frames were taken while the cup was IN" is a lookup, not a
matching exercise. The stamp is when a frame arrived, not when its exposure began; at
millisecond exposures the difference is negligible, and the frame log says so.

A crash of the application costs no recorded frame, but recording stops with it. A run
that must survive the GUI dying needs reattachment, which this ADR deliberately leaves
out; it can be added later without changing any file format.

Nothing in CI can open a real camera. The Capture Process takes a fake camera that
produces known frames, so its files and its contract are tested end to end; the real
camera is covered by bench tickets.
