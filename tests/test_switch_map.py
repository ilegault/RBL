"""Pure edge-finding and summary formatting for the switch map."""
from rbl.hardware.switch_map import (
    SwitchEdges,
    SwitchMapRow,
    find_switch_edges,
    switch_map_summary,
)


def _row(
    step: int,
    direction: str,
    pos: int,
    *,
    home: bool = False,
    reverse: bool = False,
    forward: bool = False,
) -> SwitchMapRow:
    return SwitchMapRow(
        step=step,
        direction=direction,
        position_counts=pos,
        home=home,
        reverse=reverse,
        forward=forward,
    )


def test_find_switch_edges_from_rows():
    shared_rows = [
        _row(1, "in", -1, home=False, reverse=False),
        _row(2, "in", -2, home=False, reverse=False),
        _row(3, "in", -3, home=True, reverse=True),
        _row(4, "out", -2, home=True, reverse=True),
        _row(5, "out", 5, home=True, reverse=True),
        _row(6, "out", 6, home=False, reverse=False),
        _row(7, "out", 7, home=False, reverse=False),
    ]
    assert find_switch_edges(shared_rows) == SwitchEdges(-3, -3, 6, 6)

    home_only_rows = [
        _row(1, "in", -2, home=False, reverse=False),
        _row(2, "in", -3, home=True, reverse=False),
        _row(3, "in", -4, home=True, reverse=False),
        _row(4, "out", 5, home=True, reverse=False),
        _row(5, "out", 6, home=False, reverse=False),
    ]
    edges = find_switch_edges(home_only_rows)
    assert edges.home_trip == -3
    assert edges.home_release == 6
    assert edges.reverse_trip is None
    assert edges.reverse_release is None


def test_switch_map_summary_line():
    assert (
        switch_map_summary("A", SwitchEdges(-3, None, 6, None), "100-count cap")
        == "A: in — home trips at -3, reverse trips at never (gap —); "
        "out — home releases at 6, reverse releases at never; "
        "inward stop: 100-count cap"
    )
