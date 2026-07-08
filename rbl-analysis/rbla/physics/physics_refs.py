"""
Source citations for the physics constants used in this package.

This module is data-only — no logic, no imports, no side effects.
Other modules import these strings to embed in docstrings/help text.
"""

TAU_RECOMB_REFS = (
    "Wallace et al. (2017), 'The role of Frenkel defect diffusion in "
    "dynamic annealing in ion-irradiated Si,' Sci. Rep. 7, 39754, "
    "https://doi.org/10.1038/srep39754 -- reports tau ~ 10 to 0.2 ms "
    "for Si over -20 to +140 C.\n"
    "Wallace et al. (2017), 'Dynamic annealing in Ge studied by pulsed "
    "ion beams,' Sci. Rep. 7, 13153, "
    "https://doi.org/10.1038/s41598-017-13161-1 -- reports tau ~ 8 to "
    "0.3 ms for Ge at 100-160 C."
)

FDRT_REFS = (
    "Gigax et al. (2015), 'The influence of ion beam rastering on the "
    "swelling of self-ion irradiated pure iron at 450 C,' J. Nucl. "
    "Mater. 465, 343-348, "
    "https://doi.org/10.1016/j.jnucmat.2015.06.001 -- empirical 500 Hz "
    "anti-pulsing threshold for Fe at 450 C.\n"
    "ASTM E521-16, 'Standard Practice for Investigating the Effects of "
    "Neutron Radiation Damage Using Charged-Particle Irradiation.'\n"
    "Was (2007), Fundamentals of Radiation Materials Science (Springer) "
    "-- rate theory framework.\n"
    "Zinkle & Snead (2018), Scripta Materialia 143, 154-160, "
    "https://doi.org/10.1016/j.scriptamat.2017.06.041 -- review of "
    "ion-beam rastering artifacts."
)

PIXEL_REVISIT_RULE = (
    "For a classic raster (fast triangle on X, slow ramp on Y), any given "
    "pixel is revisited once per Y cycle. The relevant 'revisit "
    "frequency' for FDRT is therefore min(fx, fy), NOT max(fx, fy). "
    "Off-time between visits = 1 / min(fx, fy)."
)
