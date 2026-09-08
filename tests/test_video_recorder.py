"""
Tests for VideoRecorder — decimation, drift, segmentation, frames.csv.
Uses a monkeypatched cv2.VideoWriter so no real codec is needed.
"""
import csv
import os
import time

import pytest

from PySide6.QtWidgets import QApplication

try:
    import numpy as np
    _NP_OK = True
except ImportError:
    _NP_OK = False


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _fake_frame():
    if _NP_OK:
        import numpy as np
        return np.zeros((4, 4, 3), dtype="uint8")
    return None


class _FakeWriter:
    """Minimal stub that records write() calls and fakes isOpened()."""
    def __init__(self):
        self.calls = 0
        self._released = False

    def isOpened(self):
        return not self._released

    def write(self, frame):
        self.calls += 1

    def release(self):
        self._released = True


def _patch_cv2(monkeypatch, writers: list):
    """Patch cv2.VideoWriter so each construction appends to writers list."""
    import rbl.services.video_recorder as mod

    call_idx = [0]

    class _VW:
        def __init__(self, path, *rest):
            self._path = path
            self._fps, self._size = rest[-2], rest[-1]
            self._w = _FakeWriter()
            writers.append(self._w)

        def isOpened(self):
            return self._w.isOpened()

        def write(self, frame):
            self._w.write(frame)

        def release(self):
            self._w.release()

        def set(self, prop, value):
            pass

    class _FourCC:
        def __call__(self, *args):
            return 0

    class _FakeCv2:
        VideoWriter = _VW
        VideoWriter_fourcc = _FourCC()
        CAP_OPENCV_MJPEG = 1900
        VIDEOWRITER_PROP_QUALITY = 1

    monkeypatch.setattr(mod, "_CV2_OK", True)
    monkeypatch.setattr(mod, "cv2", _FakeCv2(), raising=False)


def _make_recorder(tmp_path, *, record_fps=2, seg_secs=3600,
                   seg_max_bytes=2**30, monkeypatch=None):
    from rbl.services.video_recorder import VideoRecorder
    writers = []
    if monkeypatch:
        _patch_cv2(monkeypatch, writers)
    rec = VideoRecorder(
        str(tmp_path),
        width=4, height=4,
        record_fps=record_fps,
        segment_seconds=seg_secs,
        segment_max_bytes=seg_max_bytes,
    )
    return rec, writers


def _offer_frames(rec, n_frames, total_s, wall_fn=None):
    """Offer n_frames evenly spaced over total_s seconds."""
    interval = total_s / n_frames
    for i in range(n_frames):
        t = i * interval
        wall = wall_fn(t) if wall_fn else f"2026-01-01T00:00:{t:.3f}Z"
        rec.offer_frame(_fake_frame(), t, wall)


def test_fps_decimation(tmp_path, monkeypatch):
    """300 frames over 10 s at record_fps=2 → 20 ± 1 frames written."""
    rec, writers = _make_recorder(tmp_path, record_fps=2, monkeypatch=monkeypatch)
    assert rec.start()
    _offer_frames(rec, 300, 10.0)
    rec.stop()
    total = sum(w.calls for w in writers)
    assert 19 <= total <= 21, f"expected ~20 frames written, got {total}"


def test_no_drift_over_long_run(tmp_path, monkeypatch):
    """3600 frames over 3600 s at record_fps=1 → 3600 ± 2 frames written."""
    rec, writers = _make_recorder(tmp_path, record_fps=1, seg_secs=7200,
                                  monkeypatch=monkeypatch)
    assert rec.start()
    # Simulate at 1 fps: offer one frame per second
    for i in range(3600):
        rec.offer_frame(_fake_frame(), float(i), f"t={i}")
    rec.stop()
    total = sum(w.calls for w in writers)
    assert 3598 <= total <= 3602, f"expected ~3600 frames written, got {total}"


def test_segment_roll_on_time(tmp_path, monkeypatch):
    """Segment rolls at segment_seconds; each roll emits segment_closed."""
    closed = []
    rec, writers = _make_recorder(tmp_path, record_fps=30, seg_secs=10,
                                  monkeypatch=monkeypatch)
    rec.segment_closed.connect(lambda path, idx: closed.append(idx))
    assert rec.start()
    # Offer 900 frames over 30 s → should roll 2 or 3 times
    _offer_frames(rec, 900, 30.0)
    rec.stop()
    assert len(closed) >= 2, f"expected at least 2 segment rolls, got {len(closed)}"


def test_frames_csv_contiguous(tmp_path, monkeypatch):
    """frame_index must be contiguous from 0; segment_frame resets at each roll."""
    rec, writers = _make_recorder(tmp_path, record_fps=5, seg_secs=5,
                                  monkeypatch=monkeypatch)
    assert rec.start()
    _offer_frames(rec, 100, 20.0)
    rec.stop()

    rows = list(csv.DictReader(
        open(os.path.join(str(tmp_path), "frames.csv"), encoding="utf-8")))
    indices = [int(r["frame_index"]) for r in rows]
    assert indices == list(range(len(indices))), "frame_index gaps or duplicates"

    # segment_frame resets to 0 at each new segment
    prev_seg = None
    for r in rows:
        seg = int(r["segment"])
        sf  = int(r["segment_frame"])
        if seg != prev_seg:
            assert sf == 0, f"segment_frame did not reset at segment {seg}"
            prev_seg = seg


def test_size_cap_roll(tmp_path, monkeypatch):
    """Roll fires when fake file size exceeds segment_max_bytes."""
    closed = []
    rec, writers = _make_recorder(tmp_path, record_fps=30, seg_secs=9999,
                                  seg_max_bytes=1, monkeypatch=monkeypatch)
    rec.segment_closed.connect(lambda path, idx: closed.append(idx))
    assert rec.start()
    # Create fake AVI file so getsize() returns something > 1 byte
    avi = os.path.join(str(tmp_path), "video_000.avi")
    with open(avi, "wb") as f:
        f.write(b"x" * 10)
    # Force enough frames to trigger _SIZE_CHECK_INTERVAL
    for i in range(110):
        rec.offer_frame(_fake_frame(), float(i) / 30, "t")
    rec.stop()
    assert len(closed) >= 1, "size-cap roll never fired"
