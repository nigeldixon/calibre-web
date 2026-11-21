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


def sarif(results, notifications=None):
    run = {
        "tool": {"driver": {"name": "codeql"}},
        "results": results,
    }
    if notifications:
        run["invocations"] = [{"toolExecutionNotifications": notifications}]
    return {"version": "2.1.0", "runs": [run]}


def write_language_artifact(tmp_path, language, data, status=None):
    directory = tmp_path / "codeql-results" / f"codeql-sarif-{language}"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "results.sarif").write_text(data, encoding="utf-8")
    if status is not None:
        (directory / "status.txt").write_text(status, encoding="utf-8")
    return directory


def test_partial_run_counts_missing_language_and_keeps_details_in_sync(
    tmp_path, run_quality_script, write_json
):
    python_dir = write_language_artifact(
        tmp_path,
        "python",
        '{"version": "2.1.0", "runs": []}',
        status="failure",
    )
    write_json(
        python_dir / "results.sarif",
        sarif(
            results=[finding("cps/a.py", "error"), finding("cps/b.py", "warning")],
            notifications=[
                notification("error", "E1", "scan aborted", "cps/a.py"),
                notification("warning", "W1", "timeout", "cps/a.py"),
            ],
        ),
    )
    changed = tmp_path / "changed-files.txt"
    changed.write_text("cps/a.py\n", encoding="utf-8")

    proc = run_quality_script(
        "codeql_pr_comment.py",
        tmp_path,
        env={
            "CHANGED_FILES_FILE": str(changed),
            "PR_BASE_SHA": "a" * 40,
            "PR_HEAD_SHA": "b" * 40,
            "ANALYZE_RESULT": "failure",
            "RUN_URL": "https://example.test/run/1",
            "ANALYSIS_URL": "https://example.test/alerts",
        },
    )

    out = proc.stdout
    assert proc.returncode == 0
    assert "**Whole-solution issues:** 2 · **In changed files:** 1" in out
    assert "**Analysis errors:** 2 · **Analysis warnings:** 1" in out
    assert "| ⚪ | `javascript-typescript` | - | - | - | - |" in out
    assert "| ⚠️ | `python` | 1 | 1 | 0 | 0 |" in out
    assert "<details><summary>❌ Analysis errors (2)</summary>" in out
    assert "- `cps/a.py` — E1: scan aborted" in out
    assert "- No SARIF artifact was produced for `javascript-typescript`." in out
    assert "<details><summary>⚠️ Analysis warnings (1)</summary>" in out
    assert "- `cps/a.py` — W1: timeout" in out
    assert "❌ CodeQL analyze failed for: `python`." in out
    assert "**Full analysis:** [All CodeQL alerts](https://example.test/alerts) · [Workflow run](https://example.test/run/1)" in out


def test_no_sarif_artifacts_reports_one_analysis_error(tmp_path, run_quality_script):
    proc = run_quality_script("codeql_pr_comment.py", tmp_path, env={"ANALYZE_RESULT": "success"})

    out = proc.stdout
    assert proc.returncode == 0
    assert "**Analysis errors:** 1 · **Analysis warnings:** 0" in out
    assert "<details><summary>❌ Analysis errors (1)</summary>" in out
    assert "- No CodeQL SARIF artifacts were produced for this run." in out
    assert "| ⚪ | `javascript-typescript` | - | - | - | - |" in out
    assert "| ⚪ | `python` | - | - | - | - |" in out
    assert "_No SARIF artifacts were found for this run; results may be unavailable (analysis may have failed or not produced output)._" in out


def test_failed_analyze_job_without_status_files_is_reported(tmp_path, run_quality_script, write_json):
    for language in ("javascript-typescript", "python"):
        directory = tmp_path / "codeql-results" / f"codeql-sarif-{language}"
        directory.mkdir(parents=True)
        write_json(directory / "results.sarif", sarif(results=[]))

    proc = run_quality_script("codeql_pr_comment.py", tmp_path, env={"ANALYZE_RESULT": "failure"})

    out = proc.stdout
    assert proc.returncode == 0
    assert "**Analysis errors:** 1 · **Analysis warnings:** 0" in out
    assert "- CodeQL analyze job reported `failure`." in out
    assert "❌ CodeQL analyze job reported `failure`." in out
