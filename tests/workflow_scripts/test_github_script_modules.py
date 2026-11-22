import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]

MODULE_WORKFLOWS = {
    "resolve_workflow_run_pr": ["quality_reports.yml"],
    "publish_quality_report": ["quality_reports.yml"],
    "resolve_push_runs": ["quality_reports.yml"],
    "publish_backfilled_reports": ["quality_reports.yml"],
    "sweep_stale_placeholders": ["quality_reports.yml"],
    "create_pending_quality_reports": ["codeql.yml"],
}


@pytest.mark.parametrize("module,workflows", MODULE_WORKFLOWS.items())
def test_extracted_module_is_loaded_by_its_workflows(module, workflows):
    wrapper = f"require('./.github/scripts/{module}.js')"
    for workflow in workflows:
        path = REPO_ROOT / ".github" / "workflows" / workflow
        assert wrapper in path.read_text(encoding="utf-8"), (module, workflow)


@pytest.mark.parametrize("module", MODULE_WORKFLOWS)
def test_extracted_module_exports_an_async_function(module):
    if not shutil.which("node"):
        pytest.skip("node is not installed")

    script = (
        f"const m = require('./.github/scripts/{module}.js'); "
        "if (typeof m !== 'function') throw new Error('expected a function export');"
    )
    proc = subprocess.run(
        ["node", "-e", script],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
