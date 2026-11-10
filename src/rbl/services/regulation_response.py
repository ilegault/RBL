"""
regulation_response.py
Wires RegulationMonitor's `fault_detected` signal to the Section 7.4
response: stop commanding the channel, ask the operator which front-panel
LED is lit, and log the answer with full operating conditions to the
persistent trip history.

Kept separate from RegulationMonitor itself so the debounce/ramp-coupling
math (pure-ish, Qt-signal-only) stays independent of the GUI dialog and the
on-disk log — this is the thin glue between them, following the pattern
`CalibrationRunner`/`AmpDrive` already use to keep hardware commands, policy,
and persistence in separate objects.

WHY A QOBJECT
-------------
A plain Python object connected to a signal only survives as long as
something holds a Python reference to it; construct one inline without
assigning it to a variable (`RegulationResponder(monitor, ...)` with no
`self._responder = ...`) and it is garbage-collected immediately, silently
dropping the connection the moment the fault it exists to catch actually
happens. Subclassing QObject and accepting a `parent` lets Qt's own
parent-child ownership keep it alive instead, the same safety net every
other long-lived service in this app (CalibrationRunner, RampEngine,
RegulationMonitor) already gets for free.

TWO MODES (docs/adr/0006-amplifier-faults-are-observed-not-stopped-during-a-session.md)
----------------------------------------------------------------------------------
`stop` (default) is the calibration/characterization response above. `observe`
is for a running session: the amplifiers protect themselves and the application
is the witness, so a confirmed fault never commands the channel off and never
opens a dialog (a modal box in a 12-hour irradiation would block the GUI
thread). It writes the trip history with `mode: "observe"`, asks the spike
recorder to save the 10 s leading up to an `amp_off`, and emits `warning(str)`
for the Overview warnings list.
"""
import logging

from PySide6.QtCore import QObject, Signal

from rbl.services import trip_history

log = logging.getLogger(__name__)


class RegulationResponder(QObject):
    warning = Signal(str)   # observe mode only: one line for the Overview warnings list

    def __init__(self, monitor, amp_drive, get_operating_conditions,
                 dialog_factory=None, parent_widget=None, parent=None,
                 mode: str = "stop", spike_recorder=None):
        """
        monitor: RegulationMonitor — its fault_detected signal drives this.
        amp_drive: AmpDrive — output_off(label) is called on a confirmed fault.
        get_operating_conditions: callable(label) -> dict, assembled by the
            caller since only it knows the run's commanded kV, frequency,
            waveform, chamber pressure, etc. at the moment of the fault.
            May return None/{} if nothing is available.
        dialog_factory: callable(label, state, reason, parent) -> a dialog
            object with .exec() and .selected_reason() — overridable so this
            can be tested without a real Qt dialog blocking on user input.
        parent_widget: passed to the dialog as ITS Qt parent (for placement).
        parent: this object's own QObject parent (for lifetime — see the
            module docstring's "why a QObject" note). Independent of
            parent_widget: the dialog's parent decides where it appears, this
            object's parent decides how long it survives.
        mode: "stop" (default) or "observe"; see the module docstring.
        spike_recorder: observe mode only; save_pre_event(label) is called on
            an amp_off fault.
        """
        if mode not in ("stop", "observe"):
            raise ValueError(f"unknown regulation response mode: {mode!r}")
        super().__init__(parent)
        self._mode = mode
        self._spike_recorder = spike_recorder
        self._monitor = monitor
        self._amp_drive = amp_drive
        self._get_conditions = get_operating_conditions
        self._dialog_factory = dialog_factory
        self._parent = parent_widget
        monitor.fault_detected.connect(self.handle_fault)

    def handle_fault(self, label: str, state: str, reason: str) -> None:
        if self._mode == "observe":
            self._observe_fault(label, state, reason)
            return
        try:
            self._amp_drive.output_off(label)
        except Exception as e:
            log.error("regulation response: failed to stop %s: %s", label, e)

        operator_answer = None
        if self._dialog_factory is not None:
            dialog = self._dialog_factory(label, state, reason, parent=self._parent)
            dialog.exec()
            operator_answer = dialog.selected_reason()

        record = {}
        try:
            conditions = self._get_conditions(label)
            if conditions:
                record.update(conditions)
        except Exception as e:
            log.error("regulation response: get_operating_conditions failed: %s", e)

        record.update(label=label, state=state, reason=reason,
                       operator_answer=operator_answer)
        trip_history.append_trip(record)

    def _observe_fault(self, label: str, state: str, reason: str) -> None:
        """Witness a confirmed fault: log it and warn, command nothing."""
        record = {}
        try:
            conditions = self._get_conditions(label)
            if conditions:
                record.update(conditions)
        except Exception as e:
            log.error("regulation response: get_operating_conditions failed: %s", e)
        record.update(label=label, state=state, reason=reason, mode="observe")
        trip_history.append_trip(record)

        if state == "amp_off" and self._spike_recorder is not None:
            try:
                self._spike_recorder.save_pre_event(label)
            except Exception as e:
                log.error("regulation response: save_pre_event failed for %s: %s", label, e)

        self.warning.emit(f"{label}: {state} - {reason}")
