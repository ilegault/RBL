"""
CLI entry point for the Raster Scan Analysis Tool.

Usage:
    python main.py --validate
    python main.py --config config.yaml
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import argparse
import json
import csv

import matplotlib
matplotlib.use("Agg")   # headless backend for CLI — must be set before any pyplot import

import numpy as np


def run_validate():
    import validation  # runs all tests on import via __main__ guard


def run_config(config_path):
    import yaml
    from defaults import DEFAULTS
    from patterns import get_pattern
    from dose import compute_dose
    from metrics import compute_all_metrics
    from viz import animate_trajectory

    with open(config_path) as f:
        overrides = yaml.safe_load(f) or {}

    params = dict(DEFAULTS)
    params.update(overrides)

    print(f"Loaded config: {config_path}")
    print(f"Pattern: {params['pattern']}, fx={params['fx_hz']}, fy={params['fy_hz']}")

    t_arr, x_arr, y_arr = get_pattern(params["pattern"], params)
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
    parser.add_argument("--config", type=str, help="YAML config file path")
    args = parser.parse_args()

    if args.validate:
        # Run validation as subprocess so it has its own exit code
        import subprocess
        result = subprocess.run(
            [sys.executable, os.path.join(os.path.dirname(__file__), "validation.py")],
            cwd=os.path.dirname(__file__),
        )
        sys.exit(result.returncode)
    elif args.config:
        run_config(args.config)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
