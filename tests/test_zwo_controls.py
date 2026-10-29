"""
test_zwo_controls.py
Tests for ZwoControls widget and preview_caption (ticket 73).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from rbl.config import paths
from rbl.gui.widgets.zwo_controls import ZwoControls, preview_caption
from rbl.hardware.camera_source import CameraSource
from rbl.services.capture_client import CaptureClient
from rbl.services.session_recorder import SessionRecorder
from tests.qt_wait import wait_until


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def capture_client(qapp):
    c = CaptureClient()
    yield c
    c.shutdown()


@pytest.fixture
def recorder_and_widget(tmp_path, monkeypatch, qapp, capture_client):
    logs_dir = tmp_path / "logs"
    darks_dir = tmp_path / "zwo_darks"
    monkeypatch.setattr("rbl.services.session_recorder._logs_dir", lambda: str(logs_dir))
    monkeypatch.setattr(paths, "ZWO_DARKS_DIR", darks_dir)

    cam = CameraSource()
    capture_client.start("fake")
    rec = SessionRecorder(lambda: {"beamline_v": 1.0}, cam, capture=capture_client)
    rec.set_camera_kind("zwo")

    widget = ZwoControls(rec)
    return rec, widget, darks_dir


def test_inputs_disabled_until_open_then_ranged_from_controls(recorder_and_widget, qapp):
    rec, widget, _ = recorder_and_widget

    # Before open, every input must be disabled
    inputs = [
        widget.spin_exposure_ms,
        widget.spin_gain,
        widget.combo_bit_depth,
        widget.spin_roi_x,
        widget.spin_roi_y,
        widget.spin_roi_width,
        widget.spin_roi_height,
        widget.btn_full_sensor,
        widget.spin_keep_one_in_n,
        widget.combo_preview,
        widget.slider_brightness,
        widget.btn_take_darks,
    ]
    for inp in inputs:
        assert not inp.isEnabled(), f"{inp} should be disabled before open"

    rec.connect_zwo()
    assert wait_until(lambda: rec.state()["zwo_open"])
    assert wait_until(lambda: bool(rec.state()["zwo_info"]))

    # After open, they are enabled
    for inp in inputs:
        assert inp.isEnabled(), f"{inp} should be enabled after open"

    gain_max = rec.state()["zwo_info"]["controls"]["Gain"]["max"]
    assert widget.spin_gain.maximum() == gain_max
    assert widget.spin_roi_width.maximum() == 1920


def test_values_shown_come_from_the_reply(recorder_and_widget, qapp):
    rec, widget, _ = recorder_and_widget
    rec.connect_zwo()
    assert wait_until(lambda: rec.state()["zwo_open"])
    assert wait_until(lambda: bool(rec.state()["zwo_settings"]))

    widget.spin_roi_x.setValue(1)
    widget.spin_roi_width.setValue(101)
    widget.spin_roi_x.editingFinished.emit()
    widget.spin_roi_width.editingFinished.emit()

    assert wait_until(lambda: rec.state()["zwo_settings"].get("roi", {}).get("width") == 96)
    assert widget.spin_roi_x.value() == 0
    assert widget.spin_roi_width.value() == 96

    widget.spin_exposure_ms.setValue(5.0)
    widget.spin_exposure_ms.editingFinished.emit()

    assert wait_until(lambda: rec.state()["zwo_settings"].get("exposure_us") == 5000)
    assert rec.state()["zwo_settings"]["exposure_us"] == 5000
    assert widget.spin_exposure_ms.value() == pytest.approx(5.0, abs=1e-3)
    assert f"{widget.spin_exposure_ms.value():.3f}" == "5.000"

    widget.btn_full_sensor.click()
    assert wait_until(lambda: rec.state()["zwo_settings"].get("roi", {}).get("width") == 1920)
    assert widget.spin_roi_x.value() == 0
    assert widget.spin_roi_y.value() == 0
    assert widget.spin_roi_width.value() == 1920
    assert widget.spin_roi_height.value() == 1080


def test_preview_changes_never_touch_the_recording(recorder_and_widget, qapp):
    rec, widget, _ = recorder_and_widget
    rec.connect_zwo()
    assert wait_until(lambda: rec.state()["zwo_open"])

    rec.set_zwo_settings(
        roi={"x": 0, "y": 0, "width": 64, "height": 32},
        exposure_us=10000,
    )
    assert wait_until(lambda: rec.state()["zwo_settings"].get("roi", {}).get("width") == 64)

    assert rec.start()
    session_folder = Path(rec.state()["folder"])
    assert rec.start_video()
    assert wait_until(lambda: rec.state()["zwo_recording"])

    time.sleep(1.0)
    qapp.processEvents()

    # Change preview settings via the widget controls
    linear_idx = widget.combo_preview.findData("linear")
    if linear_idx < 0:
        # fallback to text match
        linear_idx = widget.combo_preview.findText("Linear")
    widget.combo_preview.setCurrentIndex(linear_idx)
    widget.combo_preview.currentIndexChanged.emit(linear_idx)
    widget.slider_brightness.setValue(300)

    time.sleep(1.0)
    qapp.processEvents()

    rec.stop_video()
    assert wait_until(lambda: not rec.state()["zwo_recording"])
    rec.stop()

    zwo_dir = session_folder / "zwo"
    ser_files = list(zwo_dir.glob("segment_*.ser"))
    assert len(ser_files) == 1, f"Expected exactly one segment, got {ser_files}"

    json_files = list(zwo_dir.glob("segment_*.json"))
    assert len(json_files) == 1
    with open(json_files[0], "r", encoding="utf-8") as f:
        meta = json.load(f)
    assert meta["settings"]["roi"]["width"] == 64
    assert meta["settings"]["roi"]["height"] == 32


def test_take_darks_asks_first(recorder_and_widget, qapp):
    rec, widget, darks_dir = recorder_and_widget
    rec.connect_zwo()
    assert wait_until(lambda: rec.state()["zwo_open"])

    # confirm_darks returning False
    widget.confirm_darks = lambda: False
    widget.btn_take_darks.click()
    time.sleep(0.5)
    qapp.processEvents()
    assert not list(darks_dir.glob("**/*.fits"))

    # confirm_darks returning True
    widget.confirm_darks = lambda: True
    widget.btn_take_darks.click()
    assert wait_until(lambda: len(list(darks_dir.glob("FAKE0001/dark_*.fits"))) > 0, timeout_s=30.0)

    # During a ZWO run the button is disabled
    assert rec.start()
    assert rec.start_video()
    assert wait_until(lambda: rec.state()["zwo_recording"])
    assert not widget.btn_take_darks.isEnabled()
    rec.stop_video()
    assert wait_until(lambda: not rec.state()["zwo_recording"])
    rec.stop()


def test_status_line_and_caption(recorder_and_widget, qapp):
    rec, widget, _ = recorder_and_widget
    rec.connect_zwo()
    assert wait_until(lambda: rec.state()["zwo_open"])

    assert wait_until(lambda: bool(rec.state()["zwo_status"]))
    txt = widget.lbl_status.text()
    assert "fps" in txt
    assert "written" in txt
    assert "°C" in txt
    assert "GB/h" in txt
    assert "GB free" in txt

    assert preview_caption("auto") == "Display stretch: not intensity"
    assert preview_caption("linear") == "Linear display"
