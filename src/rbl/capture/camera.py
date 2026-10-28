"""
camera.py
Camera protocol and fake camera implementation for the Capture Process.

WHY THIS EXISTS
---------------
ADR 0008 requires that the ZWO camera be isolated behind a clear contract so the
Capture Process can drive both real hardware (zwoasi) and simulated hardware
(FakeCamera). FakeCamera produces bit-for-bit deterministic frames so all
recording formats, preview transformations, frame logging, and failure modes can
be tested without physical instruments attached to the host.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import Protocol, runtime_checkable

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

        # Sleep max(exposure_us, 1000) microseconds
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
