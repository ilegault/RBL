"""
beam_reconstruction.py
Infer where the beam sits from four slit currents and four slit positions.

Each NEC log amp is wired to a whole slit blade, so its current is the total
beam flux landing on that blade -- i.e. the integral of the beam profile over
everything past that blade's edge.  Four blades at *known* mm positions
therefore give four knife-edge measurements of the beam.

What that can and cannot determine
----------------------------------
Per axis there are two currents and three unknowns (centroid, width, beam
intensity).  Taking the ratio of the two currents cancels the intensity, which3
leaves ONE equation in TWO unknowns:

    I+ / I-  =  tail(edge+, x0, width) / tail(edge-, x0, width)

So the centroid can only be solved once a width is assumed.  The saving grace
is that the width-sensitivity is not uniform:

  * A centred beam gives I+ == I-, and the solution is the edge midpoint for
    EVERY width -- the assumption does not matter at all.
  * The further off-centre the beam, the more the answer swings with the
    assumed width.

Every solver here therefore returns an interval, not a point: solve at the low
and high ends of a plausible width range and report the spread.  The interval
collapsing near centre and fanning out at the edges is the honest picture, and
it is what the GUI draws.

Two beam models are supported, matching the two ways the beamline is run:

  STATIC  -- a stationary Gaussian spot.  Tail past an edge is the Gaussian
             upper-tail function Q().
  RASTER  -- a spot swept back and forth by the electrostatic steerer.  A
             fast-axis triangular sweep dwells uniformly across its travel, so
             the time-averaged profile is a flat top of half-width A centred on
             c, with edges softened by the spot size.

Pure math -- no Qt, no hardware, no I/O.
"""
import math

# Currents at or below this are treated as "no signal": the NEC log amps bottom
# out at 1 nA, and a decade above the floor is where the reading stops being
# trustworthy enough to base a position on.
NOISE_FLOOR_A = 1e-8      # 10 nA

# How far either side of the operator's nominal spot size to probe when
# building the ambiguity interval.  Wide enough to be honest, narrow enough
# that the band stays readable.
WIDTH_RANGE_LO = 0.5      # x nominal
WIDTH_RANGE_HI = 2.0      # x nominal

# Bisection controls for the centroid solve.
_BISECT_ITERS = 60
_SEARCH_PAD   = 10.0      # mm searched beyond the slit edges

# FWHM -> sigma for a Gaussian.  Operators think in spot width, the maths wants
# sigma, so callers taking a FWHM spinbox value convert with this.
FWHM_TO_SIGMA = 1.0 / 2.35482


# --- Profile tails -----------------------------------------------------------

def _gauss_tail(edge_mm: float, centre_mm: float, sigma_mm: float) -> float:
    """Fraction of a Gaussian beam lying beyond ``edge_mm``, on the + side.

    The Gaussian upper tail Q(u) = 0.5 * erfc(u / sqrt(2)) with
    u = (edge - centre) / sigma.  Returns a value in (0, 1).
    """
    if sigma_mm <= 0.0:
        return 1.0 if centre_mm > edge_mm else 0.0
    u = (edge_mm - centre_mm) / sigma_mm
    return 0.5 * math.erfc(u / math.sqrt(2.0))


def _gauss_tail_integral(u: float) -> float:
    """Antiderivative of the Gaussian upper tail: G(u) = u*Q(u) - phi(u).

    Differentiating gives Q(u) - u*phi(u) + u*phi(u) = Q(u), as required.
    """
    phi = math.exp(-0.5 * u * u) / math.sqrt(2.0 * math.pi)
    return u * (0.5 * math.erfc(u / math.sqrt(2.0))) - phi


def _raster_tail(edge_mm: float, centre_mm: float, half_span_mm: float,
                 sigma_mm: float) -> float:
    """Fraction of a swept beam lying beyond ``edge_mm``, on the + side.

    A triangular sweep spends equal time at every point of its travel, so the
    time-averaged profile is the spot smeared uniformly over [c - A, c + A],
    and the flux past an edge is the spot's tail averaged over that travel:

        (1 / 2A) * integral over the sweep of Q((edge - x) / sigma) dx

    Substituting u = (edge - x) / sigma turns that into a difference of the
    tail's antiderivative at the two turn-around points -- exact, and two
    special-function calls instead of a numerical quadrature.  That matters:
    this sits inside a bisection that runs on the GUI thread at 10 Hz.
    """
    if half_span_mm <= 0.0 or sigma_mm <= 0.0:
        return _gauss_tail(edge_mm, centre_mm, sigma_mm)

    u_lo = (edge_mm - centre_mm + half_span_mm) / sigma_mm
    u_hi = (edge_mm - centre_mm - half_span_mm) / sigma_mm
    frac = (sigma_mm / (2.0 * half_span_mm)) * (
        _gauss_tail_integral(u_lo) - _gauss_tail_integral(u_hi))
    return 0.0 if frac < 0.0 else (1.0 if frac > 1.0 else frac)


# --- Single-axis solve -------------------------------------------------------

def solve_axis_centre(i_plus: float, i_minus: float,
                      edge_plus_mm: float, edge_minus_mm: float,
                      sigma_mm: float, half_span_mm: float = 0.0) -> float:
    """Beam centre (mm) on one axis, given both slit currents and edge positions.

    ``edge_plus_mm`` is the signed position of the '+' slit edge (positive side
    of beam centre) and ``edge_minus_mm`` that of the '-' slit (negative).
    ``half_span_mm`` > 0 selects the swept-beam model.

    Returns NaN when either current is unusable or the geometry is degenerate.

    The ratio of predicted currents rises monotonically as the beam moves
    toward the '+' slit, so a bisection on the centre position converges without
    needing a general-purpose optimiser.
    """
    if not _usable(i_plus) or not _usable(i_minus):
        return float("nan")
    if edge_minus_mm >= edge_plus_mm:
        return float("nan")

    target = i_plus / i_minus

    def imbalance(c: float) -> float:
        """log(predicted ratio) - log(measured ratio); increasing in c."""
        t_plus  = _tail(edge_plus_mm,   c,  sigma_mm, half_span_mm, sign=+1)
        t_minus = _tail(edge_minus_mm,  c,  sigma_mm, half_span_mm, sign=-1)
        # Guard the logs: tails underflow hard once the beam is many sigma away.
        t_plus  = max(t_plus,  1e-300)
        t_minus = max(t_minus, 1e-300)
        return math.log(t_plus / t_minus) - math.log(target)

    lo = edge_minus_mm - _SEARCH_PAD
    hi = edge_plus_mm  + _SEARCH_PAD
    f_lo = imbalance(lo)
    f_hi = imbalance(hi)
    if f_lo > 0.0 or f_hi < 0.0:
        # Measured imbalance is beyond anything the model can produce inside the
        # search span -- the beam is further out than this geometry explains.
        return float("nan")

    for _ in range(_BISECT_ITERS):
        mid = 0.5 * (lo + hi)
        if imbalance(mid) < 0.0:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def solve_axis_interval(i_plus: float, i_minus: float,
                        edge_plus_mm: float, edge_minus_mm: float,
                        sigma_nominal_mm: float,
                        half_span_mm: float = 0.0) -> tuple:
    """Beam centre on one axis as ``(centre, lo, hi)``, all mm.

    ``centre`` uses the nominal width; ``lo``/``hi`` bracket the centres that
    remain consistent with the same currents across the plausible width range.
    A centred beam gives lo == hi == centre; the spread grows with offset.

    Any component is NaN when it cannot be solved.
    """
    args = (i_plus, i_minus, edge_plus_mm, edge_minus_mm)
    centre = solve_axis_centre(*args, sigma_nominal_mm, half_span_mm)
    if math.isnan(centre):
        return float("nan"), float("nan"), float("nan")

    # Probe the corners of the plausible parameter box.  In raster mode the
    # sweep amplitude is every bit as uncertain as the spot size, so it gets
    # varied too and the band widens accordingly.
    sigmas = [sigma_nominal_mm * WIDTH_RANGE_LO, sigma_nominal_mm * WIDTH_RANGE_HI]
    if half_span_mm > 0.0:
        spans = [half_span_mm * WIDTH_RANGE_LO, half_span_mm * WIDTH_RANGE_HI]
    else:
        spans = [0.0]

    bounds = [centre]
    for s in sigmas:
        for a in spans:
            v = solve_axis_centre(*args, s, a)
            if not math.isnan(v):
                bounds.append(v)
    return centre, min(bounds), max(bounds)


# --- Full 2-D reconstruction -------------------------------------------------

class BeamEstimate:
    """What the four slit currents say about the beam, and how sure that is.

    Attributes
    ----------
    x, y            : centre in mm (NaN if unsolved)
    x_lo/x_hi, y_lo/y_hi : width-ambiguity bounds on each centre, mm
    ok              : True when both axes solved and every slit reads cleanly
    bad_slits        : slit labels ("X+", ...) whose current is unusable
    reason          : short human-readable explanation when not ``ok``
    """

    def __init__(self):
        self.x = self.y = float("nan")
        self.x_lo = self.x_hi = float("nan")
        self.y_lo = self.y_hi = float("nan")
        self.ok       = False
        self.bad_slits = []
        self.reason   = "no data"

    def __repr__(self):
        return (f"BeamEstimate(x={self.x:.3f} [{self.x_lo:.3f},{self.x_hi:.3f}], "
                f"y={self.y:.3f} [{self.y_lo:.3f},{self.y_hi:.3f}], "
                f"ok={self.ok}, bad={self.bad_slits})")


def reconstruct(currents: dict, edges_mm: dict, sigma_mm: float,
                half_span_x_mm: float = 0.0,
                half_span_y_mm: float = 0.0) -> BeamEstimate:
    """Reconstruct the beam centre from all four slits.

    ``currents``  : {"X+": A, "X-": A, "Y+": A, "Y-": A}
    ``edges_mm``  : {"X+": mm, "X-": mm, "Y+": mm, "Y-": mm}, SIGNED -- the
                    minus slits are negative.  The Galil reports unsigned
                    distance-from-centre, so the caller must apply the sign.
    ``sigma_mm``  : nominal spot sigma (operator-supplied).
    ``half_span_x_mm`` / ``half_span_y_mm`` : per-axis sweep half-travel for
                    raster mode, 0 for a static beam.  The fast and slow raster
                    axes generally sweep different distances, so they are
                    independent.

    A single unusable slit invalidates the whole estimate: the reconstruction is
    only as good as its weakest edge, and a half-drawn beam is easier to
    misread than no beam at all.
    """
    est = BeamEstimate()

    est.bad_slits = [j for j in ("X+", "X-", "Y+", "Y-")
                    if not _usable(currents.get(j))]
    if est.bad_slits:
        est.reason = "no usable signal on " + ", ".join(est.bad_slits)
        return est

    missing = [j for j in ("X+", "X-", "Y+", "Y-")
               if edges_mm.get(j) is None
               or math.isnan(edges_mm.get(j, float("nan")))]
    if missing:
        est.reason = "slit position unknown for " + ", ".join(missing)
        return est

    est.x, est.x_lo, est.x_hi = solve_axis_interval(
        currents["X+"], currents["X-"], edges_mm["X+"], edges_mm["X-"],
        sigma_mm, half_span_x_mm)
    est.y, est.y_lo, est.y_hi = solve_axis_interval(
        currents["Y+"], currents["Y-"], edges_mm["Y+"], edges_mm["Y-"],
        sigma_mm, half_span_y_mm)

    if math.isnan(est.x) or math.isnan(est.y):
        est.reason = "currents inconsistent with slit geometry"
        return est

    est.ok     = True
    est.reason = ""
    return est


def overscan_flags(currents: dict) -> dict:
    """Which blades the beam is actually reaching, by slit label.

    In raster mode the sweep is supposed to carry the beam clear off both ends
    of the aperture so deposition velocity stays constant across the sample.  A
    blade carrying real current is one the sweep is reaching; a blade at the
    noise floor is one it is not.  That is a direct qualitative read on
    overscan without needing to know the sweep amplitude.
    """
    return {slit: _usable(currents.get(slit))
            for slit in ("X+", "X-", "Y+", "Y-")}


# --- Internals ---------------------------------------------------------------

def _usable(i: float) -> bool:
    """True when a current reading is real signal rather than floor or fault."""
    return (i is not None and not math.isnan(i) and i > NOISE_FLOOR_A)


def _tail(edge_mm: float, centre_mm: float, sigma_mm: float,
          half_span_mm: float, sign: int) -> float:
    """Beam fraction past ``edge_mm``, on the side ``sign`` points to.

    The '-' slit collects everything BELOW its edge, which is the same integral
    mirrored about the beam centre.
    """
    if sign > 0:
        e, c = edge_mm, centre_mm
    else:
        e, c = -edge_mm, -centre_mm
    if half_span_mm > 0.0:
        return _raster_tail(e, c, half_span_mm, sigma_mm)
    return _gauss_tail(e, c, sigma_mm)


# --- Self-test ---------------------------------------------------------------

if __name__ == "__main__":
    # A perfectly centred beam sits at the edge midpoint regardless of width --
    # this is the case where the width assumption genuinely does not matter.
    for sig in (0.5, 1.0, 2.0, 5.0):
        c = solve_axis_centre(1e-6, 1e-6, 1.5, -1.5, sig)
        assert abs(c) < 1e-6, (sig, c)

    # Asymmetric edges: equal currents mean the beam sits at THEIR midpoint.
    c = solve_axis_centre(1e-6, 1e-6, 3.0, -1.0, 1.0)
    assert abs(c - 1.0) < 1e-6, c

    # More current on '+' pulls the estimate toward the '+' slit, and back again.
    assert solve_axis_centre(2e-6, 1e-6, 1.5, -1.5, 1.0) > 0
    assert solve_axis_centre(1e-6, 2e-6, 1.5, -1.5, 1.0) < 0

    # Round-trip: plant a beam, generate its currents, recover its centre.
    for true_c in (-1.0, -0.4, 0.0, 0.4, 1.0):
        sig = 0.85
        ip = _gauss_tail(1.5, true_c, sig)
        im = _gauss_tail(1.5, -true_c, sig)     # mirrored for the '-' slit
        got = solve_axis_centre(ip, im, 1.5, -1.5, sig)
        assert abs(got - true_c) < 1e-6, (true_c, got)

    # The ambiguity band closes at centre and opens up off-centre.
    _, lo0, hi0 = solve_axis_interval(1e-6, 1e-6, 1.5, -1.5, 1.0)
    _, lo1, hi1 = solve_axis_interval(8e-6, 1e-6, 1.5, -1.5, 1.0)
    assert (hi0 - lo0) < 1e-6,        (lo0, hi0)
    assert (hi1 - lo1) > (hi0 - lo0), (lo1, hi1)

    # Raster tails behave like a spread-out beam: a swept beam puts more flux
    # past a given edge than a static one at the same centre.
    assert _raster_tail(2.0, 0.0, 1.5, 0.5) > _gauss_tail(2.0, 0.0, 0.5)
    # ...and collapses back to the static case as the sweep vanishes.
    assert abs(_raster_tail(2.0, 0.0, 1e-9, 0.5) - _gauss_tail(2.0, 0.0, 0.5)) < 1e-6

    # Raster round-trip.
    span = 2.5
    ip = _raster_tail(1.5, 0.3, span, 0.5)
    im = _raster_tail(1.5, -0.3, span, 0.5)
    got = solve_axis_centre(ip, im, 1.5, -1.5, 0.5, half_span_mm=span)
    assert abs(got - 0.3) < 1e-5, got

    # A slit at the noise floor invalidates the whole estimate.
    edges = {"X+": 1.5, "X-": -1.5, "Y+": 5.0, "Y-": -5.0}
    good  = {"X+": 1e-6, "X-": 1e-6, "Y+": 1e-6, "Y-": 1e-6}
    e = reconstruct(good, edges, 1.0)
    assert e.ok and abs(e.x) < 1e-6 and abs(e.y) < 1e-6, e

    bad = dict(good, **{"Y-": 1e-12})
    e = reconstruct(bad, edges, 1.0)
    assert not e.ok and e.bad_slits == ["Y-"], e

    e = reconstruct(good, {**edges, "X+": float("nan")}, 1.0)
    assert not e.ok and "position unknown" in e.reason, e

    # Overscan flags follow the noise floor.
    flags = overscan_flags(bad)
    assert flags["X+"] and not flags["Y-"], flags

    print("[OK] beam_reconstruction self-test passed")
