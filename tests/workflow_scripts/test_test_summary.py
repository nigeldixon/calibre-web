import os


def test_summary_reports_each_suite_and_coverage(tmp_path, run_quality_script):
    (tmp_path / "workflow-script-test-output.txt").write_text("18 passed in 0.87s\n", encoding="utf-8")
    (tmp_path / "unit-test-output.txt").write_text(".... 12 passed in 1.50s\n", encoding="utf-8")
    (tmp_path / "e2e-test-output.txt").write_text("no tests ran in 0.02s\n", encoding="utf-8")
    (tmp_path / "functional-test-output.txt").write_text("2 failed, 3 passed in 4.00s\n", encoding="utf-8")
    (tmp_path / "coverage-term.txt").write_text("TOTAL 71%\n", encoding="utf-8")
    (tmp_path / "coverage-workflow-scripts-term.txt").write_text("TOTAL 88%\n", encoding="utf-8")

    proc = run_quality_script(
        "test_summary.py",
        tmp_path,
        env={
            "WORKFLOW_SCRIPT_OUTCOME": "success",
            "UNIT_OUTCOME": "success",
            "E2E_OUTCOME": "skipped",
            "FUNCTIONAL_OUTCOME": "failure",
        },
    )

    out = proc.stdout
    assert proc.returncode == 0
    assert "| ✅ | Workflow script tests | `18 passed in 0.87s` |" in out
    assert "| ✅ | Unit (in-process) | `.... 12 passed in 1.50s` |" in out
    assert "| ⏭️ | Playwright E2E | `no tests ran in 0.02s` |" in out
    assert "| ❌ | HTTP functional | `2 failed, 3 passed in 4.00s` |" in out
    assert "**App coverage:**" in out
    assert "TOTAL 71%" in out
    assert "**Workflow script coverage:**" in out
    assert "TOTAL 88%" in out


def test_extract_summary_handles_unreadable_and_plain_count_lines(
    load_quality_script, tmp_path
):
    script = load_quality_script("test_summary.py", tmp_path)
    extract_summary = script["extract_summary"]

    unreadable = tmp_path / "unreadable.txt"
    unreadable.mkdir()
    assert extract_summary(str(unreadable)) == "no results"

    counts = tmp_path / "counts.txt"
    counts.write_text("collecting tests\n12 passed, 1 failed\n", encoding="utf-8")
    assert extract_summary(str(counts)) == "12 passed, 1 failed"

    no_tests = tmp_path / "no-tests.txt"
    no_tests.write_text("no tests ran\n", encoding="utf-8")
    assert extract_summary(str(no_tests)) == "no tests ran"

    noise = tmp_path / "noise.txt"
    noise.write_text("nothing useful here\n", encoding="utf-8")
    assert extract_summary(str(noise)) == "no results"


def test_read_coverage_handles_unreadable_file(load_quality_script, tmp_path):
    script = load_quality_script("test_summary.py", tmp_path)
    unreadable = tmp_path / "coverage-term.txt"
    unreadable.mkdir()

    assert script["read_coverage"](str(unreadable)) == "No coverage data generated."


def test_app_coverage_falls_back_to_coverage_command(tmp_path, run_quality_script):
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir()
    fake_python = bin_dir / "python"
    fake_python.write_text("#!/bin/sh\nprintf 'TOTAL 42%%\\n'\n", encoding="utf-8")
    fake_python.chmod(0o755)
    (tmp_path / ".coverage").write_text("dummy", encoding="utf-8")

    proc = run_quality_script(
        "test_summary.py",
        tmp_path,
        env={"PATH": f"{bin_dir}:{os.environ['PATH']}"},
    )

    assert proc.returncode == 0
    assert "TOTAL 42%" in proc.stdout


def test_app_coverage_command_failure_is_not_reported_as_data(tmp_path, run_quality_script):
    (tmp_path / ".coverage").write_text("dummy", encoding="utf-8")

    proc = run_quality_script("test_summary.py", tmp_path, env={"PATH": ""})

    assert proc.returncode == 0
    assert proc.stdout.count("No coverage data generated.") == 2


def test_summary_without_outputs_does_not_claim_results(tmp_path, run_quality_script):
    proc = run_quality_script("test_summary.py", tmp_path)

    out = proc.stdout
    assert proc.returncode == 0
    assert out.count("| ⚪ |") == 4
    assert out.count("`no results`") == 4
    assert out.count("No coverage data generated.") == 2
