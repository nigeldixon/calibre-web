import json


def test_collects_failures_from_failed_suites(tmp_path, run_quality_script):
    (tmp_path / "workflow-script-test-output.txt").write_text(
        "\x1b[31mFAILED tests/workflow_scripts/test_x.py::test_a\x1b[0m - AssertionError: bad\n"
        "ERROR tests/workflow_scripts/test_y.py::test_b - collection failed\n"
        "1 failed, 50 passed in 2.10s\n",
        encoding="utf-8",
    )
    (tmp_path / "unit-test-output.txt").write_text("12 passed in 1.50s\n", encoding="utf-8")

    proc = run_quality_script(
        "collect_test_results.py",
        tmp_path,
        env={
            "WORKFLOW_SCRIPT_OUTCOME": "failure",
            "UNIT_OUTCOME": "success",
            "RUN_URL": "https://example.test/run/1",
        },
    )

    assert proc.returncode == 0
    data = json.loads((tmp_path / "test-results.json").read_text(encoding="utf-8"))
    assert data["run_url"] == "https://example.test/run/1"
    suites = {suite["key"]: suite for suite in data["suites"]}
    assert suites["workflow_script_tests"]["summary"] == "1 failed, 50 passed in 2.10s"
    assert suites["workflow_script_tests"]["failures"] == [
        {
            "kind": "FAILED",
            "nodeid": "tests/workflow_scripts/test_x.py::test_a",
            "message": "AssertionError: bad",
        },
        {
            "kind": "ERROR",
            "nodeid": "tests/workflow_scripts/test_y.py::test_b",
            "message": "collection failed",
        },
    ]
    assert suites["unit_tests"]["failures"] == []
    assert suites["e2e_tests"]["summary"] == "no results"


def test_successful_outcome_ignores_stale_failure_lines(tmp_path, run_quality_script):
    (tmp_path / "workflow-script-test-output.txt").write_text(
        "FAILED tests/workflow_scripts/test_x.py::test_a - old failure\n"
        "51 passed in 2.10s\n",
        encoding="utf-8",
    )

    proc = run_quality_script(
        "collect_test_results.py",
        tmp_path,
        env={"WORKFLOW_SCRIPT_OUTCOME": "success"},
    )

    assert proc.returncode == 0
    data = json.loads((tmp_path / "test-results.json").read_text(encoding="utf-8"))
    assert data["suites"][0]["failures"] == []


def test_custom_output_paths_are_honoured(tmp_path, run_quality_script):
    (tmp_path / "custom.txt").write_text(
        "FAILED tests/unit/test_a.py::test_fail - boom\n2 failed in 0.10s\n",
        encoding="utf-8",
    )

    proc = run_quality_script(
        "collect_test_results.py",
        tmp_path,
        env={
            "UNIT_TESTS_OUTPUT": "custom.txt",
            "UNIT_OUTCOME": "failure",
            "TEST_RESULTS_JSON": "custom-results.json",
        },
    )

    assert proc.returncode == 0
    data = json.loads((tmp_path / "custom-results.json").read_text(encoding="utf-8"))
    assert data["suites"][1]["failures"][0]["nodeid"] == "tests/unit/test_a.py::test_fail"
