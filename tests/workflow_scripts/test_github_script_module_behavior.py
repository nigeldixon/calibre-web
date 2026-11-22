import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("github_script_module_behavior.js")


def test_extracted_github_script_modules_have_expected_behavior():
    if not shutil.which("node"):
        pytest.skip("node is not installed")

    proc = subprocess.run(
        ["node", str(HARNESS)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
