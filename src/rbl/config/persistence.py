"""
persistence.py
Small on-disk JSON config store, keyed on instrument serial so a saved
function-generator config survives a USB replug (a re-enumerated device can
land at a different VISA address).
"""
import json

from rbl.config.paths import FUNCGEN_CONFIG

CONFIG_PATH = FUNCGEN_CONFIG


def load_config() -> dict:
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(data: dict):
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass
