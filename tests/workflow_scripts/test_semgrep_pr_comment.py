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


def test_json_uri_and_counting_helpers(load_quality_script, tmp_path):
    script = load_quality_script("semgrep_pr_comment.py", tmp_path)

    invalid = tmp_path / "invalid.json"
    invalid.write_text("{not json", encoding="utf-8")
    assert script["load_json"](str(invalid)) is None
    assert script["load_json"](str(tmp_path / "missing.json")) is None
    assert script["norm_uri"]("") == ""
    assert script["norm_uri"](".\\cps\\a.py") == "cps/a.py"
    assert script["result_in_changed"](finding("cps/a.py", "warning"), None) is True
    assert script["result_in_changed"]({"level": "warning"}, {"cps/a.py"}) is False

    sarif = {
        "runs": [
            {
                "results": [
                    finding("a.py", "note"),
                    finding("b.py", "none"),
                    finding("c.py", "error"),
                    finding("d.py", "info"),
                ]
            }
        ]
    }
    assert script["count_findings"](sarif) == {
        "results": 4, "error": 1, "warning": 1, "note": 2,
    }


def test_trusted_path_rejects_invalid_raw_sarif(tmp_path, run_quality_script):
    (tmp_path / "semgrep.raw.sarif").write_text("{not json", encoding="utf-8")
    changed = tmp_path / "changed-files.txt"
    changed.write_text("cps/a.py\n", encoding="utf-8")

    proc = run_quality_script(
        "semgrep_pr_comment.py",
        tmp_path,
        env={
            "CHANGED_FILES_FILE": str(changed),
            "RAW_SARIF_PATH": "semgrep.raw.sarif",
            "SCAN_EXIT_PATH": "missing-exit-code.txt",
            "ANALYZE_RESULT": "success",
        },
    )

    out = proc.stdout
    assert proc.returncode == 0
    assert "| ⚪ | - | - | - | - |" in out
    assert "**Analysis errors:** 1" in out
    assert "_Semgrep SARIF could not be parsed; the scan is marked as failed._" in out


def test_unreadable_changed_list_and_scan_exit_do_not_claim_success(
    tmp_path, run_quality_script, write_json
):
    write_json(tmp_path / "semgrep.raw.sarif", make_raw_sarif())
    changed = tmp_path / "changed-files.txt"
    changed.mkdir()
    (tmp_path / "scan-exit-code.txt").write_text("not-a-number\n", encoding="utf-8")

    proc = run_quality_script(
        "semgrep_pr_comment.py",
        tmp_path,
        env={
            "CHANGED_FILES_FILE": str(changed),
            "RAW_SARIF_PATH": "semgrep.raw.sarif",
            "SCAN_EXIT_PATH": "scan-exit-code.txt",
            "ANALYZE_RESULT": "success",
        },
    )

    out = proc.stdout
    assert proc.returncode == 0
    assert "❌ Semgrep scan failed (exit None," in out
    assert "**Analysis errors:** 1" in out


def test_scope_line_without_base_and_head_hashes(tmp_path, run_quality_script, write_json):
    write_json(tmp_path / "semgrep.raw.sarif", make_raw_sarif())
    (tmp_path / "scan-exit-code.txt").write_text("0\n", encoding="utf-8")
    changed = tmp_path / "changed-files.txt"
    changed.write_text("\ncps/a.py\n", encoding="utf-8")

    proc = run_quality_script(
        "semgrep_pr_comment.py",
        tmp_path,
        env={
            "CHANGED_FILES_FILE": str(changed),
            "RAW_SARIF_PATH": "semgrep.raw.sarif",
            "SCAN_EXIT_PATH": "scan-exit-code.txt",
            "ANALYZE_RESULT": "success",
        },
    )

    out = proc.stdout
    assert proc.returncode == 0
    assert "_Scope: 1 changed file(s) in this PR._" in out
    assert " (`" not in out


def test_legacy_scope_reasons_change_the_description(tmp_path, run_quality_script, write_json):
    results = tmp_path / "semgrep-results"
    results.mkdir()
    write_json(
        results / "semgrep.sarif",
        {"version": "2.1.0", "runs": [{"tool": {"driver": {"name": "semgrep"}}, "results": []}]},
    )

    for reason, expected in (
        ("diff_failed_full_scan", "Automated Semgrep summary (could not determine PR diff; showing unfiltered/full scan results)."),
        ("unknown", "Automated Semgrep summary (scheduled full scan)."),
    ):
        write_json(
            results / "scope.json",
            {
                "scoped": False,
                "reason": reason,
                "scan_ok": True,
                "scan_exit": 0,
                "total_all": 0,
                "warnings": 0,
                "warning_detail": {},
                "exec_errors": 0,
            },
        )

        proc = run_quality_script(
            "semgrep_pr_comment.py",
            tmp_path,
            env={"ANALYZE_RESULT": "success", "GITHUB_EVENT_NAME": "schedule"},
        )

        assert proc.returncode == 0
        assert expected in proc.stdout


def test_legacy_path_missing_scope_file_is_an_error(tmp_path, run_quality_script, write_json):
    results = tmp_path / "semgrep-results"
    results.mkdir()
    write_json(
        results / "semgrep.sarif",
        {"version": "2.1.0", "runs": [{"tool": {"driver": {"name": "semgrep"}}, "results": []}]},
    )

    proc = run_quality_script(
        "semgrep_pr_comment.py", tmp_path, env={"ANALYZE_RESULT": "success"}
    )

    out = proc.stdout
    assert proc.returncode == 0
    assert "**Analysis errors:** 1" in out
    assert "_Semgrep SARIF could not be parsed; the scan is marked as failed._" in out


def test_empty_scope_file_is_reported_as_an_error(tmp_path, run_quality_script, write_json):
    results = tmp_path / "semgrep-results"
    results.mkdir()
    write_json(
        results / "semgrep.sarif",
        {"version": "2.1.0", "runs": [{"tool": {"driver": {"name": "semgrep"}}, "results": []}]},
    )
    write_json(results / "scope.json", {})

    proc = run_quality_script(
        "semgrep_pr_comment.py",
        tmp_path,
        env={"ANALYZE_RESULT": "success", "GITHUB_EVENT_NAME": "schedule"},
    )

    out = proc.stdout
    assert proc.returncode == 0
    assert "Automated Semgrep summary (scheduled full scan)." in out
    assert "**Analysis errors:** 1" in out
    assert "**Analysis warnings:** 0" in out
    assert "_Semgrep SARIF could not be parsed; the scan is marked as failed._" in out


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
