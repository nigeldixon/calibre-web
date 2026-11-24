import pytest

pytest.importorskip("defusedxml")


def load_helpers(load_quality_script, tmp_path):
    return load_quality_script(
        "test_coverage_comment.py",
        tmp_path,
        env={"COVERAGE_XML_PATH": str(tmp_path / "missing.xml")},
    )


def write_shell_lcov(tmp_path, hit=10, total=10, branches=2, hit_branches=2):
    path = tmp_path / "shell-lcov.info"
    path.write_text(
        "TN:\n"
        "SF:.github/scripts/combine_coverage.sh\n"
        f"LF:{total}\nLH:{hit}\n"
        f"BRF:{branches}\nBRH:{hit_branches}\n"
        "end_of_record\n",
        encoding="utf-8",
    )
    return path


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
    assert script["status_icon"]("success", "no results") == "⚪"
    assert script["status_icon"]("success", "12 passed") == "✅"


def test_coverage_by_file_ignores_malformed_and_empty_entries(load_quality_script, tmp_path):
    script = load_helpers(load_quality_script, tmp_path)
    coverage_by_file = script["coverage_by_file"]

    assert coverage_by_file(str(tmp_path / "missing.xml")) == {}

    invalid = tmp_path / "invalid.xml"
    invalid.write_text("<coverage><broken>", encoding="utf-8")
    with pytest.raises(script["CoverageDataError"]):
        coverage_by_file(str(invalid))

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
    coverage_stats = script["coverage_stats"]

    assert coverage_total(str(tmp_path / "missing.txt")) is None
    assert coverage_stats(str(tmp_path / "missing.txt")) is None
    assert coverage_total(str(tmp_path)) is None

    unformatted = tmp_path / "unformatted.txt"
    unformatted.write_text("TOTAL 10 5\n", encoding="utf-8")
    assert coverage_total(str(unformatted)) == "TOTAL 10 5"
    assert coverage_stats(str(unformatted)) is None

    no_total = tmp_path / "no-total.txt"
    no_total.write_text("some coverage output\n", encoding="utf-8")
    assert coverage_total(str(no_total)) == "some coverage output"

    formatted = tmp_path / "formatted.txt"
    formatted.write_text("TOTAL 100 20 80%\n", encoding="utf-8")
    assert coverage_total(str(formatted)) == "100 statements · 20 missed · 80% covered"
    assert coverage_stats(str(formatted)) == {
        "statements": 100, "missed": 20, "branches": 0, "partial": 0, "percent": 80.0,
    }

    branched = tmp_path / "branched.txt"
    branched.write_text("TOTAL 1352 0 626 0 100%\n", encoding="utf-8")
    assert coverage_total(str(branched)) == (
        "1352 statements · 0 missed · 626 branches · 0 partial · 100% covered"
    )
    assert coverage_stats(str(branched)) == {
        "statements": 1352, "missed": 0, "branches": 626, "partial": 0, "percent": 100.0,
    }

    partial_branches = tmp_path / "partial-branches.txt"
    partial_branches.write_text("TOTAL 100 20 80 30 36%\n", encoding="utf-8")
    assert coverage_total(str(partial_branches)) == (
        "100 statements · 20 missed · 80 branches · 30 partial · 36% covered"
    )


def test_js_coverage_detail_uses_only_workflow_scripts(load_quality_script, tmp_path):
    lcov = tmp_path / "js-lcov.info"
    lcov.write_text(
        "TN:\n"
        "SF:.github/scripts/publish_quality_report.js\n"
        "LF:10\nLH:9\nBRF:8\nBRH:6\n"
        "end_of_record\n"
        "SF:tests/workflow_scripts/js/workflow_modules.test.js\n"
        "LF:100\nLH:100\nBRF:50\nBRH:50\n"
        "end_of_record\n",
        encoding="utf-8",
    )
    script = load_helpers(load_quality_script, tmp_path)
    assert script["js_coverage_total"](str(lcov)) == {
        "lines": 10, "hit_lines": 9, "branches": 8, "hit_branches": 6,
    }

    loaded = load_quality_script(
        "test_coverage_comment.py", tmp_path,
        env={"WORKFLOW_JS_COVERAGE_LCOV_PATH": str(lcov)},
    )
    js = loaded["js_coverage_detail"]()
    assert js["detail"] == "9/10 lines (90%) · 6/8 branches (75%)"
    assert js["marker"] == "⚠️ Low coverage"

    shell_lcov = write_shell_lcov(tmp_path)
    term = tmp_path / "coverage-workflow-scripts-term.txt"
    term.write_text("TOTAL 1389 0 640 0 100%\n", encoding="utf-8")
    combined = load_quality_script(
        "test_coverage_comment.py", tmp_path,
        env={
            "WORKFLOW_COVERAGE_TERM_PATH": str(term),
            "WORKFLOW_JS_COVERAGE_LCOV_PATH": str(lcov),
            "WORKFLOW_SHELL_COVERAGE_LCOV_PATH": str(shell_lcov),
        },
    )["coverage_summary_line"]()
    assert combined == (
        "**Workflow script coverage — ⚠️ Low coverage:** "
        "Python `1389/1389 statements (100%) · 640/640 branches (100%)` · "
        "JavaScript `9/10 lines (90%) · 6/8 branches (75%)` · "
        "Shell `10/10 lines (100%) · 2/2 inferred branches (100%)`"
    )

    assert script["js_coverage_total"](str(tmp_path)) is None

    no_branches = tmp_path / "no-branches.info"
    no_branches.write_text(
        "SF:.github/scripts/example.js\nLF:4\nLH:4\nend_of_record\n",
        encoding="utf-8",
    )
    assert script["js_coverage_total"](str(no_branches)) == {
        "lines": 4, "hit_lines": 4, "branches": 0, "hit_branches": 0,
    }
    no_branch = load_quality_script(
        "test_coverage_comment.py", tmp_path,
        env={"WORKFLOW_JS_COVERAGE_LCOV_PATH": str(no_branches)},
    )["js_coverage_detail"]()
    assert no_branch["detail"] == "4/4 lines (100%) · 0/0 branches (0%)"
    assert no_branch["marker"] == ""


def test_shell_coverage_detail_uses_only_workflow_scripts(load_quality_script, tmp_path):
    lcov = tmp_path / "shell-lcov.info"
    lcov.write_text(
        "TN:\n"
        "SF:.github/scripts/combine_coverage.sh\n"
        "LF:10\nLH:7\n"
        "BRF:4\nBRH:3\n"
        "end_of_record\n"
        "SF:tests/workflow_scripts/test_shell_suite.py\n"
        "LF:100\nLH:100\n"
        "end_of_record\n",
        encoding="utf-8",
    )
    script = load_helpers(load_quality_script, tmp_path)
    assert script["shell_coverage_total"](str(lcov)) == {
        "lines": 10, "hit_lines": 7, "branches": 4, "hit_branches": 3,
    }

    loaded = load_quality_script(
        "test_coverage_comment.py", tmp_path,
        env={"WORKFLOW_SHELL_COVERAGE_LCOV_PATH": str(lcov)},
    )
    shell = loaded["shell_coverage_detail"]()
    assert shell["detail"] == "7/10 lines (70%) · 3/4 inferred branches (75%)"
    assert shell["marker"] == "⚠️ Low coverage"

    combined = load_quality_script(
        "test_coverage_comment.py", tmp_path,
        env={"WORKFLOW_SHELL_COVERAGE_LCOV_PATH": str(lcov)},
    )["coverage_summary_line"]()
    assert combined == (
        "**Workflow script coverage — ⚠️ Low coverage:** "
        "Shell `7/10 lines (70%) · 3/4 inferred branches (75%)`"
    )

    assert script["shell_coverage_total"](str(tmp_path)) is None


def test_coverage_summary_line_uses_only_the_most_severe_marker(
    load_quality_script, tmp_path
):
    term = tmp_path / "coverage-term.txt"
    term.write_text("TOTAL 100 25 20 5 75%\n", encoding="utf-8")
    js = tmp_path / "js-lcov.info"
    js.write_text(
        "TN:\n"
        "SF:.github/scripts/example.js\n"
        "LF:10\nLH:5\nBRF:8\nBRH:4\n"
        "end_of_record\n",
        encoding="utf-8",
    )
    combined = load_quality_script(
        "test_coverage_comment.py",
        tmp_path,
        env={
            "WORKFLOW_COVERAGE_TERM_PATH": str(term),
            "WORKFLOW_JS_COVERAGE_LCOV_PATH": str(js),
            "WORKFLOW_SHELL_COVERAGE_LCOV_PATH": str(write_shell_lcov(tmp_path)),
        },
    )["coverage_summary_line"]()
    assert combined == (
        "**Workflow script coverage — ❌ Low coverage:** "
        "Python `75/100 statements (75%) · 15/20 branches (75%)` · "
        "JavaScript `5/10 lines (50%) · 4/8 branches (50%)` · "
        "Shell `10/10 lines (100%) · 2/2 inferred branches (100%)`"
    )


def test_workflow_changed_files_covers_workflow_scripts_and_tests(
    load_quality_script, tmp_path, write_json
):
    script = load_helpers(load_quality_script, tmp_path)
    workflow_changed_files = script["workflow_changed_files"]

    assert workflow_changed_files("") is None
    assert workflow_changed_files(str(tmp_path / "missing.json")) is None

    changed = write_json(
        tmp_path / "changed-files.json",
        [
            {"filename": "cps/a.py", "status": "modified"},
            {"filename": "README.md", "status": "modified"},
            {
                "filename": "/home/runner/work/calibre-web/calibre-web/.github/workflows/test.yml",
                "status": "modified",
            },
            {"filename": ".github/scripts/new.py", "status": "added"},
            {
                "filename": "tests/workflow_scripts/js/report_body.test.js",
                "status": "modified",
            },
            {"filename": "tests/workflow_scripts/removed.py", "status": "removed"},
        ],
    )

    assert workflow_changed_files(str(changed)) == [
        ".github/scripts/new.py",
        ".github/workflows/test.yml",
        "tests/workflow_scripts/js/report_body.test.js",
        "tests/workflow_scripts/removed.py",
    ]


def test_workflow_coverage_collapses_when_no_workflow_files_changed(
    tmp_path, run_quality_script, write_json
):
    (tmp_path / "coverage-workflow-scripts-term.txt").write_text(
        "TOTAL 100 30 20 11 45%\n", encoding="utf-8"
    )
    changed = write_json(
        tmp_path / "changed-files.json",
        [{"filename": "cps/a.py", "status": "modified", "patch": "@@ -1 +1 @@\n+edit"}],
    )

    proc = run_quality_script(
        "test_coverage_comment.py",
        tmp_path,
        env={
            "COVERAGE_XML_PATH": str(tmp_path / "missing.xml"),
            "CHANGED_FILES_JSON": str(changed),
        },
    )

    assert proc.returncode == 0
    assert "\n".join([
        "<details><summary>Workflow script coverage</summary>",
        "",
        "**Workflow script coverage — ❌ Low coverage:** "
        "Python `70/100 statements (70%) · 9/20 branches (45%)`",
        "",
        "</details>",
    ]) in proc.stdout


@pytest.mark.parametrize(
    "filename",
    [
        ".github/workflows/test.yml",
        ".github/scripts/test_coverage_comment.py",
        "tests/workflow_scripts/test_test_coverage_comment.py",
    ],
)
def test_workflow_coverage_stays_visible_when_workflow_files_changed(
    filename, tmp_path, run_quality_script, write_json
):
    (tmp_path / "coverage-workflow-scripts-term.txt").write_text(
        "TOTAL 100 30 20 11 45%\n", encoding="utf-8"
    )
    changed = write_json(
        tmp_path / "changed-files.json",
        [{"filename": filename, "status": "modified"}],
    )

    proc = run_quality_script(
        "test_coverage_comment.py",
        tmp_path,
        env={
            "COVERAGE_XML_PATH": str(tmp_path / "missing.xml"),
            "CHANGED_FILES_JSON": str(changed),
        },
    )

    assert proc.returncode == 0
    assert (
        "**Workflow script coverage — ❌ Low coverage:** "
        "Python `70/100 statements (70%) · 9/20 branches (45%)`"
    ) in proc.stdout
    assert "<details><summary>Workflow script coverage</summary>" not in proc.stdout


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
    shell_lcov = write_shell_lcov(tmp_path)
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
            "WORKFLOW_SHELL_COVERAGE_LCOV_PATH": str(shell_lcov),
            "RUN_URL": "https://example.test/run/1",
        },
    )

    out = proc.stdout
    assert proc.returncode == 0
    assert "**Full analysis:** [Workflow run](https://example.test/run/1)" in out
    assert "\n".join([
        "<details><summary>Workflow script coverage</summary>",
        "",
        "**Workflow script coverage — ❌ Low coverage:** "
        "Python `241/673 statements (36%)` · Shell `10/10 lines (100%) · 2/2 inferred branches (100%)`",
        "",
        "</details>",
    ]) in out
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
                {
                    "key": "functional_tests",
                    "name": "HTTP functional",
                    "outcome": "success",
                    "summary": "no results",
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
    assert "### 🧪 Tests" in out
    assert out.count("**Full analysis:**") == 1
    assert "**Full analysis:** [Workflow run](https://example.test/run/9)" in out
    assert "| ❌ | Workflow script tests | `1 failed, 50 passed in 2.10s` | 1 |" in out
    assert "| ✅ | Unit (in-process) | `12 passed in 1.50s` | 0 |" in out
    assert "| ⚪ | Playwright E2E | `no results` | 0 |" in out
    assert "| ⚪ | HTTP functional | `no results` | 0 |" in out
    assert "<details><summary>❌ Workflow script tests failures (1)</summary>" in out
    assert "- `tests/workflow_scripts/test_x.py::test_a` — AssertionError: bad" in out
    assert "**Analysis errors:**" not in out
    assert "#### Patch coverage" in out


def test_missing_coverage_xml_is_not_an_error(tmp_path, run_quality_script):
    proc = run_quality_script(
        "test_coverage_comment.py",
        tmp_path,
        env={"COVERAGE_XML_PATH": str(tmp_path / "missing.xml")},
    )

    assert proc.returncode == 0
    assert "#### Patch coverage" in proc.stdout
    assert "No application coverage data was generated for this run." in proc.stdout


def test_malformed_coverage_xml_fails_closed(tmp_path, run_quality_script):
    (tmp_path / "coverage.xml").write_text("<coverage><broken>", encoding="utf-8")
    (tmp_path / "changed-files.json").write_text("[]", encoding="utf-8")

    proc = run_quality_script(
        "test_coverage_comment.py",
        tmp_path,
        env={
            "CHANGED_FILES_JSON": str(tmp_path / "changed-files.json"),
            "COVERAGE_XML_PATH": str(tmp_path / "coverage.xml"),
        },
    )

    assert proc.returncode != 0
    assert "❌ Coverage XML could not be parsed; patch coverage is unavailable for this run." in proc.stdout
    assert "0% covered" not in proc.stdout


def test_missing_coverage_xml_still_reports_workflow_coverage(
    tmp_path, run_quality_script, load_quality_script
):
    (tmp_path / "coverage-workflow-scripts-term.txt").write_text(
        "TOTAL 100 20 80%\n", encoding="utf-8"
    )
    shell_lcov = write_shell_lcov(tmp_path)

    proc = run_quality_script(
        "test_coverage_comment.py",
        tmp_path,
        env={
            "COVERAGE_XML_PATH": str(tmp_path / "missing.xml"),
            "WORKFLOW_SHELL_COVERAGE_LCOV_PATH": str(shell_lcov),
        },
    )

    assert proc.returncode == 0
    assert (
        "<small>Workflow script coverage: "
        "Python `80/100 statements (80%)` · Shell `10/10 lines (100%) · 2/2 inferred branches (100%)`</small>"
    ) in proc.stdout
    assert "<details><summary>Workflow script coverage</summary>" not in proc.stdout

    empty = load_quality_script(
        "test_coverage_comment.py", tmp_path,
        env={
            "WORKFLOW_COVERAGE_TERM_PATH": str(tmp_path / "missing.txt"),
            "WORKFLOW_JS_COVERAGE_LCOV_PATH": str(tmp_path / "missing.info"),
            "WORKFLOW_SHELL_COVERAGE_LCOV_PATH": str(tmp_path / "missing-shell.info"),
        },
    )
    assert empty["coverage_summary_line"]() is None

    zero = tmp_path / "zero.txt"
    zero.write_text("TOTAL 0 0 0%\n", encoding="utf-8")
    zero_detail = load_quality_script(
        "test_coverage_comment.py", tmp_path,
        env={"WORKFLOW_COVERAGE_TERM_PATH": str(zero)},
    )["python_coverage_detail"]()
    assert zero_detail["detail"] == "0/0 statements (0%)"


def test_python_coverage_icon_uses_worst_line_or_branch_percentage(tmp_path, load_quality_script):
    term = tmp_path / "coverage-workflow-scripts-term.txt"
    for total, expected_detail, expected_marker in (
        (
            "TOTAL 100 10 20 10 83%",
            "90/100 statements (90%) · 10/20 branches (50%)",
            "❌ Low coverage",
        ),
        (
            "TOTAL 100 0 20 5 99%",
            "100/100 statements (100%) · 15/20 branches (75%)",
            "⚠️ Low coverage",
        ),
    ):
        term.write_text(total + "\n", encoding="utf-8")
        detail = load_quality_script(
            "test_coverage_comment.py", tmp_path,
            env={"WORKFLOW_COVERAGE_TERM_PATH": str(term)},
        )["python_coverage_detail"]()
        assert detail["detail"] == expected_detail
        assert detail["marker"] == expected_marker


def test_medium_and_low_coverage_use_severity_icons(tmp_path, run_quality_script):
    term = tmp_path / "coverage-workflow-scripts-term.txt"
    shell_lcov = write_shell_lcov(tmp_path)

    for total, detail, marker in (
        (
            "TOTAL 100 30 20 5 75%",
            "70/100 statements (70%) · 15/20 branches (75%)",
            "⚠️ Low coverage",
        ),
        (
            "TOTAL 100 30 20 11 45%",
            "70/100 statements (70%) · 9/20 branches (45%)",
            "❌ Low coverage",
        ),
    ):
        term.write_text(total + "\n", encoding="utf-8")
        proc = run_quality_script(
            "test_coverage_comment.py",
            tmp_path,
            env={
                "COVERAGE_XML_PATH": str(tmp_path / "missing.xml"),
                "WORKFLOW_SHELL_COVERAGE_LCOV_PATH": str(shell_lcov),
            },
        )

        assert proc.returncode == 0
        assert (
            f"**Workflow script coverage — {marker}:** "
            f"Python `{detail}` · Shell `10/10 lines (100%) · 2/2 inferred branches (100%)`"
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


def test_non_application_files_do_not_dilute_patch_coverage(tmp_path, run_quality_script, write_json):
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
                "filename": "tests/unit/test_new.py",
                "status": "added",
                "patch": "@@ -0,0 +1,2 @@\n+def test_new():\n+    pass",
            },
            {
                "filename": ".github/scripts/check_new.py",
                "status": "added",
                "patch": "@@ -0,0 +1,2 @@\n+print('new')\n+",
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
    assert "**Diff lines:** 1 &nbsp;|&nbsp; **Missing:** 0 &nbsp;|&nbsp; **Covered:** 1" in out
    assert "- `cps/a.py`: 1/1 covered" in out
    assert "tests/unit/test_new.py" not in out
    assert ".github/scripts/check_new.py" not in out


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
    shell_lcov = write_shell_lcov(tmp_path)

    proc = run_quality_script(
        "test_coverage_comment.py",
        tmp_path,
        env={
            "COVERAGE_XML_PATH": str(xml),
            "CHANGED_FILES_JSON": "",
            "WORKFLOW_SHELL_COVERAGE_LCOV_PATH": str(shell_lcov),
        },
    )

    assert proc.returncode == 0
    assert "_Patch coverage could not be computed for this run._" in proc.stdout
    assert (
        "<small>Workflow script coverage: "
        "Python `80/100 statements (80%)` · Shell `10/10 lines (100%) · 2/2 inferred branches (100%)`</small>"
    ) in proc.stdout
