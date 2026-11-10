"""
camera.py
Camera protocol, ZWO camera implementation over zwoasi, and fake camera for testing.

WHY THIS EXISTS
---------------
ADR 0008 requires that the ZWO camera be isolated behind a clear CaptureCamera
contract so the Capture Process can drive both real hardware (zwoasi over ASICamera2.dll)
and simulated hardware (FakeCamera). FakeCamera produces bit-for-bit deterministic
frames so all recording formats, preview transformations, frame logging, and failure
modes can be tested without physical instruments attached to the host.
ZwoCamera wraps zwoasi 0.2.0, delaying library import until open() so test suites and
environments without the native DLL can freely import and inspect the module.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import Any, Protocol, runtime_checkable

import numpy as np

from rbl.config.capture_config import (
    ZWO_BIT_DEPTH_DEFAULT,
    ZWO_EXPOSURE_US_DEFAULT,
    ZWO_GAIN_DEFAULT,
)
from rbl.hardware.capture_protocol import snap_roi


class CameraLost(Exception):
    """Raised when communication with the camera is lost or fails unrecoverably."""


@dataclass(frozen=True)
class CameraInfo:
    """Fixed hardware properties of a connected camera."""
    model: str
    serial: str
    sdk_version: str
    sensor_width: int
    sensor_height: int
    bayer_pattern: str


@dataclass(frozen=True)
class CameraSettings:
    """Active acquisition settings for a camera."""
    exposure_us: int
    gain: int
    bit_depth: int
    roi: tuple[int, int, int, int]
    offset: int
    usb_bandwidth: int
    high_speed_mode: int


@runtime_checkable
class CaptureCamera(Protocol):
    """Abstract interface to a camera device."""

    def open(self, index: int) -> None:
        """Open the camera by index."""
        ...

    def info(self) -> CameraInfo:
        """Return static camera information."""
        ...

    def controls(self) -> dict[str, dict[str, int]]:
        """Return control ranges and defaults for Exposure, Gain, Offset, BandWidth."""
        ...

    def apply(self, settings: CameraSettings) -> CameraSettings:
        """Apply requested acquisition settings and return applied settings."""
        ...

    def start_stream(self) -> None:
        """Begin video stream acquisition."""
        ...

    def get_frame(self, timeout_ms: int) -> np.ndarray | None:
        """Wait for the next frame. Return None on timeout, raise CameraLost on error."""
        ...

    def dropped_frames(self) -> int:
        """Return total hardware dropped frames since stream start."""
        ...

    def temperature_c(self) -> float:
        """Return current sensor temperature in degrees Celsius."""
        ...

    def stop_stream(self) -> None:
        """Stop video stream acquisition."""
        ...

    def close(self) -> None:
        """Release the camera."""
        ...


FAKE_MODEL = "Fake ASI"
FAKE_SERIAL = "FAKE0001"
FAKE_SDK_VERSION = "fake"
FAKE_SENSOR_WIDTH = 1920
FAKE_SENSOR_HEIGHT = 1080
FAKE_BAYER_PATTERN = "RG"
FAKE_TEMPERATURE_C = 25.0

FAKE_CONTROLS: dict[str, dict[str, int]] = {
    "Exposure": {"min": 32, "max": 2_000_000_000, "default": ZWO_EXPOSURE_US_DEFAULT},
    "Gain": {"min": 0, "max": 600, "default": ZWO_GAIN_DEFAULT},
    "Offset": {"min": 0, "max": 100, "default": 0},
    "BandWidth": {"min": 40, "max": 100, "default": 50},
}


class FakeCamera:
    """Deterministic simulated camera implementing CaptureCamera."""

    def __init__(self, fail_after: int | None = None) -> None:
        self._fail_after = fail_after
        self._streaming = False
        self._k = 0
        self._info = CameraInfo(
            model=FAKE_MODEL,
            serial=FAKE_SERIAL,
            sdk_version=FAKE_SDK_VERSION,
            sensor_width=FAKE_SENSOR_WIDTH,
            sensor_height=FAKE_SENSOR_HEIGHT,
            bayer_pattern=FAKE_BAYER_PATTERN,
        )
        self._settings = CameraSettings(
            exposure_us=ZWO_EXPOSURE_US_DEFAULT,
            gain=ZWO_GAIN_DEFAULT,
            bit_depth=ZWO_BIT_DEPTH_DEFAULT,
            roi=(0, 0, FAKE_SENSOR_WIDTH, FAKE_SENSOR_HEIGHT),
            offset=0,
            usb_bandwidth=50,
            high_speed_mode=0,
        )

    def open(self, index: int) -> None:
        self._k = 0
        self._streaming = False
        self._settings = CameraSettings(
            exposure_us=ZWO_EXPOSURE_US_DEFAULT,
            gain=ZWO_GAIN_DEFAULT,
            bit_depth=ZWO_BIT_DEPTH_DEFAULT,
            roi=(0, 0, FAKE_SENSOR_WIDTH, FAKE_SENSOR_HEIGHT),
            offset=0,
            usb_bandwidth=50,
            high_speed_mode=0,
        )

    def info(self) -> CameraInfo:
        return self._info

    def controls(self) -> dict[str, dict[str, int]]:
        return dict(FAKE_CONTROLS)

    def apply(self, settings: CameraSettings) -> CameraSettings:
        snapped = snap_roi(
            settings.roi[0],
            settings.roi[1],
            settings.roi[2],
            settings.roi[3],
            FAKE_SENSOR_WIDTH,
            FAKE_SENSOR_HEIGHT,
        )
        applied = replace(settings, roi=snapped)
        self._settings = applied
        return applied

    def start_stream(self) -> None:
        self._streaming = True

    def get_frame(self, timeout_ms: int) -> np.ndarray | None:
        if not self._streaming:
            time.sleep(timeout_ms / 1000.0)
            return None

        if self._fail_after is not None and self._k >= self._fail_after:
            raise CameraLost("fake camera lost")

        time.sleep(max(self._settings.exposure_us, 1000) / 1_000_000.0)

        k = self._k
        self._k += 1

        width = self._settings.roi[2]
        height = self._settings.roi[3]

        x = np.arange(width, dtype=np.int64)
        y = np.arange(height, dtype=np.int64).reshape(-1, 1)

        if self._settings.bit_depth == 16:
            frame = (((k * 7 + x + 3 * y) % 4096) << 4).astype(np.uint16)
        else:
            frame = ((k + x + y) % 256).astype(np.uint8)

        return frame

    def dropped_frames(self) -> int:
        return 0

    def temperature_c(self) -> float:
        return FAKE_TEMPERATURE_C

    def stop_stream(self) -> None:
        self._streaming = False

    def close(self) -> None:
        self._streaming = False


class ZwoCamera:
    """CaptureCamera implementation driving real ZWO hardware via zwoasi.

    WHY THIS EXISTS
    ---------------
    ADR 0008 requires that the Capture Process isolate all hardware interaction
    behind CaptureCamera. ZwoCamera wraps the third-party zwoasi 0.2.0 wrapper
    around ASICamera2.dll.

    CONFIRMED ZWOASI 0.2.0 SHAPE
    -----------------------------
    Confirmed from zwoasi 0.2.0 installed package (__init__.py):
    - Module functions: init(library_file), list_cameras(), get_num_cameras().
    - Camera class: Camera(id_) where id_ is integer index or model string.
    - Camera methods:
        * get_camera_property() -> dict (Name, CameraID, MaxHeight, MaxWidth, BayerPattern, ...)
        * get_controls() -> dict[str, dict] with MinValue, MaxValue, DefaultValue, ControlType
        * get_control_value(control_type) -> [value, auto_bool]
        * set_control_value(control_type, value, auto=False)
        * set_roi(start_x, start_y, width, height, bins, image_type)
        * set_image_type(image_type)
        * start_video_capture()
        * capture_video_frame(buffer_=None, filename=None, timeout=None) -> np.ndarray
        * get_dropped_frames() -> int
        * stop_video_capture()
        * get_id() -> str (camera identifier)
        * close()
    - Constants:
        * ASI_BAYER_RG=0, ASI_BAYER_BG=1, ASI_BAYER_GR=2, ASI_BAYER_RB=3
        * ASI_IMG_RAW8=0, ASI_IMG_RGB24=1, ASI_IMG_RAW16=2, ASI_IMG_Y8=3
        * ASI_GAIN=0, ASI_EXPOSURE=1, ASI_OFFSET=5, ASI_BANDWIDTHOVERLOAD=6,
          ASI_TEMPERATURE=8, ASI_HIGH_SPEED_MODE=14
    - Timeout behavior:
        When capture_video_frame times out, the underlying C call returns ASI_ERROR_TIMEOUT (11).
        zwoasi maps error code 11 to zwoasi.ZWO_IOError('Timeout', 11) where exc.error_code == 11.
        ZwoCamera catches this exception, verifies error_code == 11 (or str(exc) == "Timeout"),
        and returns None. Any other library exception (ZWO_Error / ZWO_IOError with
        error_code != 11) raises CameraLost(str(exc)).
    """

    def __init__(self, sdk_dll: str) -> None:
        self._sdk_dll = sdk_dll
        self._cam: Any | None = None
        self._info: CameraInfo | None = None
        self._settings: CameraSettings | None = None

    def open(self, index: int) -> None:
        """Open the camera by index with explicit SDK DLL path."""
        import zwoasi

        zwoasi.init(self._sdk_dll)
        self._cam = zwoasi.Camera(index)

        prop = self._cam.get_camera_property()
        model = str(prop.get("Name", "ZWO ASI"))

        # Serial from library: get_id() or CameraID fallback
        serial = ""
        try:
            raw_id = self._cam.get_id()
            if raw_id is not None:
                serial = str(raw_id).strip()
        except Exception:
            pass
        if not serial:
            serial = str(prop.get("CameraID", ""))

        # SDK version: zwoasi exposes __version__; ASICamera2 C SDK version is not exposed by 0.2.0
        sdk_version = getattr(zwoasi, "sdk_version", getattr(zwoasi, "__version__", "0.2.0"))

        sensor_width = int(prop.get("MaxWidth", 0))
        sensor_height = int(prop.get("MaxHeight", 0))

        bayer_int = int(prop.get("BayerPattern", 0))
        bayer_map = {0: "RG", 1: "BG", 2: "GR", 3: "GB"}
        bayer_pattern = bayer_map.get(bayer_int, "RG")

        self._info = CameraInfo(
            model=model,
            serial=serial,
            sdk_version=str(sdk_version),
            sensor_width=sensor_width,
            sensor_height=sensor_height,
            bayer_pattern=bayer_pattern,
        )

    def info(self) -> CameraInfo:
        if self._info is None:
            raise RuntimeError("Camera is not open")
        return self._info

    def controls(self) -> dict[str, dict[str, int]]:
        if self._cam is None:
            raise RuntimeError("Camera is not open")
        raw = self._cam.get_controls()
        res: dict[str, dict[str, int]] = {}
        mapping = {
            "Exposure": ["Exposure"],
            "Gain": ["Gain"],
            "Offset": ["Offset", "Brightness"],
            "BandWidth": ["BandWidth", "BandWidthOverload"],
        }
        for target, candidates in mapping.items():
            for c in candidates:
                if c in raw:
                    ctrl = raw[c]
                    res[target] = {
                        "min": int(ctrl["MinValue"]),
                        "max": int(ctrl["MaxValue"]),
                        "default": int(ctrl["DefaultValue"]),
                    }
                    break
        return res

    def apply(self, settings: CameraSettings) -> CameraSettings:
        if self._cam is None or self._info is None:
            raise RuntimeError("Camera is not open")

        import zwoasi

        snapped = snap_roi(
            settings.roi[0],
            settings.roi[1],
            settings.roi[2],
            settings.roi[3],
            self._info.sensor_width,
            self._info.sensor_height,
        )

        if settings.bit_depth == 16:
            img_type = zwoasi.ASI_IMG_RAW16
        elif settings.bit_depth == 8:
            img_type = zwoasi.ASI_IMG_RAW8
        else:
            raise ValueError(f"Unsupported bit depth: {settings.bit_depth}")

        # Set Exposure, Gain with auto=False, HighSpeedMode=0
        self._cam.set_control_value(zwoasi.ASI_EXPOSURE, settings.exposure_us, auto=False)
        self._cam.set_control_value(zwoasi.ASI_GAIN, settings.gain, auto=False)
        self._cam.set_control_value(zwoasi.ASI_HIGH_SPEED_MODE, 0, auto=False)

        # Set ROI
        self._cam.set_roi(
            start_x=snapped[0],
            start_y=snapped[1],
            width=snapped[2],
            height=snapped[3],
            bins=1,
            image_type=img_type,
        )

        # Read back offset, usb_bandwidth, and high_speed_mode
        offset_val = int(self._cam.get_control_value(zwoasi.ASI_OFFSET)[0])
        bandwidth_val = int(self._cam.get_control_value(zwoasi.ASI_BANDWIDTHOVERLOAD)[0])
        hsm_val = int(self._cam.get_control_value(zwoasi.ASI_HIGH_SPEED_MODE)[0])

        applied = CameraSettings(
            exposure_us=settings.exposure_us,
            gain=settings.gain,
            bit_depth=settings.bit_depth,
            roi=snapped,
            offset=offset_val,
            usb_bandwidth=bandwidth_val,
            high_speed_mode=hsm_val,
        )
        self._settings = applied
        return applied

    def start_stream(self) -> None:
        if self._cam is None:
            raise RuntimeError("Camera is not open")
        self._cam.start_video_capture()

    def get_frame(self, timeout_ms: int) -> np.ndarray | None:
        if self._cam is None:
            raise RuntimeError("Camera is not open")
        try:
            return self._cam.capture_video_frame(timeout=timeout_ms)
        except Exception as exc:
            error_code = getattr(exc, "error_code", None)
            if error_code == 11 or str(exc) == "Timeout":
                return None
            raise CameraLost(str(exc)) from exc

    def dropped_frames(self) -> int:
        if self._cam is None:
            raise RuntimeError("Camera is not open")
        return int(self._cam.get_dropped_frames())

    def temperature_c(self) -> float:
        if self._cam is None:
            raise RuntimeError("Camera is not open")
        import zwoasi

        temp_raw = self._cam.get_control_value(zwoasi.ASI_TEMPERATURE)[0]
        return float(temp_raw) / 10.0

    def stop_stream(self) -> None:
        if self._cam is not None:
            try:
                self._cam.stop_video_capture()
            except Exception:
                pass

    def close(self) -> None:
        if self._cam is not None:
            try:
                self._cam.close()
            except Exception:
                pass
            self._cam = None
