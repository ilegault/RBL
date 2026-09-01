"""
recording_config.py
All tunables for the unified session recorder in one place.

Follows the pattern of scope_config.py: one authoritative place for every
constant that would otherwise be scattered across session_recorder.py,
video_recorder.py, and the UI panels.  Changing the defaults here is safe
because the recorder always reads these at start-up, not at import time.
"""

# ---- CSV logging -----------------------------------------------------------

CSV_INTERVAL_DEFAULT_S   = 5
CSV_INTERVAL_MIN_S       = 1
CSV_INTERVAL_MAX_S       = 300

# ---- Preview and record frame rates ----------------------------------------

PREVIEW_FPS_DEFAULT      = 30
RECORD_FPS_DEFAULT       = 30
RECORD_FPS_MIN           = 1
RECORD_FPS_MAX           = 120

# Cap on preview frames pushed to the GUI event queue.  At 1080p30 a raw
# BGR ndarray is ~6 MB; flooding the Qt event queue at 30 Hz for 8 hours
# would create sustained GC pressure.  15 Hz is indistinguishable to the
# eye on a still or slow-moving beam image.
PREVIEW_EMIT_MAX_FPS     = 15

# ---- Video segmentation ----------------------------------------------------

SEGMENT_SECONDS_DEFAULT  = 600     # 10 min
SEGMENT_SECONDS_CHOICES  = [60, 300, 600, 1800, 3600]

# AVI container hard ceiling is 2 GB (32-bit RIFF length field).  Stay well
# below it so even a burst of unusually large MJPG frames can't push us over.
SEGMENT_MAX_BYTES        = 1_610_612_736   # 1.5 GB

# ---- Master (on-disk capture) codec ----------------------------------------
#
# THIS is the compression that actually happens in the lab.  The MP4 transcode
# below requires ffmpeg.exe, which rbl.spec deliberately does not bundle
# (antivirus quarantine, see its lines 63-87), so on a lab machine the CRF
# preset is inert and the master codec is the ONLY thing standing between the
# sensor and the archive.
#
# label -> (fourcc, container_ext, jpeg_quality|None, lossless: bool)
#
# Notes:
#   MJPG quality must be set via CAP_OPENCV_MJPEG backend; on the default
#   FFmpeg backend, VideoWriter.set(VIDEOWRITER_PROP_QUALITY, q) silently
#   does nothing and the writer keeps an internal default that measures
#   worse than every explicit setting we tested.
#
#   FFV1 uses .mkv, not .avi.  AVI's RIFF length field is 32-bit (2 GB
#   ceiling); FFV1 fills 1.5 GB fast enough that SEGMENT_MAX_BYTES would
#   trigger constantly.  Matroska has no such ceiling.
#
#   "PNGS" is a sentinel, not a fourcc — VideoRecorder writes a folder of
#   per-frame PNGs instead of a single container file.

MASTER_CODECS = {
    "MJPEG q98 (default)":     ("MJPG", ".avi", 98,   False),
    "MJPEG q100":              ("MJPG", ".avi", 100,  False),
    "FFV1 lossless":           ("FFV1", ".mkv", None, True),
    "PNG sequence (lossless)": ("PNGS", "",     None, True),
}
MASTER_CODEC_DEFAULT = "MJPEG q98 (default)"

# ---- MP4 transcode quality -------------------------------------------------

QUALITY_PRESETS = {                # label -> (crf, x264 preset)
    "Archive (CRF 12)":  (12, "slow"),
    "Standard (CRF 18)": (18, "medium"),
    "Small (CRF 23)":    (23, "fast"),
}
QUALITY_DEFAULT = "Standard (CRF 18)"

# ---- Disk space guards -----------------------------------------------------

DISK_WARN_BYTES     = 5  * 1024**3   # warn below 5 GB free
DISK_AUTOSTOP_BYTES = 1  * 1024**3   # clean auto-stop below 1 GB free

# ---- Camera resolution presets ---------------------------------------------
# Carried over from camera_widget.py unchanged.  (0, 0) is a sentinel meaning
# "request 10000x10000 so the camera delivers its maximum supported size."

RES_PRESETS = [
    (640,  480,  "640x480"),
    (800,  600,  "800x600"),
    (1280, 720,  "1280x720 (HD)"),
    (1920, 1080, "1920x1080 (FHD)"),
    (2560, 1440, "2560x1440 (QHD)"),
    (3840, 2160, "3840x2160 (4K)"),
    (0,    0,    "Max"),
]
