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
