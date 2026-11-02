#!/usr/bin/env python3
"""
zwo_bench.py  -  standalone bench tool: measure what the ZWO ASI585MC really
delivers over this PC's USB link, and whether RAW16 carries 12 real bits.

This file deliberately depends on NOTHING inside the RBL package, and imports
``zwoasi`` only inside ``main`` so ``--help`` works on a PC that has neither.
Run it on the lab PC with the camera plugged in, before trusting any frame-rate
or bit-depth assumption in the Capture Process.

WHY THIS EXISTS
    The Capture Process (docs/adr/0008-zwo-camera-owned-by-a-capture-process.md)
    records at full bit depth, and its design leans on two numbers nobody has
    measured on this PC: how many frames per second each mode really streams
    without dropping, and whether a RAW16 frame holds 12 genuine bits or 8-bit
    data stretched into 16.  Bench tickets 78 and 79 need those answers; this
    tool produces them without involving the rest of the application, so a
    surprise cannot be blamed on RBL.

--------------------------------------------------------------------------
FLOW OF OPERATIONS
--------------------------------------------------------------------------
    1. zwoasi.init(--sdk-dll)      explicit path, never a search
    2. open camera --camera-index  auto exposure and auto gain off
    3. for each of full RAW16, full RAW8, 640x400 RAW16, 640x400 RAW8:
         a. set ROI + image type
         b. stream video frames for --seconds
         c. fps_summary(frames, seconds, dropped)  -> one printed line
    4. grab one RAW16 full frame, levels_report(img) -> distinct values
       and the low-nibble values (img % 16)

    A 12-bit sensor in a 16-bit container shows low-nibble values of only
    [0]: the pixel is 12 bits shifted left by 4.  If the nibble takes all 16
    values the data really is 16-bit-wide.

--------------------------------------------------------------------------
TYPICAL USE
--------------------------------------------------------------------------
    python zwo_bench.py --sdk-dll C:\\path\\to\\ASICamera2.dll
    python zwo_bench.py --sdk-dll ASICamera2.dll --seconds 30 --camera-index 1
"""

from __future__ import annotations

import argparse
import time

import numpy as np


def fps_summary(frames: int, seconds: float, dropped: int) -> dict:
    """Per-case numbers, as plain values (no camera needed to test this)."""
    fps = frames / seconds if seconds > 0 else 0.0
    return {"frames": frames, "seconds": seconds, "fps": fps, "dropped": dropped}


def levels_report(img: np.ndarray) -> dict:
    """How many distinct pixel values a frame holds, and which low nibbles occur.

    ``low_nibble_levels`` is the first 20 distinct values of ``img % 16``.
    12-bit data stored in 16-bit pixels shows only ``[0]`` there.
    """
    return {
        "distinct_levels": int(len(np.unique(img))),
        "low_nibble_levels": [int(v) for v in np.unique(img % 16)[:20]],
    }


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Measure ZWO camera frame rate and RAW16 bit depth (standalone; no rbl).")
    p.add_argument("--sdk-dll", required=True, help="full path to ASICamera2.dll")
    p.add_argument("--seconds", type=float, default=10.0,
                   help="seconds to stream per case (default 10)")
    p.add_argument("--camera-index", type=int, default=0, help="camera index (default 0)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    import zwoasi  # imported here so --help works without it

    zwoasi.init(args.sdk_dll)
    cam = zwoasi.Camera(args.camera_index)
    info = cam.get_camera_property()
    full_w, full_h = int(info["MaxWidth"]), int(info["MaxHeight"])
    cam.set_control_value(zwoasi.ASI_EXPOSURE, 10_000, auto=False)
    cam.set_control_value(zwoasi.ASI_GAIN, 0, auto=False)

    cases = [
        (full_w, full_h, "RAW16", zwoasi.ASI_IMG_RAW16),
        (full_w, full_h, "RAW8", zwoasi.ASI_IMG_RAW8),
        (640, 400, "RAW16", zwoasi.ASI_IMG_RAW16),
        (640, 400, "RAW8", zwoasi.ASI_IMG_RAW8),
    ]
    try:
        for width, height, label, img_type in cases:
            cam.set_roi(0, 0, width, height, 1, img_type)
            cam.start_video_capture()
            frames = 0
            start = time.monotonic()
            try:
                while time.monotonic() - start < args.seconds:
                    try:
                        cam.capture_video_frame(timeout=2000)
                        frames += 1
                    except zwoasi.ZWO_IOError:
                        pass  # timeout: counted as a missing frame, not a crash
                elapsed = time.monotonic() - start
            finally:
                cam.stop_video_capture()
            s = fps_summary(frames, elapsed, int(cam.get_dropped_frames()))
            print(f"{width}x{height} {label}: frames={s['frames']} "
                  f"fps={s['fps']:.2f} dropped={s['dropped']}")

        cam.set_roi(0, 0, full_w, full_h, 1, zwoasi.ASI_IMG_RAW16)
        img = cam.capture(initial_sleep=0.1)
        img = np.asarray(img, dtype=np.uint16)
        rep = levels_report(img)
        print(f"RAW16 full frame: distinct_levels={rep['distinct_levels']}")
        print(f"RAW16 low nibble (img % 16) first 20 distinct: {rep['low_nibble_levels']}")
    finally:
        cam.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
