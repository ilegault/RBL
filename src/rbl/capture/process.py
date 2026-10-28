"""
process.py
The Capture Process: standalone child process owning the ZWO camera.

WHY THIS EXISTS
---------------
ADR 0008 requires that the ZWO camera be driven by a separate process (launched as
RBL.exe with --capture-process) so that USB frame ingestion, preview stretching,
and disk writes never stall the GUI or steerer motor control.
This process contains no Qt dependencies and communicates with the parent GUI
process via UTF-8 JSON lines on stdin and stdout according to capture_protocol.
"""
from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import queue
import shutil
import sys
import threading
import time
from dataclasses import replace
from typing import Any, Callable

import cv2
import numpy as np

from rbl.capture.camera import (
    CameraInfo,
    CameraLost,
    CameraSettings,
    CaptureCamera,
    FakeCamera,
)
from rbl.config.capture_config import (
    CAPTURE_CAMERA_LOST_TIMEOUTS,
    CAPTURE_DISK_STOP_BYTES,
    CAPTURE_FRAME_TIMEOUT_MIN_MS,
    CAPTURE_HEARTBEAT_TIMEOUT_S,
    CAPTURE_KEEP_ONE_IN_N_DEFAULT,
    CAPTURE_PREVIEW_JPEG_QUALITY,
    CAPTURE_PREVIEW_MAX_FPS,
    CAPTURE_PREVIEW_MAX_WIDTH_PX,
    CAPTURE_PROTOCOL_VERSION,
    CAPTURE_STATUS_INTERVAL_S,
    PREVIEW_BRIGHTNESS_DEFAULT,
    PREVIEW_MODE_DEFAULT,
    ZWO_BIT_DEPTH_DEFAULT,
    ZWO_EXPOSURE_US_DEFAULT,
    ZWO_GAIN_DEFAULT,
)
from rbl.hardware import capture_protocol
from rbl.hardware.capture_protocol import (
    estimate_bytes_per_hour,
    snap_roi,
)
from rbl.hardware.preview_stretch import (
    debayer_for_display,
    downscale_for_display,
    stretch_preview,
)

_log = logging.getLogger("rbl.capture")


def build_arg_parser() -> argparse.ArgumentParser:
    """Construct command-line argument parser matching spec D1."""
    parser = argparse.ArgumentParser(
        description="RBL Capture Process: isolated owner of the ZWO camera."
    )
    parser.add_argument(
        "--camera",
        choices=["zwo", "fake"],
        default="zwo",
        help="Which camera implementation to use (default: zwo)",
    )
    parser.add_argument(
        "--sdk-dll",
        type=str,
        default=None,
        help="Absolute path to ASICamera2.dll (required when --camera zwo)",
    )
    parser.add_argument(
        "--disk-stop-bytes",
        type=int,
        default=CAPTURE_DISK_STOP_BYTES,
        help="Free disk bytes threshold that halts recording",
    )
    parser.add_argument(
        "--heartbeat-timeout-s",
        type=float,
        default=CAPTURE_HEARTBEAT_TIMEOUT_S,
        help="Seconds without a heartbeat before automatic clean shutdown",
    )
    parser.add_argument(
        "--fake-fail-after",
        type=int,
        default=None,
        help="Fake camera only: raise CameraLost after N frames",
    )
    return parser


class CaptureProcess:
    """Orchestrates camera acquisition, command dispatch, and notifications."""

    def __init__(
        self,
        send: Callable[[dict[str, Any]], None],
        camera: CaptureCamera,
        disk_free: Callable[[str], int] = lambda p: shutil.disk_usage(p).free,
        monotonic: Callable[[], float] = time.monotonic,
        heartbeat_timeout_s: float = CAPTURE_HEARTBEAT_TIMEOUT_S,
        disk_stop_bytes: int = CAPTURE_DISK_STOP_BYTES,
    ) -> None:
        self._send = send
        self._camera = camera
        self._disk_free = disk_free
        self._monotonic = monotonic
        self._heartbeat_timeout_s = heartbeat_timeout_s
        self._disk_stop_bytes = disk_stop_bytes

        self._is_open = False
        self._camera_info: CameraInfo | None = None
        self._applied_settings: CameraSettings | None = None
        self._keep_one_in_n = CAPTURE_KEEP_ONE_IN_N_DEFAULT
        self._settings_generation = 0
        self._preview_mode = PREVIEW_MODE_DEFAULT
        self._preview_brightness = PREVIEW_BRIGHTNESS_DEFAULT

        self._last_heartbeat_time = self._monotonic()
        self._last_status_time = 0.0
        self._last_preview_time = 0.0

        self._cmd_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self._running = True
        self._exit_code = 0

        self._latest_frame: np.ndarray | None = None
        self._latest_frame_lock = threading.Lock()
        self._acquired_frames_total = 0
        self._last_fps_calc_time = self._monotonic()
        self._last_fps_calc_frames = 0

        self._acquisition_thread: threading.Thread | None = None
        self._acquisition_stop_event = threading.Event()

    def queue_command(self, cmd: dict[str, Any]) -> None:
        """Enqueue a decoded command dictionary from the input reader."""
        self._cmd_queue.put(cmd)

    def run(self) -> int:
        """Main event loop running on the primary process thread."""
        # 1. First message, always: hello
        cams: list[str]
        if isinstance(self._camera, FakeCamera):
            cams = ["Fake ASI"]
        else:
            try:
                cams = [self._camera.info().model]
            except Exception:
                cams = ["ZWO ASI"]

        self._send({
            "type": "hello",
            "protocol": CAPTURE_PROTOCOL_VERSION,
            "pid": os.getpid(),
            "cameras": cams,
        })

        min_preview_interval = 1.0 / CAPTURE_PREVIEW_MAX_FPS

        while self._running:
            now = self._monotonic()

            # Heartbeat check
            if now - self._last_heartbeat_time > self._heartbeat_timeout_s:
                _log.warning(
                    "Heartbeat timed out (%.1f s); shutting down cleanly",
                    self._heartbeat_timeout_s,
                )
                if self._is_open:
                    self._close_camera()
                self._running = False
                self._exit_code = 0
                break

            # Process command queue
            try:
                cmd = self._cmd_queue.get(timeout=0.02)
                self._last_heartbeat_time = self._monotonic()
                self._dispatch_command(cmd)
            except queue.Empty:
                pass

            if not self._running:
                break

            # Periodic status
            now = self._monotonic()
            if self._is_open and (now - self._last_status_time >= CAPTURE_STATUS_INTERVAL_S):
                self._last_status_time = now
                self._send_status(now)

            # Periodic preview
            if self._is_open and (now - self._last_preview_time >= min_preview_interval):
                with self._latest_frame_lock:
                    frame = self._latest_frame
                if frame is not None:
                    self._last_preview_time = now
                    self._send_preview(frame)

        return self._exit_code

    def _dispatch_command(self, cmd: dict[str, Any]) -> None:
        cmd_type = cmd.get("type")
        cmd_id = cmd.get("id", 0)

        if cmd_type == "open":
            self._handle_open(cmd_id, cmd.get("camera_index", 0))
        elif cmd_type == "close":
            self._handle_close(cmd_id)
        elif cmd_type == "set":
            self._handle_set(cmd_id, cmd)
        elif cmd_type == "preview":
            self._handle_preview(cmd_id, cmd)
        elif cmd_type == "heartbeat":
            # Heartbeat received; no reply
            pass
        elif cmd_type == "shutdown":
            self._handle_shutdown(cmd_id)
        elif cmd_type in ("start_recording", "stop_recording", "photo", "take_darks"):
            self._send({
                "type": "error",
                "reply_to": cmd_id,
                "code": "bad_request",
                "message": "not implemented",
            })
        elif cmd_type == "beamline":
            self._send({"type": "ok", "reply_to": cmd_id})
        else:
            self._send({
                "type": "error",
                "reply_to": cmd_id,
                "code": "bad_request",
                "message": f"unknown command {cmd_type!r}",
            })

    def _handle_open(self, cmd_id: int, camera_index: int) -> None:
        if self._is_open and self._camera_info is not None and self._applied_settings is not None:
            # A second open while open replies the same fields again
            self._send({
                "type": "ok",
                "reply_to": cmd_id,
                "model": self._camera_info.model,
                "serial": self._camera_info.serial,
                "sdk_version": self._camera_info.sdk_version,
                "sensor_width": self._camera_info.sensor_width,
                "sensor_height": self._camera_info.sensor_height,
                "bayer_pattern": self._camera_info.bayer_pattern,
                "controls": self._camera.controls(),
                "settings": self._format_settings(),
            })
            return

        try:
            self._camera.open(camera_index)
            self._camera_info = self._camera.info()
            controls = self._camera.controls()
            offset = controls.get("Offset", {}).get("default", 0)
            usb_bandwidth = controls.get("BandWidth", {}).get("default", 50)

            settings = CameraSettings(
                exposure_us=ZWO_EXPOSURE_US_DEFAULT,
                gain=ZWO_GAIN_DEFAULT,
                bit_depth=ZWO_BIT_DEPTH_DEFAULT,
                roi=(0, 0, self._camera_info.sensor_width, self._camera_info.sensor_height),
                offset=offset,
                usb_bandwidth=usb_bandwidth,
                high_speed_mode=0,
            )
            self._applied_settings = self._camera.apply(settings)
            self._keep_one_in_n = CAPTURE_KEEP_ONE_IN_N_DEFAULT
            self._is_open = True
            self._camera.start_stream()

            _log.info("camera opened: %s %s", self._camera_info.model, self._camera_info.serial)
            self._start_acquisition_thread()

            self._send({
                "type": "ok",
                "reply_to": cmd_id,
                "model": self._camera_info.model,
                "serial": self._camera_info.serial,
                "sdk_version": self._camera_info.sdk_version,
                "sensor_width": self._camera_info.sensor_width,
                "sensor_height": self._camera_info.sensor_height,
                "bayer_pattern": self._camera_info.bayer_pattern,
                "controls": controls,
                "settings": self._format_settings(),
            })
        except Exception as exc:
            _log.error("Failed to open camera: %s", exc)
            self._send({
                "type": "error",
                "reply_to": cmd_id,
                "code": "camera_error",
                "message": str(exc),
            })

    def _handle_close(self, cmd_id: int) -> None:
        if not self._is_open:
            self._send({
                "type": "error",
                "reply_to": cmd_id,
                "code": "not_open",
                "message": "camera not open",
            })
            return

        self._close_camera()
        self._send({"type": "ok", "reply_to": cmd_id})

    def _handle_set(self, cmd_id: int, cmd: dict[str, Any]) -> None:
        if not self._is_open or self._applied_settings is None or self._camera_info is None:
            self._send({
                "type": "error",
                "reply_to": cmd_id,
                "code": "not_open",
                "message": "camera not open",
            })
            return

        new_roi = self._applied_settings.roi
        if "roi" in cmd:
            r = cmd["roi"]
            new_roi = snap_roi(
                r["x"],
                r["y"],
                r["width"],
                r["height"],
                self._camera_info.sensor_width,
                self._camera_info.sensor_height,
            )

        new_exposure = cmd.get("exposure_us", self._applied_settings.exposure_us)
        new_gain = cmd.get("gain", self._applied_settings.gain)
        new_bit_depth = cmd.get("bit_depth", self._applied_settings.bit_depth)
        new_keep = cmd.get("keep_one_in_n", self._keep_one_in_n)

        changes: list[tuple[str, str]] = []
        old_set = self._applied_settings
        if new_exposure != old_set.exposure_us:
            changes.append(
                ("exposure_us", f"exposure_us: {old_set.exposure_us} -> {new_exposure}")
            )
        if new_gain != old_set.gain:
            changes.append(("gain", f"gain: {old_set.gain} -> {new_gain}"))
        if new_bit_depth != old_set.bit_depth:
            changes.append(
                ("bit_depth", f"bit_depth: {old_set.bit_depth} -> {new_bit_depth}")
            )
        if new_roi != old_set.roi:
            old_roi_str = (
                f"{old_set.roi[0]},{old_set.roi[1]},{old_set.roi[2]},{old_set.roi[3]}"
            )
            new_roi_str = f"{new_roi[0]},{new_roi[1]},{new_roi[2]},{new_roi[3]}"
            changes.append(("roi", f"roi: {old_roi_str} -> {new_roi_str}"))
        if new_keep != self._keep_one_in_n:
            changes.append(("keep_one_in_n", f"keep_one_in_n: {self._keep_one_in_n} -> {new_keep}"))

        if changes:
            self._stop_stream_for_settings()
            target_settings = replace(
                self._applied_settings,
                exposure_us=new_exposure,
                gain=new_gain,
                bit_depth=new_bit_depth,
                roi=new_roi,
            )
            self._applied_settings = self._camera.apply(target_settings)
            self._keep_one_in_n = new_keep
            self._settings_generation += 1
            self._restart_stream_after_settings()

        self._send({
            "type": "ok",
            "reply_to": cmd_id,
            "settings": self._format_settings(),
        })

        for _, detail in changes:
            self._send({"type": "event", "event": "settings_changed", "detail": detail})

    def _handle_preview(self, cmd_id: int, cmd: dict[str, Any]) -> None:
        self._preview_mode = cmd.get("mode", self._preview_mode)
        self._preview_brightness = cmd.get("brightness", self._preview_brightness)
        self._send({"type": "ok", "reply_to": cmd_id})

    def _handle_shutdown(self, cmd_id: int) -> None:
        self._send({"type": "ok", "reply_to": cmd_id})
        if self._is_open:
            self._close_camera()
        self._running = False
        self._exit_code = 0

    def _format_settings(self) -> dict[str, Any]:
        assert self._applied_settings is not None
        return {
            "exposure_us": self._applied_settings.exposure_us,
            "gain": self._applied_settings.gain,
            "bit_depth": self._applied_settings.bit_depth,
            "roi": {
                "x": self._applied_settings.roi[0],
                "y": self._applied_settings.roi[1],
                "width": self._applied_settings.roi[2],
                "height": self._applied_settings.roi[3],
            },
            "keep_one_in_n": self._keep_one_in_n,
            "offset": self._applied_settings.offset,
            "usb_bandwidth": self._applied_settings.usb_bandwidth,
            "high_speed_mode": self._applied_settings.high_speed_mode,
        }

    def _start_acquisition_thread(self) -> None:
        self._acquisition_stop_event.clear()
        self._acquisition_thread = threading.Thread(
            target=self._acquisition_loop,
            daemon=True,
            name="CaptureAcquisitionThread",
        )
        self._acquisition_thread.start()

    def _stop_stream_for_settings(self) -> None:
        self._acquisition_stop_event.set()
        if self._acquisition_thread and self._acquisition_thread.is_alive():
            self._acquisition_thread.join(timeout=2.0)
        self._camera.stop_stream()

    def _restart_stream_after_settings(self) -> None:
        self._camera.start_stream()
        self._start_acquisition_thread()

    def _close_camera(self) -> None:
        self._is_open = False
        self._acquisition_stop_event.set()
        if self._acquisition_thread and self._acquisition_thread.is_alive():
            self._acquisition_thread.join(timeout=2.0)
            self._acquisition_thread = None
        try:
            self._camera.stop_stream()
        except Exception:
            pass
        try:
            self._camera.close()
        except Exception:
            pass
        with self._latest_frame_lock:
            self._latest_frame = None

    def _handle_camera_lost(self, reason: str) -> None:
        _log.error("Camera lost: %s", reason)
        self._close_camera()
        self._send({"type": "event", "event": "camera_lost", "detail": reason})

    def _acquisition_loop(self) -> None:
        consecutive_timeouts = 0
        while not self._acquisition_stop_event.is_set():
            if not self._is_open:
                time.sleep(0.01)
                continue

            exposure_us = (
                self._applied_settings.exposure_us
                if self._applied_settings
                else ZWO_EXPOSURE_US_DEFAULT
            )
            timeout_ms = max(2 * (exposure_us // 1000) + 500, CAPTURE_FRAME_TIMEOUT_MIN_MS)

            try:
                frame = self._camera.get_frame(timeout_ms)
            except CameraLost as exc:
                self._handle_camera_lost(str(exc))
                break
            except Exception as exc:
                self._handle_camera_lost(str(exc))
                break

            if frame is not None:
                consecutive_timeouts = 0
                with self._latest_frame_lock:
                    self._latest_frame = frame
                self._acquired_frames_total += 1
            else:
                consecutive_timeouts += 1
                if consecutive_timeouts >= CAPTURE_CAMERA_LOST_TIMEOUTS:
                    self._handle_camera_lost("consecutive timeouts")
                    break

    def _send_status(self, now: float) -> None:
        assert self._applied_settings is not None
        elapsed = now - self._last_fps_calc_time
        if elapsed > 0:
            acquired_fps = (self._acquired_frames_total - self._last_fps_calc_frames) / elapsed
        else:
            acquired_fps = 0.0
        self._last_fps_calc_time = now
        self._last_fps_calc_frames = self._acquired_frames_total

        bytes_per_hour = estimate_bytes_per_hour(
            self._applied_settings.roi[2],
            self._applied_settings.roi[3],
            self._applied_settings.bit_depth,
            acquired_fps,
            self._keep_one_in_n,
        )
        try:
            free_bytes = self._disk_free(os.getcwd())
        except Exception:
            free_bytes = 0

        self._send({
            "type": "status",
            "acquired_fps": float(acquired_fps),
            "frames_written": 0,
            "frames_dropped_camera": self._camera.dropped_frames(),
            "frames_dropped_queue": 0,
            "recording": False,
            "segment": -1,
            "bytes_per_hour": float(bytes_per_hour),
            "disk_free_bytes": int(free_bytes),
            "sensor_temperature_c": self._camera.temperature_c(),
        })

    def _send_preview(self, frame: np.ndarray) -> None:
        assert self._applied_settings is not None
        assert self._camera_info is not None

        try:
            debayered = debayer_for_display(frame, self._camera_info.bayer_pattern)
            downscaled = downscale_for_display(debayered, CAPTURE_PREVIEW_MAX_WIDTH_PX)
            stretched = stretch_preview(
                downscaled,
                self._applied_settings.bit_depth,
                self._preview_mode,
                self._preview_brightness,
            )
            bgr = cv2.cvtColor(stretched, cv2.COLOR_RGB2BGR)
            success, buf = cv2.imencode(
                ".jpg",
                bgr,
                [int(cv2.IMWRITE_JPEG_QUALITY), CAPTURE_PREVIEW_JPEG_QUALITY],
            )
            if success:
                jpeg_b64 = base64.b64encode(buf.tobytes()).decode("ascii")
                h, w = stretched.shape[:2]
                self._send({
                    "type": "preview",
                    "width": w,
                    "height": h,
                    "roi": {
                        "x": self._applied_settings.roi[0],
                        "y": self._applied_settings.roi[1],
                        "width": self._applied_settings.roi[2],
                        "height": self._applied_settings.roi[3],
                    },
                    "stretch_mode": self._preview_mode,
                    "brightness": float(self._preview_brightness),
                    "jpeg_b64": jpeg_b64,
                })
        except Exception as exc:
            _log.error("Failed to generate preview: %s", exc)


def main(argv: list[str]) -> int:
    """Capture process entry point."""
    protocol_out = sys.stdout.buffer
    sys.stdout = sys.stderr

    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    args = build_arg_parser().parse_args(argv)

    send_lock = threading.Lock()

    def send(msg: dict[str, Any]) -> None:
        data = capture_protocol.encode(msg)
        with send_lock:
            protocol_out.write(data)
            protocol_out.flush()

    if args.camera == "zwo":
        send({"type": "fatal", "message": "ZWO camera support is not built yet"})
        return 1

    camera = FakeCamera(fail_after=args.fake_fail_after)

    process = CaptureProcess(
        send=send,
        camera=camera,
        heartbeat_timeout_s=args.heartbeat_timeout_s,
        disk_stop_bytes=args.disk_stop_bytes,
    )

    def stdin_reader() -> None:
        fd = sys.stdin.fileno()
        buf = bytearray()
        while True:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                chunk = b""
            if not chunk:
                process.queue_command({"type": "shutdown", "id": 0})
                break
            buf.extend(chunk)
            while b"\n" in buf:
                pos = buf.index(b"\n")
                line = bytes(buf[: pos + 1])
                del buf[: pos + 1]
                try:
                    msg = capture_protocol.decode(line)
                    process.queue_command(msg)
                except capture_protocol.ProtocolError as exc:
                    reply_to = -1
                    try:
                        raw_obj = json.loads(line.decode("utf-8", errors="replace"))
                        if (
                            isinstance(raw_obj, dict)
                            and isinstance(raw_obj.get("id"), int)
                            and not isinstance(raw_obj.get("id"), bool)
                        ):
                            reply_to = raw_obj["id"]
                    except Exception:
                        pass
                    send({
                        "type": "error",
                        "reply_to": reply_to,
                        "code": "bad_request",
                        "message": str(exc),
                    })

    reader_thread = threading.Thread(target=stdin_reader, daemon=True, name="CaptureStdinReader")
    reader_thread.start()

    return process.run()
