import os
from pathlib import Path

import coverage

# Coverage starts in subprocesses whose cwd is a temporary fixture directory.
# Resolve the script tree from this config file so `source` in coveragerc always
# points at the real workflow scripts, including files no test has imported.
os.environ.setdefault(
    "COVERAGE_SCRIPTS_DIR",
    str(Path(__file__).resolve().parents[3] / ".github" / "scripts"),
)

coverage.process_startup()
