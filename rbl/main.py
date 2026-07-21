"""
Entry point for the Raster Scan Analysis Tool.

Usage:
    python main.py              # launch PySide6 GUI (default)
    python main.py --validate   # run validation suite
    python main.py --config config.yaml   # headless CLI pipeline
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import argparse
import json
import csv

import numpy as np


def run_gui():
    from rbl.gui.app import main as app_main
    app_main()


def run_validate():
    pass


def run_config(config_path):
    import matplotlib
    matplotlib.use("Agg")   # headless backend — set before any pyplot import
    import matplotlib.pyplot as _plt  # noqa: F401 — triggers backend registration

    import yaml
    from rbl.scan.patterns import get_realistic_trajectory
    from rbl.scan.dose import compute_dose
    from rbl.scan.metrics import compute_all_metrics
    from rbl.gui.viz import animate_trajectory

    with open(config_path) as f:
        overrides = yaml.safe_load(f) or {}

    params = dict(DEFAULTS)
    params.update(overrides)

    print(f"Loaded config: {config_path}")
    print(f"Pattern: {params['pattern']}, fx={params['fx_hz']}, fy={params['fy_hz']}")

    t_arr, x_arr, y_arr = get_realistic_trajectory(params)
    dose, rho, xe, ye = compute_dose(params, t_arr, x_arr, y_arr)
    dt = t_arr[1] - t_arr[0] if len(t_arr) > 1 else 1.0
    metrics = compute_all_metrics(dose, rho, x_arr, y_arr, dt, xe, ye, params)

    # Save dose_map.csv
    print("Saving dose_map.csv ...")
    with open("dose_map.csv", "w", newline="") as f:
        writer = csv.writer(f)
        for row in dose.T:
            writer.writerow(row)

    # Save metrics.json
    print("Saving metrics.json ...")
    serializable = {k: (float(v) if isinstance(v, (np.floating, np.integer)) else v)
                    for k, v in metrics.items()}
    with open("metrics.json", "w") as f:
        json.dump(serializable, f, indent=2)

    # Save trajectory.gif
    print("Saving trajectory.gif ...")
    aperture_rect = (
        params["aperture_xL_mm"], params["aperture_xR_mm"],
        params["aperture_yB_mm"], params["aperture_yT_mm"],
    )
    ani = animate_trajectory(x_arr, y_arr, t_arr, aperture_rect=aperture_rect,
                             save_path="trajectory.gif")

    print("\nMetrics summary:")
    for k, v in metrics.items():
        print(f"  {k}: {v}")
    print("\nDone. Outputs: dose_map.csv, metrics.json, trajectory.gif")


def main():
    parser = argparse.ArgumentParser(description="Raster Scan Analysis Tool")
    parser.add_argument("--validate", action="store_true", help="Run validation suite")
    parser.add_argument("--config", type=str, help="YAML config file path (headless CLI)")
    args = parser.parse_args()

    if args.validate:
        import subprocess
        result = subprocess.run(
            [sys.executable, os.path.join(os.path.dirname(__file__), "config/validation.py")],
            cwd=os.path.dirname(__file__),
        )
        sys.exit(result.returncode)
    elif args.config:
        run_config(args.config)
    else:
        run_gui()


if __name__ == "__main__":
    main()
