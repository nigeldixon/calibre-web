def test_summary_reports_each_suite_and_coverage(tmp_path, run_quality_script):
    (tmp_path / "unit-test-output.txt").write_text(".... 12 passed in 1.50s\n", encoding="utf-8")
    (tmp_path / "e2e-test-output.txt").write_text("no tests ran in 0.02s\n", encoding="utf-8")
    (tmp_path / "functional-test-output.txt").write_text("2 failed, 3 passed in 4.00s\n", encoding="utf-8")
    (tmp_path / "coverage-term.txt").write_text("TOTAL 71%\n", encoding="utf-8")

    proc = run_quality_script(
        "test_summary.py",
        tmp_path,
        env={
            "UNIT_OUTCOME": "success",
            "E2E_OUTCOME": "skipped",
            "FUNCTIONAL_OUTCOME": "failure",
        },
    )

    out = proc.stdout
    assert proc.returncode == 0
    assert "| ✅ | Unit (in-process) | `.... 12 passed in 1.50s` |" in out
    assert "| ⏭️ | Playwright E2E | `no tests ran in 0.02s` |" in out
    assert "| ❌ | HTTP functional | `2 failed, 3 passed in 4.00s` |" in out
    assert "TOTAL 71%" in out


def test_summary_without_outputs_does_not_claim_results(tmp_path, run_quality_script):
    proc = run_quality_script("test_summary.py", tmp_path)

    out = proc.stdout
    assert proc.returncode == 0
    assert out.count("| ⚪ |") == 3
    assert out.count("`no results`") == 3
    assert "No coverage data generated." in out
