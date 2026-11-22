from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
GITIGNORE = ROOT / ".gitignore"

GENERATED_ARTIFACTS = {
    ".coverage",
    ".coverage.*",
    "coverage.xml",
    "coverage-term.txt",
    "coverage-term.tmp",
    "diff-coverage.md",
    "coverage-workflow-scripts.xml",
    "coverage-workflow-scripts-term.txt",
    "coverage-workflow-js-lcov.info",
    "coverage-workflow-shell-lcov.info",
    "workflow-script-test-output.txt",
    "unit-test-output.txt",
    "e2e-test-output.txt",
    "functional-test-output.txt",
    "test-results.json",
    "test-failure-comments.json",
    "changed-files.json",
    "coverage-comment.md",
}


def test_generated_workflow_artifacts_are_ignored():
    patterns = set(GITIGNORE.read_text(encoding="utf-8").splitlines())
    missing = sorted(GENERATED_ARTIFACTS - patterns)
    assert not missing, f"missing ignore patterns: {missing}"


def test_ci_cleanup_removes_all_generated_artifacts():
    workflow = (ROOT / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")
    cleanup = workflow.split("- name: Remove checked-in coverage data", 1)[1].split(
        "- name: Set up Python", 1
    )[0]
    missing = sorted(artifact for artifact in GENERATED_ARTIFACTS if artifact not in cleanup)
    assert not missing, f"cleanup misses generated artifacts: {missing}"
