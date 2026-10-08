"""
Tests for RecordingPanel — constructs offscreen, video button states and clicks.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QComboBox, QPushButton, QSpinBox


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

    cam_open = False
    def is_open(self): return self.cam_open
    def actual_size(self): return (640, 480) if self.cam_open else (0, 0)
    def latest_frame(self): return None
    _thread = None

    def open(self):
        self.cam_open = True
        self.opened.emit(640, 480, 30.0)

    def close(self):
        self.cam_open = False
        self.closed.emit()


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

    active_video     = False

    def __init__(self, camera):
        super().__init__()
        self._camera = camera
        self.cam_ref = camera

    def is_recording(self):  return getattr(self, '_recording')
    def set_csv_interval_s(self, v): pass
    def set_record_fps(self, v): pass
    def set_segment_seconds(self, v): pass
    def set_quality(self, label): pass
    def set_master_codec(self, label): pass

    def start(self):
        setattr(self, '_recording', True)
        self.state_changed.emit()

    def stop(self):
        self.stop_video()
        setattr(self, '_recording', False)
        self.state_changed.emit()

    def start_video(self):
        if not self.is_recording() or not self.cam_ref.is_open():
            return False
        self.active_video = True
        self.state_changed.emit()
        return True

    def stop_video(self):
        self.active_video = False
        self.state_changed.emit()

    def take_photo(self): return None
    def add_note(self, t): pass
    def open_session_folder(self): pass
    def state(self):
        return dict(recording=self.is_recording(), session_id="",
                    folder="", elapsed_s=0, csv_rows=0, frames_written=0,
                    frames_dropped=0, segments_done=0, transcode_pending=0,
                    disk_free_bytes=0, video_active=self.active_video, ffmpeg_found=False)


@pytest.fixture
def panel(qapp):
    cam = _StubCamera()
    rec = _StubRecorder(cam)
    from rbl.gui.widgets.recording_panel import RecordingPanel
    p = RecordingPanel(rec)
    return p, rec, cam


def test_start_session_enabled_with_no_camera(panel):
    """START SESSION must be enabled even when the camera is closed."""
    p, rec, cam = panel
    session_btn = [
        b for b in p.findChildren(QPushButton) if "Session" in b.text() or "START" in b.text()
    ][0]
    assert session_btn.isEnabled()


def test_record_video_checkbox_disabled_with_camera_closed(panel):
    """Video button must be disabled with appropriate tooltip when camera is not open."""
    p, rec, cam = panel
    video_btn = [b for b in p.findChildren(QPushButton) if "Video" in b.text()][0]
    assert not video_btn.isEnabled()
    assert video_btn.toolTip() == "Start a session first"

    rec.start()
    assert not video_btn.isEnabled()
    assert video_btn.toolTip() == "Open the camera first"


def test_settings_disable_while_recording(panel):
    """CSV interval disables while recording; video settings disable while video runs."""
    p, rec, cam = panel
    rec_fps_spin = [s for s in p.findChildren(QSpinBox) if "acquired frames" in s.toolTip()][0]
    csv_spin = [s for s in p.findChildren(QSpinBox) if s.suffix().strip() == "s"][0]
    seg_combo = [
        c for c in p.findChildren(QComboBox)
        if any("min" in c.itemText(i) for i in range(c.count()))
    ][0]

    cam.open()
    rec.start()
    assert not csv_spin.isEnabled()
    # Video settings stay editable while video is not running
    assert rec_fps_spin.isEnabled()
    assert seg_combo.isEnabled()

    rec.start_video()
    assert not rec_fps_spin.isEnabled()
    assert not seg_combo.isEnabled()

    rec.stop_video()
    assert rec_fps_spin.isEnabled()
    assert seg_combo.isEnabled()

    rec.stop()
    assert csv_spin.isEnabled()


def test_settings_reenable_after_stop(panel):
    p, rec, cam = panel
    rec_fps_spin = [s for s in p.findChildren(QSpinBox) if "acquired frames" in s.toolTip()][0]
    csv_spin = [s for s in p.findChildren(QSpinBox) if s.suffix().strip() == "s"][0]

    rec.start()
    rec.stop()
    assert rec_fps_spin.isEnabled()
    assert csv_spin.isEnabled()


class _FakeCamera(QObject):
    opened = Signal(int, int, float)
    closed = Signal()
    error = Signal(str)
    frame_ready = Signal(object, float)
    preview_ready = Signal(object)
    format_ready = Signal(str)

    fake_open = False

    def is_open(self):
        return self.fake_open

    def actual_size(self):
        return (640, 480) if self.fake_open else (0, 0)

    def latest_frame(self):
        return None

    def requested_fourcc(self):
        return "MJPG"

    _thread = None

    def open(self, idx=0, w=640, h=480, fps=30.0, fourcc="MJPG"):
        self.fake_open = True
        self.opened.emit(w, h, fps)

    def close(self):
        self.fake_open = False
        self.closed.emit()


def test_recording_panel_video_button_tooltips_and_clicks(tmp_path, qapp, monkeypatch):
    """Test criteria 1 & 2 on RecordingPanel with real SessionRecorder and real RecordingPanel."""
    from rbl.gui.widgets.recording_panel import RecordingPanel
    from rbl.services.session_recorder import SessionRecorder
    from tests.test_video_recorder import _patch_cv2

    writers = []
    _patch_cv2(monkeypatch, writers)
    monkeypatch.setattr("rbl.services.session_recorder._logs_dir", lambda: str(tmp_path))

    cam = _FakeCamera()
    rec = SessionRecorder(lambda: {}, cam)
    panel = RecordingPanel(rec)

    video_btn = [b for b in panel.findChildren(QPushButton) if "Video" in b.text()][0]

    # Before session: disabled, tooltip 'Start a session first', text 'Start Video'
    assert not video_btn.isEnabled()
    assert video_btn.toolTip() == "Start a session first"
    assert video_btn.text() == "Start Video"

    # Start session with fake camera closed: disabled, tooltip 'Open the camera first'
    rec.start()
    assert not video_btn.isEnabled()
    assert video_btn.toolTip() == "Open the camera first"
    assert video_btn.text() == "Start Video"

    # Open fake camera: enabled, reading 'Start Video'
    cam.open()
    assert video_btn.isEnabled()
    assert video_btn.text() == "Start Video"

    # Click video button during session: recorder reports video running, button reads 'Stop Video'
    video_btn.click()
    assert rec.state()["video_active"] is True
    assert video_btn.text() == "Stop Video"

    # Click again: stops video, recorder reports video not running, button reads 'Start Video'
    video_btn.click()
    assert rec.state()["video_active"] is False
    assert video_btn.text() == "Start Video"

    rec.stop()
