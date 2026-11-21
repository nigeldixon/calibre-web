import json
import os


def make_sarif(results=None, notifications=None):
    run = {
        "tool": {"driver": {"name": "semgrep", "rules": []}},
        "results": results or [],
    }
    if notifications is not None:
        run["invocations"] = [{"toolExecutionNotifications": notifications}]
    return {"version": "2.1.0", "runs": [run]}


def result(uri, level="warning"):
    return {
        "level": level,
        "message": {"text": f"finding in {uri}"},
        "locations": [
            {"physicalLocation": {"artifactLocation": {"uri": uri}}}
        ],
    }


def prepare(tmp_path, sarif_text, exit_code="0"):
    results = tmp_path / "semgrep-results"
    results.mkdir(exist_ok=True)
    (results / "semgrep.raw.sarif").write_text(sarif_text, encoding="utf-8")
    (results / "scan-exit-code.txt").write_text(exit_code + "\n", encoding="utf-8")
    return results


def scope_of(results):
    return json.loads((results / "scope.json").read_text(encoding="utf-8"))


def filtered_of(results):
    return json.loads((results / "semgrep.sarif").read_text(encoding="utf-8"))


def write_fake_git(bin_dir, body):
    bin_dir.mkdir(exist_ok=True)
    git = bin_dir / "git"
    git.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    git.chmod(0o755)
    return bin_dir


def test_sarif_shape_validators_reject_bad_nested_types(load_quality_script, tmp_path):
    (tmp_path / "semgrep-results").mkdir()
    script = load_quality_script("semgrep_filter_sarif.py", tmp_path)

    assert script["valid_artifact_location"]({"uri": "a.py"}) is True
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
    assert script["valid_notification_shape"]("bad") is False
    assert script["valid_run_shape"]({"tool": {"driver": {}}, "results": "bad"}) is False
    assert script["valid_run_shape"]({"tool": {"driver": {}}, "results": [{"level": 7}]}) is False


def test_unreadable_scan_exit_code_fails_closed(tmp_path, run_quality_script):
    results = prepare(tmp_path, json.dumps(make_sarif()), exit_code="not-a-number")

    proc = run_quality_script("semgrep_filter_sarif.py", tmp_path)

    assert proc.returncode == 1
    scope = scope_of(results)
    assert scope["scan_exit"] is None
    assert scope["scan_ok"] is False
    assert scope["reason"] == "full_scan"


def test_base_ref_fetch_and_two_dot_diff_fallback(tmp_path, run_quality_script, write_json):
    results = prepare(tmp_path, "")
    write_json(results / "semgrep.raw.sarif", make_sarif(results=[result("./cps/b.py")]))
    log = tmp_path / "git.log"
    bin_dir = write_fake_git(
        tmp_path / "bin",
        'printf "%s\\n" "$*" >> "$GIT_LOG"\n'
        'if [ "$1" = "diff" ]; then\n'
        '  if [ -z "$4" ]; then exit 1; fi\n'
        '  printf "%s\\n" "" "./cps/b.py" "cps/a.py"\n'
        "fi\n"
        "exit 0\n",
    )

    proc = run_quality_script(
        "semgrep_filter_sarif.py",
        tmp_path,
        env={
            "EVENT_NAME": "pull_request",
            "PR_BASE_SHA": "THREE_DOT",
            "PR_HEAD_SHA": "b" * 40,
            "PR_BASE_REF": "refs/heads/develop",
            "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
            "GIT_LOG": str(log),
        },
    )

    assert proc.returncode == 0
    scope = scope_of(results)
    assert scope["reason"] == "pr_changed_files"
    assert scope["changed_count"] == 3
    assert scope["findings"]["total"] == 1
    commands = log.read_text(encoding="utf-8")
    assert "fetch origin refs/heads/develop --depth=1" in commands
    assert "diff --name-only THREE_DOT bbbb" in commands.replace("b" * 40, "bbbb")


def test_failed_diff_keeps_full_scan_and_marks_it_unscoped(
    tmp_path, run_quality_script, write_json
):
    results = prepare(tmp_path, "")
    write_json(
        results / "semgrep.raw.sarif",
        make_sarif(results=[result("cps/a.py"), result("cps/b.py")]),
    )
    bin_dir = write_fake_git(tmp_path / "bin", 'if [ "$1" = "diff" ]; then exit 1; fi\nexit 0\n')

    proc = run_quality_script(
        "semgrep_filter_sarif.py",
        tmp_path,
        env={
            "EVENT_NAME": "push",
            "PR_BASE_SHA": "a" * 40,
            "PR_HEAD_SHA": "b" * 40,
            "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
        },
    )

    assert proc.returncode == 0
    scope = scope_of(results)
    assert scope["scoped"] is False
    assert scope["reason"] == "diff_failed_full_scan"
    assert scope["total_all"] == 2
    assert len(filtered_of(results)["runs"][0]["results"]) == 2


def test_git_failure_keeps_full_scan_when_diff_cannot_run(
    tmp_path, run_quality_script, write_json
):
    results = prepare(tmp_path, "")
    write_json(results / "semgrep.raw.sarif", make_sarif(results=[result("cps/a.py")]))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "git").mkdir()

    proc = run_quality_script(
        "semgrep_filter_sarif.py",
        tmp_path,
        env={
            "EVENT_NAME": "push",
            "PR_BASE_SHA": "a" * 40,
            "PR_HEAD_SHA": "b" * 40,
            "PATH": str(bin_dir),
        },
    )

    assert proc.returncode == 0
    scope = scope_of(results)
    assert scope["scoped"] is False
    assert scope["reason"] == "diff_failed_full_scan"
    assert len(filtered_of(results)["runs"][0]["results"]) == 1


def test_warning_without_descriptor_and_note_findings_are_counted(
    tmp_path, run_quality_script, write_json
):
    results = prepare(tmp_path, "")
    write_json(
        results / "semgrep.raw.sarif",
        make_sarif(
            results=[result("cps/a.py", "note"), result("cps/b.py", "none")],
            notifications=[{"level": "warning", "message": {"text": "slow rule"}}],
        ),
    )

    proc = run_quality_script("semgrep_filter_sarif.py", tmp_path)

    assert proc.returncode == 0
    scope = scope_of(results)
    assert scope["warnings"] == 1
    assert scope["warning_detail"] == {"warning": 1}
    assert scope["findings"]["note"] == 2
    assert scope["scan_ok"] is True


def test_uri_and_changed_set_helpers(load_quality_script, tmp_path):
    prepare(tmp_path, json.dumps(make_sarif()))
    script = load_quality_script("semgrep_filter_sarif.py", tmp_path)

    assert script["norm"]("") == ""
    assert script["norm"](".\\cps\\a.py") == "cps/a.py"
    assert script["in_changed"](result("cps/a.py"), None) is True
    assert script["in_changed"]({"level": "warning"}, {"cps/a.py"}) is False
    assert script["in_changed"](
        {
            "relatedLocations": [
                {"physicalLocation": {"artifactLocation": {"uri": "./cps/b.py"}}}
            ]
        },
        {"cps/b.py"},
    ) is True


def test_missing_raw_sarif_fails_closed(tmp_path, run_quality_script):
    results = tmp_path / "semgrep-results"
    results.mkdir()
    (results / "scan-exit-code.txt").write_text("0\n", encoding="utf-8")

    proc = run_quality_script("semgrep_filter_sarif.py", tmp_path)

    assert proc.returncode == 1
    scope = scope_of(results)
    assert scope["reason"] == "no_sarif"
    assert scope["scan_ok"] is False
    assert filtered_of(results)["runs"] == []


def test_invalid_raw_sarif_fails_closed(tmp_path, run_quality_script):
    for raw in (
        "not json",
        '{"version": "2.1.0", "runs": []}',
        '{"version": "2.1.0", "runs": [{"tool": {}}]}',
        '{"version": "2.1.0", "runs": [{"results": []}]}',
    ):
        results = prepare(tmp_path, raw)

        proc = run_quality_script("semgrep_filter_sarif.py", tmp_path)

        assert proc.returncode == 1, raw
        scope = scope_of(results)
        assert scope["reason"] == "invalid_sarif", raw
        assert scope["scan_ok"] is False, raw
        assert filtered_of(results)["runs"] == [], raw


def test_error_notification_fails_gate_but_keeps_results_for_report(tmp_path, run_quality_script, write_json):
    sarif = make_sarif(
        results=[result("cps/a.py", "error")],
        notifications=[
            {
                "level": "error",
                "descriptor": {"id": "semgrep_parse_error"},
                "message": {"text": "scan aborted"},
            }
        ],
    )
    results = prepare(tmp_path, "")
    write_json(results / "semgrep.raw.sarif", sarif)

    proc = run_quality_script("semgrep_filter_sarif.py", tmp_path)

    assert proc.returncode == 1
    scope = scope_of(results)
    assert scope["exec_errors"] == 1
    assert scope["scan_ok"] is False
    assert scope["findings"]["total"] == 1
    assert len(filtered_of(results)["runs"][0]["results"]) == 1


def test_findings_exit_code_allows_sarif_upload(tmp_path, run_quality_script, write_json):
    results = prepare(tmp_path, "")
    write_json(results / "semgrep.raw.sarif", make_sarif(results=[result("cps/a.py")]))
    (results / "scan-exit-code.txt").write_text("1\n", encoding="utf-8")

    proc = run_quality_script("semgrep_filter_sarif.py", tmp_path)

    assert proc.returncode == 0
    scope = scope_of(results)
    assert scope["scan_exit"] == 1
    assert scope["scan_ok"] is True
    assert scope["findings"]["total"] == 1


def test_nonzero_scan_exit_fails_the_gate(tmp_path, run_quality_script, write_json):
    results = prepare(tmp_path, "")
    write_json(results / "semgrep.raw.sarif", make_sarif(results=[result("cps/a.py")]))
    (results / "scan-exit-code.txt").write_text("2\n", encoding="utf-8")

    proc = run_quality_script("semgrep_filter_sarif.py", tmp_path)

    assert proc.returncode == 1
    scope = scope_of(results)
    assert scope["scan_exit"] == 2
    assert scope["scan_ok"] is False
    assert scope["reason"] == "full_scan"


def test_changed_file_filtering_uses_git_diff_and_normalizes_paths(tmp_path, run_quality_script, write_json):
    results = prepare(tmp_path, "")
    write_json(
        results / "semgrep.raw.sarif",
        make_sarif(results=[result("cps/a.py"), result("./cps\\b.py")]),
    )

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_git = bin_dir / "git"
    fake_git.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "diff" ]; then printf "%s\\n" cps/a.py; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)

    proc = run_quality_script(
        "semgrep_filter_sarif.py",
        tmp_path,
        env={
            "EVENT_NAME": "push",
            "PR_BASE_SHA": "a" * 40,
            "PR_HEAD_SHA": "b" * 40,
            "PR_BASE_REF": "",
            "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
        },
    )

    assert proc.returncode == 0
    scope = scope_of(results)
    assert scope["scoped"] is True
    assert scope["reason"] == "pr_changed_files"
    assert scope["changed_count"] == 1
    assert scope["total_all"] == 2
    assert scope["findings"]["total"] == 1
    kept = filtered_of(results)["runs"][0]["results"]
    assert len(kept) == 1
    assert kept[0]["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "cps/a.py"
