"""Load the shared quality-report constants used by both JS and Python scripts."""

import json
from pathlib import Path

CONSTANTS_PATH = Path(__file__).resolve().parent / "report_constants.json"


def load_constants():
    with CONSTANTS_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)
