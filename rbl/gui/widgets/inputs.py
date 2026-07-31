"""
inputs.py
The numeric entry box every screen in this app uses, and the row that labels it.

WHY THIS EXISTS — the "auto-complete while typing" bug.

A plain QDoubleSpinBox re-interprets its text on EVERY keystroke
(`keyboardTracking` defaults to True) and emits `valueChanged` each time. On a
screen where that value is round-tripped through a shared model — which is
every setpoint on the Overview and Function Generators tabs — the trip back
calls `setValue()`, and `setValue()` REWRITES THE TEXT UNDER THE CURSOR.

Typing "0.514" into a frequency box whose minimum is 0.0001 played out like
this before this class existed:

    keystroke "0"  -> text "0"      -> valueChanged(0.0001)  [clamped to min]
                                    -> setpoint model changes
                                    -> sync writes back setValue(0.0001)
                                    -> box now reads "0.0001", cursor at the end
    keystrokes ".514"               -> "0.0001.514" — rejected, and the number
                                       you meant is gone

Same story for any sub-integer entry anywhere: the first digit is a complete,
valid number, so the box commits it and reformats before you finish. Turning
`keyboardTracking` off is the fix: the box still shows exactly what you type,
but it does not tell anyone a value changed until you press Enter, Tab, or
click away. Arrow keys and the up/down buttons still commit immediately,
because those are complete gestures.

`sync_value()` is the second half of the fix, for the boxes that ALSO receive
values from a live sync: even a legitimate outside update must not overwrite a
box whose text the operator is part-way through. It holds the write while the
box has focus and settles it on focus-out — unless the operator's own edit
committed on the way out, in which case theirs wins.

UNITS LIVE OUTSIDE THE BOX. `setSuffix(" mm")` puts the unit inside the
editable text, which means the cursor can land after it, a select-all-and-type
wipes it, and the validator has to re-parse it on every keystroke. `unit_row()`
puts the unit in a QLabel beside the box instead, where it cannot be typed
into. Nothing in this app should call setSuffix().
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDoubleSpinBox, QHBoxLayout, QLabel, QWidget,
)


class QuietDoubleSpinBox(QDoubleSpinBox):
    """A QDoubleSpinBox that does not commit half-typed numbers.

    Drop-in replacement — same API, same signals. The only differences are
    that `valueChanged` fires when the edit is FINISHED rather than on each
    keystroke, and that `sync_value()` exists for writes coming from a shared
    model.
    """

    # A pending sync that arrived while the operator was typing, held until
    # focus leaves so it cannot eat the number being entered.
    #
    # Declared on the CLASS, not only in __init__: Qt delivers a final
    # focus-out while a widget is being destroyed, by which point the instance
    # dict may already be gone, and an AttributeError raised from inside an
    # event handler during teardown is both noisy and untraceable.
    _pending_sync = None

    def __init__(self, parent=None):
        super().__init__(parent)
        # The fix itself. Everything else in this class supports it.
        self.setKeyboardTracking(False)
        self._pending_sync = None

    def focusInEvent(self, event):
        """Select the whole value on focus, so typing REPLACES it.

        Without this, clicking into a box that reads "10.0000" and typing
        "0.514" leaves you with some splice of the two.
        """
        super().focusInEvent(event)
        self.selectAll()

    def focusOutEvent(self, event):
        """Leave the box: commit what was typed, then settle any held sync.

        Order matters and so does the guard. Losing focus is what makes
        QAbstractSpinBox interpret the typed text, so `super()` below is where
        the operator's own number finally commits. A sync that was deferred
        while they were typing is stale the moment that happens — applying it
        anyway would undo the edit at the instant it took effect, which is the
        original bug wearing a different hat.
        """
        pending = self._pending_sync
        self._pending_sync = None
        before = self.value()
        super().focusOutEvent(event)
        if pending is not None and self.value() == before:
            self.setValue(pending)

    def sync_value(self, value: float):
        """Write a value that came from somewhere else (a shared model, a
        readback), deferring it if the operator is mid-edit in this box."""
        if self.hasFocus():
            self._pending_sync = value
            return
        self.setValue(value)


def unit_row(widget: QWidget, unit: str, spacing: int = 4,
             font_size: int = 0) -> QHBoxLayout:
    """`widget` with its unit in a label BESIDE it, not inside it.

    Returns a layout, ready to drop into a form row. See the module docstring
    for why the unit never goes in the box itself.
    """
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(spacing)
    row.addWidget(widget, stretch=1)
    lbl = QLabel(unit)
    lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    if font_size:
        lbl.setStyleSheet(f"font-size: {font_size}px;")
    row.addWidget(lbl)
    # Hang the label off the widget so callers that hide a field can hide its
    # unit with it. A unit label is part of its box; left behind, a stray "Hz"
    # floats in an empty row (which is exactly what DC mode used to do on the
    # Function Generators tab).
    widget.unit_label = lbl
    return row
