"""
Tests for RecordingPanel — constructs offscreen, button/checkbox states.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class _StubCamera(QObject):
    opened        = Signal(int, int, float)
    closed        = Signal()
    error         = Signal(str)
    frame_ready   = Signal(object, float)
    preview_ready = Signal(object)
    format_ready  = Signal(str)

    def is_open(self): return False
    def actual_size(self): return (0, 0)
    def latest_frame(self): return None
    _thread = None


class _StubRecorder(QObject):
    state_changed    = Signal()
    settings_changed = Signal()
    status           = Signal(str, str)
    session_started  = Signal(str)
    session_stopped  = Signal(str)
    photo_taken      = Signal(str)

    _recording       = False
    _csv_interval_s  = 5
    _record_fps      = 2
    _segment_seconds = 600
    _quality_label   = "Standard (CRF 18)"
    _master_codec    = "MJPEG q98 (default)"
    _video_enabled   = True

    def __init__(self, camera):
        super().__init__()
        self._camera = camera

    def is_recording(self):  return self._recording
    def set_csv_interval_s(self, v): pass
    def set_record_fps(self, v): pass
    def set_segment_seconds(self, v): pass
    def set_quality(self, label): pass
    def set_video_enabled(self, on): pass

    def start(self):
        self._recording = True
        self.state_changed.emit()

    def stop(self):
        self._recording = False
        self.state_changed.emit()
    def take_photo(self): return None
    def add_note(self, t): pass
    def open_session_folder(self): pass
    def state(self):
        return dict(recording=self._recording, session_id="",
                    folder="", elapsed_s=0, csv_rows=0, frames_written=0,
                    frames_dropped=0, segments_done=0, transcode_pending=0,
                    disk_free_bytes=0, video_active=False, ffmpeg_found=False)


@pytest.fixture
def panel(qapp):
    cam = _StubCamera()
    rec = _StubRecorder(cam)
    from rbl.gui.widgets.recording_panel import RecordingPanel
    p = RecordingPanel(rec)
    return p, rec, cam


def test_panel_constructs_without_error(panel):
    p, rec, cam = panel
    assert p is not None


def test_start_session_enabled_with_no_camera(panel):
    """START SESSION must be enabled even when the camera is closed."""
    p, rec, cam = panel
    assert p._btn_session.isEnabled()


def test_record_video_checkbox_disabled_with_camera_closed(panel):
    """'Record video' must be greyed out when camera is not open."""
    p, rec, cam = panel
    assert not p._chk_video.isEnabled()


def test_settings_disable_while_recording(panel):
    p, rec, cam = panel
    rec.start()
    assert not p._spin_rec_fps.isEnabled()
    assert not p._spin_csv.isEnabled()
    assert not p._combo_seg.isEnabled()
    rec.stop()


def test_settings_reenable_after_stop(panel):
    p, rec, cam = panel
    rec.start()
    rec.stop()
    assert p._spin_rec_fps.isEnabled()
    assert p._spin_csv.isEnabled()
