"""
Tests for AcquisitionSettingsGroup standalone widget.
Ticket 11 of cup-settings.
"""
from pathlib import Path

from PySide6.QtWidgets import QApplication

import rbl.gui.theme as theme
from rbl.config.cup_config import (
    CUP_ARM_THRESHOLD_A,
    CUP_RELEASE_THRESHOLD_A,
    CUP_SETTLE_WINDOW_S,
)
from rbl.config.cup_settings_store import CupSettings, SettingsLoadWarning
from rbl.gui.widgets.acquisition_settings import AcquisitionSettingsGroup
from rbl.gui.widgets.inputs import QuietDoubleSpinBox, ScientificDoubleSpinBox


def _ensure_app():
    return QApplication.instance() or QApplication([])


def _make_widget():
    _ensure_app()
    w = AcquisitionSettingsGroup()
    w.show()
    return w


def test_widget_types_and_no_bare_spinbox():
    widget = _make_widget()
    assert widget.title() == "Acquisition Settings"
    assert isinstance(widget.spn_arm_threshold, ScientificDoubleSpinBox)
    assert isinstance(widget.spn_release_threshold, ScientificDoubleSpinBox)
    assert isinstance(widget.spn_settle_window, QuietDoubleSpinBox)

    import rbl.gui.widgets.acquisition_settings as mod
    source_text = Path(mod.__file__).read_text(encoding="utf-8")
    assert "QDoubleSpinBox" not in source_text


def test_set_values_and_values_roundtrip():
    widget = _make_widget()
    emitted = []
    widget.settings_changed.connect(lambda k, o, n: emitted.append((k, o, n)))

    custom = CupSettings(
        arm_threshold_a=2e-6,
        release_threshold_a=1e-6,
        settle_window_s=0.75,
        cycle_period_s=300.0,
        cycle_dwell_s=10.0,
        arm_debounce_s=1.0,
        release_interval_s=3.0,
    )
    widget.set_values(custom)
    assert emitted == []

    vals = widget.values()
    assert vals == (2e-6, 1e-6, 0.75)


def test_committed_edit_valid_and_invalid():
    widget = _make_widget()
    widget.set_values(CupSettings.defaults())

    emitted = []
    widget.settings_changed.connect(lambda k, o, n: emitted.append((k, o, n)))

    # 1. Valid edit to arm_threshold
    widget.spn_arm_threshold.setValue(10e-6)
    widget.spn_arm_threshold.editingFinished.emit()

    assert len(emitted) == 1
    assert emitted[0] == ("arm_threshold_a", CUP_ARM_THRESHOLD_A, 10e-6)
    assert not widget.lbl_warning.isVisible()

    # 2. Invalid edit: release threshold above arm threshold (10e-6)
    emitted.clear()
    widget.spn_release_threshold.setValue(15e-6)
    widget.spn_release_threshold.editingFinished.emit()

    assert emitted == []
    assert widget.spn_release_threshold.value() == CUP_RELEASE_THRESHOLD_A
    assert widget.lbl_warning.isVisible()
    assert theme.FAULT in widget.lbl_warning.styleSheet()
    assert "below the arm threshold" in widget.lbl_warning.text()


def test_set_locked():
    widget = _make_widget()

    # Locked
    widget.set_locked(True)
    assert not widget.spn_arm_threshold.isEnabled()
    assert not widget.spn_release_threshold.isEnabled()
    assert not widget.spn_settle_window.isEnabled()
    assert widget.lbl_lock.isVisible()
    assert widget.lbl_lock.text() == (
        "Locked while a run is open or the cycle is armed. Edits apply to the next run."
    )
    assert theme.MUTED in widget.lbl_lock.styleSheet()

    # Unlocked
    widget.set_locked(False)
    assert widget.spn_arm_threshold.isEnabled()
    assert widget.spn_release_threshold.isEnabled()
    assert widget.spn_settle_window.isEnabled()
    assert not widget.lbl_lock.isVisible()


def test_load_warnings_and_reset_defaults():
    widget = _make_widget()
    widget.set_values(CupSettings.defaults())

    # Set load warnings
    w1 = SettingsLoadWarning(
        key="arm_threshold_a",
        found="5.0",
        reason="must be between 1e-9 A and 1e-3 A",
        fallback=CUP_ARM_THRESHOLD_A,
    )
    widget.set_load_warnings([w1])
    assert widget.lbl_load_warnings.isVisible()
    assert theme.FAULT in widget.lbl_load_warnings.styleSheet()
    text = widget.lbl_load_warnings.text()
    assert "arm_threshold_a" in text
    assert "5.0" in text
    assert "must be between 1e-9 A and 1e-3 A" in text
    assert str(CUP_ARM_THRESHOLD_A) in text

    # Clear warnings
    widget.set_load_warnings([])
    assert not widget.lbl_load_warnings.isVisible()

    # Change two fields
    widget.spn_arm_threshold.setValue(5e-6)
    widget.spn_arm_threshold.editingFinished.emit()
    widget.spn_settle_window.setValue(2.5)
    widget.spn_settle_window.editingFinished.emit()

    emitted = []
    widget.settings_changed.connect(lambda k, o, n: emitted.append((k, o, n)))

    # Reset to defaults emits exactly for the two changed fields
    widget.btn_reset_defaults.click()
    assert len(emitted) == 2
    keys = {e[0] for e in emitted}
    assert keys == {"arm_threshold_a", "settle_window_s"}
    for k, old_val, new_val in emitted:
        if k == "arm_threshold_a":
            assert old_val == 5e-6
            assert new_val == CUP_ARM_THRESHOLD_A
        elif k == "settle_window_s":
            assert old_val == 2.5
            assert new_val == CUP_SETTLE_WINDOW_S
