"""
Tests for CameraTab — constructs offscreen, two-view sync, crosshair.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
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

    def is_recording(self):
        return self._recording

    def set_csv_interval_s(self, v):
        self._csv_interval_s = v
        self.settings_changed.emit()

    def set_record_fps(self, v):
        self._record_fps = v
        self.settings_changed.emit()

    def set_segment_seconds(self, v):
        self._segment_seconds = v

    def set_quality(self, label):
        self._quality_label = label
        self.settings_changed.emit()

    def set_video_enabled(self, on):
        self._video_enabled = on

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


def test_camera_tab_constructs_without_cv2(qapp, monkeypatch):
    """CameraTab must construct cleanly even if cv2 is absent."""
    import rbl.gui.camera_tab as mod
    monkeypatch.setattr(mod, "_CV2_OK", False)
    cam = _StubCamera()
    rec = _StubRecorder(cam)
    from rbl.gui.camera_tab import CameraTab
    try:
        CameraTab(rec, cam)
    except Exception as exc:
        pytest.fail(f"CameraTab raised with cv2 absent: {exc}")


def test_record_fps_change_syncs_between_views(setup):
    """Changing record FPS in one view → other view updates via settings_changed."""
    tab, panel, rec, cam = setup
    # Simulate user changing rec fps in the camera tab
    tab._spin_rec_fps.setValue(5)
    # recorder now has _record_fps=5; panel should reflect it on settings_changed
    assert rec._record_fps == 5
    # Panel should now show 5 in its spinbox (rendered on settings_changed)
    assert panel._spin_rec_fps.value() == 5


def test_crosshair_toggle_no_raise_with_no_frame(setup):
    """Toggling crosshair without a frame in the feed must not raise."""
    tab, panel, rec, cam = setup
    try:
        tab._chk_crosshair.setChecked(True)
        tab._chk_crosshair.setChecked(False)
    except Exception as exc:
        pytest.fail(f"Crosshair toggle raised: {exc}")
