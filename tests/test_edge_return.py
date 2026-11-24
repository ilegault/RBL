"""Pure-math tests for the edge-return analysis (ticket 129). No fakes needed."""
import pytest

from rbl.hardware.edge_return import (
    analyse_edge_returns,
    is_lost_step_jump,
    summary_line,
)


def test_lost_step_jump_rule():
    for delta in (8, -8, 6, 10, 16, -15):
        assert is_lost_step_jump(delta) is True, delta
    for delta in (0, 1, 3, 5, 12, -4):
        assert is_lost_step_jump(delta) is False, delta


def test_analysis_of_a_steady_edge():
    s = analyse_edge_returns([1000, 1001, 999, 1000, 1001])
    assert s.n == 5
    assert s.deltas_counts == (1, -2, 1, 1)
    assert s.total_drift_counts == 1
    assert s.spread_counts == 2
    assert s.lost_step_jumps == ()
    assert s.std_counts == pytest.approx(0.748, abs=1e-3)


def test_analysis_finds_a_lost_step_jump():
    s = analyse_edge_returns([1000, 1000, 1008, 1008, 1009])
    assert s.lost_step_jumps == (1,)
    assert s.total_drift_counts == 9


def test_summary_line_text():
    s = analyse_edge_returns([1000, 1001, 999, 1000, 1001])
    assert summary_line(s, "A", 58) == (
        "A [X+] @ 58 cps: n=5, spread 2 cts (3.2 µm), drift +1 cts, "
        "σ 0.75 cts, lost-step jumps: 0"
    )


def test_summary_line_with_a_label():
    s = analyse_edge_returns([1000, 1001, 999, 1000, 1001])
    assert summary_line(s, "A", 58, "stop") == (
        "A [X+] @ 58 cps, stop: n=5, spread 2 cts (3.2 µm), drift +1 cts, "
        "σ 0.75 cts, lost-step jumps: 0"
    )
    assert summary_line(s, "A", 58) == (
        "A [X+] @ 58 cps: n=5, spread 2 cts (3.2 µm), drift +1 cts, "
        "σ 0.75 cts, lost-step jumps: 0"
    )


def test_analysis_needs_two_readings():
    for readings in ([], [5]):
        with pytest.raises(ValueError):
            analyse_edge_returns(readings)
