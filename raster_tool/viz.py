import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401 (registers 3D projection)
from matplotlib.animation import FuncAnimation


def plot_heatmap(dose, x_edges, y_edges, aperture_rect, metrics_dict=None):
    """
    Dose map heatmap with aperture outline and deviation contours.
    Green contours = ±5% from mean.  Red contours = ±10% from mean.
    aperture_rect = (xL, xR, yB, yT).  Returns matplotlib Figure.
    """
    xL, xR, yB, yT = aperture_rect
    x_centers = 0.5 * (x_edges[:-1] + x_edges[1:])
    y_centers = 0.5 * (y_edges[:-1] + y_edges[1:])

    fig, ax = plt.subplots(figsize=(7, 6))
    pcm = ax.pcolormesh(x_edges, y_edges, dose.T, cmap="viridis", shading="auto")
    fig.colorbar(pcm, ax=ax, label="Dose (a.u.)")

    # Aperture rectangle — bright cyan stands out against viridis
    rect = patches.Rectangle(
        (xL, yB), xR - xL, yT - yB,
        linewidth=2, edgecolor="#00e5cc", facecolor="none", linestyle="--",
    )
    ax.add_patch(rect)

    title = "Dose Map"
    annotation_lines = []

    if metrics_dict:
        flat  = metrics_dict.get("flatness_pct", float("nan"))
        pinch = metrics_dict.get("pinch_pct",    float("nan"))
        rms   = metrics_dict.get("rms_pct",      float("nan"))
        ss    = metrics_dict.get("steady_state",  None)

        title = f"Dose Map — Flatness: {flat:.1f}%"
        ok = flat <= 10
        annotation_lines = [
            f"Flatness: {flat:.1f}%  {'✓' if ok else '✗'}  (target ≤10%)",
            f"Pinch:    {pinch:.1f}%",
            f"RMS:      {rms:.1f}%",
            "✓ STEADY STATE" if ss else "✗ TRANSIENT",
        ]

        # ── Deviation contours inside aperture ───────────────────────────────
        aperture_mask = (
            (x_centers[:, None] > xL) & (x_centers[:, None] < xR) &
            (y_centers[None, :] > yB) & (y_centers[None, :] < yT)
        )
        inside = dose[aperture_mask]
        if inside.size > 0 and inside.mean() > 0:
            mu = inside.mean()
            dose_clipped = np.where(aperture_mask, dose, np.nan)

            lv5  = sorted([mu * 0.95, mu * 1.05])
            lv10 = sorted([mu * 0.90, mu * 1.10])

            fmt = lambda x: f"{(x / mu - 1) * 100:+.0f}%"  # noqa: E731

            cs5 = ax.contour(x_centers, y_centers, dose_clipped.T,
                             levels=lv5, colors=["#33dd66", "#33dd66"],
                             linewidths=1.4, linestyles=["solid", "dashed"])
            ax.clabel(cs5, fmt=fmt, fontsize=7, inline=True)

            cs10 = ax.contour(x_centers, y_centers, dose_clipped.T,
                              levels=lv10, colors=["#ff4444", "#ff4444"],
                              linewidths=1.4, linestyles=["solid", "dashed"])
            ax.clabel(cs10, fmt=fmt, fontsize=7, inline=True)

            annotation_lines += ["", "— green = ±5%  red = ±10%"]

    ax.set_xlabel("X (mm)")
    ax.set_ylabel("Y (mm)")
    ax.set_title(title)

    if annotation_lines:
        ax.text(
            0.02, 0.98, "\n".join(annotation_lines),
            transform=ax.transAxes, fontsize=8, verticalalignment="top",
            color="white",
            bbox=dict(boxstyle="round,pad=0.4", facecolor="#1a1a2e", alpha=0.85),
        )

    fig.tight_layout()
    return fig


def plot_dose_3d(dose, x_edges, y_edges, aperture_rect, metrics_dict=None):
    """
    3D surface plot of dose — inspired by Yan et al. (2005) Fig. 4.
    plasma colormap (purple=low, yellow=high).
    White semi-transparent plane = mean dose inside aperture (flatness target).
    aperture_rect = (xL, xR, yB, yT).  Returns matplotlib Figure.
    """
    xL, xR, yB, yT = aperture_rect
    x_centers = 0.5 * (x_edges[:-1] + x_edges[1:])
    y_centers = 0.5 * (y_edges[:-1] + y_edges[1:])

    stride_x = max(1, len(x_centers) // 64)
    stride_y = max(1, len(y_centers) // 64)
    xc = x_centers[::stride_x]
    yc = y_centers[::stride_y]
    Z  = dose[::stride_x, ::stride_y]
    X, Y = np.meshgrid(xc, yc, indexing="ij")

    fig = plt.figure(figsize=(8, 6))
    ax  = fig.add_subplot(111, projection="3d")

    norm       = plt.Normalize(Z.min(), Z.max())
    facecolors = plt.cm.plasma(norm(Z))

    ax.plot_surface(X, Y, Z, facecolors=facecolors, rstride=1, cstride=1,
                    linewidth=0, antialiased=False, shade=False)

    # Mean-dose plane inside aperture — shows the flatness target level
    aperture_mask = (
        (xc[:, None] > xL) & (xc[:, None] < xR) &
        (yc[None, :] > yB) & (yc[None, :] < yT)
    )
    inside = Z[aperture_mask]
    if inside.size > 0 and inside.mean() > 0:
        mu = inside.mean()
        from mpl_toolkits.mplot3d.art3d import Poly3DCollection
        verts = [[(xL, yB, mu), (xR, yB, mu), (xR, yT, mu), (xL, yT, mu)]]
        plane = Poly3DCollection(verts, alpha=0.18,
                                 facecolor="white", edgecolor="#aaaaaa",
                                 linestyle="--")
        ax.add_collection3d(plane)

    # Aperture outline on the floor
    z_floor = Z.min()
    box_x = [xL, xR, xR, xL, xL]
    box_y = [yB, yB, yT, yT, yB]
    ax.plot(box_x, box_y, [z_floor] * 5, "#00e5cc",
            linewidth=2.0, linestyle="--", alpha=0.9)

    ax.set_xlabel("X (mm)", fontsize=9, labelpad=6)
    ax.set_ylabel("Y (mm)", fontsize=9, labelpad=6)
    ax.set_zlabel("Dose (a.u.)", fontsize=9, labelpad=6)

    title = "Dose surface (3D)"
    if metrics_dict:
        flat  = metrics_dict.get("flatness_pct", float("nan"))
        title = f"Dose surface — Flatness: {flat:.1f}%  (white plane = mean)"

    ax.set_title(title, fontsize=10, pad=10)
    ax.view_init(elev=30, azim=-60)

    sm = plt.cm.ScalarMappable(cmap="plasma", norm=norm)
    sm.set_array([])
    fig.colorbar(sm, ax=ax, shrink=0.5, pad=0.1, label="Dose (a.u.)")

    fig.tight_layout()
    return fig


def plot_velocity_profile(params, t_arr, x_arr, y_arr):
    """
    Probe velocity vs time — inspired by Teo et al. (2018) Fig. 7.
    Left panel: speed vs time.
    Right panel: speed histogram coloured red (slow) → green (fast) so you
    immediately see how much time the beam spends at low speed (= high dose).
    Returns matplotlib Figure.
    """
    if len(t_arr) < 2:
        fig, ax = plt.subplots()
        ax.text(0.5, 0.5, "Not enough samples", ha="center", va="center")
        return fig

    dt    = t_arr[1] - t_arr[0]
    vx    = np.gradient(x_arr, dt)
    vy    = np.gradient(y_arr, dt)
    speed = np.sqrt(vx**2 + vy**2)   # mm/s

    n      = len(t_arr)
    stride = max(1, n // 5000)
    t_plot = t_arr[::stride] * 1000  # ms
    s_plot = speed[::stride]

    pattern = params.get("pattern", "classic")
    fx      = params.get("fx_hz", 0)
    fy      = params.get("fy_hz", 0)
    ax_mm   = params.get("ax_mm", 1.0)
    ay_mm   = params.get("ay_mm", 1.0)

    if pattern == "classic":
        v_theory = 4 * ax_mm * fx
    elif pattern in ("sinusoidal", "wobble"):
        v_theory = np.pi * ax_mm * fx
    elif pattern == "lissajous":
        v_theory = np.pi * np.sqrt((ax_mm * fx)**2 + (ay_mm * fy)**2)
    else:
        v_theory = speed.max()

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4))
    fig.suptitle(
        f"Probe velocity — pattern: {pattern}  (f₁={fx:.0f} Hz, f₂={fy:.0f} Hz)",
        fontsize=10,
    )

    # ── Left: speed vs time ──────────────────────────────────────────────────
    ax1.plot(t_plot, s_plot, lw=0.7, color="#2176ae", alpha=0.85)
    ax1.axhline(speed.mean(), color="orange", lw=1.5, ls="--",
                label=f"Mean = {speed.mean():.0f} mm/s")
    ax1.axhline(v_theory, color="#cc3333", lw=1.0, ls=":",
                label=f"v_max theory = {v_theory:.0f} mm/s")

    # Shade the low-speed zone (below 25th percentile) in light red
    p25_speed = np.percentile(speed, 25)
    ax1.axhspan(0, p25_speed, alpha=0.12, color="red",
                label=f"Low-speed zone (< {p25_speed:.0f} mm/s)")

    ax1.set_xlabel("Time (ms)")
    ax1.set_ylabel("Speed (mm/s)")
    ax1.set_title("Speed vs time  (low speed → high dose)")
    ax1.legend(fontsize=8)
    ax1.set_xlim(t_plot[0], min(t_plot[-1], t_plot[0] + 20))

    # ── Right: coloured speed histogram ──────────────────────────────────────
    n_bins               = 80
    counts, bin_edges    = np.histogram(speed, bins=n_bins, density=True)
    bin_centers          = 0.5 * (bin_edges[:-1] + bin_edges[1:])
    s_min, s_max         = bin_centers.min(), bin_centers.max()
    s_norm               = (bin_centers - s_min) / max(s_max - s_min, 1e-12)
    bar_colors           = plt.cm.RdYlGn(s_norm)   # red=slow, green=fast

    for i in range(len(counts)):
        ax2.bar(bin_centers[i], counts[i],
                width=bin_edges[i + 1] - bin_edges[i],
                color=bar_colors[i], edgecolor="none", alpha=0.9)

    ax2.axvline(speed.mean(), color="orange", lw=2, ls="--",
                label=f"Mean = {speed.mean():.0f} mm/s")
    ax2.set_xlabel("Speed (mm/s)")
    ax2.set_ylabel("Probability density")
    ax2.set_title("Speed distribution\nRed = slow = more dose  |  Green = fast = less dose")
    ax2.legend(fontsize=8)

    # Pattern-specific annotation
    if pattern in ("sinusoidal", "lissajous"):
        ax2.text(
            0.98, 0.97,
            "Sinusoidal → arcsine\ndistribution:\nmore time at v≈0\n(edge cusping)",
            transform=ax2.transAxes, fontsize=7, va="top", ha="right",
            bbox=dict(boxstyle="round,pad=0.3", facecolor="lightyellow",
                      edgecolor="#ccaa00", alpha=0.9),
        )
    elif pattern == "classic":
        ax2.text(
            0.98, 0.97,
            "Triangle wave →\nnearly constant speed\n(good uniformity)\nSmall peak at v≈0\n(vertex dwell)",
            transform=ax2.transAxes, fontsize=7, va="top", ha="right",
            bbox=dict(boxstyle="round,pad=0.3", facecolor="#e8f4e8",
                      edgecolor="#448844", alpha=0.9),
        )

    fig.tight_layout()
    return fig


def animate_trajectory(x_traj, y_traj, t_arr, aperture_rect=None, save_path=None):
    """Animate beam trajectory. Subsamples to <= 500 frames. Returns FuncAnimation."""
    stride    = max(1, len(t_arr) // 500)
    x_s       = x_traj[::stride]
    y_s       = y_traj[::stride]
    t_s       = t_arr[::stride]
    n_frames  = len(x_s)
    tail_len  = min(50, n_frames)

    fig, ax = plt.subplots(figsize=(6, 6))

    if aperture_rect is not None:
        xL, xR, yB, yT = aperture_rect
        rect = patches.Rectangle(
            (xL, yB), xR - xL, yT - yB,
            linewidth=2, edgecolor="#00e5cc", facecolor="none", linestyle="--",
        )
        ax.add_patch(rect)

    scatter = ax.scatter([], [], s=20, c=[], cmap="plasma", vmin=t_s[0], vmax=t_s[-1])
    dot,    = ax.plot([], [], "ro", markersize=8)

    margin = max(np.ptp(x_s), np.ptp(y_s)) * 0.05 + 1
    ax.set_xlim(x_s.min() - margin, x_s.max() + margin)
    ax.set_ylim(y_s.min() - margin, y_s.max() + margin)
    ax.set_xlabel("X (mm)")
    ax.set_ylabel("Y (mm)")
    ax.set_title("Beam Trajectory")

    def init():
        scatter.set_offsets(np.empty((0, 2)))
        dot.set_data([], [])
        return scatter, dot

    def update(frame):
        start  = max(0, frame - tail_len)
        offsets = np.column_stack([x_s[start:frame + 1], y_s[start:frame + 1]])
        scatter.set_offsets(offsets)
        scatter.set_array(t_s[start:frame + 1])
        dot.set_data([x_s[frame]], [y_s[frame]])
        return scatter, dot

    ani = FuncAnimation(fig, update, frames=n_frames, init_func=init,
                        blit=True, interval=40)

    if save_path is not None:
        ani.save(save_path, writer="pillow", fps=25)

    return ani


def plot_dwell_hist(rho, aperture_mask):
    """
    Histogram of dwell-time values inside aperture.
    Bars are blue.  Vertical lines mark mean (orange), IQR bounds (green),
    and 95th percentile (red) so skew and outliers are immediately visible.
    Returns matplotlib Figure.
    """
    vals = rho[aperture_mask > 0]
    vals = vals[vals > 0]  # drop unvisited pixels

    fig, ax = plt.subplots(figsize=(6, 4))

    if len(vals) > 0:
        iqr = np.percentile(vals, 75) - np.percentile(vals, 25)
        if iqr > 0:
            bin_width = 2.0 * iqr / len(vals) ** (1 / 3)
            n_bins = max(20, min(200, int((vals.max() - vals.min()) / bin_width)))
        else:
            n_bins = 50

        ax.hist(vals, bins=n_bins, color="#4488cc", edgecolor="none", alpha=0.75)

        mu  = vals.mean()
        p25 = np.percentile(vals, 25)
        p75 = np.percentile(vals, 75)
        p95 = np.percentile(vals, 95)

        ax.axvline(mu,  color="#ff7700", lw=2.0, ls="--",
                   label=f"Mean = {mu:.4g} s")
        ax.axvline(p25, color="#44bb44", lw=1.5, ls=":",
                   label=f"P25  = {p25:.4g} s")
        ax.axvline(p75, color="#44bb44", lw=1.5, ls=":",
                   label=f"P75  = {p75:.4g} s")
        ax.axvline(p95, color="#cc4444", lw=1.5, ls="-.",
                   label=f"P95  = {p95:.4g} s")
        ax.axvspan(p25, p75, alpha=0.12, color="green", label="IQR (25–75%)")
        ax.legend(fontsize=8)
    else:
        ax.text(0.5, 0.5, "No dwell data inside aperture",
                ha="center", va="center", transform=ax.transAxes)

    ax.set_xlabel("Dwell Time (s/bin)")
    ax.set_ylabel("Pixel Count")
    ax.set_title("Dwell-Time Distribution (aperture pixels only)\n"
                 "Narrow IQR = uniform dwell = good flatness")
    fig.tight_layout()
    return fig
