"""
Tests for rbl.capture.camera and FakeCamera.

WHY THIS EXISTS
---------------
ADR 0008 requires a clean CaptureCamera protocol and a deterministic FakeCamera
so capture behaviour can be thoroughly tested without physical ZWO hardware.
"""
from __future__ import annotations

import numpy as np
import pytest

from rbl.capture.camera import (
    FAKE_BAYER_PATTERN,
    FAKE_CONTROLS,
    FAKE_MODEL,
    FAKE_SDK_VERSION,
    FAKE_SENSOR_HEIGHT,
    FAKE_SENSOR_WIDTH,
    FAKE_SERIAL,
    FAKE_TEMPERATURE_C,
    CameraInfo,
    CameraLost,
    CameraSettings,
    CaptureCamera,
    FakeCamera,
)
from rbl.config.capture_config import (
    ZWO_BIT_DEPTH_DEFAULT,
    ZWO_EXPOSURE_US_DEFAULT,
    ZWO_GAIN_DEFAULT,
)


def test_camera_dataclasses() -> None:
    info = CameraInfo(
        model=FAKE_MODEL,
        serial=FAKE_SERIAL,
        sdk_version=FAKE_SDK_VERSION,
        sensor_width=FAKE_SENSOR_WIDTH,
        sensor_height=FAKE_SENSOR_HEIGHT,
        bayer_pattern=FAKE_BAYER_PATTERN,
    )
    assert info.model == "Fake ASI"
    assert info.serial == "FAKE0001"
    assert info.sensor_width == 1920

    settings = CameraSettings(
        exposure_us=ZWO_EXPOSURE_US_DEFAULT,
        gain=ZWO_GAIN_DEFAULT,
        bit_depth=ZWO_BIT_DEPTH_DEFAULT,
        roi=(0, 0, 1920, 1080),
        offset=0,
        usb_bandwidth=50,
        high_speed_mode=0,
    )
    assert settings.exposure_us == 20000
    assert settings.roi == (0, 0, 1920, 1080)


def test_fake_camera_implements_protocol() -> None:
    cam = FakeCamera()
    assert isinstance(cam, CaptureCamera)


def test_fake_camera_lifecycle_and_frames() -> None:
    cam = FakeCamera()
    cam.open(0)

    info = cam.info()
    assert info.model == FAKE_MODEL
    assert info.serial == FAKE_SERIAL
    assert info.sensor_width == FAKE_SENSOR_WIDTH
    assert info.sensor_height == FAKE_SENSOR_HEIGHT
    assert info.bayer_pattern == FAKE_BAYER_PATTERN
    assert info.sdk_version == FAKE_SDK_VERSION

    controls = cam.controls()
    assert controls == FAKE_CONTROLS
    assert cam.temperature_c() == FAKE_TEMPERATURE_C
    assert cam.dropped_frames() == 0

    # When not streaming, get_frame returns None after timeout
    assert cam.get_frame(timeout_ms=10) is None

    # Start stream and get 16-bit frame
    cam.start_stream()
    frame0 = cam.get_frame(timeout_ms=100)
    assert frame0 is not None
    assert frame0.dtype == np.uint16
    assert frame0.shape == (1080, 1920)

    # Formula at k=0, x=0, y=0: ((0*7 + 0 + 0) % 4096) << 4 = 0
    assert frame0[0, 0] == 0
    # Formula at k=0, x=1, y=2: ((1 + 3*2) % 4096) << 4 = 7 << 4 = 112
    assert frame0[2, 1] == (7 << 4)

    # Frame 1 (k=1)
    frame1 = cam.get_frame(timeout_ms=100)
    assert frame1 is not None
    # k=1, x=0, y=0 -> (7 % 4096) << 4 = 112
    assert frame1[0, 0] == (7 << 4)

    # Switch to 8-bit and smaller ROI
    new_settings = CameraSettings(
        exposure_us=1000,
        gain=50,
        bit_depth=8,
        roi=(0, 0, 96, 50),
        offset=0,
        usb_bandwidth=50,
        high_speed_mode=0,
    )
    applied = cam.apply(new_settings)
    assert applied.roi == (0, 0, 96, 50)
    assert applied.bit_depth == 8

    # Frame 2 (k=2) with 8-bit
    frame2 = cam.get_frame(timeout_ms=100)
    assert frame2 is not None
    assert frame2.dtype == np.uint8
    assert frame2.shape == (50, 96)
    # k=2, x=0, y=0 -> (2 + 0 + 0) % 256 = 2
    assert frame2[0, 0] == 2
    # k=2, x=3, y=4 -> (2 + 3 + 4) % 256 = 9
    assert frame2[4, 3] == 9

    cam.stop_stream()
    cam.close()


def test_fake_camera_fail_after() -> None:
    cam = FakeCamera(fail_after=2)
    cam.open(0)
    cam.start_stream()

    f0 = cam.get_frame(timeout_ms=100)
    assert f0 is not None
    f1 = cam.get_frame(timeout_ms=100)
    assert f1 is not None

    with pytest.raises(CameraLost, match="fake camera lost"):
        cam.get_frame(timeout_ms=100)
