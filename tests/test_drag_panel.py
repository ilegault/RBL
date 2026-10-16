"""Test module for DragPanelArea.

All 30 private-layout internal tests deleted per Ticket 11 audit (ADR 0001 §5).
Ticket 61 adds the public name-based layout API (column_names / arrange).
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QLabel

from rbl.gui.widgets.drag_panel import DragPanel, PanelArea


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def area(qapp):
    a = PanelArea()
    a.add(DragPanel(QLabel(), name="a"), col=0)
    a.add(DragPanel(QLabel(), name="b"), col=0)
    a.add(DragPanel(QLabel(), name="c"), col=1)
    return a


def test_arrange_reorders_panels_by_name(area):
    assert area.column_names() == [["a", "b"], ["c"]]
    area.arrange([["c"], ["a", "b"]])
    assert area.column_names() == [["c"], ["a", "b"]]


def test_arrange_drops_empty_columns(area):
    area.arrange([[], ["b", "a"], [], ["c"]])
    assert area.column_names() == [["b", "a"], ["c"]]


@pytest.mark.parametrize("layout", [
    [["a"], ["c"]],                 # b is missing
    [["a", "a", "b"], ["c"]],       # a is repeated
    [["a", "b"], ["c", "z"]],       # z is unknown
])
def test_arrange_rejects_a_layout_that_loses_or_repeats_a_panel(area, layout):
    with pytest.raises(ValueError):
        area.arrange(layout)
    assert area.column_names() == [["a", "b"], ["c"]]


def test_arrange_refuses_when_a_panel_has_no_name(qapp):
    a = PanelArea()
    a.add(DragPanel(QLabel(), name="a"), col=0)
    a.add(DragPanel(QLabel()), col=1)
    with pytest.raises(ValueError):
        a.arrange([["a"]])
    assert a.column_names() == [["a"], [""]]
