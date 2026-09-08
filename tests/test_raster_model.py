"""
Unit tests for rbl.hardware.raster_model — no Qt, no hardware.
"""
import math

import pytest

# The installed steerer: NEC 2EA021441 (ES10 plates).
from rbl.config.steerer_geometry import PLATE_GAP_CM, PLATE_LENGTH_CM
from rbl.hardware.raster_model import (
    deflection_mrad,
    displacement_mm,
    dwell_uniformity,
    required_differential_kv,
    required_drive,
)

L_CM, D_CM = PLATE_LENGTH_CM, PLATE_GAP_CM
Q, E_EV, DRIFT_CM = 1, 30_000.0, 100.0


class TestAgainstTheManualsWorkedExample:
    """XY Steerer Manual Section IV works one example end to end:

        theta = V l q / (2 d E),  V = 10 kV, l = 12.5 cm, q = 1e,
        d = 3.8 cm, E = 1 MeV  ->  theta = 0.0164 rad,
        and 1.64 cm of deflection after a 1 m drift.

    This pins the FORMULA; the geometry tests in test_steerer_geometry.py
    pin the numbers fed into it.
    """

    @pytest.mark.xfail(reason="TEMPORARY: ticket 06 xfail_strict demonstration, reverted next commit")
    def test_deflection_angle(self):
        theta_mrad = deflection_mrad(10.0, 12.5, 3.8, 1, 1_000_000.0)
        assert theta_mrad / 1000.0 == pytest.approx(0.0164, abs=5e-5)

    def test_deflection_after_one_metre_of_drift(self):
        # "and a drift of 1 meter, the deflection will be 1.64 cm" - the
        # drift alone, nothing added to it.
        x_mm = displacement_mm(10.0, 12.5, 3.8, 1, 1_000_000.0, drift_cm=100.0)
        assert x_mm == pytest.approx(16.4, abs=0.05)


class TestAgainstTheLabDeflectionSheet:
    """`Hirst RHBL Deflection Information.xlsx`, sheet "Deflection on Sample".

    Its per-species column is

        I8 = ((C8*10^3) * 12.5 * q) / (2 * 3.8 * E*10^6) * C9 * 2

    with C8 = 2 kV PER PLATE, C9 = 2475.99 mm of drift, and the trailing *2
    the push-pull factor.  For the protons row (3 MeV, q = 1) that is
    **5.4298 mm**.

    This is the beamline's reference number, so it is reproduced EXACTLY -
    no pivot correction, no fudge.  See the NO l/2 PIVOT TERM note in
    `displacement_mm`.
    """

    SHEET_DRIFT_CM = 247.5992     # SUM(F4:N4) * 25.4, in cm
    SHEET_PROTONS_MM = 5.4298     # I8 for 3 MeV protons at 2 kV/plate

    def test_reproduces_the_sheets_proton_row_exactly(self):
        x_mm = displacement_mm(4.0, 12.5, 3.8, 1, 3.0e6,
                               drift_cm=self.SHEET_DRIFT_CM)
        assert x_mm == pytest.approx(self.SHEET_PROTONS_MM, abs=0.001)

    def test_displacement_is_theta_times_drift_and_nothing_else(self):
        # The regression that matters: if anyone ever re-adds an l/2 pivot
        # term, or any other geometric correction, this fails.  Plate length
        # must affect the ANGLE and only the angle.
        theta_rad = deflection_mrad(4.0, 12.5, 3.8, 1, 3.0e6) / 1000.0
        x_mm = displacement_mm(4.0, 12.5, 3.8, 1, 3.0e6,
                               drift_cm=self.SHEET_DRIFT_CM)
        assert x_mm == pytest.approx(theta_rad * self.SHEET_DRIFT_CM * 10.0)

    def test_all_four_species_rows(self):
        # Sheet rows I8..I11, recomputed from its own formula: protons,
        # Al 2.25 MeV, Ni 3 MeV q=3, Ti 3.4 MeV q=2 - all at 2 kV/plate.
        for energy_mev, q, expected in [(3.0, 1, 5.4298),
                                        (2.25, 1, 7.2397),
                                        (3.0, 3, 16.2894),
                                        (3.4, 2, 9.5820)]:
            x_mm = displacement_mm(4.0, 12.5, 3.8, q, energy_mev * 1e6,
                                   drift_cm=self.SHEET_DRIFT_CM)
            assert x_mm == pytest.approx(expected, abs=0.001), (energy_mev, q)

    def test_per_plate_kv_is_half_the_differential(self):
        # The sheet's C8 is per plate and it multiplies by 2; we take the
        # plate-to-plate voltage directly.  Same number, said once.
        from_differential = displacement_mm(4.0, 12.5, 3.8, 1, 3.0e6,
                                            drift_cm=self.SHEET_DRIFT_CM)
        from_per_plate = 2.0 * displacement_mm(2.0, 12.5, 3.8, 1, 3.0e6,
                                               drift_cm=self.SHEET_DRIFT_CM)
        assert from_differential == pytest.approx(from_per_plate)


class TestDeflection:
    def test_positive_for_positive_voltage(self):
        assert deflection_mrad(1.0, L_CM, D_CM, Q, E_EV) > 0

    def test_zero_energy_is_nan(self):
        assert math.isnan(deflection_mrad(1.0, L_CM, D_CM, Q, 0.0))

    def test_linear_in_voltage(self):
        theta1 = deflection_mrad(1.0, L_CM, D_CM, Q, E_EV)
        theta2 = deflection_mrad(2.0, L_CM, D_CM, Q, E_EV)
        assert theta2 == pytest.approx(2 * theta1)


class TestDisplacement:
    def test_linear_in_voltage(self):
        x1 = displacement_mm(1.0, L_CM, D_CM, Q, E_EV, DRIFT_CM)
        x2 = displacement_mm(2.0, L_CM, D_CM, Q, E_EV, DRIFT_CM)
        assert x2 == pytest.approx(2 * x1)

    def test_zero_energy_is_nan(self):
        assert math.isnan(displacement_mm(1.0, L_CM, D_CM, Q, 0.0, DRIFT_CM))


class TestRequiredDifferentialKv:
    def test_inverts_displacement_exactly(self):
        req_kv = required_differential_kv(
            target_half_width_mm=5.0, fwhm_mm=2.0, plate_length_cm=L_CM,
            plate_gap_cm=D_CM, charge_state=Q, beam_energy_ev=E_EV,
            drift_cm=DRIFT_CM, turnaround_k=1.5,
        )
        x_at_req = displacement_mm(req_kv, L_CM, D_CM, Q, E_EV, DRIFT_CM)
        assert x_at_req == pytest.approx(5.0 + 1.5 * 2.0, rel=1e-6)

    def test_turnaround_k_is_a_real_parameter(self):
        kv_1 = required_differential_kv(5.0, 2.0, L_CM, D_CM, Q, E_EV, DRIFT_CM,
                                         turnaround_k=1.0)
        kv_2 = required_differential_kv(5.0, 2.0, L_CM, D_CM, Q, E_EV, DRIFT_CM,
                                         turnaround_k=2.0)
        assert kv_2 > kv_1


class TestDwellUniformity:
    def test_wide_scan_is_uniform_over_narrow_sample(self):
        du = dwell_uniformity(fwhm_mm=1.0, scan_half_width_mm=10.0,
                               sample_half_width_mm=5.0)
        assert du["uniformity_pct"] < 1.0

    def test_tight_scan_is_less_uniform(self):
        wide = dwell_uniformity(1.0, 10.0, 5.0)
        tight = dwell_uniformity(1.0, 5.5, 5.0)
        assert tight["uniformity_pct"] > wide["uniformity_pct"]

    def test_degenerate_inputs_return_nan(self):
        du = dwell_uniformity(0.0, 10.0, 5.0)
        assert math.isnan(du["uniformity_pct"])


class TestRequiredDrive:
    """Full width in, halves inside; asymmetry as a DC term."""

    def _drive(self, width_mm, offset_mm=0.0, fwhm_mm=1.0, k=1.5):
        return required_drive(target_width_mm=width_mm, fwhm_mm=fwhm_mm,
                              plate_length_cm=L_CM, plate_gap_cm=D_CM,
                              charge_state=Q, beam_energy_ev=E_EV,
                              drift_cm=DRIFT_CM, turnaround_k=k,
                              center_offset_mm=offset_mm)

    def test_full_width_is_halved(self):
        d = self._drive(16.0, k=0.0, fwhm_mm=1.0)
        assert d["sample_half_mm"] == pytest.approx(8.0)
        assert d["scan_half_span_mm"] == pytest.approx(8.0)

    def test_it_agrees_with_required_differential_kv_on_the_same_geometry(self):
        d = self._drive(16.0)
        legacy = required_differential_kv(
            target_half_width_mm=8.0, fwhm_mm=1.0, plate_length_cm=L_CM,
            plate_gap_cm=D_CM, charge_state=Q, beam_energy_ev=E_EV,
            drift_cm=DRIFT_CM, turnaround_k=1.5)
        assert d["amplitude_kv"] == pytest.approx(legacy)

    def test_amplitude_actually_sweeps_the_requested_span(self):
        d = self._drive(16.0)
        swept = displacement_mm(d["amplitude_kv"], L_CM, D_CM, Q, E_EV, DRIFT_CM)
        assert swept == pytest.approx(d["scan_half_span_mm"], abs=1e-9)

    def test_zero_offset_means_zero_dc_and_a_centred_sweep(self):
        d = self._drive(16.0)
        assert d["offset_kv"] == 0.0
        assert d["scan_min_mm"] == pytest.approx(-d["scan_max_mm"])
        assert d["peak_kv"] == pytest.approx(d["amplitude_kv"])

    def test_offset_moves_the_interval_without_resizing_it(self):
        sym = self._drive(16.0)
        off = self._drive(16.0, offset_mm=3.0)
        assert off["amplitude_kv"] == pytest.approx(sym["amplitude_kv"])
        assert off["scan_max_mm"] - off["scan_min_mm"] == pytest.approx(
            sym["scan_max_mm"] - sym["scan_min_mm"])
        assert off["scan_min_mm"] == pytest.approx(sym["scan_min_mm"] + 3.0)
        assert off["scan_max_mm"] == pytest.approx(sym["scan_max_mm"] + 3.0)

    def test_the_dc_term_lands_the_centre_where_it_was_asked_for(self):
        off = self._drive(16.0, offset_mm=3.0)
        centre = displacement_mm(off["offset_kv"], L_CM, D_CM, Q, E_EV, DRIFT_CM)
        assert centre == pytest.approx(3.0, abs=1e-9)

    def test_a_negative_offset_still_costs_headroom(self):
        # |offset|, not offset: the peak instant is the same size either way.
        plus  = self._drive(16.0, offset_mm=+3.0)
        minus = self._drive(16.0, offset_mm=-3.0)
        assert plus["peak_kv"] == pytest.approx(minus["peak_kv"])
        assert plus["peak_kv"] > plus["amplitude_kv"]

    def test_plate_values_are_exactly_half_the_differential(self):
        d = self._drive(16.0, offset_mm=3.0)
        assert d["ac_plate_kv"] == pytest.approx(d["amplitude_kv"] / 2)
        assert d["offset_plate_kv"] == pytest.approx(d["offset_kv"] / 2)
        assert d["peak_plate_kv"] == pytest.approx(d["peak_kv"] / 2)

    def test_degenerate_energy_does_not_raise(self):
        d = required_drive(target_width_mm=16.0, fwhm_mm=1.0,
                           plate_length_cm=L_CM, plate_gap_cm=D_CM,
                           charge_state=Q, beam_energy_ev=0.0,
                           drift_cm=DRIFT_CM)
        assert math.isnan(d["amplitude_kv"])
        assert d["sample_half_mm"] == pytest.approx(8.0)
