import numpy as np
from scipy.signal.windows import gaussian


def fwhm_to_sigma(fwhm_mm: float) -> float:
    """Convert FWHM to Gaussian sigma. sigma = fwhm / (2*sqrt(2*ln(2)))."""
    return fwhm_mm / (2.0 * np.sqrt(2.0 * np.log(2.0)))


def gaussian_kernel_2d(sx_pix: float, sy_pix: float, n_sigma: float = 4) -> np.ndarray:
    """
    Build a normalized 2D Gaussian kernel.
    Shape is (M, M) where M = 2*ceil(n_sigma * max(sx, sy)) + 1.
    """
    # scipy.signal.windows.gaussian(M, std=0) divides by zero at the center
    # tap, producing NaN; clamp to a tiny positive sigma so a zero-width axis
    # degenerates to a near-delta function instead of poisoning the kernel.
    sx_pix = max(sx_pix, 1e-6)
    sy_pix = max(sy_pix, 1e-6)
    M = 2 * int(np.ceil(n_sigma * max(sx_pix, sy_pix))) + 1
    gx = gaussian(M, sx_pix)
    gy = gaussian(M, sy_pix)
    kernel = np.outer(gx, gy)
    kernel /= kernel.sum()
    return kernel
