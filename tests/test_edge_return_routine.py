"""
Edge-return routine: repeatability measurement for slit homing without
redefining zero.

Each cycle moves out away from home at the approach speed, runs HM at the
approach speed, records the stop position, then creeps out one count at a time
to the release edge and records it without calling DP=0 or DE=0.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QApplication

from rbl.hardware import galil_workers
from rbl.hardware.edge_return import EdgeReturnReading
from rbl.hardware.galil_driver import GalilController
from rbl.hardware.galil_workers import AxisHomeRoutine, EdgeReturnRoutine, EdgeReturnWorker
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
    assert readings == [
        EdgeReturnReading(-3, 6),
        EdgeReturnReading(-3, 6),
        EdgeReturnReading(-3, 6),
    ]


def test_edge_return_never_defines_zero():
    sim = SimGalil(trip_at=-3, release_at=6, start_at=2000)

    ok, _, _ = EdgeReturnRoutine(sim, "A", 58, cycles=3, out_counts=2000).run()

    assert ok is True
    assert sim.slits["A"].zeroed_at == []
    assert not any(
        name in ("define_zero", "define_zero_multi", "define_encoder_zero", "begin_home")
        for name, _ in sim.calls
    )


def test_edge_return_cancel_keeps_the_readings_so_far():
    sim = SimGalil(trip_at=-3, release_at=6, start_at=2000)
    cycles_seen = 0

    def on_progress(text: str) -> None:
        nonlocal cycles_seen
        if "stop at" in text and "edge at" in text:
            cycles_seen += 1

    ok, msg, readings = EdgeReturnRoutine(
        sim, "A", 58, cycles=3, out_counts=2000,
        progress=on_progress,
        cancelled=lambda: cycles_seen >= 1,
    ).run()

    assert ok is False
    assert "cancelled after 1 cycles" in msg
    assert len(readings) == 1
    assert readings == [EdgeReturnReading(-3, 6)]


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
    assert readings == [
        EdgeReturnReading(-3, 6),
        EdgeReturnReading(-3, 6),
        EdgeReturnReading(-3, 6),
    ]


def test_edge_return_records_release_edges():
    sim = SimGalil(trip_at=-3, release_at=6, start_at=2000)

    ok, msg, readings = EdgeReturnRoutine(
        sim, "A", 58, cycles=3, out_counts=2000
    ).run()

    assert ok is True
    assert [r.edge_counts for r in readings] == [6, 6, 6]
    assert sim.slits["A"].zeroed_at == []


def test_edge_return_shows_a_lost_step():
    sim = SimGalil(trip_at=-3, release_at=6, start_at=2000, slip_on_home={2: 8})

    ok, _, readings = EdgeReturnRoutine(
        sim, "A", 58, cycles=3, out_counts=2000
    ).run()

    assert ok is True
    assert readings == [
        EdgeReturnReading(-3, 6),
        EdgeReturnReading(-11, -2),
        EdgeReturnReading(-11, -2),
    ]


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

        homes = [
            i for i, call in enumerate(sim.calls)
            if call == ("home", ("A",))
        ]
        assert len(homes) == 2
        for idx in homes:
            earlier_speeds = [
                args[1]
                for name, args in sim.calls[:idx]
                if name == "set_speed" and args[0] == "A"
            ]
            assert earlier_speeds
            assert earlier_speeds[-1] == speed


def test_edge_return_records_stop_and_edge_each_cycle():
    sim = SimGalil(trip_at=-3, release_at=6, start_at=2000)
    progress_lines: list[str] = []

    ok, msg, readings = EdgeReturnRoutine(
        sim, "A", 600, cycles=3, out_counts=2000, progress=progress_lines.append
    ).run()

    assert ok is True, msg
    assert len(readings) == 3
    assert all(isinstance(r, EdgeReturnReading) for r in readings)

    home_indices = [i for i, c in enumerate(sim.calls) if c == ("home", ("A",))]
    assert len(home_indices) == 3
    for k, idx in enumerate(home_indices):
        earlier_speeds = [
            args[1]
            for name, args in sim.calls[:idx]
            if name == "set_speed" and args[0] == "A"
        ]
        assert earlier_speeds[-1] == 600
        # Stop position at 600 cps with overshoot_per_100_cps=1: -3 - 6 = -9
        assert readings[k].stop_counts == -9
        assert readings[k].edge_counts > readings[k].stop_counts

    assert not any(
        name in ("define_zero", "define_encoder_zero") for name, _ in sim.calls
    )
    for k, r in enumerate(readings, start=1):
        expected = f"A: cycle {k}/3 — stop at {r.stop_counts} cts, edge at {r.edge_counts} cts"
        assert expected in progress_lines


def test_edge_return_slip_shows_in_both_columns():
    sim = SimGalil(trip_at=-3, release_at=6, start_at=2000, slip_on_home={2: 8})

    ok, msg, readings = EdgeReturnRoutine(
        sim, "A", 600, cycles=3, out_counts=2000
    ).run()

    assert ok is True, msg
    assert len(readings) == 3
    for k in (1, 2):
        assert readings[k].stop_counts == readings[0].stop_counts - 8
        assert readings[k].edge_counts == readings[0].edge_counts - 8


def test_edge_return_starts_off_the_switch_and_stops_on_a_bad_stop():
    sim = SimGalil(trip_at=-3, release_at=6, start_at=-3)
    ok, msg, readings = EdgeReturnRoutine(
        sim, "A", 600, cycles=1, out_counts=2000
    ).run()
    assert ok is True, msg
    motion_calls = [
        (i, c) for i, c in enumerate(sim.calls)
        if c[0] in ("move_relative", "home", "jog_start")
    ]
    first_idx, first_call = motion_calls[0]
    assert first_call == ("move_relative", ("A", 2000))
    speeds_before = [
        args[1]
        for name, args in sim.calls[:first_idx]
        if name == "set_speed" and args[0] == "A"
    ]
    assert speeds_before[-1] == 1800

    # Bad stop on second cycle's home move: reverse limit tripped, home switch clear
    galil = MagicMock(spec=GalilController)
    galil.is_moving.return_value = False
    galil.get_step_count.return_value = 0
    galil.get_position.return_value = 0
    galil.get_stop_code.return_value = 3
    clear = {"forward_switch": False, "reverse_switch": False, "home_switch": False}
    home_and_rev = {"forward_switch": False, "reverse_switch": True, "home_switch": True}
    rev_only = {"forward_switch": False, "reverse_switch": True, "home_switch": False}
    galil.get_switch_states.side_effect = [
        clear,         # pre_home_move
        clear,         # cycle 1 pre-HM check
        home_and_rev,  # cycle 1 HM stop
        clear,         # cycle 1 creep release
        clear,         # cycle 2 pre-HM check
        rev_only,      # cycle 2 HM bad stop
    ]

    ok, msg, readings = EdgeReturnRoutine(
        galil, "A", 600, cycles=3, out_counts=2000
    ).run()

    assert ok is False
    assert msg == AxisHomeRoutine._reverse_msg("A")
    assert len(readings) == 1
