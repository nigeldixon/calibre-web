import pytest

PYTHON_HELPERS = {
    "codeql_pr_comment.py": "marker",
    "semgrep_filter_sarif.py": "write_scope",
    "semgrep_pr_comment.py": "marker",
    "test_coverage_comment.py": "marker",
    "test_summary.py": "suites",
}


@pytest.mark.parametrize("name, expected_key", PYTHON_HELPERS.items())
def test_helpers_execute_in_process_for_coverage(name, expected_key, tmp_path, load_quality_script):
    # The behavioural tests run these helpers as subprocesses, matching CI.
    # This in-process pass exists so pytest-cov can measure .github/scripts.
    if name == "semgrep_filter_sarif.py":
        results = tmp_path / "semgrep-results"
        results.mkdir()
        (results / "scan-exit-code.txt").write_text("0\n", encoding="utf-8")

    namespace = load_quality_script(
        name,
        tmp_path,
        env={"COVERAGE_XML_PATH": str(tmp_path / "missing.xml")},
    )

    assert expected_key in namespace
