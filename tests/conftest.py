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
