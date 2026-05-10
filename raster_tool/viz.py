import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.animation import FuncAnimation
from matplotlib.collections import LineCollection


def plot_heatmap(dose, x_edges, y_edges, aperture_rect, metrics_dict=None):
    """
    Plot dose map as a heatmap with aperture outline and optional metric annotations.
    aperture_rect = (xL, xR, yB, yT).
    Returns matplotlib Figure.
    """
    xL, xR, yB, yT = aperture_rect
    fig, ax = plt.subplots(figsize=(7, 6))

    pcm = ax.pcolormesh(x_edges, y_edges, dose.T, cmap="hot", shading="auto")
    fig.colorbar(pcm, ax=ax, label="Dose (a.u.)")

    rect = patches.Rectangle(
        (xL, yB), xR - xL, yT - yB,
        linewidth=2, edgecolor="white", facecolor="none", linestyle="--",
    )
    ax.add_patch(rect)

    ax.set_xlabel("X (mm)")
    ax.set_ylabel("Y (mm)")

    title = "Dose Map"
    if metrics_dict:
        flat = metrics_dict.get("flatness_pct", float("nan"))
        title = f"Dose Map — Flatness: {flat:.1f}%"

        pinch = metrics_dict.get("pinch_pct", float("nan"))
        ss = metrics_dict.get("steady_state", None)
        ss_str = "✓ STEADY" if ss else "✗ TRANSIENT"

        annotation = f"Pinch: {pinch:.1f}%\n{ss_str}"
        ax.text(
            0.02, 0.98, annotation,
            transform=ax.transAxes,
            fontsize=9, verticalalignment="top",
            color="white",
            bbox=dict(boxstyle="round,pad=0.3", facecolor="black", alpha=0.5),
        )

    ax.set_title(title)
    fig.tight_layout()
    return fig


def animate_trajectory(x_traj, y_traj, t_arr, aperture_rect=None, save_path=None):
    """
    Animate beam trajectory. Subsamples to <= 500 frames.
    Returns FuncAnimation object.
    """
    stride = max(1, len(t_arr) // 500)
    x_s = x_traj[::stride]
    y_s = y_traj[::stride]
    t_s = t_arr[::stride]
    n_frames = len(x_s)
    tail_len = min(50, n_frames)

    fig, ax = plt.subplots(figsize=(6, 6))

    if aperture_rect is not None:
        xL, xR, yB, yT = aperture_rect
        rect = patches.Rectangle(
            (xL, yB), xR - xL, yT - yB,
            linewidth=2, edgecolor="blue", facecolor="none", linestyle="--",
        )
        ax.add_patch(rect)

    scatter = ax.scatter([], [], s=20, c=[], cmap="plasma", vmin=t_s[0], vmax=t_s[-1])
    dot, = ax.plot([], [], "ro", markersize=8)

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
        start = max(0, frame - tail_len)
        tail_x = x_s[start : frame + 1]
        tail_y = y_s[start : frame + 1]
        tail_t = t_s[start : frame + 1]
        offsets = np.column_stack([tail_x, tail_y])
        scatter.set_offsets(offsets)
        scatter.set_array(tail_t)
        dot.set_data([x_s[frame]], [y_s[frame]])
        return scatter, dot

    ani = FuncAnimation(
        fig, update, frames=n_frames, init_func=init,
        blit=True, interval=40,
    )

    if save_path is not None:
        ani.save(save_path, writer="pillow", fps=25)

    return ani


def plot_dwell_hist(rho, aperture_mask):
    """
    Histogram of dwell-time values inside aperture.
    Returns matplotlib Figure.
    """
    vals = rho[aperture_mask > 0]

    fig, ax = plt.subplots(figsize=(6, 4))
    ax.hist(vals, bins=50, color="steelblue", edgecolor="white", alpha=0.8)
    if len(vals) > 0:
        mu = vals.mean()
        ax.axvline(mu, color="orange", linestyle="--", linewidth=2, label=f"Mean = {mu:.4g} s")
        ax.legend()
    ax.set_xlabel("Dwell Time (s/bin)")
    ax.set_ylabel("Pixel Count")
    ax.set_title("Dwell-Time Distribution")
    fig.tight_layout()
    return fig
