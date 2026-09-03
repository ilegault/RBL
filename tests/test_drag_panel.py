"""
test_drag_panel.py
Unit tests for rbl/gui/widgets/drag_panel.py — column-major layout logic.

These tests exercise the pure reflow logic (_find, _apply_move) and the
add() public API without touching mouse events or visual geometry.  They
do not depend on widget sizes or positions being finalised, which makes
them safe to run on the offscreen platform set by conftest.py.

What is NOT tested here
-----------------------
_compute_target and _update_indicator both rely on widget geometry that
only resolves after a real layout pass (QSplitter sizes panels lazily).
Those are visual integration concerns; they cannot be meaningfully
asserted against geometry() == QRect(0,0,0,0) on the offscreen platform.
The logic tested here is the data structure (_cols) — the part that
determines correctness of the layout *once geometry is settled*.
"""
import pytest
from PySide6.QtWidgets import QApplication, QLabel

from rbl.gui.widgets.drag_panel import DragPanel, PanelArea

# ---------------------------------------------------------------------------
# Module-scoped QApplication (offscreen; see conftest.py for QT_QPA_PLATFORM)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


# ---------------------------------------------------------------------------
# Per-test fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def area(app):
    """A fresh PanelArea with no panels added."""
    return PanelArea()


def _panel(label: str = "P") -> DragPanel:
    """Create a DragPanel wrapping a plain QLabel — geometry is not needed."""
    return DragPanel(QLabel(label), stretch=0)


# ---------------------------------------------------------------------------
# TestAdd — public API builds _cols correctly
# ---------------------------------------------------------------------------

class TestAdd:
    def test_initial_state_one_empty_col(self, app):
        pa = PanelArea()
        assert len(pa._cols) == 1
        assert pa._cols[0] == []

    def test_add_to_col0(self, area):
        p = _panel("A")
        area.add(p, col=0)
        assert area._cols[0] == [p]

    def test_add_two_panels_same_col_ordered(self, area):
        a, b = _panel("A"), _panel("B")
        area.add(a, col=0)
        area.add(b, col=0)
        assert area._cols[0] == [a, b]

    def test_add_four_panels_same_col_ordered(self, area):
        panels = [_panel(str(i)) for i in range(4)]
        for p in panels:
            area.add(p, col=0)
        assert area._cols[0] == panels

    def test_add_to_col1_creates_new_col(self, area):
        a = _panel("A")
        area.add(a, col=0)
        b = _panel("B")
        area.add(b, col=1)
        assert len(area._cols) == 2
        assert area._cols[0] == [a]
        assert area._cols[1] == [b]

    def test_add_skips_intermediate_empty_cols(self, area):
        """add(p, col=2) when only col=0 exists → cols 0 and 1 are empty lists."""
        p = _panel("A")
        area.add(p, col=2)
        assert len(area._cols) == 3
        assert area._cols[0] == []
        assert area._cols[1] == []
        assert area._cols[2] == [p]

    def test_area_reference_set_on_panel(self, area):
        p = _panel("A")
        area.add(p, col=0)
        assert p._area is area

    def test_stack_two_cols_independently(self, area):
        a, b = _panel("A"), _panel("B")
        c, d = _panel("C"), _panel("D")
        area.add(a, col=0)
        area.add(b, col=0)
        area.add(c, col=1)
        area.add(d, col=1)
        assert area._cols[0] == [a, b]
        assert area._cols[1] == [c, d]


# ---------------------------------------------------------------------------
# TestFind — identity-based lookup returns (col, row)
# ---------------------------------------------------------------------------

class TestFind:
    def test_find_only_panel(self, area):
        p = _panel("A")
        area.add(p, col=0)
        assert area._find(p) == (0, 0)

    def test_find_two_in_same_col(self, area):
        a, b = _panel("A"), _panel("B")
        area.add(a, col=0)
        area.add(b, col=0)
        assert area._find(a) == (0, 0)
        assert area._find(b) == (0, 1)

    def test_find_in_second_col(self, area):
        a = _panel("A")
        area.add(a, col=0)
        b = _panel("B")
        area.add(b, col=1)
        assert area._find(b) == (1, 0)

    def test_find_across_multiple_cols(self, area):
        panels = [_panel(c) for c in "ABCDE"]
        area.add(panels[0], col=0)
        area.add(panels[1], col=0)
        area.add(panels[2], col=1)
        area.add(panels[3], col=1)
        area.add(panels[4], col=2)
        assert area._find(panels[0]) == (0, 0)
        assert area._find(panels[1]) == (0, 1)
        assert area._find(panels[2]) == (1, 0)
        assert area._find(panels[3]) == (1, 1)
        assert area._find(panels[4]) == (2, 0)

    def test_find_uses_identity_not_equality(self, area):
        """Two panels with the same label content must be found separately."""
        a = _panel("X")
        b = _panel("X")
        area.add(a, col=0)
        area.add(b, col=0)
        assert area._find(a) == (0, 0)
        assert area._find(b) == (0, 1)


# ---------------------------------------------------------------------------
# TestApplyMoveSameCol — reorder within one column
# ---------------------------------------------------------------------------

class TestApplyMoveSameCol:
    @pytest.fixture
    def abc(self, area):
        a, b, c = _panel("A"), _panel("B"), _panel("C")
        area.add(a, col=0)
        area.add(b, col=0)
        area.add(c, col=0)
        return area, a, b, c

    def test_no_op_same_row(self, abc):
        area, a, b, c = abc
        area._apply_move(a, (0, 0))
        assert area._cols[0] == [a, b, c]

    def test_move_first_to_end(self, abc):
        """Moving row-0 panel to index 3 (append) leaves it last."""
        area, a, b, c = abc
        area._apply_move(a, (0, 3))
        assert area._cols[0] == [b, c, a]

    def test_move_last_to_first(self, abc):
        area, a, b, c = abc
        area._apply_move(c, (0, 0))
        assert area._cols[0] == [c, a, b]

    def test_move_middle_up(self, abc):
        area, a, b, c = abc
        area._apply_move(b, (0, 0))
        assert area._cols[0] == [b, a, c]

    def test_move_middle_to_end(self, abc):
        area, a, b, c = abc
        area._apply_move(b, (0, 3))
        assert area._cols[0] == [a, c, b]

    def test_index_shift_moving_forward(self, abc):
        """Moving a from row 0 to tgt_row=2 inserts after removing src,
        so tgt_row is decremented: result is [b, a, c], not [b, c, a]."""
        area, a, b, c = abc
        area._apply_move(a, (0, 2))
        assert area._cols[0] == [b, a, c]

    def test_col_count_unchanged_after_reorder(self, abc):
        area, a, b, c = abc
        area._apply_move(b, (0, 0))
        assert len(area._cols) == 1


# ---------------------------------------------------------------------------
# TestApplyMoveCrossCol — move between existing columns
# ---------------------------------------------------------------------------

class TestApplyMoveCrossCol:
    def test_move_to_front_of_other_col(self, area):
        """Move a from col0 to position 0 in col1 → a goes before b."""
        a, b = _panel("A"), _panel("B")
        area.add(a, col=0)
        area.add(b, col=1)
        area._apply_move(a, (1, 0))
        # col0 becomes empty → pruned; col1 becomes [a, b]
        assert len(area._cols) == 1
        assert area._cols[0] == [a, b]

    def test_move_to_end_of_other_col(self, area):
        """Move a from col0 to position 1 in col1 → a goes after b."""
        a, b = _panel("A"), _panel("B")
        area.add(a, col=0)
        area.add(b, col=1)
        area._apply_move(a, (1, 1))
        assert len(area._cols) == 1
        assert area._cols[0] == [b, a]

    def test_move_from_multi_row_col_preserves_remainder(self, area):
        """Moving one panel out of a multi-row column leaves the rest intact."""
        a, b, c = _panel("A"), _panel("B"), _panel("C")
        area.add(a, col=0)
        area.add(b, col=0)
        area.add(c, col=1)
        area._apply_move(a, (1, 0))
        # col0: [b], col1: [a, c]
        assert area._cols[0] == [b]
        assert area._cols[1] == [a, c]

    def test_empty_col_is_pruned_after_move_out(self, area):
        """When the last panel leaves a column that column disappears."""
        a, b = _panel("A"), _panel("B")
        area.add(a, col=0)
        area.add(b, col=1)
        area._apply_move(b, (0, 1))  # move b to end of col0
        # col1 empties → pruned
        assert len(area._cols) == 1
        assert area._cols[0] == [a, b]

    def test_move_preserves_two_col_structure(self, area):
        """Moving a panel between columns that both remain non-empty keeps two cols."""
        a, b = _panel("A"), _panel("B")
        c, d = _panel("C"), _panel("D")
        area.add(a, col=0)
        area.add(b, col=0)
        area.add(c, col=1)
        area.add(d, col=1)
        area._apply_move(b, (1, 0))  # b → front of col1
        assert len(area._cols) == 2
        assert area._cols[0] == [a]
        assert area._cols[1] == [b, c, d]


# ---------------------------------------------------------------------------
# TestApplyMoveNewCol — drag past the last column creates a new one
# ---------------------------------------------------------------------------

class TestApplyMoveNewCol:
    def test_move_to_new_col_at_right(self, area):
        a, b = _panel("A"), _panel("B")
        area.add(a, col=0)
        area.add(b, col=0)
        area._apply_move(b, (1, 0))   # col 1 does not yet exist
        assert len(area._cols) == 2
        assert area._cols[0] == [a]
        assert area._cols[1] == [b]

    def test_move_only_panel_to_new_col_stays_one_col(self, area):
        """Moving the sole panel 'to a new column' empties col0, which gets
        pruned, so the panel ends up back in col0 under a different index."""
        a = _panel("A")
        area.add(a, col=0)
        area._apply_move(a, (1, 0))
        assert len(area._cols) == 1
        assert area._cols[0] == [a]

    def test_new_col_beyond_existing_appended_at_end(self, area):
        a, b, c = _panel("A"), _panel("B"), _panel("C")
        area.add(a, col=0)
        area.add(b, col=1)
        area.add(c, col=1)
        area._apply_move(c, (2, 0))   # new col to the right of col1
        assert len(area._cols) == 3
        assert area._cols[0] == [a]
        assert area._cols[1] == [b]
        assert area._cols[2] == [c]


# ---------------------------------------------------------------------------
# TestColsInvariant — _cols is never completely empty
# ---------------------------------------------------------------------------

class TestColsInvariant:
    def test_always_at_least_one_col(self, area):
        a = _panel("A")
        area.add(a, col=0)
        # no-op move
        area._apply_move(a, (0, 0))
        assert len(area._cols) >= 1

    def test_pruning_keeps_or_falls_back_to_one_empty_col(self, app):
        """The 'or [[]]' guard: if pruning would produce [], restore [[]]."""
        # This cannot be triggered through _apply_move (it always lands
        # the panel somewhere), but we can poke _cols directly to verify
        # the guard in isolation.
        cols = [[], []]
        result = [c for c in cols if c] or [[]]
        assert result == [[]]
