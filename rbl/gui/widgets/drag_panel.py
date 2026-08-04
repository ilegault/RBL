"""
drag_panel.py
Draggable, resizable panel columns for the Overview tab.

Layout model: COLUMN-MAJOR
  _cols is a list of columns; each column is a list of DragPanels stacked
  vertically.  Columns sit side-by-side in a QSplitter so their widths can
  be resized by dragging the handle between them.

DragPanel   — wraps any QWidget and overlays a ⠿ grip handle in its top-right.

PanelArea   — the container.
  Resize columns       →  drag the handle between two columns.
  Drag ⠿ left/right   →  move panel to a different column.
  Drag ⠿ up/down      →  reorder within a column, or pick a row in another column.
  Drag ⠿ past all cols →  create a new column on the right.

The drop indicator (blue highlight) tracks the target during drag.  The layout
only rebuilds on mouse RELEASE so no widget is reparented while a button is held.

Usage
-----
    area = PanelArea()
    area.add(DragPanel(beam,    stretch=0), col=0)
    area.add(DragPanel(slits,   stretch=1), col=1)
    area.add(DragPanel(funcgen, stretch=1), col=2)
    area.add(DragPanel(hv,      stretch=1), col=2)   # stacked under funcgen
"""

from PySide6.QtCore import Qt, QPoint, QRect
from PySide6.QtGui import QPainter, QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QSplitter,
)

from rbl.gui import theme


# ---------------------------------------------------------------------------
# Drop indicator
# ---------------------------------------------------------------------------

class _DropIndicator(QWidget):
    """Semi-transparent blue rectangle shown over the would-be drop target."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 120, 215, 55))
        pen = p.pen()
        pen.setColor(QColor(0, 120, 215, 230))
        pen.setWidth(2)
        p.setPen(pen)
        p.drawRect(self.rect().adjusted(1, 1, -1, -1))


# ---------------------------------------------------------------------------
# Grip handle
# ---------------------------------------------------------------------------

class _GripHandle(QLabel):
    """⠿ icon — drag it to reorder the parent DragPanel."""

    def __init__(self, drag_panel: "DragPanel"):
        super().__init__("⠿", drag_panel)
        self._dp = drag_panel
        self.setFixedSize(22, 22)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(
            f"color: {theme.NEUTRAL}; font-size: 15px;"
            " background: rgba(200, 200, 200, 180);"
            " border: 1px solid #aaa; border-radius: 3px;"
        )
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setToolTip(
            "Drag left/right  →  move to a different column\n"
            "Drag up/down     →  reorder within this column\n"
            "Drag right past all columns  →  new column\n\n"
            "Drag the thin handle between columns to resize them."
        )
        self._pressing = False

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self._pressing = True

    def mouseMoveEvent(self, ev):
        if self._pressing:
            area = self._dp._area
            if area is not None:
                area._on_drag(self._dp, ev.globalPosition().toPoint())

    def mouseReleaseEvent(self, ev):
        if self._pressing and ev.button() == Qt.MouseButton.LeftButton:
            self._pressing = False
            area = self._dp._area
            if area is not None:
                area._on_drop(self._dp, ev.globalPosition().toPoint())


# ---------------------------------------------------------------------------
# DragPanel
# ---------------------------------------------------------------------------

class DragPanel(QWidget):
    """Thin wrapper around *content* with an overlaid ⠿ grip handle.

    Parameters
    ----------
    content : QWidget
        The widget to display inside the panel.
    stretch : int
        Horizontal stretch factor applied to the *column* this panel belongs
        to.  The column's stretch = max(p.stretch for p in column).
        stretch=0  →  column does not grow when the splitter is resized.
        stretch=1  →  column grows proportionally.
    """

    def __init__(self, content: QWidget, stretch: int = 0, parent=None):
        super().__init__(parent)
        self.stretch = stretch
        self._area: "PanelArea | None" = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(content)

        self._grip = _GripHandle(self)
        self._grip.show()
        self._grip.raise_()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        g = self._grip
        g.move(self.width() - g.width() - 4, 4)
        g.raise_()


# ---------------------------------------------------------------------------
# PanelArea — column-major, splitter-resizable 2-D container
# ---------------------------------------------------------------------------

class PanelArea(QWidget):
    """Columns of stacked panels; column widths are draggable via a QSplitter.

    _cols[c][r] is the DragPanel at column c, row r.

    Drag the ⠿ grip left/right to move between columns, up/down to
    reorder within a column, or right past everything to add a new column.
    Drag the vertical handle between columns to resize them.
    """

    # Stylesheet shared by both the horizontal column splitter and the
    # vertical panel splitters inside each column.  Qt applies the same
    # rule regardless of orientation; only the handle's visible edge
    # differs (left/right vs top/bottom), so we keep one style string.
    _SPLITTER_STYLE = (
        "QSplitter::handle {"
        f"  background: {theme.TRACK};"
        f"  border: 1px solid {theme.TRACK_EDGE};"
        "}"
        "QSplitter::handle:hover  { background: #0078d7; }"
        "QSplitter::handle:pressed{ background: #005a9e; }"
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cols: list[list[DragPanel]] = [[]]

        # Outer layout: just the horizontal splitter + indicator overlay
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self._splitter.setChildrenCollapsible(False)
        self._splitter.setHandleWidth(8)
        self._splitter.setStyleSheet(self._SPLITTER_STYLE)
        outer.addWidget(self._splitter)

        # One QWidget+QVBoxLayout per column, rebuilt on every drop
        self._col_widgets: list[QWidget] = []

        # Drop indicator is always a direct child of PanelArea so it can be
        # placed in PanelArea-local coordinates regardless of splitter layout
        self._indicator = _DropIndicator(self)

    # ---- Public API --------------------------------------------------------

    def add(self, panel: DragPanel, col: int = 0) -> None:
        """Append *panel* to the bottom of column *col*."""
        panel._area = self
        while len(self._cols) <= col:
            self._cols.append([])
        self._cols[col].append(panel)
        self._rebuild()

    # ---- Drag callbacks (called by _GripHandle) ----------------------------

    def _on_drag(self, panel: DragPanel, global_pos: QPoint) -> None:
        sp_local = self._splitter.mapFromGlobal(global_pos)
        target   = self._compute_target(panel, sp_local)
        self._update_indicator(target, panel)

    def _on_drop(self, panel: DragPanel, global_pos: QPoint) -> None:
        self._indicator.hide()
        sp_local = self._splitter.mapFromGlobal(global_pos)
        self._apply_move(panel, self._compute_target(panel, sp_local))

    # ---- Target computation (in splitter-local coordinates) ----------------

    def _find(self, panel: DragPanel) -> tuple[int, int]:
        for c, col in enumerate(self._cols):
            for r, p in enumerate(col):
                if p is panel:
                    return c, r
        return 0, 0

    def _compute_target(
        self, dragged: DragPanel, sp_local: QPoint
    ) -> tuple[int, int]:
        """Return (col, row) for the drop position (splitter-local coords).

        col == len(self._cols)  means "new column to the right".
        """
        # --- Which column does the cursor X fall into? ---
        tgt_col = len(self._cols)       # default: new column at right
        for c, cw in enumerate(self._col_widgets):
            if sp_local.x() <= cw.geometry().right():
                tgt_col = c
                break

        if tgt_col >= len(self._cols) or not self._cols[tgt_col]:
            return (tgt_col, 0)

        cw  = self._col_widgets[tgt_col]
        col = self._cols[tgt_col]

        # Cursor Y relative to this column widget
        col_y = sp_local.y() - cw.geometry().top()

        tgt_row = len(col)              # default: append at bottom
        for r, p in enumerate(col):
            if p is dragged:
                continue
            # Cross the midpoint → insert before this panel
            if col_y <= p.geometry().center().y():
                tgt_row = r
                break

        return (tgt_col, tgt_row)

    # ---- Drop indicator (geometry in PanelArea-local coords) ---------------

    def _sp_rect_to_area(self, cw: QWidget, panel_rect: QRect) -> QRect:
        """Convert a rect that is (panel-relative inside cw) to PanelArea coords."""
        # cw.geometry() is splitter-local; panel_rect is cw-local
        sp_tl = cw.geometry().topLeft() + panel_rect.topLeft()
        area_tl = self._splitter.mapTo(self, sp_tl)
        return QRect(area_tl, panel_rect.size())

    def _update_indicator(
        self, target: tuple[int, int], dragged: DragPanel
    ) -> None:
        tgt_col, tgt_row = target

        # New column to the right of everything
        if tgt_col >= len(self._cols) or not self._col_widgets:
            if self._col_widgets:
                last   = self._col_widgets[-1]
                sp_x   = last.geometry().right() + self._splitter.handleWidth()
                sp_tl  = QPoint(sp_x, last.geometry().top())
                area_tl = self._splitter.mapTo(self, sp_tl)
                self._show_indicator(QRect(area_tl,
                                           last.geometry().size()))
            else:
                self._show_indicator(QRect(0, 0, 80, self.height()))
            return

        cw  = self._col_widgets[tgt_col]
        col = self._cols[tgt_col]

        others = [p for p in col if p is not dragged]
        if not others:
            # Only the dragged panel would remain → highlight entire column
            self._show_indicator(
                self._sp_rect_to_area(cw, QRect(QPoint(0, 0), cw.size())))
            return

        idx          = min(tgt_row, len(others) - 1)
        target_panel = others[idx]
        self._show_indicator(self._sp_rect_to_area(cw, target_panel.geometry()))

    def _show_indicator(self, rect: QRect) -> None:
        self._indicator.setGeometry(rect)
        self._indicator.show()
        self._indicator.raise_()

    # ---- Apply move --------------------------------------------------------

    def _apply_move(
        self, panel: DragPanel, target: tuple[int, int]
    ) -> None:
        src_col, src_row = self._find(panel)
        tgt_col, tgt_row = target

        if src_col == tgt_col and src_row == tgt_row:
            return

        self._cols[src_col].pop(src_row)

        # Removal shifts later indices in the same column
        if tgt_col == src_col and src_row < tgt_row:
            tgt_row -= 1

        if tgt_col >= len(self._cols):
            self._cols.append([])

        self._cols[tgt_col].insert(tgt_row, panel)
        self._cols = [c for c in self._cols if c] or [[]]
        self._rebuild()

    # ---- Layout rebuild ----------------------------------------------------

    def _rebuild(self) -> None:
        """Re-parent panels into new column containers, rebuild the splitter.

        Called only after a drop (mouse already released).
        """
        # Step 1: temporarily reparent every panel to self so that deleting
        #         the old column widgets does not take the panels with them.
        for p in (p for col in self._cols for p in col):
            p.setParent(self)

        # Step 2: remove old column widgets from the splitter.
        #         setParent(None) detaches from the splitter; deleteLater()
        #         queues destruction after the event handler returns.
        for cw in self._col_widgets:
            cw.setParent(None)
            cw.deleteLater()
        self._col_widgets.clear()

        # Step 3: build a vertical QSplitter for each non-empty column and
        #         add it to the horizontal splitter.  Each panel inside the
        #         column splitter is individually height-resizable.
        for i, col in enumerate(self._cols):
            if not col:
                continue

            cw = QSplitter(Qt.Orientation.Vertical)
            cw.setChildrenCollapsible(False)
            cw.setHandleWidth(8)
            cw.setStyleSheet(self._SPLITTER_STYLE)

            for r, panel in enumerate(col):
                panel.setParent(cw)
                cw.addWidget(panel)
                cw.setStretchFactor(r, 1)   # equal share by default
                panel.show()

            self._splitter.addWidget(cw)
            self._col_widgets.append(cw)

        # Step 4: apply horizontal stretch factors (column width behaviour).
        for i, col in enumerate(self._cols):
            self._splitter.setStretchFactor(i, max(p.stretch for p in col))

        # Step 5: keep the indicator as a direct child of PanelArea, on top.
        self._indicator.setParent(self)
        self._indicator.raise_()
