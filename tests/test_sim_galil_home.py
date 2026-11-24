"""Tests for SimGalil.home() and SimGalil.define_encoder_zero() (ADR 0010)."""

import pytest

from rbl.config import hardware_config as SC
from rbl.hardware.galil_driver import GalilError
from tests.sim_slit import SimGalil


def test_sim_hm_stops_on_the_reverse_limit_with_code_3():
    g = SimGalil()
    g.set_speed("A", SC.HOMING_SPEED_COUNTS_PER_SEC)
    g.home("A")

    stop_code = g.get_stop_code("A")
    assert stop_code == 3
    assert stop_code in SC.HOME_ACCEPTED_STOP_CODES
    sw = g.get_switch_states("A")
    assert sw["home_switch"] is True
    assert sw["reverse_switch"] is True
    assert g.slits["A"].phys == g.slits["A"].trip_at - 9
    assert ("home", ("A",)) in g.calls

    with pytest.raises(GalilError) as exc:
        g.home("A")
    assert exc.value.code == 22


def test_sim_hm_stops_at_the_home_switch_with_code_10_when_the_limit_is_deeper():
    g = SimGalil(trip_at=-3, reverse_trip_at=-53)
    g.set_speed("A", SC.HOMING_SPEED_COUNTS_PER_SEC)
    g.home("A")

    sw = g.get_switch_states("A")
    assert sw["home_switch"] is True
    assert sw["reverse_switch"] is False
    assert g.slits["A"].phys == g.slits["A"].trip_at
    assert g.get_position("A") == g.slits["A"].trip_at
    stop_code = g.get_stop_code("A")
    assert stop_code == 10
    assert stop_code in SC.HOME_ACCEPTED_STOP_CODES


def test_sim_hm_loses_steps_on_the_chosen_call():
    g = SimGalil(slip_on_home={2: 8})
    g.set_speed("A", SC.HOMING_SPEED_COUNTS_PER_SEC)
    g.home("A")
    first_pos = g.get_position("A")

    g.move_relative("A", SC.PRE_HOME_MOVE_COUNTS)
    g.home("A")
    second_pos = g.get_position("A")
    assert second_pos == first_pos - 8

    zeroed_before = list(g.slits["A"].zeroed_at)
    pos_before = g.get_position("A")
    g.define_encoder_zero("A")

    assert ("define_encoder_zero", ("A",)) in g.calls
    assert g.slits["A"].zeroed_at == zeroed_before
    assert g.get_position("A") == pos_before
