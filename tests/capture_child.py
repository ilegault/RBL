"""
Helper for tests running the Capture Process child in a separate process.
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from rbl.hardware import capture_protocol

REPO = Path(__file__).resolve().parent.parent


class CaptureChild:
    """Manages a running Capture Process child for testing."""

    def __init__(
        self,
        cwd: Path | str,
        extra_args: list[str] | None = None,
        python_args: list[str] | None = None,
        env_extra: dict[str, str] | None = None,
    ) -> None:
        self.cwd = Path(cwd)
        cmd = [
            sys.executable,
            *(python_args or []),
            "-m",
            "rbl.main",
            "--capture-process",
            "--camera",
            "fake",
            *(extra_args or []),
        ]
        env = {
            **os.environ,
            "PYTHONPATH": str(REPO / "src"),
            **(env_extra or {}),
        }
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(self.cwd),
            env=env,
        )
        self.raw_stdout_lines: list[bytes] = []
        self.stdout_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self.all_messages: list[dict[str, Any]] = []
        self.stderr_lines: list[str] = []
        self._next_id = 1
        self._lock = threading.Lock()

        self._stdout_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._stdout_thread.start()
        self._stderr_thread.start()

    def _read_stdout(self) -> None:
        assert self.proc.stdout is not None
        while True:
            line = self.proc.stdout.readline()
            if not line:
                break
            with self._lock:
                self.raw_stdout_lines.append(line)
            try:
                msg = capture_protocol.decode(line)
                with self._lock:
                    self.all_messages.append(msg)
                self.stdout_queue.put(msg)
            except Exception:
                pass

    def _read_stderr(self) -> None:
        assert self.proc.stderr is not None
        while True:
            line = self.proc.stderr.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip("\r\n")
            with self._lock:
                self.stderr_lines.append(text)

    def send(self, msg: dict[str, Any]) -> None:
        if self.proc.stdin is None or self.proc.stdin.closed:
            raise RuntimeError("child stdin is closed")
        data = capture_protocol.encode(msg)
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def request(self, msg: dict[str, Any], timeout: float = 30.0) -> dict[str, Any]:
        with self._lock:
            if "id" not in msg:
                msg["id"] = self._next_id
                self._next_id += 1
            cmd_id = msg["id"]
        self.send(msg)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = max(0.01, deadline - time.monotonic())
            try:
                reply = self.stdout_queue.get(timeout=remaining)
                if reply.get("reply_to") == cmd_id and reply.get("type") in ("ok", "error"):
                    return reply
            except queue.Empty:
                pass
        raise TimeoutError(f"timed out waiting for reply to command {cmd_id}")

    def wait_for(
        self,
        msg_type: str,
        timeout: float = 30.0,
        predicate: Callable[[dict[str, Any]], bool] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            for m in self.all_messages:
                if m.get("type") == msg_type and (predicate is None or predicate(m)):
                    return m
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = max(0.01, deadline - time.monotonic())
            try:
                msg = self.stdout_queue.get(timeout=remaining)
                if msg.get("type") == msg_type and (predicate is None or predicate(msg)):
                    return msg
            except queue.Empty:
                pass
        raise TimeoutError(f"timed out waiting for message of type {msg_type!r}")

    def close_stdin(self) -> None:
        if self.proc.stdin is not None and not self.proc.stdin.closed:
            self.proc.stdin.close()

    def kill(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
            try:
                self.proc.wait(timeout=5.0)
            except Exception:
                pass

    def wait_exit(self, timeout: float = 30.0) -> int:
        return self.proc.wait(timeout=timeout)
