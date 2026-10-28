"""
capture_client.py
The application's one owner of the Capture Process (ZWO camera child).

WHY THIS EXISTS
---------------
ADR 0008: the ZWO camera is driven by a separate process so that USB frame
ingestion, preview stretching and disk writes can never stall the GUI or the
steerer motors. Something in the GUI process must still start that child, speak
the line protocol to it, and turn its output into Qt signals. That is this
class, and nothing else may own the child (invariant 1: one instrument, one
owner).

* The GUI never touches a full frame. The child sends a downscaled JPEG
  preview; this class decodes it into a ``QImage`` and nothing larger crosses
  the pipe.
* The pipe is the contract. Every line is validated by
  ``rbl.hardware.capture_protocol`` in both directions; a first line that is
  not a compatible ``hello`` makes us send ``shutdown`` and never open the
  camera.
* A heartbeat ``QTimer`` runs while the child does. If the application dies the
  heartbeat stops and the child shuts itself down.
* Refusals (``error`` replies) are reported on ``error`` and ``reply`` but never
  end anything by themselves; only the child's events or its exit do.
* ``capture_command_line`` and ``capture_child_env`` are pure so the frozen and
  source layouts are testable on one machine. CI never pip-installs ``rbl``
  (it runs from ``src`` via ``pytest.ini``), so when not frozen the child is
  given the ``rbl`` package's parent on ``PYTHONPATH``; in development this also
  makes the child independent of the working directory.
* The ``beamline`` command carries the cup position label so every frame log
  row says whether the beam could have reached the camera (ADR 0003). It is
  sent only when the label changes, and once right after each ``open``.
"""
from __future__ import annotations

import base64
import binascii
import logging
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Optional

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal
from PySide6.QtGui import QImage

import rbl
from rbl.config import paths
from rbl.config.capture_config import (
    CAPTURE_HEARTBEAT_INTERVAL_S,
    CAPTURE_SHUTDOWN_WAIT_S,
)
from rbl.hardware import capture_protocol as proto
from rbl.hardware.capture_protocol import cup_position_label
from rbl.snapshots import CupActuationState

_log = logging.getLogger("rbl.capture")

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ALUMINA_UNKNOWN = ""


def capture_command_line(frozen: bool, executable: str, args: list[str]) -> list[str]:
    """The child's argv. Frozen, the exe itself; otherwise ``python -m rbl.main``."""
    if frozen:
        return [executable, "--capture-process", *args]
    return [executable, "-m", "rbl.main", "--capture-process", *args]


def capture_child_env(
    frozen: bool, rbl_parent: str, env: Mapping[str, str]
) -> dict[str, str]:
    """A copy of ``env``; unless frozen, ``rbl_parent`` leads ``PYTHONPATH``."""
    out = dict(env)
    if not frozen:
        existing = out.get("PYTHONPATH")
        out["PYTHONPATH"] = (
            rbl_parent + os.pathsep + existing if existing else rbl_parent
        )
    return out


class CaptureClient(QObject):
    hello = Signal(dict)
    opened = Signal(dict)
    closed = Signal()
    status = Signal(dict)
    preview = Signal(QImage, dict)
    event = Signal(str, str)
    error = Signal(str)
    exited = Signal(int)
    reply = Signal(int, dict)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._proc: Optional[QProcess] = None
        self._buf = bytearray()
        self._next_id = 1
        self._open = False
        self._got_hello = False
        self._shutting_down = False
        self._pending: dict[int, str] = {}
        self._last_label: Optional[str] = None
        self._cup_label = "UNKNOWN"
        self._heartbeat = QTimer(self)
        self._heartbeat.setInterval(int(CAPTURE_HEARTBEAT_INTERVAL_S * 1000))
        self._heartbeat.timeout.connect(self._send_heartbeat)

    # ----------------------------------------------------------- lifecycle
    def start(
        self, camera: str = "zwo", heartbeat_timeout_s: float | None = None
    ) -> None:
        if self.is_running():
            return
        args = ["--camera", camera]
        if camera == "zwo":
            frozen = bool(getattr(sys, "frozen", False))
            dll = paths.zwo_sdk_dll_path(
                frozen, _bundle_dir(), _REPO_ROOT, os.environ
            )
            if not dll.is_file():
                self.error.emit(f"ZWO SDK library not found: {dll}")
                return
            args += ["--sdk-dll", str(dll)]
        if heartbeat_timeout_s is not None:
            args += ["--heartbeat-timeout-s", str(heartbeat_timeout_s)]
        frozen = bool(getattr(sys, "frozen", False))
        argv = capture_command_line(frozen, sys.executable, args)
        rbl_parent = str(Path(rbl.__file__).resolve().parent.parent)
        env = capture_child_env(frozen, rbl_parent, os.environ)
        qenv = QProcessEnvironment()
        for key, value in env.items():
            qenv.insert(key, value)

        self._buf.clear()
        self._open = False
        self._got_hello = False
        self._shutting_down = False
        self._pending.clear()
        self._last_label = None
        self._cup_label = "UNKNOWN"
        proc = QProcess(self)
        proc.setProcessEnvironment(qenv)
        proc.readyReadStandardOutput.connect(self._on_stdout)
        proc.readyReadStandardError.connect(self._on_stderr)
        proc.finished.connect(self._on_finished)
        proc.errorOccurred.connect(self._on_process_error)
        self._proc = proc
        proc.start(argv[0], argv[1:])
        self._heartbeat.start()

    def is_running(self) -> bool:
        return (
            self._proc is not None
            and self._proc.state() != QProcess.ProcessState.NotRunning
        )

    def is_open(self) -> bool:
        return self._open and self.is_running()

    def shutdown(self) -> None:
        self._heartbeat.stop()
        proc = self._proc
        if proc is None or proc.state() == QProcess.ProcessState.NotRunning:
            return
        self._shutting_down = True
        self._write(proto.ShutdownCmd())
        if not proc.waitForFinished(int(CAPTURE_SHUTDOWN_WAIT_S * 1000)):
            proc.kill()
            proc.waitForFinished(2000)

    # ------------------------------------------------------------ commands
    def open(self, camera_index: int = 0) -> int:
        return self._command(proto.OpenCmd(camera_index))

    def close(self) -> int:
        return self._command(proto.CloseCmd())

    def apply(self, **settings: Any) -> int:
        return self._command(proto.SetCmd(**settings))

    def set_preview(self, mode: str, brightness: float) -> int:
        return self._command(proto.PreviewCmd(mode, brightness))

    def start_recording(
        self,
        folder: str,
        session_t0_utc: str,
        first_segment_index: int = 0,
        first_frame_index: int = 0,
    ) -> int:
        return self._command(
            proto.StartRecordingCmd(
                folder, session_t0_utc, first_segment_index, first_frame_index
            )
        )

    def stop_recording(self) -> int:
        return self._command(proto.StopRecordingCmd())

    def photo(self, folder: str, burst: int = 1) -> int:
        return self._command(proto.PhotoCmd(folder, burst))

    def take_darks(self, folder: str, frames: int) -> int:
        return self._command(proto.TakeDarksCmd(folder, frames))

    def on_cup_actuation_state(self, state: CupActuationState) -> Optional[int]:
        """Send ``beamline`` only when the cup label changed; the id if sent."""
        label = cup_position_label(state.connected, state.stale, state.confirmed)
        self._cup_label = label
        if label == self._last_label or not self.is_open():
            return None
        return self._send_beamline()

    def _send_beamline(self) -> int:
        self._last_label = self._cup_label
        return self._command(proto.BeamlineCmd(self._cup_label, _ALUMINA_UNKNOWN))

    # ------------------------------------------------------------- plumbing
    def _command(self, cmd: Any) -> int:
        if not self.is_running():
            self.error.emit("Capture Process is not running")
            return -1
        return self._write(cmd)

    def _write(self, cmd: Any) -> int:
        assert self._proc is not None
        cmd_id = self._next_id
        self._next_id += 1
        msg = cmd.to_message(cmd_id)
        try:
            data = proto.encode(msg)
        except proto.ProtocolError as exc:
            self.error.emit(str(exc))
            return -1
        self._pending[cmd_id] = msg["type"]
        self._proc.write(data)
        return cmd_id

    def _send_heartbeat(self) -> None:
        if self.is_running() and not self._shutting_down:
            self._write(proto.HeartbeatCmd())

    def _on_stderr(self) -> None:
        assert self._proc is not None
        text = bytes(self._proc.readAllStandardError().data()).decode(
            "utf-8", errors="replace"
        )
        for line in text.splitlines():
            if line.strip():
                _log.info("%s", line)

    def _on_stdout(self) -> None:
        assert self._proc is not None
        self._buf.extend(bytes(self._proc.readAllStandardOutput().data()))
        while b"\n" in self._buf:
            pos = self._buf.index(b"\n")
            line = bytes(self._buf[: pos + 1])
            del self._buf[: pos + 1]
            if line.strip():
                self._handle_line(line)

    def _handle_line(self, line: bytes) -> None:
        try:
            msg = proto.decode(line)
            if not self._got_hello:
                proto.check_hello(msg)
        except proto.ProtocolError as exc:
            if not self._got_hello and self.is_running():
                self._shutting_down = True
                self._write(proto.ShutdownCmd())
            self.error.emit(str(exc))
            return
        mtype = msg["type"]
        if mtype == "hello":
            self._got_hello = True
            self.hello.emit(msg)
        elif mtype in ("ok", "error"):
            self._handle_reply(msg)
        elif mtype == "status":
            self.status.emit(msg)
        elif mtype == "preview":
            self._handle_preview(msg)
        elif mtype == "event":
            if msg["event"] == "camera_lost":
                self._open = False
            self.event.emit(msg["event"], msg["detail"])
        elif mtype == "fatal":
            self.error.emit(msg["message"])

    def _handle_reply(self, msg: dict[str, Any]) -> None:
        reply_to = msg["reply_to"]
        sent = self._pending.pop(reply_to, None)
        if msg["type"] == "error":
            self.error.emit(f"{msg['code']}: {msg['message']}")
            self.reply.emit(reply_to, msg)
            return
        self.reply.emit(reply_to, msg)
        if sent == "open":
            self._open = True
            self.opened.emit(msg)
            self._send_beamline()
        elif sent == "close":
            self._open = False
            self.closed.emit()

    def _handle_preview(self, msg: dict[str, Any]) -> None:
        fields = {k: v for k, v in msg.items() if k != "jpeg_b64"}
        try:
            data = base64.b64decode(msg["jpeg_b64"], validate=True)
        except (binascii.Error, ValueError) as exc:
            self.error.emit(str(exc))
            return
        image = QImage.fromData(data)
        if image.isNull():
            self.error.emit("preview JPEG could not be decoded")
            return
        self.preview.emit(image, fields)

    def _on_process_error(self, err: QProcess.ProcessError) -> None:
        if err == QProcess.ProcessError.FailedToStart:
            self._heartbeat.stop()
            self.error.emit("Capture Process failed to start")

    def _on_finished(self, code: int, _status: QProcess.ExitStatus) -> None:
        self._heartbeat.stop()
        self._open = False
        self.exited.emit(code)
        if not self._shutting_down:
            self.error.emit(f"Capture Process exited unexpectedly ({code})")


def _bundle_dir() -> Path | None:
    meipass = getattr(sys, "_MEIPASS", None)
    return Path(meipass) if meipass else None
