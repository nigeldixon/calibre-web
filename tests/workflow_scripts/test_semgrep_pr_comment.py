def finding(uri, level):
    return {
        "level": level,
        "message": {"text": f"{level} in {uri}"},
        "locations": [
            {"physicalLocation": {"artifactLocation": {"uri": uri}}}
        ],
    }


def notification(level, key, text, uri):
    return {
        "level": level,
        "descriptor": {"id": key},
        "message": {"text": text},
        "locations": [
            {"physicalLocation": {"artifactLocation": {"uri": uri}}}
        ],
    }


def make_raw_sarif():
    return {
        "version": "2.1.0",
        "runs": [
            {
                "tool": {"driver": {"name": "semgrep"}},
                "results": [
                    finding("cps/a.py", "error"),
                    finding("cps/a.py", "warning"),
                    finding("cps/b.py", "note"),
                ],
                "invocations": [
                    {
                        "toolExecutionNotifications": [
                            notification("error", "E1", "scan aborted", "cps/a.py"),
                            notification("warning", "W1", "rule timed out", "cps/a.py"),
                        ]
                    }
                ],
            }
        ],
    }


def test_trusted_path_filters_findings_and_reports_exec_errors(tmp_path, run_quality_script, write_json):
    write_json(tmp_path / "semgrep.raw.sarif", make_raw_sarif())
    (tmp_path / "scan-exit-code.txt").write_text("0\n", encoding="utf-8")
    changed = tmp_path / "changed-files.txt"
    changed.write_text("cps/a.py\n./cps\\a.py\n", encoding="utf-8")

    proc = run_quality_script(
        "semgrep_pr_comment.py",
        tmp_path,
        env={
            "CHANGED_FILES_FILE": str(changed),
            "RAW_SARIF_PATH": "semgrep.raw.sarif",
            "SCAN_EXIT_PATH": "scan-exit-code.txt",
            "PR_BASE_SHA": "a" * 40,
            "PR_HEAD_SHA": "b" * 40,
            "ANALYZE_RESULT": "success",
            "RUN_URL": "https://example.test/run/1",
            "ANALYSIS_URL": "https://example.test/alerts",
        },
    )

    out = proc.stdout
    assert proc.returncode == 0
    assert "| ⚠️ | 2 | 1 | 1 | 0 |" in out
    assert "**Whole-solution issues:** 3 · **In changed files:** 2" in out
    assert "**Analysis errors:** 1 · **Analysis warnings:** 1" in out
    assert "<details><summary>⚠️ Analysis warnings (1)</summary>" in out
    assert "- 1 × W1" in out
    assert "❌ Semgrep scan failed (exit 0, 1 error-level notification(s))" in out
    assert "**Full analysis:** [Workflow run](https://example.test/run/1)" in out
    assert "**Code scanning:** [PR-scoped Semgrep alerts](https://example.test/alerts)" in out
    assert "_Scope: 1 changed file(s)" in out


def test_legacy_path_without_sarif_does_not_report_all_clear(tmp_path, run_quality_script):
    proc = run_quality_script("semgrep_pr_comment.py", tmp_path, env={"ANALYZE_RESULT": "success"})

    out = proc.stdout
    assert proc.returncode == 0
    assert "| ⚪ | - | - | - | - |" in out
    assert "**Analysis errors:** 1 · **Analysis warnings:** 0" in out
    assert "_No Semgrep SARIF was produced; the scan did not complete._" in out


def test_legacy_path_tolerates_null_exec_errors_in_scope(tmp_path, run_quality_script, write_json):
    results = tmp_path / "semgrep-results"
    results.mkdir()
    write_json(
        results / "semgrep.sarif",
        {
            "version": "2.1.0",
            "runs": [
                {
                    "tool": {"driver": {"name": "semgrep"}},
                    "results": [finding("cps/a.py", "error")],
                }
            ],
        },
    )
    write_json(
        results / "scope.json",
        {
            "scoped": False,
            "reason": "unknown",
            "scan_ok": True,
            "scan_exit": 0,
            "total_all": 5,
            "warnings": 0,
            "warning_detail": {},
            "exec_errors": None,
        },
    )

    proc = run_quality_script("semgrep_pr_comment.py", tmp_path, env={"ANALYZE_RESULT": "success"})

    out = proc.stdout
    assert proc.returncode == 0
    assert "**Analysis errors:** 0 · **Analysis warnings:** 0" in out
    assert "**Whole-solution issues:** 5 · **In changed files:** 1" in out
    assert "❌ Semgrep scan failed" not in out
