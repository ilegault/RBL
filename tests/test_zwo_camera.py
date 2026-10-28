"""
test_zwo_camera.py
Unit tests for ZwoCamera wrapping zwoasi against a recorded stand-in module,
and Seam A integration testing missing DLL behavior.

WHY THIS EXISTS
---------------
ADR 0008 and Spec D5 require that ZwoCamera implement CaptureCamera over the
third-party zwoasi 0.2.0 wrapper around ASICamera2.dll.
Because CI runners do not have physical ZWO hardware or the proprietary SDK DLL
installed, tests run against an in-memory stand-in module with the confirmed
zwoasi 0.2.0 API shape that records all invocations and arguments.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from rbl.capture.camera import (
    CameraLost,
    CameraSettings,
    CaptureCamera,
    ZwoCamera,
)
from rbl.hardware import capture_protocol
from tests.capture_child import CaptureChild


class _StandInZwoasiModule:
    """Creates a mock zwoasi 0.2.0 module object for testing."""

    def __init__(self) -> None:
        self.__version__ = "0.2.0"
        self.sdk_version = "0.2.0"

        # Bayer patterns
        self.ASI_BAYER_RG = 0
        self.ASI_BAYER_BG = 1
        self.ASI_BAYER_GR = 2
        self.ASI_BAYER_RB = 3

        # Image types
        self.ASI_IMG_RAW8 = 0
        self.ASI_IMG_RGB24 = 1
        self.ASI_IMG_RAW16 = 2
        self.ASI_IMG_Y8 = 3
        self.ASI_IMG_END = -1

        # Control types
        self.ASI_GAIN = 0
        self.ASI_EXPOSURE = 1
        self.ASI_GAMMA = 2
        self.ASI_WB_R = 3
        self.ASI_WB_B = 4
        self.ASI_BRIGHTNESS = 5
        self.ASI_OFFSET = 5
        self.ASI_BANDWIDTHOVERLOAD = 6
        self.ASI_TEMPERATURE = 8
        self.ASI_HIGH_SPEED_MODE = 14

        # Exceptions
        class ZWO_Error(Exception):
            pass

        class ZWO_IOError(ZWO_Error):
            def __init__(self, message: str, error_code: int | None = None) -> None:
                super().__init__(message)
                self.error_code = error_code

        self.ZWO_Error = ZWO_Error
        self.ZWO_IOError = ZWO_IOError

        # Spy / Recording state
        self.init_calls: list[str | None] = []
        self.call_sequence: list[str] = []
        self.cameras: list[str] = ["ZWO ASI585MC"]
        self.current_camera: _SpyCamera | None = None
        self.bayer_pattern_to_report: int = 0

        # Functions
        def init(library_file: str | None = None) -> None:
            self.init_calls.append(library_file)
            self.call_sequence.append(f"init:{library_file}")

        def list_cameras() -> list[str]:
            return list(self.cameras)

        def get_num_cameras() -> int:
            return len(self.cameras)

        self.init = init
        self.list_cameras = list_cameras
        self.get_num_cameras = get_num_cameras

        outer = self

        class SpyCamera:
            def __init__(self, camera_id: int | str) -> None:
                outer.call_sequence.append(f"Camera:{camera_id}")
                self.id = camera_id
                self.set_control_calls: list[tuple[int, int, bool]] = []
                self.set_roi_calls: list[dict[str, Any]] = []
                self.capture_calls: list[dict[str, Any]] = []
                self.start_video_calls = 0
                self.stop_video_calls = 0
                self.close_calls = 0
                self.raise_on_capture: Exception | None = None
                self.return_frame: np.ndarray | None = None
                self.dropped_count = 0
                self.temp_raw = 250  # 25.0 C
                outer.current_camera = self

            def get_camera_property(self) -> dict[str, Any]:
                return {
                    "Name": "ZWO ASI585MC",
                    "CameraID": 0,
                    "MaxWidth": 3840,
                    "MaxHeight": 2160,
                    "IsColorCam": True,
                    "BayerPattern": outer.bayer_pattern_to_report,
                }

            def get_id(self) -> str:
                return "TEST_SERIAL_585"

            def get_num_controls(self) -> int:
                return 4

            def get_controls(self) -> dict[str, dict[str, Any]]:
                return {
                    "Exposure": {
                        "Name": "Exposure",
                        "MinValue": 32,
                        "MaxValue": 2_000_000_000,
                        "DefaultValue": 20_000,
                        "ControlType": outer.ASI_EXPOSURE,
                    },
                    "Gain": {
                        "Name": "Gain",
                        "MinValue": 0,
                        "MaxValue": 600,
                        "DefaultValue": 100,
                        "ControlType": outer.ASI_GAIN,
                    },
                    "Offset": {
                        "Name": "Offset",
                        "MinValue": 0,
                        "MaxValue": 100,
                        "DefaultValue": 10,
                        "ControlType": outer.ASI_OFFSET,
                    },
                    "BandWidth": {
                        "Name": "BandWidth",
                        "MinValue": 40,
                        "MaxValue": 100,
                        "DefaultValue": 50,
                        "ControlType": outer.ASI_BANDWIDTHOVERLOAD,
                    },
                }

            def get_control_value(self, control_type: int) -> list[Any]:
                if control_type == outer.ASI_OFFSET:
                    return [10, False]
                if control_type == outer.ASI_BANDWIDTHOVERLOAD:
                    return [50, False]
                if control_type == outer.ASI_HIGH_SPEED_MODE:
                    return [0, False]
                if control_type == outer.ASI_TEMPERATURE:
                    return [self.temp_raw, False]
                if control_type == outer.ASI_EXPOSURE:
                    return [20000, False]
                if control_type == outer.ASI_GAIN:
                    return [100, False]
                return [0, False]

            def set_control_value(
                self, control_type: int, value: int, auto: bool = False
            ) -> None:
                self.set_control_calls.append((control_type, value, auto))

            def set_roi(
                self,
                start_x: int | None = None,
                start_y: int | None = None,
                width: int | None = None,
                height: int | None = None,
                bins: int | None = None,
                image_type: int | None = None,
            ) -> None:
                self.set_roi_calls.append({
                    "start_x": start_x,
                    "start_y": start_y,
                    "width": width,
                    "height": height,
                    "bins": bins,
                    "image_type": image_type,
                })

            def capture_video_frame(
                self,
                buffer_: Any = None,
                filename: str | None = None,
                timeout: int | None = None,
            ) -> np.ndarray:
                self.capture_calls.append({"timeout": timeout})
                if self.raise_on_capture is not None:
                    raise self.raise_on_capture
                if self.return_frame is not None:
                    return self.return_frame
                return np.zeros((100, 100), dtype=np.uint16)

            def get_dropped_frames(self) -> int:
                return self.dropped_count

            def start_video_capture(self) -> None:
                self.start_video_calls += 1

            def stop_video_capture(self) -> None:
                self.stop_video_calls += 1

            def close(self) -> None:
                self.close_calls += 1

        self.Camera = SpyCamera


_SpyCamera = Any


@pytest.fixture
def stand_in_zwoasi(monkeypatch: pytest.MonkeyPatch) -> _StandInZwoasiModule:
    """Install stand-in zwoasi module in sys.modules."""
    stand_in = _StandInZwoasiModule()
    mod = types.ModuleType("zwoasi")
    for k, v in stand_in.__dict__.items():
        setattr(mod, k, v)
    monkeypatch.setitem(sys.modules, "zwoasi", mod)
    return stand_in


def test_open_passes_the_explicit_dll_path(stand_in_zwoasi: _StandInZwoasiModule) -> None:
    cam = ZwoCamera("C:/x/ASICamera2.dll")
    assert isinstance(cam, CaptureCamera)
    cam.open(0)

    assert stand_in_zwoasi.init_calls == ["C:/x/ASICamera2.dll"]
    assert stand_in_zwoasi.call_sequence == ["init:C:/x/ASICamera2.dll", "Camera:0"]


@pytest.mark.parametrize(
    ("bayer_int", "expected_pattern"),
    [
        (0, "RG"),
        (1, "BG"),
        (2, "GR"),
        (3, "GB"),
    ],
)
def test_info_and_bayer_mapping(
    stand_in_zwoasi: _StandInZwoasiModule,
    bayer_int: int,
    expected_pattern: str,
) -> None:
    stand_in_zwoasi.bayer_pattern_to_report = bayer_int
    cam = ZwoCamera("C:/x/ASICamera2.dll")
    cam.open(0)

    info = cam.info()
    assert info.bayer_pattern == expected_pattern
    assert info.sensor_width == 3840
    assert info.sensor_height == 2160
    assert info.model == "ZWO ASI585MC"
    assert info.serial == "TEST_SERIAL_585"
    assert info.sdk_version == "0.2.0"

    controls = cam.controls()
    assert set(controls.keys()) == {"Exposure", "Gain", "Offset", "BandWidth"}
    assert controls["Exposure"] == {"min": 32, "max": 2_000_000_000, "default": 20_000}
    assert controls["Gain"] == {"min": 0, "max": 600, "default": 100}
    assert controls["Offset"] == {"min": 0, "max": 100, "default": 10}
    assert controls["BandWidth"] == {"min": 40, "max": 100, "default": 50}


def test_apply_never_switches_on_auto_and_never_asks_for_rgb(
    stand_in_zwoasi: _StandInZwoasiModule,
) -> None:
    cam = ZwoCamera("C:/x/ASICamera2.dll")
    cam.open(0)
    spy = stand_in_zwoasi.current_camera
    assert spy is not None

    # Apply 1: bit depth 16
    settings16 = CameraSettings(
        exposure_us=30000,
        gain=150,
        bit_depth=16,
        roi=(8, 16, 1280, 720),
        offset=0,
        usb_bandwidth=0,
        high_speed_mode=0,
    )
    applied16 = cam.apply(settings16)
    assert applied16.offset == 10
    assert applied16.usb_bandwidth == 50
    assert applied16.high_speed_mode == 0

    # Apply 2: bit depth 8
    settings8 = CameraSettings(
        exposure_us=15000,
        gain=200,
        bit_depth=8,
        roi=(0, 0, 1920, 1080),
        offset=0,
        usb_bandwidth=0,
        high_speed_mode=0,
    )
    applied8 = cam.apply(settings8)
    assert applied8.offset == 10
    assert applied8.usb_bandwidth == 50
    assert applied8.high_speed_mode == 0

    # Check set_control_calls:
    # All exposure and gain calls have auto=False
    # HighSpeedMode is set to 0
    # Offset (5) and BandWidth (6) are never set
    for ctrl_type, val, auto in spy.set_control_calls:
        if ctrl_type in (stand_in_zwoasi.ASI_EXPOSURE, stand_in_zwoasi.ASI_GAIN):
            assert auto is False
        if ctrl_type == stand_in_zwoasi.ASI_HIGH_SPEED_MODE:
            assert val == 0
        assert ctrl_type not in (
            stand_in_zwoasi.ASI_OFFSET,
            stand_in_zwoasi.ASI_BANDWIDTHOVERLOAD,
        )

    # Check set_roi_calls:
    assert len(spy.set_roi_calls) == 2
    # Only ASI_IMG_RAW16 then ASI_IMG_RAW8 ever passed
    assert spy.set_roi_calls[0]["image_type"] == stand_in_zwoasi.ASI_IMG_RAW16
    assert spy.set_roi_calls[0]["bins"] == 1
    assert spy.set_roi_calls[0]["start_x"] == 8
    assert spy.set_roi_calls[0]["start_y"] == 16
    assert spy.set_roi_calls[0]["width"] == 1280
    assert spy.set_roi_calls[0]["height"] == 720

    assert spy.set_roi_calls[1]["image_type"] == stand_in_zwoasi.ASI_IMG_RAW8
    assert spy.set_roi_calls[1]["bins"] == 1
    assert spy.set_roi_calls[1]["start_x"] == 0
    assert spy.set_roi_calls[1]["start_y"] == 0
    assert spy.set_roi_calls[1]["width"] == 1920
    assert spy.set_roi_calls[1]["height"] == 1080


def test_timeout_is_none_and_other_errors_are_camera_lost(
    stand_in_zwoasi: _StandInZwoasiModule,
) -> None:
    cam = ZwoCamera("C:/x/ASICamera2.dll")
    cam.open(0)
    spy = stand_in_zwoasi.current_camera
    assert spy is not None

    # Timeout error (code 11) returns None
    spy.raise_on_capture = stand_in_zwoasi.ZWO_IOError("Timeout", 11)
    frame = cam.get_frame(timeout_ms=50)
    assert frame is None

    # Another ZWO_IOError raises CameraLost
    spy.raise_on_capture = stand_in_zwoasi.ZWO_IOError("Camera removed", 5)
    with pytest.raises(CameraLost, match="Camera removed"):
        cam.get_frame(timeout_ms=50)

    # A general ZWO_Error raises CameraLost
    spy.raise_on_capture = stand_in_zwoasi.ZWO_Error("General ASI failure")
    with pytest.raises(CameraLost, match="General ASI failure"):
        cam.get_frame(timeout_ms=50)

    # Normal return passes through exact array object
    sample_array = np.zeros((32, 32), dtype=np.uint16)
    spy.raise_on_capture = None
    spy.return_frame = sample_array
    result = cam.get_frame(timeout_ms=50)
    assert result is sample_array


def test_temperature_and_dropped_frames(stand_in_zwoasi: _StandInZwoasiModule) -> None:
    cam = ZwoCamera("C:/x/ASICamera2.dll")
    cam.open(0)
    spy = stand_in_zwoasi.current_camera
    assert spy is not None

    spy.temp_raw = 312  # 31.2 C
    spy.dropped_count = 7
    assert cam.temperature_c() == 31.2
    assert cam.dropped_frames() == 7

    cam.start_stream()
    assert spy.start_video_calls == 1
    cam.stop_stream()
    assert spy.stop_video_calls == 1
    cam.close()
    assert spy.close_calls == 1


def test_missing_dll_is_fatal(tmp_path: Path) -> None:
    missing_dll = tmp_path / "missing_ASICamera2.dll"
    child = CaptureChild(
        cwd=tmp_path,
        extra_args=["--camera", "zwo", "--sdk-dll", str(missing_dll)],
    )
    fatal_msg = child.wait_for("fatal")
    assert fatal_msg["message"] == f"ZWO SDK library not found: {missing_dll}"

    assert child.raw_stdout_lines
    first_stdout = capture_protocol.decode(child.raw_stdout_lines[0])
    assert first_stdout["type"] == "fatal"
    assert first_stdout["message"] == f"ZWO SDK library not found: {missing_dll}"

    exit_code = child.wait_exit()
    assert exit_code == 1


def test_camera_zwo_requires_sdk_dll(tmp_path: Path) -> None:
    child = CaptureChild(
        cwd=tmp_path,
        extra_args=["--camera", "zwo"],
    )
    exit_code = child.wait_exit()
    assert exit_code == 2
    assert any("--sdk-dll is required when --camera zwo" in line for line in child.stderr_lines)


def test_init_exception_is_fatal(tmp_path: Path) -> None:
    # A dummy DLL exists, but init fails
    dummy_dll = tmp_path / "dummy.dll"
    dummy_dll.write_bytes(b"dummy")

    # In a subprocess, running with an invalid DLL raises an exception from init
    child = CaptureChild(
        cwd=tmp_path,
        extra_args=["--camera", "zwo", "--sdk-dll", str(dummy_dll)],
    )
    fatal_msg = child.wait_for("fatal")
    assert fatal_msg["type"] == "fatal"
    assert fatal_msg["message"] != ""
    exit_code = child.wait_exit()
    assert exit_code == 1


def test_not_open_raises_runtime_error() -> None:
    cam = ZwoCamera("C:/x/ASICamera2.dll")
    with pytest.raises(RuntimeError, match="Camera is not open"):
        cam.info()
    with pytest.raises(RuntimeError, match="Camera is not open"):
        cam.controls()
    with pytest.raises(RuntimeError, match="Camera is not open"):
        cam.start_stream()
    with pytest.raises(RuntimeError, match="Camera is not open"):
        cam.get_frame(100)
    with pytest.raises(RuntimeError, match="Camera is not open"):
        cam.dropped_frames()
    with pytest.raises(RuntimeError, match="Camera is not open"):
        cam.temperature_c()


def test_apply_invalid_bit_depth(stand_in_zwoasi: _StandInZwoasiModule) -> None:
    cam = ZwoCamera("C:/x/ASICamera2.dll")
    cam.open(0)
    bad_settings = CameraSettings(
        exposure_us=20000,
        gain=100,
        bit_depth=12,
        roi=(0, 0, 1920, 1080),
        offset=0,
        usb_bandwidth=50,
        high_speed_mode=0,
    )
    with pytest.raises(ValueError, match="Unsupported bit depth: 12"):
        cam.apply(bad_settings)

