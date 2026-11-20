"""
Edge-return routine: repeatability measurement for slit homing without
redefining zero.

Each cycle backs out away from home, drives onto the home switch at the
approach speed, creeps out one count at a time to the release edge, and
records that edge without calling DP=0.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from rbl.hardware import galil_workers
from rbl.hardware.galil_workers import EdgeReturnRoutine, EdgeReturnWorker
from tests.sim_slit import SimGalil


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(galil_workers.time, "sleep", lambda *a, **k: None)


def test_edge_return_collects_one_reading_per_cycle():
    sim = SimGalil(trip_at=-3, release_at=6, start_at=2000)

    ok, msg, readings = EdgeReturnRoutine(
        sim, "A", 58, cycles=3, out_counts=2000
    ).run()

    assert ok is True
    assert msg == "A: 3 edge returns at 58 cps"
    assert readings == [6, 6, 6]


def test_edge_return_never_defines_zero():
    sim = SimGalil(trip_at=-3, release_at=6, start_at=2000)

    ok, _, _ = EdgeReturnRoutine(sim, "A", 58, cycles=3, out_counts=2000).run()

    assert ok is True
    assert sim.slits["A"].zeroed_at == []
    assert not any(
        name in ("define_zero", "define_zero_multi", "begin_home")
        for name, _ in sim.calls
    )


def test_edge_return_cancel_keeps_the_readings_so_far():
    sim = SimGalil(trip_at=-3, release_at=6, start_at=2000)
    edges_seen = 0

    def on_progress(text: str) -> None:
        nonlocal edges_seen
        if "edge at" in text:
            edges_seen += 1

    ok, msg, readings = EdgeReturnRoutine(
        sim, "A", 58, cycles=3, out_counts=2000,
        progress=on_progress,
        cancelled=lambda: edges_seen >= 1,
    ).run()

    assert ok is False
    assert "cancelled after 1 cycles" in msg
    assert len(readings) == 1
    assert readings == [6]


def test_edge_return_worker_emits_done_with_readings(qapp):
    sim = SimGalil(trip_at=-3, release_at=6, start_at=2000)

    worker = EdgeReturnWorker(sim, "A", 58, cycles=3, out_counts=2000)
    emissions = []
    worker.done.connect(lambda ok, msg, readings: emissions.append((ok, msg, readings)))

    worker.run()

    assert len(emissions) == 1
    ok, msg, readings = emissions[0]
    assert ok is True
    assert msg == "A: 3 edge returns at 58 cps"
    assert readings == [6, 6, 6]


def test_edge_return_records_release_edges():
    sim = SimGalil(trip_at=-3, release_at=6, start_at=2000)

    ok, msg, readings = EdgeReturnRoutine(
        sim, "A", 58, cycles=3, out_counts=2000
    ).run()

    assert ok is True
    assert readings == [6, 6, 6]
    assert sim.slits["A"].zeroed_at == []


def test_edge_return_shows_a_lost_step():
    sim = SimGalil(trip_at=-3, release_at=6, start_at=2000, slip_on_jog={2: 8})

    ok, _, readings = EdgeReturnRoutine(
        sim, "A", 58, cycles=3, out_counts=2000
    ).run()

    assert ok is True
    assert readings == [6, -2, -2]


def test_edge_return_fails_when_the_switch_never_releases():
    sim = SimGalil(trip_at=-3, release_at=1000, start_at=2000)

    ok, msg, readings = EdgeReturnRoutine(
        sim, "A", 58, cycles=3, out_counts=2000
    ).run()

    assert ok is False
    assert "did not release within 400 counts" in msg
    assert readings == []


def test_edge_return_moves_out_at_the_approach_speed():
    for speed in (58, 1800):
        sim = SimGalil(trip_at=-3, release_at=6, start_at=2000)
        ok, msg, _ = EdgeReturnRoutine(
            sim, "A", speed, cycles=2, out_counts=2000
        ).run()
        assert ok is True, msg

        move_outs = [
            i for i, call in enumerate(sim.calls)
            if call == ("move_relative", ("A", 2000))
        ]
        assert len(move_outs) == 2
        for idx in move_outs:
            earlier_speeds = [
                args[1]
                for name, args in sim.calls[:idx]
                if name == "set_speed" and args[0] == "A"
            ]
            assert earlier_speeds
            assert earlier_speeds[-1] == speed
