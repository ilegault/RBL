"""SwitchMapRoutine driven against the simulated slit axis."""
import pytest

from rbl.hardware import galil_workers
from rbl.hardware.galil_workers import SwitchMapRoutine
from rbl.hardware.switch_map import SwitchEdges, find_switch_edges
from tests.sim_slit import SimGalil


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(galil_workers.time, "sleep", lambda _s: None)


def test_switch_map_finds_a_shared_switch():
    sim = SimGalil(trip_at=-3, release_at=6, start_at=2000)
    lines: list[str] = []

    ok, msg, rows = SwitchMapRoutine(sim, "A", progress=lines.append).run()

    assert ok is True
    assert find_switch_edges(rows) == SwitchEdges(-3, -3, 6, 6)
    in_rows = [r for r in rows if r.direction == "in"]
    assert in_rows[-1].reverse is True
    assert "A: in — home trips at -3 cts" in lines
    assert "inward stop: reverse limit" in msg
    assert sim.slits["A"].zeroed_at == []


def test_switch_map_stops_at_the_cap_without_a_reverse_limit():
    sim = SimGalil(
        trip_at=-3,
        release_at=6,
        reverse_trip_at=-1000,
        reverse_release_at=-990,
    )

    ok, msg, rows = SwitchMapRoutine(sim, "A").run()

    assert ok is True
    in_rows = [r for r in rows if r.direction == "in"]
    assert in_rows[-1].position_counts == -103
    assert "reverse trips at never" in msg
    assert "inward stop: 100-count cap" in msg
