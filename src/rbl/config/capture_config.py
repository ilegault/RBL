"""
capture_config.py
Numbers for the ZWO Capture Process and its contract with the application.

WHY THIS EXISTS
---------------
The application and the Capture Process are two programs that must agree on
every limit that crosses the pipe (protocol version, burst sizes, keep-1-in-N
range) and on every timing the two sides watch each other by (heartbeat,
status cadence). If each side kept its own copy the two would drift, and the
failure would show up as a refused command or a "lost" child on the bench.
Every number has one home (AGENTS.md invariant 3), and it is here.

Numbers only: no functions, classes or imports. ``tests/test_capture_config.py``
enforces that with ``ast``. Rules that use these numbers live in
``rbl/hardware/capture_protocol.py``.
"""

CAPTURE_PROTOCOL_VERSION = 1
CAPTURE_DISK_STOP_BYTES = 20 * 1024**3
CAPTURE_HEARTBEAT_INTERVAL_S = 1.0
CAPTURE_HEARTBEAT_TIMEOUT_S = 10.0
CAPTURE_STATUS_INTERVAL_S = 1.0
CAPTURE_PREVIEW_MAX_FPS = 5.0
CAPTURE_PREVIEW_MAX_WIDTH_PX = 960
CAPTURE_PREVIEW_JPEG_QUALITY = 85
CAPTURE_PHOTO_BURST_MAX = 20
CAPTURE_DARK_FRAMES_MAX = 100
CAPTURE_KEEP_ONE_IN_N_MAX = 1000
CAPTURE_SER_HEADER_REFRESH_S = 2.0
CAPTURE_SEGMENT_MAX_BYTES = int(3.5 * 1024**3)
CAPTURE_WRITE_QUEUE_FRAMES = 64
CAPTURE_FRAME_TIMEOUT_MIN_MS = 1000
CAPTURE_CAMERA_LOST_TIMEOUTS = 5
CAPTURE_SHUTDOWN_WAIT_S = 5.0
ZWO_EXPOSURE_US_DEFAULT = 20_000
ZWO_GAIN_DEFAULT = 100
ZWO_BIT_DEPTH_DEFAULT = 16
PREVIEW_AUTO_LOW_PCT = 0.5
PREVIEW_AUTO_HIGH_PCT = 99.5
PREVIEW_BRIGHTNESS_MIN = 0.25
PREVIEW_BRIGHTNESS_MAX = 4.0
PREVIEW_BRIGHTNESS_DEFAULT = 1.0
ZWO_CAMERA_KIND_DEFAULT = "usb"
CAPTURE_KEEP_ONE_IN_N_DEFAULT = 1
PREVIEW_MODE_DEFAULT = "auto"
