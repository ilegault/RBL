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

THE MOUSE WHEEL DOES NOT EDIT ANYTHING. Qt's default is that a wheel event
over a spin box or a combo box changes its value — focused or not, just from
the pointer being over it. Scrolling a tab is a READING gesture, so that turns
"scroll past a control" into "silently re-command it", and on these screens the
controls in question are slit targets in mm, jog speeds, drive amplitudes in
kV-per-plate, and the unit dropdowns that decide how the box beside them is
read. Nothing on screen announces the change, and on a setpoint that is
Applied later there is no moment where it looks wrong.

Both classes below therefore ignore the wheel, and both `ignore()` rather than
swallow it so the gesture still reaches a scroll area above them. Values change
by typing, by the arrow keys, or by a spin box's own up/down buttons — all of
which are deliberate, aimed at one control, and already committed correctly by
the keyboardTracking fix above.
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QHBoxLayout, QLabel, QWidget,
)


class NoScrollComboBox(QComboBox):
    """A QComboBox that cannot be changed with the mouse wheel.

    Qt's default selects the next or previous item on a wheel notch, focused
    or not. The dropdowns here pick units (cps vs mm/s, counts vs mm), waveform
    shape, and which instrument a command is addressed to, so a wheel that
    lands on one is not merely cosmetic: the next number typed into the box
    beside it is then interpreted in whatever unit the wheel chose.

    Selecting an item stays a two-part deliberate gesture — click to open,
    click to choose — and the wheel is left to do the one thing it should do
    over a long tab, which is scroll it.
    """

    def wheelEvent(self, event):
        # ignore(), not accept(): an ignored wheel event propagates to the
        # parent, so a dropdown inside a scroll area scrolls the AREA rather
        # than eating the gesture. Swallowing it would trade a surprise for a
        # dead spot.
        event.ignore()


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

    def wheelEvent(self, event):
        """The wheel does not edit a number. See the module docstring.

        Qt's default steps the value by singleStep per notch — and unlike the
        half-typed-number problem this class was built for, that change is
        COMMITTED: it goes straight out as valueChanged, into the shared
        setpoint model, and from there onto the other tab showing the same
        number. A slit target nudged 0.1 mm on the way past looks like a
        number somebody typed, and the next Move goes there.

        Ignored rather than swallowed, so the scroll still reaches whatever is
        above this box. The arrows and the up/down buttons still step it —
        those are aimed at one control on purpose.
        """
        event.ignore()

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
