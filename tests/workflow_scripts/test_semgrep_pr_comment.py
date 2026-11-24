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
    shared = load_quality_script("sarif_validation.py", tmp_path)

    invalid = tmp_path / "invalid.json"
    invalid.write_text("{not json", encoding="utf-8")
    assert script["load_json"](str(invalid)) is None
    assert script["load_json"](str(tmp_path / "missing.json")) is None
    assert script["norm_uri"]("") == ""
    assert script["norm_uri"](".\\cps\\a.py") == "cps/a.py"
    assert script["result_in_changed"](finding("cps/a.py", "warning"), None) is True
    assert script["result_in_changed"]({"level": "warning"}, {"cps/a.py"}) is False

    assert shared["valid_artifact_location"]({"uri": "a.py"}) is True
    assert shared["valid_artifact_location"]({"uri": 7}) is False
    assert shared["valid_artifact_location"]("bad") is False
    assert shared["valid_location"]({}) is True
    assert shared["valid_location"]({"physicalLocation": "bad"}) is False
    assert shared["valid_location"]("bad") is False
    assert shared["valid_message"]({"text": "ok"}) is True
    assert shared["valid_message"]({"text": 7}) is False
    assert shared["valid_message"]("bad") is False
    assert shared["valid_result_shape"]({"level": "warning"}) is True
    assert shared["valid_result_shape"]({"level": 7}) is False
    assert shared["valid_result_shape"]({"message": "bad"}) is False
    assert shared["valid_result_shape"]({"locations": "bad"}) is False
    assert shared["valid_result_shape"]({"locations": ["bad"]}) is False
    assert shared["valid_result_shape"]("bad") is False
    assert shared["valid_notification_shape"]({"level": "warning"}) is True
    assert shared["valid_notification_shape"]({"level": 7}) is False
    assert shared["valid_notification_shape"]({"message": "bad"}) is False
    assert shared["valid_notification_shape"]({"descriptor": "bad"}) is False
    assert shared["valid_notification_shape"]({"descriptor": {"id": {"nested": 1}}}) is False
    assert shared["valid_notification_shape"]({"descriptor": {"id": ["x"]}}) is False
    assert shared["valid_notification_shape"]({"descriptor": {"id": "W1"}}) is True
    assert shared["valid_notification_shape"]("bad") is False
    assert script["valid_sarif"]({"runs": ["bad"]}) is False
    assert script["valid_sarif"]({"runs": [{"tool": {"driver": {}}, "results": [{"level": 7}]}]}) is False
    assert script["valid_sarif"]({"runs": [{
        "tool": {"driver": {}}, "results": [], "invocations": "bad",
    }]}) is False
    assert script["valid_sarif"]({"runs": [{
        "tool": {"driver": {}}, "results": [], "invocations": ["bad"],
    }]}) is False
    assert script["valid_sarif"]({"runs": [{
        "tool": {"driver": {}}, "results": [],
        "invocations": [{"toolExecutionNotifications": "bad"}],
    }]}) is False
    assert script["valid_sarif"]({"runs": [{
        "tool": {"driver": {}}, "results": [],
        "invocations": [{"toolExecutionNotifications": [{"level": 7}]}],
    }]}) is False

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


def test_findings_exit_code_is_not_reported_as_scan_failure(
    tmp_path, run_quality_script, write_json
):
    write_json(
        tmp_path / "semgrep.raw.sarif",
        {
            "version": "2.1.0",
            "runs": [
                {
                    "tool": {"driver": {"name": "semgrep"}},
                    "results": [finding("cps/a.py", "warning")],
                }
            ],
        },
    )
    (tmp_path / "scan-exit-code.txt").write_text("1\n", encoding="utf-8")
    changed = tmp_path / "changed-files.txt"
    changed.write_text("cps/a.py\n", encoding="utf-8")

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

    assert proc.returncode == 0
    assert "❌ Semgrep scan failed" not in proc.stdout
    assert "**Analysis errors:** 0" in proc.stdout


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


def test_legacy_path_reports_line_scope_from_filter_artifact(tmp_path, run_quality_script, write_json):
    results = tmp_path / "semgrep-results"
    results.mkdir()
    write_json(
        results / "semgrep.sarif",
        {"version": "2.1.0", "runs": [{"tool": {"driver": {"name": "semgrep"}}, "results": []}]},
    )
    write_json(
        results / "scope.json",
        {
            "scoped": True,
            "reason": "pr_changed_lines",
            "scope_kind": "lines",
            "changed_count": 2,
            "base": "a" * 40,
            "head": "b" * 40,
            "scan_ok": True,
            "scan_exit": 0,
            "total_all": 121,
            "warnings": 0,
            "warning_detail": {},
            "exec_errors": 0,
        },
    )

    proc = run_quality_script("semgrep_pr_comment.py", tmp_path, env={"ANALYZE_RESULT": "success"})

    out = proc.stdout
    assert proc.returncode == 0
    assert "Automated Semgrep summary for lines changed in this PR (filtered SARIF)." in out
    assert "**Whole-solution issues:** 121 · **In changed files:** 0" in out
    assert "_Scope: 2 changed file(s) in this PR (`aaaaaaa...bbbbbbb`)._" in out


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
    assert "### 🛡️ Semgrep analysis" in out
    assert "| ⚠️ | 2 | 1 | 1 | 0 |" in out
    assert "**Whole-solution issues:** 3 · **In changed files:** 2" in out
    assert "**Analysis errors:** 1 · **Analysis warnings:** 1" in out
    assert "<details><summary id="quality-semgrep-warnings"><a href="#quality-semgrep-warnings-anchor">⚠️ Analysis warnings (1)</a></summary>" in out
    assert "- 1 × W1" in out
    assert "❌ Semgrep scan failed (exit 0, 1 error-level notification(s))" in out
    assert "**Full analysis:** [Workflow run](https://example.test/run/1)" in out
    assert "**Code scanning:** [All Semgrep alerts](https://example.test/alerts)" in out
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


def test_trusted_path_line_scope_filters_to_changed_hunks(tmp_path, run_quality_script, write_json):
    base = "a" * 40
    head = "b" * 40
    raw = {
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {"name": "semgrep"}},
            "results": [
                {"level": "warning", "message": {"text": "on line"}, "locations": [{"physicalLocation": {"artifactLocation": {"uri": "cps/a.py"}, "region": {"startLine": 10}}}]},
                {"level": "warning", "message": {"text": "off line"}, "locations": [{"physicalLocation": {"artifactLocation": {"uri": "cps/a.py"}, "region": {"startLine": 99}}}]},
                {"level": "note", "message": {"text": "via related"}, "locations": [{"physicalLocation": {"artifactLocation": {"uri": "cps/a.py"}, "region": {"startLine": 99}}}], "relatedLocations": [{"physicalLocation": {"artifactLocation": {"uri": "cps/a.py"}, "region": {"startLine": 11}}}]},
            ],
            "invocations": [{"toolExecutionNotifications": []}],
        }],
    }
    write_json(tmp_path / "semgrep.raw.sarif", raw)
    (tmp_path / "scan-exit-code.txt").write_text("0\n", encoding="utf-8")
    (tmp_path / "changed-files.txt").write_text("cps/a.py\n", encoding="utf-8")

    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir(exist_ok=True)
    git = bin_dir / "git"
    git.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "fetch" ]; then exit 0; fi\n'
        'if [ "$1" = "diff" ] && echo "$@" | grep -q -- "--unified=0"; then\n'
        '  printf "%s\\n" "diff --git a/cps/a.py b/cps/a.py" "--- a/cps/a.py" "+++ b/cps/a.py" "@@ -10,2 +10,2 @@" "+x"\n'
        '  exit 0\n'
        'fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    git.chmod(0o755)

    import os

    proc = run_quality_script(
        "semgrep_pr_comment.py",
        tmp_path,
        env={
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "CHANGED_FILES_FILE": "changed-files.txt",
            "RAW_SARIF_PATH": "semgrep.raw.sarif",
            "SCAN_EXIT_PATH": "scan-exit-code.txt",
            "PR_BASE_SHA": base,
            "PR_HEAD_SHA": head,
            "ANALYZE_RESULT": "success",
        },
    )

    assert proc.returncode == 0
    out = proc.stdout
    assert "lines changed in this PR" in out
    assert "| ⚠️ | 2 | 0 | 1 | 1 |" in out
    assert "**Whole-solution issues:** 3 · **In changed files:** 2" in out


def test_trusted_path_falls_back_to_file_scope_when_diff_unavailable(tmp_path, run_quality_script, write_json):
    raw = {
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {"name": "semgrep"}},
            "results": [
                {"level": "warning", "message": {"text": "on line"}, "locations": [{"physicalLocation": {"artifactLocation": {"uri": "cps/a.py"}, "region": {"startLine": 99}}}]},
            ],
            "invocations": [{"toolExecutionNotifications": []}],
        }],
    }
    write_json(tmp_path / "semgrep.raw.sarif", raw)
    (tmp_path / "scan-exit-code.txt").write_text("0\n", encoding="utf-8")
    (tmp_path / "changed-files.txt").write_text("cps/a.py\n", encoding="utf-8")

    proc = run_quality_script(
        "semgrep_pr_comment.py",
        tmp_path,
        env={
            "CHANGED_FILES_FILE": "changed-files.txt",
            "RAW_SARIF_PATH": "semgrep.raw.sarif",
            "SCAN_EXIT_PATH": "scan-exit-code.txt",
            "PR_BASE_SHA": "a" * 40,
            "PR_HEAD_SHA": "b" * 40,
            "ANALYZE_RESULT": "success",
        },
    )

    assert proc.returncode == 0
    out = proc.stdout
    assert "files changed in this PR" in out
    assert "**In changed files:** 1" in out


def test_trusted_path_empty_diff_ranges_falls_back_to_file_scope(tmp_path, run_quality_script, write_json):
    base = "a" * 40
    head = "b" * 40
    raw = {
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {"name": "semgrep"}},
            "results": [
                {"level": "warning", "message": {"text": "on line"}, "locations": [{"physicalLocation": {"artifactLocation": {"uri": "cps/a.py"}, "region": {"startLine": 99}}}]},
            ],
            "invocations": [{"toolExecutionNotifications": []}],
        }],
    }
    write_json(tmp_path / "semgrep.raw.sarif", raw)
    (tmp_path / "scan-exit-code.txt").write_text("0\n", encoding="utf-8")
    (tmp_path / "changed-files.txt").write_text("cps/a.py\n", encoding="utf-8")

    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir(exist_ok=True)
    git = bin_dir / "git"
    git.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = fetch ]; then exit 0; fi\n"
        "if [ \"$1\" = diff ] && echo \"$@\" | grep -q -- --unified=0; then\n"
        "  printf '%s\\n' 'diff --git a/cps/a.py b/cps/a.py' '--- a/cps/a.py' '+++ b/cps/a.py'\n"
        "  exit 0\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    git.chmod(0o755)

    import os

    proc = run_quality_script(
        "semgrep_pr_comment.py",
        tmp_path,
        env={
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "CHANGED_FILES_FILE": "changed-files.txt",
            "RAW_SARIF_PATH": "semgrep.raw.sarif",
            "SCAN_EXIT_PATH": "scan-exit-code.txt",
            "PR_BASE_SHA": base,
            "PR_HEAD_SHA": head,
            "ANALYZE_RESULT": "success",
        },
    )

    assert proc.returncode == 0
    out = proc.stdout
    assert "files changed in this PR" in out
    assert "**In changed files:** 1" in out
