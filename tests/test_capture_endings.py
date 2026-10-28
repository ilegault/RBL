"""
In-process tests for how a Capture Process recording ends (ticket 67).

WHY THIS EXISTS
---------------
Low disk is awkward to provoke in a child process, so ``CaptureProcess`` takes
``send``, ``disk_free`` and ``monotonic`` as constructor arguments (set 6 requirement 6)
and this file drives it directly, with a list as ``send``.
"""
from __future__ import annotations

import itertools
import json
import threading
import time
from pathlib import Path

from rbl.capture.camera import FakeCamera
from rbl.capture.process import CaptureProcess


def test_low_disk_stops_a_running_recording(tmp_path: Path) -> None:
    sent: list[dict] = []
    calls = itertools.count()

    def disk_free(_path: str) -> int:
        return 10**12 if next(calls) < 3 else 0

    proc = CaptureProcess(
        send=sent.append, camera=FakeCamera(), disk_free=disk_free, disk_stop_bytes=10**9
    )
    t = threading.Thread(target=proc.run, daemon=True)
    t.start()
    try:
        proc.queue_command({"type": "open", "id": 1, "camera_index": 0})
        proc.queue_command({
            "type": "set", "id": 2, "exposure_us": 10000,
            "roi": {"x": 0, "y": 0, "width": 64, "height": 32},
        })
        proc.queue_command({
            "type": "start_recording", "id": 3, "folder": str(tmp_path),
            "session_t0_utc": "2026-01-01T00:00:00Z",
            "first_segment_index": 0, "first_frame_index": 0,
        })
        deadline = time.monotonic() + 10.0

        def events() -> set[str]:
            return {m["event"] for m in list(sent) if m.get("type") == "event"}

        while time.monotonic() < deadline and not {"low_disk", "recording_stopped"} <= events():
            time.sleep(0.05)
        assert {"low_disk", "recording_stopped"} <= events()
        rec = json.loads((tmp_path / "zwo" / "segment_000.json").read_text(encoding="utf-8"))
        assert rec["closed_reason"] == "low_disk"
        n = sum(1 for m in list(sent) if m.get("type") == "status")
        time.sleep(1.5)
        assert sum(1 for m in list(sent) if m.get("type") == "status") > n
    finally:
        proc.queue_command({"type": "shutdown", "id": 9})
        t.join(timeout=10.0)
