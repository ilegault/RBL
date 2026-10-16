"""
Tests for scripts/screenshot_overview.py (ticket 61).

WHY THIS EXISTS
---------------
The screenshot launcher fakes instrument outputs and pushes them through the
real Beamline and Overview. These tests prove the scene is healthy by the app's
own rules, survives the real volts -> physical-units conversion, reaches the
real Overview widgets, that the feed stops itself the moment a real instrument
reports, and that nothing in the app can reach the script.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import subprocess
import sys
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from rbl.config import hardware_config as SC
from rbl.config.vacuum_config import UI_GOOD_VACUUM_TORR
from rbl.gui import theme
from rbl.gui.overview_tab import OverviewTab
from rbl.hardware.funcgen_safety import PEAK_WARN_VOLTS
from rbl.hardware.hv_interlock import interlock_status
from rbl.state.beamline import Beamline
from rbl.state.snapshots import MotorState
from scripts import screenshot_overview as so

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _run(code: str) -> str:
    # pytest.ini puts src/ on the path for this process; a child needs it said.
    env = {**os.environ, "PYTHONPATH": str(REPO / "src")}
    return subprocess.run([sys.executable, "-c", code], cwd=REPO, check=True,
                          capture_output=True, text=True, env=env).stdout.strip()


def test_importing_the_script_loads_no_rbl_module():
    out = _run("import sys, scripts.screenshot_overview;"
               "print(any(m == 'rbl' or m.startswith('rbl.') for m in sys.modules))")
    assert out == "False"


def test_isolation_redirects_every_persistence_root(tmp_path):
    out = _run("import pathlib;"
               "from scripts import screenshot_overview as so;"
               f"so.isolate_environment(pathlib.Path({str(tmp_path)!r}));"
               "from rbl.config import paths;"
               "print(paths.CONFIG_DIR);print(paths.LOG_ROOT)").splitlines()
    assert len(out) == 2
    for line in out:
        assert Path(line).resolve().is_relative_to(tmp_path.resolve())


def test_isolation_refuses_after_rbl_is_imported(tmp_path):
    before = os.environ.get("HOME")
    with pytest.raises(RuntimeError):
        so.isolate_environment(tmp_path)
    assert os.environ.get("HOME") == before


def test_nothing_in_the_app_references_the_script():
    offenders = [p for p in (REPO / "src").rglob("*.py")
                 if "screenshot_overview" in p.read_text(encoding="utf-8")]
    assert offenders == []
    assert "screenshot_overview" not in (REPO / "rbl.spec").read_text(encoding="utf-8")


def test_scene_is_healthy_by_the_apps_own_rules():
    scene = so.build_scene()
    for state in scene.funcgen_readback.values():
        assert state["amp"] / 2 < PEAK_WARN_VOLTS
    assert so.CHAMBER_TORR <= UI_GOOD_VACUUM_TORR
    peak = max(p for p, _ in so.AXIS_DRIVE.values())
    assert interlock_status(so.CHAMBER_TORR, peak, pressure_known=True)[0] == "ok"


def test_scene_round_trips_through_the_real_conversion(qapp):
    bl = Beamline()
    logamps, amps, motors = [], [], []
    bl.logamps_changed.connect(logamps.append)
    bl.amps_changed.connect(amps.append)
    bl.motors_changed.connect(motors.append)
    so.push_scene(bl, so.build_scene())

    for slit, amps_expected in so.SLIT_CURRENTS_A.items():
        got = logamps[-1].currents[slit]
        assert got == pytest.approx(amps_expected, rel=0.005)
    for amp in SC.AMP_LABELS:
        axis = amp[0]
        assert amps[-1].channels[amp].peak_kv == pytest.approx(
            so.AXIS_DRIVE[axis][0], abs=0.01)
    for slit, mm in so.SLIT_POSITIONS_MM.items():
        assert motors[-1].axes[slit].pos_mm == pytest.approx(mm, abs=0.01)


def test_scene_reaches_the_overview_screen(qapp):
    bl = Beamline()
    motors = []
    bl.motors_changed.connect(motors.append)
    tab = OverviewTab(bl)
    tab.show()
    qapp.processEvents()
    so.push_scene(bl, so.build_scene())
    tab.redraw()

    for key in ("motors", "logamps", "amps", "A", "B"):
        assert tab.pills[key].styleSheet() == theme.pill(True), key
    assert tab.hv_phase["X"].text().startswith("push-pull locked")
    assert tab.hv_phase["Y"].text().startswith("push-pull locked")
    assert tab.lbl_timebase.text() == "A: INT  B: EXT"
    axes = motors[-1].axes
    x = axes["X+"].pos_mm + axes["X-"].pos_mm
    y = axes["Y+"].pos_mm + axes["Y-"].pos_mm
    assert f"X: {x:.3f} mm" in tab.lbl_gaps.text()
    assert f"Y: {y:.3f} mm" in tab.lbl_gaps.text()


def test_feed_stops_when_real_data_arrives(qapp):
    bl, reasons = Beamline(), []
    feed = so.ScreenshotFeed(bl, so.build_scene(), on_stop=reasons.append)
    feed.start()
    assert feed.active and reasons == []
    bl.motors_changed.emit(MotorState(connected=True, zeroed=True, axes={}))
    assert not feed.active
    assert reasons == ["real motors_changed data arrived"]


def test_feed_stops_when_an_instrument_connects(qapp):
    bl, reasons = Beamline(), []
    feed = so.ScreenshotFeed(bl, so.build_scene(), on_stop=reasons.append)
    feed.start()
    bl.labjack_connected.emit("470000000")
    assert not feed.active
    assert reasons == ["a real instrument connected"]


def test_feed_survives_its_own_pushes(qapp):
    feed = so.ScreenshotFeed(Beamline(), so.build_scene())
    feed.start()
    for _ in range(3):
        feed.push()
    assert feed.active
    feed.stop("test")


def test_screenshot_mode_arranges_titles_and_unhooks_connect_all(qapp, monkeypatch):
    from rbl.gui.app import MainWindow
    win = MainWindow()
    recorded = []
    monkeypatch.setattr(win.beamline, "set_interlock_gauges", recorded.append)
    feed = so.start_screenshot_mode(win)
    try:
        assert win.windowTitle().endswith(so.TITLE_SUFFIX)
        assert recorded == [{so.CHAMBER_GAUGE}]
        assert win.overview_tab.panel_area.column_names() == so.SCREENSHOT_LAYOUT
        win.overview_tab.btn_connect_all.click()
        qapp.processEvents()
        assert win.overview_tab.btn_connect_all.text() == "Connect All"
    finally:
        feed.stop("test")


def test_overview_names_every_panel(qapp):
    from rbl.gui.app import MainWindow
    win = MainWindow()
    names = sorted(n for col in win.overview_tab.panel_area.column_names() for n in col)
    assert names == sorted(["beam", "slits", "current", "vacuum", "funcgen",
                            "hv", "recording", "cup"])
