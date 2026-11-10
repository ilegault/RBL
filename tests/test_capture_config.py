"""capture_config is numbers only, and has exactly the agreed values."""
import ast
from pathlib import Path

from rbl.config import capture_config

EXPECTED = {
    "CAPTURE_PROTOCOL_VERSION": 1,
    "CAPTURE_DISK_STOP_BYTES": 20 * 1024**3,
    "CAPTURE_HEARTBEAT_INTERVAL_S": 1.0,
    "CAPTURE_HEARTBEAT_TIMEOUT_S": 10.0,
    "CAPTURE_STATUS_INTERVAL_S": 1.0,
    "CAPTURE_PREVIEW_MAX_FPS": 5.0,
    "CAPTURE_PREVIEW_MAX_WIDTH_PX": 960,
    "CAPTURE_PREVIEW_JPEG_QUALITY": 85,
    "CAPTURE_PHOTO_BURST_MAX": 20,
    "CAPTURE_DARK_FRAMES_MAX": 100,
    "CAPTURE_KEEP_ONE_IN_N_MAX": 1000,
    "CAPTURE_SER_HEADER_REFRESH_S": 2.0,
    "CAPTURE_SEGMENT_MAX_BYTES": 3758096384,
    "CAPTURE_WRITE_QUEUE_FRAMES": 64,
    "CAPTURE_FRAME_TIMEOUT_MIN_MS": 1000,
    "CAPTURE_CAMERA_LOST_TIMEOUTS": 5,
    "CAPTURE_SHUTDOWN_WAIT_S": 5.0,
    "ZWO_EXPOSURE_US_DEFAULT": 20000,
    "ZWO_GAIN_DEFAULT": 100,
    "ZWO_BIT_DEPTH_DEFAULT": 16,
    "PREVIEW_AUTO_LOW_PCT": 0.5,
    "PREVIEW_AUTO_HIGH_PCT": 99.5,
    "PREVIEW_BRIGHTNESS_MIN": 0.25,
    "PREVIEW_BRIGHTNESS_MAX": 4.0,
    "PREVIEW_BRIGHTNESS_DEFAULT": 1.0,
}


def test_numbers_only():
    tree = ast.parse(Path(capture_config.__file__).read_text(encoding="utf-8"))
    banned = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom)
    assert not [n for n in ast.walk(tree) if isinstance(n, banned)]
    for name, value in EXPECTED.items():
        assert getattr(capture_config, name) == value, name
