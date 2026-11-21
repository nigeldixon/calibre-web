import pytest

pytest.importorskip("defusedxml")


def load_helpers(load_quality_script, tmp_path):
    return load_quality_script(
        "test_coverage_comment.py",
        tmp_path,
        env={"COVERAGE_XML_PATH": str(tmp_path / "missing.xml")},
    )


def test_normalize_path_strips_workspace_and_windows_prefixes(load_quality_script, tmp_path):
    script = load_helpers(load_quality_script, tmp_path)
    normalize = script["normalize_path"]

    assert normalize(".\\cps\\a.py") == "cps/a.py"
    assert normalize("./cps/a.py") == "cps/a.py"
    assert normalize("/home/runner/work/calibre-web/calibre-web/cps/a.py") == "cps/a.py"
    assert normalize("/home/runner/work/other/calibre-web/cps/a.py") == "cps/a.py"
    assert normalize("/home/runner/work/calibre-web/calibre-web/x/calibre-web/cps/a.py") == "cps/a.py"


def test_added_lines_from_patch_keeps_plusplus_lines(load_quality_script, tmp_path):
    script = load_helpers(load_quality_script, tmp_path)

    patch = "\n".join(
        [
            "diff --git a/cps/a.py b/cps/a.py",
            "+++ b/cps/a.py",
            "@@ -1,2 +10,4 @@",
            " context",
            "-old",
            "+++counter;",
            "+added",
            "\\ No newline at end of file",
        ]
    )

    assert script["added_lines_from_patch"](patch) == {11, 12}
    assert script["added_lines_from_patch"]("no hunks here\n+not counted") == set()


def test_badge_helpers(load_quality_script, tmp_path):
    script = load_helpers(load_quality_script, tmp_path)

    assert script["pct_to_color"](95)[0] == "brightgreen"
    assert script["pct_to_color"](85)[0] == "green"
    assert script["pct_to_color"](75)[0] == "yellow"
    assert script["pct_to_color"](50)[0] == "orange"
    assert script["pct_to_color"](10)[0] == "red"
    assert script["coverage_marker"]("100 statements · 20 missed · 94% covered") == ""
    assert script["coverage_marker"]("100 statements · 20 missed · 80% covered") == ""
    assert script["coverage_marker"]("75% covered") == "⚠️ Low coverage"
    assert script["coverage_marker"]("65% covered") == "❌ Low coverage"
    assert script["coverage_marker"]("35% covered") == "❌ Low coverage"
    assert script["coverage_marker"]("no percentage") == ""
    assert script["bar"](0) == "░" * 20
    assert script["bar"](50) == "██████████░░░░░░░░░░"
    assert script["bar"](100) == "█" * 20
    assert script["bar"](150) == "█" * 20


def test_load_test_results_ignores_invalid_suites_and_status_icons(
    load_quality_script, tmp_path, monkeypatch, write_json
):
    script = load_helpers(load_quality_script, tmp_path)
    results_path = write_json(
        tmp_path / "test-results.json",
        {
            "suites": [
                "not-a-suite",
                {"key": "unit_tests", "outcome": "skipped", "summary": "skipped"},
                {"key": "e2e_tests", "outcome": "unknown"},
            ],
            "run_url": "https://example.test/run/1",
        },
    )
    monkeypatch.setitem(script, "test_results_path", str(results_path))

    suites = script["load_test_results"]()

    assert len(suites) == 4
    assert suites[1]["outcome"] == "skipped"
    assert suites[2]["outcome"] == "unknown"
    assert script["run_url"] == "https://example.test/run/1"
    assert script["status_icon"]("skipped") == "⏭️"
    assert script["status_icon"]("unknown") == "⚪"


def test_coverage_by_file_ignores_malformed_and_empty_entries(load_quality_script, tmp_path):
    script = load_helpers(load_quality_script, tmp_path)
    coverage_by_file = script["coverage_by_file"]

    assert coverage_by_file(str(tmp_path / "missing.xml")) == {}

    invalid = tmp_path / "invalid.xml"
    invalid.write_text("<coverage><broken>", encoding="utf-8")
    assert coverage_by_file(str(invalid)) == {}

    xml = tmp_path / "coverage.xml"
    xml.write_text(
        """<?xml version="1.0" ?>
<coverage>
  <classes>
    <class name="empty" filename="" />
    <class name="no-lines" filename="cps/no-lines.py" />
    <class name="a" filename="cps/a.py">
      <lines>
        <line number="bad" hits="1"/>
        <line number="3" hits="bad"/>
        <line number="0" hits="1"/>
        <line number="11" hits="2"/>
      </lines>
    </class>
  </classes>
</coverage>
""",
        encoding="utf-8",
    )

    assert coverage_by_file(str(xml)) == {"cps/a.py": {11: 2}}


def test_load_changed_files_accepts_wrapped_lists_and_ignores_bad_input(
    load_quality_script, tmp_path
):
    script = load_helpers(load_quality_script, tmp_path)
    load_changed_files = script["load_changed_files"]

    invalid = tmp_path / "invalid.json"
    invalid.write_text("{not json", encoding="utf-8")
    assert load_changed_files(str(invalid)) == []

    wrapped = tmp_path / "wrapped.json"
    wrapped.write_text('{"files": [{"filename": "cps/a.py"}]}', encoding="utf-8")
    assert load_changed_files(str(wrapped)) == [{"filename": "cps/a.py"}]

    wrong_type = tmp_path / "wrong.json"
    wrong_type.write_text('"not a list"', encoding="utf-8")
    assert load_changed_files(str(wrong_type)) == []


def test_coverage_total_handles_missing_and_unformatted_totals(
    load_quality_script, tmp_path
):
    script = load_helpers(load_quality_script, tmp_path)
    coverage_total = script["coverage_total"]

    assert coverage_total(str(tmp_path / "missing.txt")) is None
    assert coverage_total(str(tmp_path)) is None

    unformatted = tmp_path / "unformatted.txt"
    unformatted.write_text("TOTAL 10 5\n", encoding="utf-8")
    assert coverage_total(str(unformatted)) == "TOTAL 10 5"

    no_total = tmp_path / "no-total.txt"
    no_total.write_text("some coverage output\n", encoding="utf-8")
    assert coverage_total(str(no_total)) == "some coverage output"

    formatted = tmp_path / "formatted.txt"
    formatted.write_text("TOTAL 100 20 80%\n", encoding="utf-8")
    assert coverage_total(str(formatted)) == "100 statements · 20 missed · 80% covered"


def test_patch_coverage_report_uses_normalized_paths(tmp_path, run_quality_script, write_json):
    coverage_xml = tmp_path / "coverage.xml"
    coverage_xml.write_text(
        """<?xml version="1.0" ?>
<coverage>
  <packages>
    <package name="cps">
      <classes>
        <class name="a" filename="/home/runner/work/calibre-web/calibre-web/cps/a.py"
               lines-valid="2" lines-covered="1">
          <lines>
            <line number="11" hits="1"/>
            <line number="12" hits="0"/>
          </lines>
        </class>
      </classes>
    </package>
  </packages>
</coverage>
""",
        encoding="utf-8",
    )
    (tmp_path / "coverage-workflow-scripts-term.txt").write_text(
        "TOTAL 673 432 36%\n", encoding="utf-8"
    )
    changed = write_json(
        tmp_path / "changed-files.json",
        [
            {
                "filename": "/home/runner/work/calibre-web/calibre-web/cps/a.py",
                "status": "modified",
                "patch": "@@ -1,0 +11,2 @@\n+covered\n+missing",
            },
            {
                "filename": "cps/removed.py",
                "status": "removed",
                "patch": "@@ -1 +0,0 @@\n-old",
            },
            {
                "filename": "cps/unknown.py",
                "status": "modified",
                "patch": "@@ -1 +1 @@\n+new",
            },
        ],
    )

    proc = run_quality_script(
        "test_coverage_comment.py",
        tmp_path,
        env={
            "COVERAGE_XML_PATH": str(coverage_xml),
            "CHANGED_FILES_JSON": str(changed),
            "RUN_URL": "https://example.test/run/1",
        },
    )

    out = proc.stdout
    assert proc.returncode == 0
    assert "**Full analysis:** [Workflow run](https://example.test/run/1)" in out
    assert (
        "**Workflow script coverage:** ❌ Low coverage "
        "`673 statements · 432 missed · 36% covered`"
    ) in out
    assert "**Diff lines:** 3 &nbsp;|&nbsp; **Missing:** 2 &nbsp;|&nbsp; **Covered:** 1" in out
    assert "<details><summary>Patch coverage details</summary>" in out
    assert "- `cps/a.py`: 1/2 covered; missing lines 12" in out
    assert "- `cps/unknown.py`: 0/1 covered; missing lines 1" in out
    assert "removed.py" not in out


def test_test_panel_renders_suite_table_and_failure_details(
    tmp_path, run_quality_script, write_json
):
    write_json(
        tmp_path / "test-results.json",
        {
            "run_url": "https://example.test/run/9",
            "suites": [
                {
                    "key": "workflow_script_tests",
                    "name": "Workflow script tests",
                    "outcome": "failure",
                    "summary": "1 failed, 50 passed in 2.10s",
                    "failures": [
                        {
                            "kind": "FAILED",
                            "nodeid": "tests/workflow_scripts/test_x.py::test_a",
                            "message": "AssertionError: bad",
                        }
                    ],
                },
                {
                    "key": "unit_tests",
                    "name": "Unit (in-process)",
                    "outcome": "success",
                    "summary": "12 passed in 1.50s",
                    "failures": [],
                },
            ],
        },
    )

    proc = run_quality_script(
        "test_coverage_comment.py",
        tmp_path,
        env={"COVERAGE_XML_PATH": str(tmp_path / "missing.xml")},
    )

    out = proc.stdout
    assert proc.returncode == 0
    assert "## Tests" in out
    assert out.count("**Full analysis:**") == 1
    assert "**Full analysis:** [Workflow run](https://example.test/run/9)" in out
    assert "| ❌ | Workflow script tests | `1 failed, 50 passed in 2.10s` | 1 |" in out
    assert "| ✅ | Unit (in-process) | `12 passed in 1.50s` | 0 |" in out
    assert "| ⚪ | Playwright E2E | `no results` | 0 |" in out
    assert "<details><summary>❌ Workflow script tests failures (1)</summary>" in out
    assert "- `tests/workflow_scripts/test_x.py::test_a` — AssertionError: bad" in out
    assert "**Analysis errors:**" not in out
    assert "### Patch coverage" in out


def test_missing_coverage_xml_is_not_an_error(tmp_path, run_quality_script):
    proc = run_quality_script(
        "test_coverage_comment.py",
        tmp_path,
        env={"COVERAGE_XML_PATH": str(tmp_path / "missing.xml")},
    )

    assert proc.returncode == 0
    assert "## Patch coverage" in proc.stdout
    assert "No coverage data was generated for this run." in proc.stdout


def test_missing_coverage_xml_still_reports_workflow_coverage(tmp_path, run_quality_script):
    (tmp_path / "coverage-workflow-scripts-term.txt").write_text(
        "TOTAL 100 20 80%\n", encoding="utf-8"
    )

    proc = run_quality_script(
        "test_coverage_comment.py",
        tmp_path,
        env={"COVERAGE_XML_PATH": str(tmp_path / "missing.xml")},
    )

    assert proc.returncode == 0
    assert (
        "<small>Workflow script coverage: "
        "`100 statements · 20 missed · 80% covered`</small>"
    ) in proc.stdout


def test_medium_and_low_coverage_use_severity_icons(tmp_path, run_quality_script):
    term = tmp_path / "coverage-workflow-scripts-term.txt"

    for percent, marker in ((75, "⚠️ Low coverage"), (45, "❌ Low coverage")):
        term.write_text(f"TOTAL 100 30 {percent}%\n", encoding="utf-8")
        proc = run_quality_script(
            "test_coverage_comment.py",
            tmp_path,
            env={"COVERAGE_XML_PATH": str(tmp_path / "missing.xml")},
        )

        assert proc.returncode == 0
        assert (
            f"**Workflow script coverage:** {marker} "
            f"`100 statements · 30 missed · {percent}% covered`"
        ) in proc.stdout


def write_simple_coverage_xml(tmp_path, covered_lines):
    xml = tmp_path / "coverage.xml"
    lines = "\n".join(
        f'<line number="{number}" hits="1"/>' for number in covered_lines
    )
    xml.write_text(
        f"""<?xml version="1.0" ?>
<coverage>
  <classes>
    <class name="a" filename="cps/a.py">
      <lines>{lines}</lines>
    </class>
  </classes>
</coverage>
""",
        encoding="utf-8",
    )
    return xml


def test_changed_files_without_covered_lines_cannot_compute_patch_coverage(
    tmp_path, run_quality_script, write_json
):
    xml = write_simple_coverage_xml(tmp_path, [11])
    changed = write_json(
        tmp_path / "changed-files.json",
        [
            {
                "filename": "cps/a.py",
                "status": "modified",
                "patch": "@@ -1 +20 @@\n+outside coverage data",
            },
            {"filename": "", "status": "modified", "patch": "@@ -1 +1 @@\n+new"},
        ],
    )

    proc = run_quality_script(
        "test_coverage_comment.py",
        tmp_path,
        env={
            "COVERAGE_XML_PATH": str(xml),
            "CHANGED_FILES_JSON": str(changed),
        },
    )

    assert proc.returncode == 0
    assert "_Patch coverage could not be computed for this run._" in proc.stdout


def test_unmeasured_python_file_is_counted_as_uncovered(
    tmp_path, run_quality_script, write_json
):
    xml = write_simple_coverage_xml(tmp_path, [11])
    changed = write_json(
        tmp_path / "changed-files.json",
        [
            {
                "filename": "cps/a.py",
                "status": "modified",
                "patch": "@@ -1 +11 @@\n+covered",
            },
            {
                "filename": "cps/new.py",
                "status": "added",
                "patch": "@@ -0,0 +1,2 @@\n+def new():\n+    pass",
            },
            {
                "filename": "README.md",
                "status": "modified",
                "patch": "@@ -1 +1 @@\n+docs",
            },
        ],
    )

    proc = run_quality_script(
        "test_coverage_comment.py",
        tmp_path,
        env={"COVERAGE_XML_PATH": str(xml), "CHANGED_FILES_JSON": str(changed)},
    )

    out = proc.stdout
    assert proc.returncode == 0
    assert "**Diff lines:** 3 &nbsp;|&nbsp; **Missing:** 2 &nbsp;|&nbsp; **Covered:** 1" in out
    assert "- `cps/new.py`: 0/2 covered; missing lines 1, 2" in out
    assert "README.md" not in out
    assert "brightgreen" not in out


def test_fully_covered_changed_file_has_no_missing_lines_detail(
    tmp_path, run_quality_script, write_json
):
    xml = write_simple_coverage_xml(tmp_path, [11])
    changed = write_json(
        tmp_path / "changed-files.json",
        [
            {
                "filename": "cps/a.py",
                "status": "modified",
                "patch": "@@ -1 +11 @@\n+covered",
            }
        ],
    )

    proc = run_quality_script(
        "test_coverage_comment.py",
        tmp_path,
        env={
            "COVERAGE_XML_PATH": str(xml),
            "CHANGED_FILES_JSON": str(changed),
        },
    )

    assert proc.returncode == 0
    assert "- `cps/a.py`: 1/1 covered" in proc.stdout
    assert "missing lines" not in proc.stdout


def test_coverage_xml_without_changed_file_json_does_not_compute_patch(
    tmp_path, run_quality_script
):
    xml = write_simple_coverage_xml(tmp_path, [11])
    (tmp_path / "coverage-workflow-scripts-term.txt").write_text(
        "TOTAL 100 20 80%\n", encoding="utf-8"
    )

    proc = run_quality_script(
        "test_coverage_comment.py",
        tmp_path,
        env={"COVERAGE_XML_PATH": str(xml), "CHANGED_FILES_JSON": ""},
    )

    assert proc.returncode == 0
    assert "_Patch coverage could not be computed for this run._" in proc.stdout
    assert (
        "<small>Workflow script coverage: "
        "`100 statements · 20 missed · 80% covered`</small>"
    ) in proc.stdout
