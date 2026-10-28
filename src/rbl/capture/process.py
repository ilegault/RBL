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

RECORDING (ticket 66)
---------------------
The acquisition thread stamps each frame (arrival UTC from this process's own
perf_counter anchor, running acquired index, settings generation) and only ever
``put_nowait``s it on a bounded queue: a full queue drops and counts the frame,
because a stalled disk must never stall the camera. A writer thread owns the disk
(``rbl.capture.recording``). Any ``set`` bumps the generation, keep 1 in N included,
and the writer opens a new segment on a new generation, so no segment mixes
settings. A keep-1-in-N-only change does not restart the camera stream.
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
from datetime import datetime, timezone
from pathlib import Path
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
from rbl.capture.recording import (
    CUP_POSITION_UNKNOWN,
    RecordingRun,
    StampedFrame,
    parse_iso_utc,
    utc_from_anchor,
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
    CAPTURE_WRITE_QUEUE_FRAMES,
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

        # The process's own clock anchor (taken once, at start-up): per-frame UTC is
        # anchor + elapsed perf_counter, so a clock correction cannot reorder rows.
        self._anchor_perf = time.perf_counter()
        self._anchor_utc = datetime.now(timezone.utc)

        # (generation, keep_one_in_n, settings dict) is replaced as one tuple so the
        # acquisition thread always reads a consistent triple.
        self._stamp: tuple[int, int, dict[str, Any]] | None = None
        self._cup_position = CUP_POSITION_UNKNOWN
        self._alumina_position = ""

        # Recording: the acquisition thread only ever put_nowait()s; the writer thread
        # owns the disk. _rec_lock makes "flag cleared" and "nothing enqueued after the
        # stop sentinel" one fact.
        self._write_queue: queue.Queue[Any] = queue.Queue(maxsize=CAPTURE_WRITE_QUEUE_FRAMES)
        self._rec_lock = threading.Lock()
        self._recording = False
        self._accepting = False
        self._writer_thread: threading.Thread | None = None
        self._run: RecordingRun | None = None
        self._rec_folder = ""
        self._frames_dropped_queue = 0

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
        elif cmd_type == "start_recording":
            self._handle_start_recording(cmd_id, cmd)
        elif cmd_type == "stop_recording":
            self._handle_stop_recording(cmd_id)
        elif cmd_type in ("photo", "take_darks"):
            self._send({
                "type": "error",
                "reply_to": cmd_id,
                "code": "bad_request",
                "message": "not implemented",
            })
        elif cmd_type == "beamline":
            self._cup_position = cmd.get("cup_position", self._cup_position)
            self._alumina_position = cmd.get("alumina_position", self._alumina_position)
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
            self._acquired_frames_total = 0
            self._last_fps_calc_frames = 0
            self._stamp = (
                self._settings_generation,
                self._keep_one_in_n,
                self._format_settings(),
            )
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

        if self._recording:
            self._send({
                "type": "error",
                "reply_to": cmd_id,
                "code": "recording",
                "message": "stop the recording before closing the camera",
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
            camera_changed = any(name != "keep_one_in_n" for name, _ in changes)
            if camera_changed:
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
            # Any change, keep 1 in N included, starts a new generation: no segment
            # ever mixes settings, and the settings record carries keep_one_in_n.
            self._settings_generation += 1
            self._stamp = (
                self._settings_generation,
                self._keep_one_in_n,
                self._format_settings(),
            )
            if camera_changed:
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

    def _handle_start_recording(self, cmd_id: int, cmd: dict[str, Any]) -> None:
        if not self._is_open or self._camera_info is None:
            self._send({
                "type": "error",
                "reply_to": cmd_id,
                "code": "not_open",
                "message": "camera not open",
            })
            return
        if self._recording:
            self._send({
                "type": "error",
                "reply_to": cmd_id,
                "code": "already_recording",
                "message": "already recording",
            })
            return

        info = self._camera_info
        try:
            run = RecordingRun(
                Path(cmd["folder"]),
                session_t0_utc=parse_iso_utc(cmd["session_t0_utc"]),
                first_segment_index=cmd["first_segment_index"],
                first_frame_index=cmd["first_frame_index"],
                camera={
                    "model": info.model,
                    "serial": info.serial,
                    "sdk_version": info.sdk_version,
                    "bayer_pattern": info.bayer_pattern,
                },
                temperature_c=self._camera.temperature_c,
                counters=lambda: (self._camera.dropped_frames(), self._frames_dropped_queue),
                event=lambda name, detail: self._send(
                    {"type": "event", "event": name, "detail": detail}
                ),
                monotonic=self._monotonic,
            )
        except Exception as exc:
            _log.error("Failed to start recording: %s", exc)
            self._send({
                "type": "error",
                "reply_to": cmd_id,
                "code": "camera_error",
                "message": str(exc),
            })
            return

        self._run = run
        self._rec_folder = str(Path(cmd["folder"]))
        self._frames_dropped_queue = 0
        self._recording = True
        self._send({"type": "ok", "reply_to": cmd_id})
        self._send({"type": "event", "event": "recording_started", "detail": ""})
        self._writer_thread = threading.Thread(
            target=self._writer_loop, args=(run,), daemon=True, name="CaptureWriterThread"
        )
        self._writer_thread.start()
        with self._rec_lock:
            self._accepting = True

    def _handle_stop_recording(self, cmd_id: int) -> None:
        if not self._recording or self._run is None:
            self._send({
                "type": "error",
                "reply_to": cmd_id,
                "code": "not_recording",
                "message": "not recording",
            })
            return
        run = self._finish_recording("stop")
        self._send({
            "type": "ok",
            "reply_to": cmd_id,
            "next_segment_index": run.next_segment_index,
            "next_frame_index": run.next_frame_index,
            "segments": list(run.segments),
        })
        self._send({"type": "event", "event": "recording_stopped", "detail": "stop"})

    def _finish_recording(self, reason: str) -> RecordingRun:
        """Stop accepting frames, drain the queue, close the segment with ``reason``."""
        run = self._run
        assert run is not None
        with self._rec_lock:
            self._accepting = False
        self._write_queue.put(("stop", reason))
        if self._writer_thread is not None:
            self._writer_thread.join()
            self._writer_thread = None
        self._recording = False
        self._run = None
        return run

    def _writer_loop(self, run: RecordingRun) -> None:
        failed = False
        while True:
            item = self._write_queue.get()
            if isinstance(item, tuple):
                try:
                    run.close(item[1])
                except Exception as exc:
                    _log.error("Failed to close recording: %s", exc)
                return
            if failed:
                continue
            try:
                run.write(item)
            except Exception as exc:
                failed = True
                _log.error("Recording write failed: %s", exc)
                with self._rec_lock:
                    self._accepting = False
                self._send({
                    "type": "event",
                    "event": "recording_stopped",
                    "detail": f"write_error: {exc}",
                })

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
                arrived = utc_from_anchor(
                    self._anchor_utc, self._anchor_perf, time.perf_counter()
                )
                index = self._acquired_frames_total
                self._acquired_frames_total += 1
                with self._latest_frame_lock:
                    self._latest_frame = frame
                self._offer_to_writer(frame, arrived, index)
            else:
                consecutive_timeouts += 1
                if consecutive_timeouts >= CAPTURE_CAMERA_LOST_TIMEOUTS:
                    self._handle_camera_lost("consecutive timeouts")
                    break

    def _offer_to_writer(self, frame: np.ndarray, arrived: datetime, index: int) -> None:
        """Hand a frame to the writer thread; a full queue drops it, never blocks."""
        stamp = self._stamp
        if stamp is None:
            return
        generation, keep, settings = stamp
        if index % keep != 0:
            return
        item = StampedFrame(frame, arrived, index, generation, settings, self._cup_position)
        with self._rec_lock:
            if not self._accepting:
                return
            try:
                self._write_queue.put_nowait(item)
            except queue.Full:
                self._frames_dropped_queue += 1

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
        run = self._run
        try:
            free_bytes = self._disk_free(self._rec_folder if run is not None else os.getcwd())
        except Exception:
            free_bytes = 0

        self._send({
            "type": "status",
            "acquired_fps": float(acquired_fps),
            "frames_written": run.frames_written if run is not None else 0,
            "frames_dropped_camera": self._camera.dropped_frames(),
            "frames_dropped_queue": self._frames_dropped_queue,
            "recording": run is not None,
            "segment": run.current_segment if run is not None else -1,
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
