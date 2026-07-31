"""
QuietDoubleSpinBox: the numeric entry box that does not commit half-typed
numbers.

The bug these tests pin down: a plain QDoubleSpinBox re-interprets its text on
every keystroke and emits valueChanged each time. Where that value is
round-tripped through a shared model — every setpoint on the Overview and
Function Generators tabs — the trip back calls setValue(), which rewrites the
text under the cursor. Typing "0.514" got as far as "0" before the box decided
that was a complete number, clamped it, reformatted it, and left the operator
typing into the wreckage.

So these tests are about WHEN a value is published and WHOSE value wins, not
about arithmetic.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pathlib

import pytest

pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtWidgets import (
    QApplication, QPushButton, QVBoxLayout, QWidget,
)

from rbl.gui.widgets.inputs import QuietDoubleSpinBox, unit_row


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def spin(qapp):
    """A spinbox inside a SHOWN window, starting out unfocused.

    Focus is the hinge of half of this behaviour and an un-shown widget never
    gets any, so the window is real. The sibling button is where focus goes
    when a test tabs away — synthesising a QFocusEvent would leave the box
    still holding focus, which is not the state being tested. And because
    showing a window focuses its first focusable child, focus is parked on that
    button up front: a test about typing has to start from "not being typed
    in".
    """
    holder = QWidget()
    lay = QVBoxLayout(holder)
    s = QuietDoubleSpinBox()
    s.setRange(0.0001, 25_000_000.0)
    s.setDecimals(4)
    s.setValue(10.0)
    lay.addWidget(s)
    elsewhere = QPushButton("elsewhere")
    lay.addWidget(elsewhere)
    holder.show()
    qapp.processEvents()
    elsewhere.setFocus()
    qapp.processEvents()
    s._test_holder = holder        # keep the window alive for the test
    s._test_elsewhere = elsewhere
    return s


def _focus_in(spin, qapp):
    spin.setFocus()
    qapp.processEvents()
    assert spin.hasFocus()


def _focus_out(spin, qapp):
    spin._test_elsewhere.setFocus()
    qapp.processEvents()
    assert not spin.hasFocus()


def _type(spin, text: str):
    """Replace the box's contents with *text*, keystroke by keystroke.

    Driving the line edit directly puts the validator through exactly what a
    typing operator puts it through, which is where the bug lived.
    """
    editor = spin.lineEdit()
    editor.selectAll()
    editor.del_()
    for ch in text:
        editor.insert(ch)


# ---- The reported bug --------------------------------------------------------

def test_typing_a_sub_integer_frequency_is_not_committed_digit_by_digit(spin, qapp):
    """"0.514" must arrive as 0.514, not as 0, then 0.5, then 0.51."""
    _focus_in(spin, qapp)
    seen = []
    spin.valueChanged.connect(seen.append)
    _type(spin, "0.514")
    assert seen == []            # nothing published while still typing
    _focus_out(spin, qapp)
    assert spin.value() == pytest.approx(0.514)
    assert seen == [pytest.approx(0.514)]


def test_leading_zero_is_not_clamped_to_the_minimum_mid_word(spin, qapp):
    """The old box saw "0", clamped it to the 0.0001 minimum, and rewrote the
    text — which is what ate the rest of the number."""
    _focus_in(spin, qapp)
    _type(spin, "0")
    assert spin.lineEdit().text() == "0"


def test_arrow_keys_still_commit_immediately(spin):
    """Stepping is a complete gesture, unlike a half-typed number."""
    seen = []
    spin.valueChanged.connect(seen.append)
    spin.stepBy(1)
    assert seen and seen[-1] == pytest.approx(spin.value())


# ---- Syncs from a shared model ----------------------------------------------

def test_sync_value_writes_straight_through_when_not_being_edited(spin):
    spin.sync_value(4.25)
    assert spin.value() == pytest.approx(4.25)


def test_sync_value_defers_while_the_box_has_focus(spin, qapp):
    """An update from the other tab must not reformat a number being typed."""
    _focus_in(spin, qapp)
    _type(spin, "0.514")
    spin.sync_value(999.0)
    assert spin.lineEdit().text() == "0.514"


def test_the_operators_own_edit_beats_a_deferred_sync(spin, qapp):
    """A sync held during typing is stale the moment the edit commits.

    Applying it on the way out would undo the number at the instant it took
    effect — the original bug in a different costume.
    """
    _focus_in(spin, qapp)
    _type(spin, "0.514")
    spin.sync_value(999.0)       # arrives from elsewhere, mid-edit
    _focus_out(spin, qapp)
    assert spin.value() == pytest.approx(0.514)


def test_a_deferred_sync_applies_when_nothing_was_typed(spin, qapp):
    """Held, not dropped: an untouched box still has to end up current."""
    _focus_in(spin, qapp)
    spin.sync_value(3.5)
    assert spin.value() == pytest.approx(10.0)
    _focus_out(spin, qapp)
    assert spin.value() == pytest.approx(3.5)


# ---- Units live outside the box ---------------------------------------------

def test_no_spinbox_in_the_app_carries_its_unit_as_a_suffix():
    """A suffix is part of the editable text: the cursor can land behind it and
    a select-all-and-retype takes it with it."""
    offenders = [
        path.name
        for path in pathlib.Path("rbl").rglob("*.py")
        # inputs.py is where the rule is written down, so it names the call it
        # forbids; anywhere else, naming it means using it.
        if path.name != "inputs.py" and "setSuffix(" in path.read_text()
    ]
    assert offenders == []


def test_every_spinbox_in_the_app_is_a_quiet_one():
    """One bare QDoubleSpinBox left behind is one box with the bug back."""
    offenders = [
        path.name
        for path in pathlib.Path("rbl").rglob("*.py")
        if path.name != "inputs.py" and "QDoubleSpinBox(" in path.read_text()
    ]
    assert offenders == []


def test_unit_row_puts_the_unit_beside_the_box(spin):
    row = unit_row(spin, "Hz")
    assert row.count() == 2
    assert row.itemAt(0).widget() is spin
    assert row.itemAt(1).widget().text() == "Hz"
    assert spin.suffix() == ""
