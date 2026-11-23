import json

import pytest


def changed_file(filename, status="modified"):
    return {"filename": filename, "status": status}


def write_changed(tmp_path, files):
    path = tmp_path / "changed-files.json"
    path.write_text(json.dumps(files), encoding="utf-8")
    return path


def test_application_changed_files_classifies_only_app_code(
    load_quality_script, tmp_path
):
    shared = load_quality_script("application_changes.py", tmp_path)
    classify = shared["application_changed_files"]

    path = write_changed(tmp_path, [
        changed_file("cps/kobo.py"),
        changed_file(".\\cps\\editbooks.py"),
        changed_file("/home/runner/work/calibre-web/calibre-web/cps.py"),
        changed_file("cps/templates/old.html", "removed"),
        changed_file(".github/workflows/test.yml"),
        changed_file("tests/workflow_scripts/test_x.py"),
        changed_file("README.md"),
    ])
    assert classify(str(path)) == ["cps.py", "cps/editbooks.py", "cps/kobo.py"]

    assert classify(str(write_changed(tmp_path, [changed_file(".github/scripts/x.py")]))) == []
    assert classify(str(tmp_path / "missing.json")) is None

    invalid = tmp_path / "invalid.json"
    invalid.write_text("{not json", encoding="utf-8")
    assert classify(str(invalid)) is None

    wrapped = tmp_path / "wrapped.json"
    wrapped.write_text('{"files": [{"filename": "cps/a.py"}]}', encoding="utf-8")
    assert classify(str(wrapped)) == ["cps/a.py"]

    wrong_type = tmp_path / "wrong.json"
    wrong_type.write_text('"not a list"', encoding="utf-8")
    assert classify(str(wrong_type)) is None


def test_gate_allows_missing_coverage_without_application_changes(tmp_path, run_quality_script):
    changed = write_changed(tmp_path, [changed_file(".github/workflows/test.yml")])

    proc = run_quality_script(
        "check_application_coverage.py",
        tmp_path,
        env={
            "CHANGED_FILES_JSON": str(changed),
            "COVERAGE_XML_PATH": str(tmp_path / "missing.xml"),
        },
    )

    assert proc.returncode == 0
    assert "no application files changed" in proc.stdout


def test_gate_fails_when_application_changes_have_no_coverage(tmp_path, run_quality_script):
    changed = write_changed(tmp_path, [
        changed_file("cps/kobo.py"),
        changed_file("cps/editbooks.py"),
    ])

    proc = run_quality_script(
        "check_application_coverage.py",
        tmp_path,
        env={
            "CHANGED_FILES_JSON": str(changed),
            "COVERAGE_XML_PATH": str(tmp_path / "missing.xml"),
        },
    )

    assert proc.returncode == 1
    assert "❌ Application files changed but no application coverage data was generated:" in proc.stdout
    assert "- cps/kobo.py" in proc.stdout
    assert "- cps/editbooks.py" in proc.stdout


def test_gate_truncates_long_application_file_lists(tmp_path, run_quality_script):
    changed = write_changed(tmp_path, [
        changed_file(f"cps/module_{index}.py") for index in range(21)
    ])

    proc = run_quality_script(
        "check_application_coverage.py",
        tmp_path,
        env={
            "CHANGED_FILES_JSON": str(changed),
            "COVERAGE_XML_PATH": str(tmp_path / "missing.xml"),
        },
    )

    assert proc.returncode == 1
    assert "... and 1 more" in proc.stdout


def test_gate_fails_closed_when_changed_files_are_unavailable(tmp_path, run_quality_script):
    proc = run_quality_script(
        "check_application_coverage.py",
        tmp_path,
        env={"COVERAGE_XML_PATH": str(tmp_path / "missing.xml")},
    )

    assert proc.returncode == 1
    assert "changed-file list is unavailable" in proc.stdout


def test_gate_accepts_existing_coverage_even_without_change_data(tmp_path, run_quality_script):
    (tmp_path / "coverage.xml").write_text("<coverage />", encoding="utf-8")

    proc = run_quality_script(
        "check_application_coverage.py",
        tmp_path,
        env={"CHANGED_FILES_JSON": str(tmp_path / "missing.json")},
    )

    assert proc.returncode == 0
    assert "Application coverage data is available." in proc.stdout


def test_panel_flags_missing_coverage_for_application_changes(tmp_path, run_quality_script):
    pytest.importorskip("defusedxml")
    changed = write_changed(tmp_path, [
        changed_file("cps/kobo.py"),
        changed_file("tests/workflow_scripts/test_x.py"),
    ])

    proc = run_quality_script(
        "test_coverage_comment.py",
        tmp_path,
        env={
            "CHANGED_FILES_JSON": str(changed),
            "COVERAGE_XML_PATH": str(tmp_path / "missing.xml"),
        },
    )

    assert proc.returncode == 0
    out = proc.stdout
    assert "❌ No application coverage data was generated for this run." in out
    assert "Application files changed in this PR:" in out
    assert "- `cps/kobo.py`" in out
    assert "tests/workflow_scripts/test_x.py" not in out


def test_panel_accepts_missing_coverage_without_application_changes(tmp_path, run_quality_script):
    pytest.importorskip("defusedxml")
    changed = write_changed(tmp_path, [changed_file(".github/workflows/test.yml")])

    proc = run_quality_script(
        "test_coverage_comment.py",
        tmp_path,
        env={
            "CHANGED_FILES_JSON": str(changed),
            "COVERAGE_XML_PATH": str(tmp_path / "missing.xml"),
        },
    )

    assert proc.returncode == 0
    assert "No application coverage data was generated for this run." in proc.stdout
    assert "No application files changed in this PR." in proc.stdout
    assert "❌ No application coverage data" not in proc.stdout


def test_panel_truncates_long_application_file_lists(tmp_path, run_quality_script):
    pytest.importorskip("defusedxml")
    changed = write_changed(tmp_path, [
        changed_file(f"cps/module_{index}.py") for index in range(21)
    ])

    proc = run_quality_script(
        "test_coverage_comment.py",
        tmp_path,
        env={
            "CHANGED_FILES_JSON": str(changed),
            "COVERAGE_XML_PATH": str(tmp_path / "missing.xml"),
        },
    )

    assert proc.returncode == 0
    assert "... and 1 more" in proc.stdout
