import json
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG = REPO_ROOT / "tests" / "workflow_scripts" / "coverage_support" / "coveragerc"
SUPPORT = CONFIG.parent


def test_python_coverage_config_counts_unimported_scripts(tmp_path):
    scripts = tmp_path / ".github" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "touched.py").write_text("value = 1\n", encoding="utf-8")
    (scripts / "untouched.py").write_text("value = 2\n", encoding="utf-8")
    data_file = tmp_path / "coverage-data"
    report = tmp_path / "report.json"

    env = os.environ.copy()
    env.update({
        "COVERAGE_PROCESS_START": str(CONFIG),
        "COVERAGE_FILE": str(data_file),
        "COVERAGE_SCRIPTS_DIR": str(scripts),
        "PYTHONPATH": os.pathsep.join([str(scripts), str(SUPPORT)]),
    })
    run = subprocess.run(
        [sys.executable, "-c", "import touched"],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0, run.stderr

    for args in (
        ["combine", "--rcfile", str(CONFIG), "--data-file", str(data_file)],
        [
            "json", "--rcfile", str(CONFIG), "--data-file", str(data_file),
            "-o", str(report),
        ],
    ):
        run = subprocess.run(
            [sys.executable, "-m", "coverage", *args],
            cwd=str(tmp_path),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert run.returncode == 0, run.stderr

    totals = json.loads(report.read_text(encoding="utf-8"))["totals"]
    assert totals["covered_lines"] == 1
    assert totals["num_statements"] == 2, "unimported script was omitted from coverage"


def test_ci_sets_up_node_for_javascript_coverage():
    workflow = (REPO_ROOT / ".github" / "workflows" / "test.yml").read_text(
        encoding="utf-8"
    )
    assert "- name: Set up Node" in workflow
    assert "actions/setup-node@" in workflow


def test_python_coverage_report_does_not_gate_on_threshold():
    workflow = (REPO_ROOT / ".github" / "workflows" / "test.yml").read_text(
        encoding="utf-8"
    )
    test_step = workflow.split("- name: Run workflow script tests", 1)[1].split(
        "- name: Run unit tests with coverage", 1
    )[0]
    assert "COVERAGE_SCRIPTS_DIR:" in test_step
    report_step = test_step.split("python -m coverage report", 1)[1].split(
        "python -m coverage xml", 1
    )[0]
    assert "--fail-under" not in report_step
    assert "coverage-workflow-scripts-term.txt" in test_step
