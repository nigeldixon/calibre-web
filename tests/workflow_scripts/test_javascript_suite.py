import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = sorted((REPO_ROOT / ".github" / "scripts").glob("*.js"))
JS_TESTS = Path(__file__).parent / "js"


def lcov_totals(path):
    """Return per-source LF/LH/BRF/BRH counts from an lcov report."""
    totals = {}
    for record in path.read_text(encoding="utf-8").split("end_of_record"):
        lines = record.splitlines()
        source = next((line[3:] for line in lines if line.startswith("SF:")), None)
        if not source:
            continue
        values = {}
        for line in lines:
            key, separator, value = line.partition(":")
            if separator and key in ("LF", "LH", "BRF", "BRH"):
                values[key] = int(value)
        totals[source] = values
    return totals


def test_javascript_workflow_scripts_have_coverage_data():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")

    lcov_path = Path(
        os.environ.get("JS_COVERAGE_LCOV_PATH", "coverage-workflow-js-lcov.info")
    )
    lcov_path.parent.mkdir(parents=True, exist_ok=True)

    test_files = sorted(str(path) for path in JS_TESTS.glob("*.test.js"))
    assert test_files, "no node:test files found"

    with tempfile.TemporaryDirectory() as tmp:
        spec_path = Path(tmp) / "javascript-tests.txt"
        proc = subprocess.run(
            [
                node,
                "--test",
                "--experimental-test-coverage",
                "--test-reporter=spec",
                "--test-reporter=lcov",
                f"--test-reporter-destination={spec_path}",
                f"--test-reporter-destination={lcov_path}",
                *test_files,
            ],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        details = spec_path.read_text(encoding="utf-8") if spec_path.exists() else ""
        assert proc.returncode == 0, details + proc.stdout + proc.stderr

    assert lcov_path.exists(), "Node did not write JavaScript coverage data"
    assert SCRIPTS, "no JavaScript workflow scripts found"

    coverage = lcov_totals(lcov_path)
    for script in SCRIPTS:
        relative = script.relative_to(REPO_ROOT).as_posix()
        values = coverage.get(relative)
        assert values is not None, f"missing JavaScript coverage for {relative}"
        assert values.get("LH") == values.get("LF"), (
            f"uncovered lines in {relative}: {values.get('LH')}/{values.get('LF')}"
        )
        assert values.get("BRH", 0) == values.get("BRF", 0), (
            f"uncovered branches in {relative}: "
            f"{values.get('BRH', 0)}/{values.get('BRF', 0)}"
        )
