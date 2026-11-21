import os

import pytest


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
                {
                    "level": "note",
                    "descriptor": {"id": "N1"},
                    "message": {"text": "informational"},
                },
                {"level": "warning", "message": {"text": "no location"}},
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
    assert "**Analysis errors:** 2 · **Analysis warnings:** 2" in out
    assert "| ⚪ | `javascript-typescript` | - | - | - | - |" in out
    assert "| ⚠️ | `python` | 1 | 1 | 0 | 0 |" in out
    assert "<details><summary>❌ Analysis errors (2)</summary>" in out
    assert "- `cps/a.py` — E1: scan aborted" in out
    assert "- No SARIF artifact was produced for `javascript-typescript`." in out
    assert "<details><summary>⚠️ Analysis warnings (2)</summary>" in out
    assert "- `cps/a.py` — W1: timeout" in out
    assert "- warning: no location" in out
    assert "❌ CodeQL analyze failed for: `python`." in out
    assert "**Full analysis:** [All CodeQL alerts](https://example.test/alerts) · [Workflow run](https://example.test/run/1)" in out


def write_fake_git(tmp_path, log_path, diff_fail=False):
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir(exist_ok=True)
    git = bin_dir / "git"
    git.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" >> "$GIT_LOG"\n'
        'if [ "$1" = "fetch" ]; then exit 0; fi\n'
        'if [ "$1" = "diff" ]; then\n'
        '  if [ "$3" = "$FAKE_BASE...$FAKE_HEAD" ] && [ "$FAKE_DIFF_FAIL" = "1" ]; then exit 1; fi\n'
        '  if [ "$3" = "$FAKE_BASE...$FAKE_HEAD" ]; then\n'
        '    printf "%s\\n" "cps/a.py" "" "./cps/b.py"\n'
        "    exit 0\n"
        "  fi\n"
        '  if [ "$FAKE_SECOND_DIFF_FAIL" = "1" ]; then exit 1; fi\n'
        '  printf "%s\\n" "cps/c.py" ""\n'
        "  exit 0\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    git.chmod(0o755)
    log_path.write_text("", encoding="utf-8")
    return bin_dir, {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "GIT_LOG": str(log_path),
        "FAKE_DIFF_FAIL": "1" if diff_fail else "0",
        "FAKE_SECOND_DIFF_FAIL": "0",
    }


def test_git_diff_scope_uses_three_dot_diff(tmp_path, run_quality_script, write_json):
    base = "a" * 40
    head = "b" * 40
    log = tmp_path / "git.log"
    bin_dir, git_env = write_fake_git(tmp_path, log)
    for language in ("javascript-typescript", "python"):
        directory = tmp_path / "codeql-results" / f"codeql-sarif-{language}"
        directory.mkdir(parents=True)
        write_json(directory / "results.sarif", sarif(results=[]))

    python_dir = tmp_path / "codeql-results" / "codeql-sarif-python"
    write_json(
        python_dir / "results.sarif",
        sarif(results=[finding("cps/a.py", "warning"), finding("cps/b.py", "note")]),
    )

    proc = run_quality_script(
        "codeql_pr_comment.py",
        tmp_path,
        env={
            **git_env,
            "FAKE_BASE": base,
            "FAKE_HEAD": head,
            "PR_BASE_SHA": base,
            "PR_HEAD_SHA": head,
            "PR_BASE_REF": "refs/heads/develop",
        },
    )

    out = proc.stdout
    assert proc.returncode == 0
    assert "_Scope: 2 changed file(s)" in out
    assert "**Whole-solution issues:** 2 · **In changed files:** 2" in out
    commands = log.read_text(encoding="utf-8")
    assert "fetch origin refs/heads/develop --depth=1" in commands
    assert f"fetch origin {base} --depth=1" in commands
    assert f"fetch origin {head} --depth=1" in commands
    assert f"diff --name-only {base}...{head}" in commands


def test_git_diff_scope_falls_back_to_two_dot_diff(tmp_path, run_quality_script, write_json):
    base = "a" * 40
    head = "b" * 40
    log = tmp_path / "git.log"
    bin_dir, git_env = write_fake_git(tmp_path, log, diff_fail=True)
    for language in ("javascript-typescript", "python"):
        directory = tmp_path / "codeql-results" / f"codeql-sarif-{language}"
        directory.mkdir(parents=True)
        write_json(directory / "results.sarif", sarif(results=[]))

    python_dir = tmp_path / "codeql-results" / "codeql-sarif-python"
    write_json(python_dir / "results.sarif", sarif(results=[finding("cps/c.py", "warning")]))

    proc = run_quality_script(
        "codeql_pr_comment.py",
        tmp_path,
        env={
            **git_env,
            "FAKE_BASE": base,
            "FAKE_HEAD": head,
            "PR_BASE_SHA": base,
            "PR_HEAD_SHA": head,
        },
    )

    out = proc.stdout
    assert proc.returncode == 0
    assert "_Scope: 1 changed file(s)" in out
    assert "**In changed files:** 1" in out
    assert f"diff --name-only {base} {head}" in log.read_text(encoding="utf-8")


def test_unresolvable_diff_reports_all_results(tmp_path, run_quality_script, write_json):
    base = "a" * 40
    head = "b" * 40
    log = tmp_path / "git.log"
    bin_dir, git_env = write_fake_git(tmp_path, log, diff_fail=True)
    for language in ("javascript-typescript", "python"):
        directory = tmp_path / "codeql-results" / f"codeql-sarif-{language}"
        directory.mkdir(parents=True)
        write_json(directory / "results.sarif", sarif(results=[]))

    proc = run_quality_script(
        "codeql_pr_comment.py",
        tmp_path,
        env={
            **git_env,
            "FAKE_BASE": base,
            "FAKE_HEAD": head,
            "FAKE_SECOND_DIFF_FAIL": "1",
            "PR_BASE_SHA": base,
            "PR_HEAD_SHA": head,
        },
    )

    out = proc.stdout
    assert proc.returncode == 0
    assert "_Scope: could not determine PR diff; showing all SARIF results._" in out


def test_unreadable_sarif_is_reported_as_failed(tmp_path, run_quality_script, write_json):
    js_dir = tmp_path / "codeql-results" / "codeql-sarif-javascript-typescript"
    js_dir.mkdir(parents=True)
    write_json(js_dir / "results.sarif", sarif(results=[]))
    python_dir = tmp_path / "codeql-results" / "codeql-sarif-python"
    python_dir.mkdir(parents=True)
    (python_dir / "results.sarif").write_text("{not json", encoding="utf-8")

    proc = run_quality_script("codeql_pr_comment.py", tmp_path)

    out = proc.stdout
    assert proc.returncode != 0
    assert "| ⚪ | `python` | - | - | - | - |" in out
    assert "❌ A CodeQL SARIF file could not be parsed; the analysis is marked as failed." in out
    assert "- `codeql-results/codeql-sarif-python/results.sarif`: SARIF could not be parsed." in out


def test_sarif_shape_validators_reject_bad_nested_types(load_quality_script, tmp_path):
    script = load_quality_script("codeql_pr_comment.py", tmp_path)

    assert script["valid_artifact_location"]({"uri": "cps/a.py"}) is True
    assert script["valid_artifact_location"]({"uri": 7}) is False
    assert script["valid_artifact_location"]("bad") is False
    assert script["valid_location"]({}) is True
    assert script["valid_location"]({"physicalLocation": "bad"}) is False
    assert script["valid_location"]("bad") is False
    assert script["valid_message"]({"text": "ok"}) is True
    assert script["valid_message"]({"text": 7}) is False
    assert script["valid_message"]("bad") is False
    assert script["valid_result_shape"]({"level": "warning"}) is True
    assert script["valid_result_shape"]({"level": 7}) is False
    assert script["valid_result_shape"]({"message": "bad"}) is False
    assert script["valid_result_shape"]({"locations": "bad"}) is False
    assert script["valid_result_shape"]({"locations": ["bad"]}) is False
    assert script["valid_result_shape"]("bad") is False
    assert script["valid_notification_shape"]({"level": "warning"}) is True
    assert script["valid_notification_shape"]({"level": 7}) is False
    assert script["valid_notification_shape"]({"message": "bad"}) is False
    assert script["valid_notification_shape"]({"descriptor": "bad"}) is False
    assert script["valid_notification_shape"]({"locations": "bad"}) is False
    assert script["valid_notification_shape"]("bad") is False
    assert script["valid_run_shape"]({"tool": {"driver": {}}, "results": "bad"}) is False
    assert script["valid_run_shape"]({"tool": {"driver": {}}, "results": [{"level": 7}]}) is False


def test_multiple_sarif_files_for_one_language_are_aggregated(
    tmp_path, run_quality_script, write_json
):
    changed = tmp_path / "changed-files.txt"
    changed.write_text("cps/a.py\ncps/b.py\n", encoding="utf-8")
    python_dir = write_language_artifact(
        tmp_path,
        "python",
        '{"version": "2.1.0", "runs": []}',
    )
    write_json(
        python_dir / "results.sarif",
        sarif(results=[finding("cps/a.py", "warning")]),
    )
    write_json(
        python_dir / "second.sarif",
        sarif(results=[finding("cps/b.py", "error")]),
    )
    js_dir = tmp_path / "codeql-results" / "codeql-sarif-javascript-typescript"
    js_dir.mkdir(parents=True)
    write_json(js_dir / "results.sarif", sarif(results=[]))

    proc = run_quality_script(
        "codeql_pr_comment.py",
        tmp_path,
        env={"CHANGED_FILES_FILE": str(changed)},
    )

    assert proc.returncode == 0
    assert "| ⚠️ | `python` | 2 | 1 | 1 | 0 |" in proc.stdout
    assert "**Whole-solution issues:** 2 · **In changed files:** 2" in proc.stdout


def test_structurally_empty_sarif_fails_closed(tmp_path, run_quality_script, write_json):
    directory = tmp_path / "codeql-results" / "codeql-sarif-python"
    directory.mkdir(parents=True)
    write_json(directory / "results.sarif", {"version": "2.1.0", "runs": []})

    proc = run_quality_script("codeql_pr_comment.py", tmp_path)

    assert proc.returncode != 0
    assert "`codeql-results/codeql-sarif-python/results.sarif`: SARIF is empty or structurally invalid." in proc.stdout


@pytest.mark.parametrize(
    "invocations",
    ["not-a-list", ["not-a-dict"], [{"toolExecutionNotifications": "not-a-list"}]],
)
def test_invalid_invocation_data_fails_closed(
    tmp_path, run_quality_script, write_json, invocations
):
    directory = tmp_path / "codeql-results" / "codeql-sarif-python"
    directory.mkdir(parents=True)
    write_json(
        directory / "results.sarif",
        {
            "version": "2.1.0",
            "runs": [
                {"tool": {"driver": {"name": "codeql"}}, "results": [],
                 "invocations": invocations}
            ],
        },
    )

    proc = run_quality_script("codeql_pr_comment.py", tmp_path)

    assert proc.returncode != 0
    assert "SARIF contains invalid run data." in proc.stdout


def test_changed_file_list_normalizes_prefixes_and_separators(
    tmp_path, run_quality_script, write_json
):
    changed = tmp_path / "changed-files.txt"
    changed.write_text("./cps/a.py\ncps\\b.py\n\ncps/unrelated.py\n", encoding="utf-8")
    python_dir = tmp_path / "codeql-results" / "codeql-sarif-python"
    python_dir.mkdir(parents=True)
    write_json(
        python_dir / "results.sarif",
        sarif(
            results=[
                finding("cps/a.py", "warning"),
                finding("cps/b.py", "warning"),
                finding("cps/ignored.py", "warning"),
            ]
        ),
    )
    js_dir = tmp_path / "codeql-results" / "codeql-sarif-javascript-typescript"
    js_dir.mkdir(parents=True)
    write_json(js_dir / "results.sarif", sarif(results=[]))

    proc = run_quality_script(
        "codeql_pr_comment.py", tmp_path, env={"CHANGED_FILES_FILE": str(changed)}
    )

    out = proc.stdout
    assert proc.returncode == 0
    assert "**Whole-solution issues:** 3 · **In changed files:** 2" in out


def test_unreadable_status_file_is_recorded_as_failed(
    tmp_path, run_quality_script, write_json
):
    python_dir = tmp_path / "codeql-results" / "codeql-sarif-python"
    python_dir.mkdir(parents=True)
    write_json(python_dir / "results.sarif", sarif(results=[]))
    (python_dir / "status.txt").mkdir()
    js_dir = tmp_path / "codeql-results" / "codeql-sarif-javascript-typescript"
    js_dir.mkdir(parents=True)
    write_json(js_dir / "results.sarif", sarif(results=[]))

    proc = run_quality_script("codeql_pr_comment.py", tmp_path)

    out = proc.stdout
    assert proc.returncode == 0
    assert "- CodeQL analyze failed for `python`." in out
    assert "❌ CodeQL analyze failed for: `python`." in out


def test_unreadable_changed_file_list_falls_back_to_all_results(
    tmp_path, run_quality_script, write_json
):
    changed = tmp_path / "changed-files.txt"
    changed.mkdir()
    python_dir = tmp_path / "codeql-results" / "codeql-sarif-python"
    python_dir.mkdir(parents=True)
    write_json(python_dir / "results.sarif", sarif(results=[finding("cps/a.py", "warning")]))
    js_dir = tmp_path / "codeql-results" / "codeql-sarif-javascript-typescript"
    js_dir.mkdir(parents=True)
    write_json(js_dir / "results.sarif", sarif(results=[]))

    proc = run_quality_script(
        "codeql_pr_comment.py",
        tmp_path,
        env={"CHANGED_FILES_FILE": str(changed)},
    )

    out = proc.stdout
    assert proc.returncode == 0
    assert "**Whole-solution issues:** 1 · **In changed files:** unknown" in out


def test_missing_git_executable_reports_all_results(
    tmp_path, run_quality_script, write_json
):
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    python_dir = tmp_path / "codeql-results" / "codeql-sarif-python"
    python_dir.mkdir(parents=True)
    write_json(python_dir / "results.sarif", sarif(results=[finding("cps/a.py", "warning")]))
    js_dir = tmp_path / "codeql-results" / "codeql-sarif-javascript-typescript"
    js_dir.mkdir(parents=True)
    write_json(js_dir / "results.sarif", sarif(results=[]))

    proc = run_quality_script(
        "codeql_pr_comment.py",
        tmp_path,
        env={
            "PATH": str(empty_bin),
            "PR_BASE_SHA": "a" * 40,
            "PR_HEAD_SHA": "b" * 40,
        },
    )

    out = proc.stdout
    assert proc.returncode == 0
    assert "_Scope: could not determine PR diff; showing all SARIF results._" in out
    assert "**In changed files:** unknown" in out


def test_result_uri_normalization_and_related_locations(
    tmp_path, run_quality_script, write_json
):
    changed = tmp_path / "changed-files.txt"
    changed.write_text("cps/a.py\ncps/related.py\n", encoding="utf-8")
    python_dir = tmp_path / "codeql-results" / "codeql-sarif-python"
    python_dir.mkdir(parents=True)
    write_json(
        python_dir / "results.sarif",
        sarif(
            results=[
                finding("./cps/a.py", "warning"),
                {
                    "level": "note",
                    "message": {"text": "found via related location"},
                    "relatedLocations": [
                        {"physicalLocation": {"artifactLocation": {"uri": "cps/other.py"}}},
                        {"physicalLocation": {"artifactLocation": {"uri": "./cps/related.py"}}},
                    ],
                },
            ]
        ),
    )
    js_dir = tmp_path / "codeql-results" / "codeql-sarif-javascript-typescript"
    js_dir.mkdir(parents=True)
    write_json(js_dir / "results.sarif", sarif(results=[]))

    proc = run_quality_script(
        "codeql_pr_comment.py",
        tmp_path,
        env={"CHANGED_FILES_FILE": str(changed)},
    )

    assert proc.returncode == 0
    assert "**In changed files:** 2" in proc.stdout


def test_result_levels_and_missing_changed_file_scope_are_counted(
    tmp_path, run_quality_script, write_json
):
    python_dir = tmp_path / "codeql-results" / "codeql-sarif-python"
    python_dir.mkdir(parents=True)
    write_json(
        python_dir / "results.sarif",
        sarif(
            results=[
                finding("cps/a.py", "error"),
                finding("cps/b.py", "warning"),
                finding("cps/c.py", "note"),
                finding("cps/d.py", "none"),
                finding("cps/e.py", "info"),
            ]
        ),
    )
    js_dir = tmp_path / "codeql-results" / "codeql-sarif-javascript-typescript"
    js_dir.mkdir(parents=True)
    write_json(js_dir / "results.sarif", sarif(results=[]))

    proc = run_quality_script("codeql_pr_comment.py", tmp_path)

    out = proc.stdout
    assert proc.returncode == 0
    assert "| ⚠️ | `python` | 5 | 1 | 2 | 2 |" in out
    assert "**In changed files:** unknown" in out


def test_related_locations_count_towards_changed_file_scope(
    tmp_path, run_quality_script, write_json
):
    changed = tmp_path / "changed-files.txt"
    changed.write_text("cps/related.py\n", encoding="utf-8")
    python_dir = tmp_path / "codeql-results" / "codeql-sarif-python"
    python_dir.mkdir(parents=True)
    write_json(
        python_dir / "results.sarif",
        sarif(
            results=[
                {
                    "level": "warning",
                    "message": {"text": "found via related location"},
                    "relatedLocations": [
                        {"physicalLocation": {"artifactLocation": {"uri": "cps/related.py"}}}
                    ],
                }
            ]
        ),
    )
    js_dir = tmp_path / "codeql-results" / "codeql-sarif-javascript-typescript"
    js_dir.mkdir(parents=True)
    write_json(js_dir / "results.sarif", sarif(results=[]))

    proc = run_quality_script(
        "codeql_pr_comment.py", tmp_path, env={"CHANGED_FILES_FILE": str(changed)}
    )

    assert proc.returncode == 0
    assert "**In changed files:** 1" in proc.stdout


def test_failed_status_without_or_with_clean_sarif_is_recorded(
    tmp_path, run_quality_script, write_json
):
    python_dir = tmp_path / "codeql-results" / "codeql-sarif-python"
    python_dir.mkdir(parents=True)
    write_json(python_dir / "results.sarif", sarif(results=[]))
    (python_dir / "status.txt").write_text("failure", encoding="utf-8")

    custom_dir = tmp_path / "codeql-results" / "codeql-sarif-custom"
    custom_dir.mkdir(parents=True)
    (custom_dir / "status.txt").write_text("failure", encoding="utf-8")

    proc = run_quality_script("codeql_pr_comment.py", tmp_path)

    out = proc.stdout
    assert proc.returncode == 0
    assert "- CodeQL analyze failed for `python`." in out
    assert "- CodeQL analyze failed for `custom` and no SARIF was available." in out


def test_status_files_without_language_component_are_ignored(
    tmp_path, run_quality_script, write_json
):
    python_dir = tmp_path / "codeql-results" / "codeql-sarif-python"
    python_dir.mkdir(parents=True)
    write_json(python_dir / "results.sarif", sarif(results=[]))
    (tmp_path / "codeql-results" / "status.txt").write_text("failure", encoding="utf-8")

    proc = run_quality_script("codeql_pr_comment.py", tmp_path)

    assert proc.returncode == 0
    assert "❌ CodeQL analyze failed for:" not in proc.stdout


def test_notification_detail_uses_first_uri_and_collapses_message(
    tmp_path, run_quality_script, write_json
):
    python_dir = tmp_path / "codeql-results" / "codeql-sarif-python"
    python_dir.mkdir(parents=True)
    write_json(
        python_dir / "results.sarif",
        sarif(
            results=[],
            notifications=[
                {
                    "level": "error",
                    "descriptor": {"id": "E1"},
                    "message": {"text": "scan   aborted"},
                    "locations": [
                        {"physicalLocation": {"artifactLocation": {"uri": ""}}},
                        {"physicalLocation": {"artifactLocation": {"uri": "cps/a.py"}}},
                    ],
                }
            ],
        ),
    )
    js_dir = tmp_path / "codeql-results" / "codeql-sarif-javascript-typescript"
    js_dir.mkdir(parents=True)
    write_json(js_dir / "results.sarif", sarif(results=[]))

    proc = run_quality_script("codeql_pr_comment.py", tmp_path)

    assert proc.returncode == 0
    assert "- `cps/a.py` — E1: scan aborted" in proc.stdout


def test_no_sarif_artifacts_reports_one_analysis_error(tmp_path, run_quality_script):
    proc = run_quality_script("codeql_pr_comment.py", tmp_path, env={"ANALYZE_RESULT": "success"})

    out = proc.stdout
    assert proc.returncode != 0
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


def test_failed_analyze_job_with_successful_language_status_files_is_reported(
    tmp_path, run_quality_script, write_json
):
    for language in ("javascript-typescript", "python"):
        directory = tmp_path / "codeql-results" / f"codeql-sarif-{language}"
        directory.mkdir(parents=True)
        write_json(directory / "results.sarif", sarif(results=[]))
        (directory / "status.txt").write_text("success", encoding="utf-8")

    proc = run_quality_script("codeql_pr_comment.py", tmp_path, env={"ANALYZE_RESULT": "failure"})

    out = proc.stdout
    assert proc.returncode == 0
    assert "**Analysis errors:** 1 · **Analysis warnings:** 0" in out
    assert "- CodeQL analyze job reported `failure`." in out
    assert "❌ CodeQL analyze job reported `failure`." in out
