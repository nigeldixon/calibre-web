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
    assert script["pct_to_color"](50)[0] == "orange"
    assert script["bar"](50) == "██████████░░░░░░░░░░"


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
    assert "**Diff lines:** 2 &nbsp;|&nbsp; **Missing:** 1 &nbsp;|&nbsp; **Covered:** 1" in out
    assert "<details><summary>Patch coverage details</summary>" in out
    assert "- `cps/a.py`: 1/2 covered; missing lines 12" in out
    assert "removed.py" not in out
    assert "unknown.py" not in out


def test_missing_coverage_xml_is_not_an_error(tmp_path, run_quality_script):
    proc = run_quality_script(
        "test_coverage_comment.py",
        tmp_path,
        env={"COVERAGE_XML_PATH": str(tmp_path / "missing.xml")},
    )

    assert proc.returncode == 0
    assert "## Patch coverage" in proc.stdout
    assert "No coverage data was generated for this run." in proc.stdout
