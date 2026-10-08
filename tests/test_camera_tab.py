"""
Tests for CameraTab — constructs offscreen, two-view sync, crosshair, video button.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QCheckBox, QPushButton, QSpinBox


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

    stub_open = False
    def is_open(self): return self.stub_open
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

    active_video     = False

    def __init__(self, camera):
        super().__init__()
        self._camera = camera
        self.cam_ref = camera

    def is_recording(self):
        return getattr(self, '_recording')

    def set_csv_interval_s(self, v):
        setattr(self, '_csv_interval_s', v)
        self.settings_changed.emit()

    def set_record_fps(self, v):
        setattr(self, '_record_fps', v)
        self.settings_changed.emit()

    def set_segment_seconds(self, v):
        setattr(self, '_segment_seconds', v)

    def set_quality(self, label):
        setattr(self, '_quality_label', label)
        self.settings_changed.emit()

    def set_master_codec(self, label):
        setattr(self, '_master_codec', label)
        self.settings_changed.emit()

    def start_video(self):
        if not self.is_recording() or not self.cam_ref.is_open():
            return False
        self.active_video = True
        self.state_changed.emit()
        return True

    def stop_video(self):
        self.active_video = False
        self.state_changed.emit()

    def start(self):
        setattr(self, '_recording', True)
        self.state_changed.emit()

    def stop(self):
        self.stop_video()
        setattr(self, '_recording', False)
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
def setup(qapp):
    cam = _StubCamera()
    rec = _StubRecorder(cam)
    from rbl.gui.camera_tab import CameraTab
    from rbl.gui.widgets.recording_panel import RecordingPanel
    tab = CameraTab(rec, cam)
    panel = RecordingPanel(rec)
    return tab, panel, rec, cam


def test_camera_tab_constructs_without_error(setup):
    tab, panel, rec, cam = setup
    assert tab is not None


def test_record_fps_change_syncs_between_views(setup):
    """Changing record FPS in one view → other view updates via settings_changed."""
    tab, panel, rec, cam = setup
    tab_fps_spin = [
        s for s in tab.findChildren(QSpinBox) if "acquired frames" in s.toolTip()
    ][0]
    panel_fps_spin = [
        s for s in panel.findChildren(QSpinBox) if "acquired frames" in s.toolTip()
    ][0]

    tab_fps_spin.setValue(5)
    assert panel_fps_spin.value() == 5


def test_crosshair_toggle_no_raise_with_no_frame(setup):
    """Toggling crosshair without a frame in the feed must not raise."""
    tab, panel, rec, cam = setup
    crosshair_chk = [c for c in tab.findChildren(QCheckBox) if "Crosshair" in c.text()][0]
    crosshair_chk.setChecked(True)
    crosshair_chk.setChecked(False)


def test_camera_tab_video_button_tooltips_and_clicks(tmp_path, qapp, monkeypatch):
    """Test criteria 1 & 2 on CameraTab with real SessionRecorder and real CameraTab."""
    from rbl.gui.camera_tab import CameraTab
    from rbl.services.session_recorder import SessionRecorder
    from tests.test_recording_panel import _FakeCamera
    from tests.test_video_recorder import _patch_cv2

    writers = []
    _patch_cv2(monkeypatch, writers)
    monkeypatch.setattr("rbl.services.session_recorder._logs_dir", lambda: str(tmp_path))

    cam = _FakeCamera()
    rec = SessionRecorder(lambda: {}, cam)
    tab = CameraTab(rec, cam)

    video_btn = [b for b in tab.findChildren(QPushButton) if "Video" in b.text()][0]

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
