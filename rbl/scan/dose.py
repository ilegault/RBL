import numpy as np
from scipy.signal import fftconvolve

from rbl.physics.beam import fwhm_to_sigma, gaussian_kernel_2d


def trajectory_density(x_traj, y_traj, dt, x_edges, y_edges):
    """
    Bin trajectory into 2D histogram weighted by dt.
    Returns rho shape (Nx, Ny) in seconds per bin.
    """
    rho, _, _ = np.histogram2d(
        x_traj, y_traj,
        bins=[x_edges, y_edges],
        weights=np.full(len(x_traj), dt),
    )
    return rho


def dose_map_fft(rho, sx_pix, sy_pix):
    """
    Convolve trajectory density with Gaussian kernel via FFT.
    Preferred for grids >= 200x200.
    """
    kernel = gaussian_kernel_2d(sx_pix, sy_pix)
    dose = fftconvolve(rho, kernel, mode="same")
    return dose


def dose_map_direct(x_pix, y_pix, dt, grid_shape, sigma_pix, kernel):
    """
    Direct-stamp fallback for small grids.
    x_pix, y_pix are floating-point pixel-space indices (0..Nx, 0..Ny).
    """
    Nx, Ny = grid_shape
    dose = np.zeros((Nx, Ny), dtype=float)
    half = kernel.shape[0] // 2

    for xi, yi in zip(x_pix, y_pix):
        i = int(round(float(xi)))
        j = int(round(float(yi)))
        # Skip stamps whose center is more than half-kernel outside the grid
        if i + half < 0 or i - half >= Nx or j + half < 0 or j - half >= Ny:
            continue
        i0 = max(0, i - half)
        i1 = min(Nx, i + half + 1)
        j0 = max(0, j - half)
        j1 = min(Ny, j + half + 1)
        ki0 = i0 - (i - half)
        ki1 = ki0 + (i1 - i0)
        kj0 = j0 - (j - half)
        kj1 = kj0 + (j1 - j0)
        if i1 > i0 and j1 > j0 and ki1 > ki0 and kj1 > kj0:
            dose[i0:i1, j0:j1] += kernel[ki0:ki1, kj0:kj1] * dt

    return dose


def apply_aperture(dose, x_edges, y_edges, xL, xR, yB, yT):
    """Zero out dose outside the rectangular aperture."""
    x_centers = 0.5 * (x_edges[:-1] + x_edges[1:])
    y_centers = 0.5 * (y_edges[:-1] + y_edges[1:])
    X, Y = np.meshgrid(x_centers, y_centers, indexing="ij")
    mask = (X > xL) & (X < xR) & (Y > yB) & (Y < yT)
    dose = dose * mask.astype(float)
    return dose


def compute_dose(params, t_arr, x_arr, y_arr):
    """
    Full dose pipeline: density -> convolve -> aperture mask.
    Returns (dose, rho, x_edges, y_edges).
    """
    Nx = params["grid_nx"]
    Ny = params["grid_ny"]
    xL = params["aperture_xL_mm"]
    xR = params["aperture_xR_mm"]
    yB = params["aperture_yB_mm"]
    yT = params["aperture_yT_mm"]
    fwhm_x = params["fwhm_x_mm"]
    fwhm_y = params["fwhm_y_mm"]

    # Grid extends slightly beyond scan amplitudes to capture tails
    ax = params["ax_mm"]
    ay = params["ay_mm"]
    margin = max(fwhm_x, fwhm_y) * 2.0
    x_lo = min(xL, -ax) - margin
    x_hi = max(xR,  ax) + margin
    y_lo = min(yB, -ay) - margin
    y_hi = max(yT,  ay) + margin

    x_edges = np.linspace(x_lo, x_hi, Nx + 1)
    y_edges = np.linspace(y_lo, y_hi, Ny + 1)

    # Pixel size in mm
    dx_mm = (x_hi - x_lo) / Nx
    dy_mm = (y_hi - y_lo) / Ny

    # Beam sigma in pixels
    sx_pix = fwhm_to_sigma(fwhm_x) / dx_mm
    sy_pix = fwhm_to_sigma(fwhm_y) / dy_mm

    dt = t_arr[1] - t_arr[0] if len(t_arr) > 1 else 1.0

    rho = trajectory_density(x_arr, y_arr, dt, x_edges, y_edges)

    if Nx * Ny >= 64 * 64:
        # FFT convolution — preferred for all grids >= 64x64
        dose = dose_map_fft(rho, sx_pix, sy_pix)
    else:
        # Direct stamp — only for tiny grids; convert mm coords to pixel indices
        kernel = gaussian_kernel_2d(sx_pix, sy_pix)
        x_pix = (x_arr - x_lo) / dx_mm
        y_pix = (y_arr - y_lo) / dy_mm
        dose = dose_map_direct(x_pix, y_pix, dt, (Nx, Ny), max(sx_pix, sy_pix), kernel)

    dose = apply_aperture(dose, x_edges, y_edges, xL, xR, yB, yT)

    return dose, rho, x_edges, y_edges
