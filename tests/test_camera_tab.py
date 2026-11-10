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


@pytest.fixture
def zwo_tab(tmp_path, qapp, monkeypatch):
    from rbl.config import paths
    from rbl.gui.camera_tab import CameraTab
    from rbl.hardware.camera_source import CameraSource
    from rbl.services.capture_client import CaptureClient
    from rbl.services.session_recorder import SessionRecorder

    monkeypatch.setattr("rbl.services.session_recorder._logs_dir",
                        lambda: str(tmp_path / "logs"))
    monkeypatch.setattr(paths, "ZWO_DARKS_DIR", tmp_path / "darks")
    client = CaptureClient()
    client.start("fake")
    cam = CameraSource()
    rec = SessionRecorder(lambda: {"beamline_v": 1.0}, cam, capture=client)
    rec.set_camera_kind("zwo")
    tab = CameraTab(rec, cam)
    tab.resize(900, 700)
    tab.show()
    yield tab, rec
    client.shutdown()


def test_drawing_a_rect_sets_the_roi(zwo_tab):
    from rbl.hardware.capture_protocol import snap_roi
    from rbl.hardware.preview_stretch import view_rect_to_sensor_roi
    from tests.qt_wait import wait_until

    tab, rec = zwo_tab
    rec.connect_zwo()
    assert wait_until(lambda: rec.state()["zwo_open"])
    assert wait_until(lambda: bool(rec.state()["zwo_settings"]))
    seen: dict = {}
    rec.zwo_preview.connect(lambda _img, fields: seen.update(fields))
    tab.zwo_controls.btn_full_sensor.click()
    assert wait_until(
        lambda: rec.state()["zwo_settings"].get("roi", {}).get("width") == 1920)
    # A Preview must have arrived after Full sensor, and been painted.
    def _ready():
        tab.grab()
        pr = tab.video_view.painted_rect()
        return not pr.isEmpty() and seen.get("roi", {}).get("width") == 1920
    assert wait_until(_ready)

    pr = tab.video_view.painted_rect()
    view = (pr.x(), pr.y(), pr.width() // 2, pr.height() // 2)
    fields = dict(seen)
    expected = snap_roi(
        *view_rect_to_sensor_roi(
            view, (pr.x(), pr.y(), pr.width(), pr.height()),
            (fields["width"], fields["height"]), fields["roi"]),
        1920, 1080)
    tab.video_view.rect_drawn.emit(*view)

    # Drawing makes a Draft; the confirmed ROI is untouched until Apply ROI.
    wait_until(lambda: False, 0.5)
    assert rec.state()["zwo_settings"]["roi"]["width"] == 1920
    assert tab.video_view.draft_fraction() is not None
    assert tab.draft() is not None

    tab.btn_apply_roi.click()
    assert wait_until(
        lambda: tuple(rec.state()["zwo_settings"]["roi"][k]
                      for k in ("x", "y", "width", "height")) == expected)
    assert 900 <= rec.state()["zwo_settings"]["roi"]["width"] <= 960
    assert tab.draft() is None


def _open_full_sensor(tab, rec):
    """Open the fake camera at the full sensor with a painted Preview; returns the fields."""
    from tests.qt_wait import wait_until

    rec.connect_zwo()
    assert wait_until(lambda: rec.state()["zwo_open"])
    assert wait_until(lambda: bool(rec.state()["zwo_settings"]))
    seen: dict = {}
    rec.zwo_preview.connect(lambda _img, fields: seen.update(fields))
    tab.zwo_controls.btn_full_sensor.click()
    assert wait_until(
        lambda: rec.state()["zwo_settings"].get("roi", {}).get("width") == 1920)

    def _ready():
        tab.grab()
        return (not tab.video_view.painted_rect().isEmpty()
                and seen.get("roi", {}).get("width") == 1920)
    assert wait_until(_ready)
    return seen


def _draw_half(tab):
    pr = tab.video_view.painted_rect()
    tab.video_view.rect_drawn.emit(pr.x() + 20, pr.y() + 20,
                                   pr.width() // 2, pr.height() // 2)


def _confirmed_roi(rec):
    r = rec.state()["zwo_settings"]["roi"]
    return (r["x"], r["y"], r["width"], r["height"])


def test_aspect_presets_shape_the_draft(zwo_tab):
    tab, rec = zwo_tab
    _open_full_sensor(tab, rec)

    tab.combo_aspect.setCurrentIndex(tab.combo_aspect.findText("16:9"))
    assert not tab.spin_aspect_w.isVisibleTo(tab)
    _draw_half(tab)
    x, y, w, h = tab.draft()
    assert abs(w - h * 16 / 9) <= 1

    cx, cy = x + w / 2, y + h / 2
    tab.combo_aspect.setCurrentIndex(tab.combo_aspect.findText("1:1"))
    x, y, w, h = tab.draft()
    assert abs(w - h) <= 1
    assert abs(x + w / 2 - cx) <= 1 and abs(y + h / 2 - cy) <= 1

    tab.combo_aspect.setCurrentIndex(tab.combo_aspect.findText("Custom"))
    assert tab.spin_aspect_w.isVisibleTo(tab) and tab.spin_aspect_h.isVisibleTo(tab)
    tab.spin_aspect_w.setValue(2)
    tab.spin_aspect_h.setValue(1)
    x, y, w, h = tab.draft()
    assert abs(w - 2 * h) <= 1

    tab.combo_aspect.setCurrentIndex(tab.combo_aspect.findText("Free"))
    assert not tab.spin_aspect_w.isVisibleTo(tab)


def test_discard_and_escape_drop_the_draft(zwo_tab):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    tab, rec = zwo_tab
    _open_full_sensor(tab, rec)
    before = _confirmed_roi(rec)

    for dismiss in (tab.btn_discard_roi.click,
                    lambda: QTest.keyClick(tab, Qt.Key.Key_Escape)):
        _draw_half(tab)
        assert tab.draft() is not None
        assert tab.btn_apply_roi.isEnabled()
        dismiss()
        assert tab.draft() is None
        assert tab.video_view.draft_fraction() is None
        assert not tab.btn_apply_roi.isEnabled()
        assert _confirmed_roi(rec) == before


def test_last_roi_becomes_the_draft_at_full_sensor(zwo_tab):
    from tests.qt_wait import wait_until

    tab, rec = zwo_tab
    _open_full_sensor(tab, rec)
    full = _confirmed_roi(rec)

    rec.set_zwo_settings(roi={"x": 320, "y": 180, "width": 640, "height": 360})
    assert wait_until(lambda: _confirmed_roi(rec) == (320, 180, 640, 360))
    a = _confirmed_roi(rec)
    rec.swap_zwo_roi()
    assert wait_until(lambda: _confirmed_roi(rec) == full)
    assert wait_until(lambda: tab.draft() == a)

    tab.btn_apply_roi.click()
    assert wait_until(lambda: _confirmed_roi(rec) == a)
    assert tab.draft() is None

    # Back to full sensor, then Discard: the Draft must not reappear.
    rec.swap_zwo_roi()
    assert wait_until(lambda: _confirmed_roi(rec) == full)
    assert wait_until(lambda: tab.draft() == a)
    tab.btn_discard_roi.click()
    rec.state_changed.emit()
    rec.state_changed.emit()
    assert tab.draft() is None


def test_drawing_is_off_while_recording(zwo_tab):
    from tests.qt_wait import wait_until

    tab, rec = zwo_tab
    assert not tab.video_view.rect_drawing_enabled()   # not open yet
    rec.connect_zwo()
    assert wait_until(lambda: rec.state()["zwo_open"])
    assert wait_until(lambda: tab.video_view.rect_drawing_enabled())

    rec.start()
    assert rec.start_video()
    assert wait_until(lambda: rec.state()["zwo_recording"])
    assert not tab.video_view.rect_drawing_enabled()

    rec.stop_video()
    assert wait_until(lambda: not rec.state()["zwo_recording"])
    assert tab.video_view.rect_drawing_enabled()

    rec.set_camera_kind("usb")
    assert not tab.video_view.rect_drawing_enabled()
    rec.stop()


def test_recording_clears_the_draft(zwo_tab):
    from tests.qt_wait import wait_until

    tab, rec = zwo_tab
    _open_full_sensor(tab, rec)
    _draw_half(tab)
    assert tab.draft() is not None

    rec.start()
    assert rec.start_video()
    assert wait_until(lambda: rec.state()["zwo_recording"])
    assert tab.draft() is None
    assert not tab.btn_apply_roi.isEnabled()

    rec.stop_video()
    assert wait_until(lambda: not rec.state()["zwo_recording"])
    rec.stop()


def test_fullscreen_button_opens_and_esc_closes(setup):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from rbl.gui.camera_tab import _FullscreenWindow

    tab, panel, rec, cam = setup
    assert hasattr(tab, "btn_fullscreen")
    tab.btn_fullscreen.click()
    try:
        win = getattr(tab, "_fullscreen_win")
        assert isinstance(win, _FullscreenWindow)
        QTest.keyClick(win, Qt.Key.Key_Escape)
        assert getattr(tab, "_fullscreen_win") is None
    finally:
        win = getattr(tab, "_fullscreen_win")
        if win is not None:
            win.close()


def test_fullscreen_button_and_double_click_agree(setup):
    from rbl.gui.camera_tab import _FullscreenWindow

    tab, panel, rec, cam = setup
    assert hasattr(tab, "btn_fullscreen")
    tab.btn_fullscreen.click()
    try:
        win = getattr(tab, "_fullscreen_win")
        assert isinstance(win, _FullscreenWindow)
        tab.btn_fullscreen.click()
        assert getattr(tab, "_fullscreen_win") is None
        tab.video_view.double_clicked.emit()
        assert isinstance(getattr(tab, "_fullscreen_win"), _FullscreenWindow)
    finally:
        win = getattr(tab, "_fullscreen_win")
        if win is not None:
            win.close()


def test_fullscreen_button_in_same_layout_as_crosshair(setup):
    from PySide6.QtWidgets import QHBoxLayout

    tab, panel, rec, cam = setup
    assert hasattr(tab, "btn_fullscreen")
    crosshair = getattr(tab, "_chk_crosshair")
    layouts = [lay for lay in tab.findChildren(QHBoxLayout) if lay.indexOf(crosshair) != -1]
    assert len(layouts) == 1
    layout = layouts[0]
    assert layout.indexOf(tab.btn_fullscreen) != -1
    assert layout.indexOf(tab.btn_fullscreen) == layout.indexOf(crosshair) + 1
    assert tab.btn_fullscreen.text() == "Full screen"
    assert tab.btn_fullscreen.toolTip() == (
        "Full screen (Esc to exit). Double-click the picture also works."
    )

