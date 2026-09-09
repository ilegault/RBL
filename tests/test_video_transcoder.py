"""
Tests for video_transcoder — ffmpeg-absent path, command construction,
failure handling.
"""
import os

import pytest
from PySide6.QtCore import Qt as _Qt

from rbl.services.video_transcoder import TranscodeQueue, find_ffmpeg

_DIRECT = _Qt.ConnectionType.DirectConnection


# ---- find_ffmpeg -----------------------------------------------------------

def test_find_ffmpeg_returns_none_when_nothing_found(monkeypatch):
    """No binary, no PATH entry → None, nothing raises."""
    monkeypatch.setattr("shutil.which", lambda _: None)
    monkeypatch.setattr("os.path.isfile", lambda _: False)
    result = find_ffmpeg()
    assert result is None




# ---- TranscodeQueue — absent ffmpeg ----------------------------------------

def test_queue_emits_failed_when_ffmpeg_absent(tmp_path, qapp):
    """With ffmpeg absent, queue must emit finished_one(ok=False)."""
    avi = os.path.join(str(tmp_path), "video_000.avi")
    open(avi, "wb").close()

    results = []
    tq = TranscodeQueue(None)
    tq.finished_one.connect(lambda a, m, ok, msg: results.append((ok, msg)))
    tq.enqueue(avi)
    tq.enqueue(None)
    tq.run()

    assert results, "no finished_one emitted"
    ok, msg = results[0]
    assert not ok
    assert "not found" in msg.lower()


# ---- TranscodeQueue — command construction ---------------------------------

def test_command_contains_required_flags(monkeypatch, tmp_path, qapp):
    """With a fake ffmpeg that records argv, check the flags."""
    captured = []

    def _fake_run(cmd, **kwargs):
        captured.append(cmd)
        class R:
            returncode = 0
            stderr = ""
        return R()

    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setattr("os.replace", lambda src, dst: None)

    avi = os.path.join(str(tmp_path), "video_000.avi")
    open(avi, "wb").close()

    tq = TranscodeQueue("/fake/ffmpeg", crf=18, preset="medium")
    tq.enqueue(avi)
    tq.enqueue(None)
    tq.run()

    assert captured, "subprocess.run was never called"
    argv = captured[0]
    assert "-c:v" in argv and "libx264" in argv
    assert "-crf" in argv and "18" in argv
    assert "-pix_fmt" in argv and "yuv420p" in argv
    assert "-movflags" in argv and "+faststart" in argv
    assert "-an" in argv


# ---- TranscodeQueue — failure handling -------------------------------------

def test_failed_transcode_leaves_avi_intact(monkeypatch, tmp_path, qapp):
    """A non-zero exit must not delete the .avi."""
    def _fake_run(cmd, **kwargs):
        class R:
            returncode = 1
            stderr = "some error"
        return R()

    monkeypatch.setattr("subprocess.run", _fake_run)

    avi = os.path.join(str(tmp_path), "video_000.avi")
    open(avi, "wb").write(b"data")

    results = []
    tq = TranscodeQueue("/fake/ffmpeg", crf=18, preset="medium")
    tq.finished_one.connect(lambda a, m, ok, msg: results.append((ok, msg)))
    tq.enqueue(avi)
    tq.enqueue(None)
    tq.run()

    assert os.path.exists(avi), ".avi was deleted on transcode failure"
    assert results and not results[0][0]


def test_part_file_renamed_on_success(monkeypatch, tmp_path, qapp):
    """On success, .mp4.part must be renamed to .mp4."""
    renamed = []

    def _fake_run(cmd, **kwargs):
        class R:
            returncode = 0
            stderr = ""
        return R()

    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setattr("os.replace", lambda src, dst: renamed.append((src, dst)))

    avi = os.path.join(str(tmp_path), "video_000.avi")
    open(avi, "wb").close()

    tq = TranscodeQueue("/fake/ffmpeg", crf=18, preset="medium")
    tq.enqueue(avi)
    tq.enqueue(None)
    tq.run()

    assert renamed, "os.replace never called — .part not renamed"
    src, dst = renamed[0]
    assert src.endswith(".mp4.part")
    assert dst.endswith(".mp4") and not dst.endswith(".part")


# ---- conftest fixture needed -----------------------------------------------

@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])
