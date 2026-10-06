"""
acquisition_settings.py
Acquisition Settings group box for the Faraday cup tab.

WHY THIS EXISTS
---------------
On the control PC, the Faraday cup acquisition parameters (arm threshold, release threshold,
settle window) define which beam currents trigger an acquisition run and which initial samples
are excluded while the picoammeter autoranges.

This standalone widget allows the operator to inspect, edit, and reset these parameters.
It owns no driver, no detector, no scheduler, and no persistence file. Edits are validated
against the central rules in rbl.config.cup_settings_store.validate_settings; rejected edits
revert visibly with an explanation in the FAULT role, while valid edits are published via
the `settings_changed` signal.
"""
from __future__ import annotations

from typing import NamedTuple

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import rbl.gui.theme as theme
from rbl.config.cup_settings_store import (
    CupSettings,
    SettingsLoadWarning,
    validate_settings,
)
from rbl.gui.widgets.inputs import (
    QuietDoubleSpinBox,
    ScientificDoubleSpinBox,
    unit_row,
)


class SettingsValues(NamedTuple):
    arm_threshold_a: float
    release_threshold_a: float
    settle_window_s: float


class AcquisitionSettingsGroup(QGroupBox):
    """Operator-facing group box for Faraday cup acquisition thresholds and settle window."""

    settings_changed = Signal(str, float, float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Acquisition Settings", parent)

        defaults = CupSettings.defaults()
        self._last_arm = defaults.arm_threshold_a
        self._last_release = defaults.release_threshold_a
        self._last_settle = defaults.settle_window_s

        vlay = QVBoxLayout(self)
        vlay.setContentsMargins(12, 8, 12, 8)
        vlay.setSpacing(6)

        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(4)

        # 1. Arm threshold (ScientificDoubleSpinBox in Amps)
        grid.addWidget(QLabel("Arm threshold:"), 0, 0)
        self.spn_arm_threshold = ScientificDoubleSpinBox()
        self.spn_arm_threshold.setRange(0.0, 1.0)
        self.spn_arm_threshold.setValue(defaults.arm_threshold_a)
        self.spn_arm_threshold.setToolTip(
            "Beam current required to trigger an acquisition run (in Amperes)"
        )
        self.spn_arm_threshold.editingFinished.connect(self._on_arm_edited)
        grid.addLayout(unit_row(self.spn_arm_threshold, "A"), 0, 1)

        # 2. Release threshold (ScientificDoubleSpinBox in Amps)
        grid.addWidget(QLabel("Release threshold:"), 0, 2)
        self.spn_release_threshold = ScientificDoubleSpinBox()
        self.spn_release_threshold.setRange(0.0, 1.0)
        self.spn_release_threshold.setValue(defaults.release_threshold_a)
        self.spn_release_threshold.setToolTip(
            "Beam current below which an acquisition run closes (in Amperes)"
        )
        self.spn_release_threshold.editingFinished.connect(self._on_release_edited)
        grid.addLayout(unit_row(self.spn_release_threshold, "A"), 0, 3)

        # 3. Settle window (QuietDoubleSpinBox in seconds)
        grid.addWidget(QLabel("Settle window:"), 0, 4)
        self.spn_settle_window = QuietDoubleSpinBox()
        self.spn_settle_window.setRange(0.0, 3600.0)
        self.spn_settle_window.setDecimals(2)
        self.spn_settle_window.setValue(defaults.settle_window_s)
        self.spn_settle_window.setToolTip(
            "Initial duration of an insertion excluded from mean calculation for autorange settling"
        )
        self.spn_settle_window.editingFinished.connect(self._on_settle_edited)
        grid.addLayout(unit_row(self.spn_settle_window, "s"), 0, 5)

        # 4. Reset to defaults button
        self.btn_reset_defaults = QPushButton("Reset to Defaults")
        self.btn_reset_defaults.setToolTip(
            "Reset arm threshold, release threshold, and settle window to defaults"
        )
        self.btn_reset_defaults.clicked.connect(self._on_reset_defaults_clicked)
        btn_lay = QHBoxLayout()
        btn_lay.addWidget(self.btn_reset_defaults)
        grid.addLayout(btn_lay, 0, 6)

        vlay.addLayout(grid)

        # Status and warning labels
        self.lbl_lock = QLabel(
            "Locked while a run is open or the cycle is armed. Edits apply to the next run."
        )
        self.lbl_lock.setStyleSheet(f"color: {theme.MUTED}; font-style: italic;")
        self.lbl_lock.setVisible(False)
        vlay.addWidget(self.lbl_lock)

        self.lbl_warning = QLabel("")
        self.lbl_warning.setStyleSheet(theme.status_label(theme.FAULT))
        self.lbl_warning.setWordWrap(True)
        self.lbl_warning.setVisible(False)
        vlay.addWidget(self.lbl_warning)

        self.lbl_load_warnings = QLabel("")
        self.lbl_load_warnings.setStyleSheet(theme.status_label(theme.FAULT))
        self.lbl_load_warnings.setWordWrap(True)
        self.lbl_load_warnings.setVisible(False)
        vlay.addWidget(self.lbl_load_warnings)

        self.lbl_save_error = QLabel("")
        self.lbl_save_error.setStyleSheet(theme.status_label(theme.FAULT))
        self.lbl_save_error.setWordWrap(True)
        self.lbl_save_error.setVisible(False)
        vlay.addWidget(self.lbl_save_error)

    def set_save_error(self, message: str | None) -> None:
        """Display an error message in FAULT role when save_settings fails, or hide it."""
        if message:
            self.lbl_save_error.setText(message)
            self.lbl_save_error.setStyleSheet(theme.status_label(theme.FAULT))
            self.lbl_save_error.setVisible(True)
            self.lbl_warning.setText(message)
            self.lbl_warning.setStyleSheet(theme.status_label(theme.FAULT))
            self.lbl_warning.setVisible(True)
        else:
            self.lbl_save_error.setText("")
            self.lbl_save_error.setVisible(False)
            if "Failed to save" in self.lbl_warning.text():
                self.lbl_warning.setText("")
                self.lbl_warning.setVisible(False)

    def set_values(self, settings: CupSettings) -> None:
        """Fill all three spin boxes without emitting edit signals."""
        self._last_arm = settings.arm_threshold_a
        self._last_release = settings.release_threshold_a
        self._last_settle = settings.settle_window_s
        self.spn_arm_threshold.sync_value(settings.arm_threshold_a)
        self.spn_release_threshold.sync_value(settings.release_threshold_a)
        self.spn_settle_window.sync_value(settings.settle_window_s)

    def values(self) -> SettingsValues:
        """Return the current numbers for the three fields as a 3-element named tuple."""
        return SettingsValues(
            self.spn_arm_threshold.value(),
            self.spn_release_threshold.value(),
            self.spn_settle_window.value(),
        )

    def set_locked(self, locked: bool) -> None:
        """Lock or unlock spin boxes and show or hide the lock explanation line."""
        self.spn_arm_threshold.setEnabled(not locked)
        self.spn_release_threshold.setEnabled(not locked)
        self.spn_settle_window.setEnabled(not locked)
        self.btn_reset_defaults.setEnabled(not locked)
        self.lbl_lock.setVisible(locked)

    def set_load_warnings(self, warnings: list[SettingsLoadWarning]) -> None:
        """Display one line per warning naming key, found, reason, and fallback; or hide."""
        if not warnings:
            self.lbl_load_warnings.setText("")
            self.lbl_load_warnings.setVisible(False)
            return

        lines = [
            f"Warning for {w.key}: found {w.found} ({w.reason}), used fallback {w.fallback}"
            for w in warnings
        ]
        self.lbl_load_warnings.setText("\n".join(lines))
        self.lbl_load_warnings.setStyleSheet(theme.status_label(theme.FAULT))
        self.lbl_load_warnings.setVisible(True)

    def _on_arm_edited(self) -> None:
        new_val = self.spn_arm_threshold.value()
        if new_val == self._last_arm:
            return
        raw = {
            "arm_threshold_a": new_val,
            "release_threshold_a": self.spn_release_threshold.value(),
            "settle_window_s": self.spn_settle_window.value(),
        }
        _, warnings = validate_settings(raw)
        arm_warn = next((w for w in warnings if w.key == "arm_threshold_a"), None)
        if arm_warn is not None:
            self.spn_arm_threshold.sync_value(self._last_arm)
            self.lbl_warning.setText(f"Arm threshold: {arm_warn.reason}")
            self.lbl_warning.setStyleSheet(theme.status_label(theme.FAULT))
            self.lbl_warning.setVisible(True)
        else:
            old_val = self._last_arm
            self._last_arm = new_val
            self.lbl_warning.setVisible(False)
            self.settings_changed.emit("arm_threshold_a", old_val, new_val)

    def _on_release_edited(self) -> None:
        new_val = self.spn_release_threshold.value()
        if new_val == self._last_release:
            return
        raw = {
            "arm_threshold_a": self.spn_arm_threshold.value(),
            "release_threshold_a": new_val,
            "settle_window_s": self.spn_settle_window.value(),
        }
        _, warnings = validate_settings(raw)
        rel_warn = next((w for w in warnings if w.key == "release_threshold_a"), None)
        if rel_warn is not None:
            self.spn_release_threshold.sync_value(self._last_release)
            self.lbl_warning.setText(f"Release threshold: {rel_warn.reason}")
            self.lbl_warning.setStyleSheet(theme.status_label(theme.FAULT))
            self.lbl_warning.setVisible(True)
        else:
            old_val = self._last_release
            self._last_release = new_val
            self.lbl_warning.setVisible(False)
            self.settings_changed.emit("release_threshold_a", old_val, new_val)

    def _on_settle_edited(self) -> None:
        new_val = self.spn_settle_window.value()
        if new_val == self._last_settle:
            return
        raw = {
            "arm_threshold_a": self.spn_arm_threshold.value(),
            "release_threshold_a": self.spn_release_threshold.value(),
            "settle_window_s": new_val,
        }
        _, warnings = validate_settings(raw)
        settle_warn = next((w for w in warnings if w.key == "settle_window_s"), None)
        if settle_warn is not None:
            self.spn_settle_window.sync_value(self._last_settle)
            self.lbl_warning.setText(f"Settle window: {settle_warn.reason}")
            self.lbl_warning.setStyleSheet(theme.status_label(theme.FAULT))
            self.lbl_warning.setVisible(True)
        else:
            old_val = self._last_settle
            self._last_settle = new_val
            self.lbl_warning.setVisible(False)
            self.settings_changed.emit("settle_window_s", old_val, new_val)

    def _on_reset_defaults_clicked(self) -> None:
        defaults = CupSettings.defaults()
        arm_diff = (self._last_arm != defaults.arm_threshold_a)
        rel_diff = (self._last_release != defaults.release_threshold_a)
        settle_diff = (self._last_settle != defaults.settle_window_s)

        old_arm, old_rel, old_settle = self._last_arm, self._last_release, self._last_settle

        if arm_diff:
            self._last_arm = defaults.arm_threshold_a
            self.spn_arm_threshold.sync_value(defaults.arm_threshold_a)
        if rel_diff:
            self._last_release = defaults.release_threshold_a
            self.spn_release_threshold.sync_value(defaults.release_threshold_a)
        if settle_diff:
            self._last_settle = defaults.settle_window_s
            self.spn_settle_window.sync_value(defaults.settle_window_s)

        if arm_diff:
            self.settings_changed.emit("arm_threshold_a", old_arm, defaults.arm_threshold_a)
        if rel_diff:
            self.settings_changed.emit("release_threshold_a", old_rel, defaults.release_threshold_a)
        if settle_diff:
            self.settings_changed.emit("settle_window_s", old_settle, defaults.settle_window_s)

        self.lbl_warning.setVisible(False)
