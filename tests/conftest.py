import os
from pathlib import Path

import pytest

# Run Qt headlessly during tests so no real windows are opened. Set before any
# PySide6 import so the offscreen platform plugin is selected.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def _never_touch_the_real_calibration_store(tmp_path, monkeypatch):
    """Point the per-channel load-calibration store at a temp file, always.

    WHY THIS IS AUTOUSE AND NOT PER-TEST
    ------------------------------------
    `load_calibration_store.STORE_PATH` is `~/.config/rbl/load_calibration.json`
    - the OPERATOR'S real measured capacitances, on the machine the tests run
    on. Most tests that touch it monkeypatch STORE_PATH themselves, but
    `test_load_characterizer.py`'s Mode C tests drive the service far enough
    to reach its real `save_measurement()` call, so running the suite wrote a
    fabricated measurement into the real store.

    That is not a cosmetic leak. The Raster Planner reads this store to
    predict per-channel current and to draw the amplifier envelope, and
    labels what it finds "measured". A capacitance invented by a unit test,
    sitting in the store under a channel name, is indistinguishable from a
    real characterisation run at the point of use.

    An opt-in fixture cannot fix that, because the tests that need it most
    are the ones that did not know they were writing. So: every test in the
    suite gets a temp store, and a test that wants to exercise persistence
    still gets a real file to read back - just not the operator's.
    """
    from rbl.config import load_calibration_store as store
    monkeypatch.setattr(store, "STORE_PATH",
                        Path(tmp_path) / "load_calibration.json")
    yield


@pytest.fixture(autouse=True)
def _never_touch_the_real_amplifier_stores(tmp_path, monkeypatch):
    """Point the amplifier-assignment history and the characterization-results
    directory at temp locations, always.

    Same reasoning as _never_touch_the_real_calibration_store above: a test
    once wrote a fabricated capacitance into the operator's real store and the
    planner then called it "measured". These two places are written by later
    tickets (assignment history, characterization results), so they are
    redirected before any of those modules exist.

    This only covers modules that read the path through `rbl.config.paths` AT
    CALL TIME (`paths.AMPLIFIER_ASSIGNMENTS_STORE`). A module that copies the
    path into its own constant at import time keeps the real location and
    escapes this fixture - do not do that.
    """
    from rbl.config import paths
    monkeypatch.setattr(paths, "AMPLIFIER_ASSIGNMENTS_STORE",
                        Path(tmp_path) / "amplifier_assignments.jsonl")
    monkeypatch.setattr(paths, "CHARACTERIZATION_DIR",
                        Path(tmp_path) / "load_characterization")
    yield


@pytest.fixture(autouse=True)
def _never_touch_the_real_cup_settings_store(tmp_path, monkeypatch):
    """Point the Faraday cup acquisition settings store at a temp file, always.

    Matches the pattern of _never_touch_the_real_calibration_store above,
    preventing any test from writing to ~/.config/rbl/cup_settings.json.
    """
    from rbl.config import cup_settings_store as store
    monkeypatch.setattr(store, "STORE_PATH",
                        Path(tmp_path) / "cup_settings.json")
    yield


@pytest.fixture(autouse=True)
def _cup_tab_never_opens_a_real_modal(monkeypatch):
    """Replace the Faraday cup tab's two blocking dialogs with non-blocking stand-ins.

    `_confirm_stop_automatic` and `_ask_restart` wrap modal dialogs; run for real
    under offscreen Qt they would wait forever for a click nobody makes, and the
    whole suite would hang rather than fail. Stopping auto-confirms (so the many
    tests that merely need the cycle stopped do not each repeat the patch), and
    restarting raises, because a restart answer decides how a dose is counted and a
    test that reaches it must say what the operator answered. A test that cares
    monkeypatches either method on the tab instance, which wins over this class patch.
    """
    from rbl.gui.faraday_cup_tab import FaradayCupTab

    def _no_real_restart_dialog(self, gap_start_t, now_t, saved_boundary_t):
        raise AssertionError(
            "this test restarted automatic cup insertion after a stop without "
            "patching tab._ask_restart to return a RestartChoice (or None)"
        )

    monkeypatch.setattr(FaradayCupTab, "_confirm_stop_automatic", lambda self: True)
    monkeypatch.setattr(FaradayCupTab, "_ask_restart", _no_real_restart_dialog)
    yield


@pytest.fixture(autouse=True)
def _main_window_never_opens_a_real_continue_dialog(monkeypatch):
    """Answer "start from zero" to the continue-the-dose question without a modal.

    `MainWindow._ask_continue_dose` wraps a modal dialog that appears whenever a session
    starts and an earlier session left counted dose under LOGS_DIR. Under offscreen Qt it
    would wait forever. A test that cares about the answer monkeypatches it on the
    window instance, which wins over this class patch.
    """
    from rbl.gui.app import MainWindow

    monkeypatch.setattr(MainWindow, "_ask_continue_dose", lambda self, totals: None)
    yield
