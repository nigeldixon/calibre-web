import os


def test_diff_scope_helpers(load_quality_script, tmp_path):
    diff_scope = load_quality_script("diff_scope.py", tmp_path)
    norm = diff_scope["normalize_diff_path"]
    parse = diff_scope["parse_unified_zero_diff"]
    collect = diff_scope["collect_diff_ranges"]
    intersects = diff_scope["result_intersects_changed_lines"]

    assert norm("") == ""
    assert norm("./a/b.py") == "b.py"
    assert norm("b/cps/a.py") == "cps/a.py"

    diff = "\n".join([
        "diff --git a/a.py b/a.py",
        "--- a/a.py",
        "+++ /dev/null",
        "@@ -1,1 +0,0 @@",
        "-old",
        "diff --git a/b.py b/b.py",
        "--- /dev/null",
        "+++ b/b.py",
        "@@ -0,0 +1,2 @@",
        "+a",
        "+b",
        "@@ bad hunk @@",
        "@@ -10,0 +10,0 @@",
    ])
    ranges = parse(diff)
    assert ranges == {"b.py": [(1, 2)]}

    # no current file before hunk -> ignored
    assert parse("@@ -1,1 +1,1 @@\n+x\n") == {}

    # collect_diff_ranges: no shas
    assert collect("", "") is None

    # fetch=False path, fake git
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    git = bin_dir / "git"
    git.write_text("#!/bin/sh\nif [ \"$1\" = \"diff\" ]; then printf '%s\\n' '+++ b/x.py' '@@ -1,1 +5,1 @@'; exit 0; fi\nexit 0\n", encoding="utf-8")
    git.chmod(0o755)
    old_path = os.environ.get("PATH", "")
    os.environ["PATH"] = f"{bin_dir}:{old_path}"
    try:
        assert collect("a" * 40, "b" * 40, fetch=False) == {"x.py": [(5, 5)]}
    finally:
        os.environ["PATH"] = old_path

    # defensive location parsing / iteration
    normalize = lambda u: (u or "").replace("\\", "/")
    result = {
        "locations": [
            "bad",
            {"physicalLocation": {"artifactLocation": "bad", "region": {"startLine": 1, "endLine": "x"}}},
            {"physicalLocation": {"artifactLocation": {"uri": "a.py"}, "region": {"startLine": 7, "endLine": 5}}},
        ],
        "relatedLocations": [{"physicalLocation": "bad"}],
        "codeFlows": [
            "bad",
            {"threadFlows": [
                "bad",
                {"locations": ["bad", {"location": "bad"}, {"location": {"physicalLocation": {"artifactLocation": {"uri": "b.py"}, "region": {"startLine": 20}}}}]},
            ]},
        ],
    }
    assert intersects(result, {"a.py", "b.py"}, {"a.py": [(1, 1)], "b.py": [(20, 20)]}, normalize) is True

    # line_intersects break optimization and no-uri continue
    result2 = {
        "locations": [
            {"physicalLocation": {"artifactLocation": {"uri": ""}, "region": {"startLine": 1}}},
            {"physicalLocation": {"artifactLocation": {"uri": "a.py"}, "region": {"startLine": 1}}},
        ]
    }
    assert intersects(result2, {"a.py"}, {"a.py": [(5, 6)]}, normalize) is False

    # changed file in list but no ranges for it -> conservative
    result3 = {"locations": [{"physicalLocation": {"artifactLocation": {"uri": "a.py"}, "region": {"startLine": 1}}}]}
    assert intersects(result3, {"a.py"}, {}, normalize) is True

    # changed_ranges None falls back to file membership
    assert intersects(result3, {"b.py"}, None, normalize) is False
    assert intersects("bad", {"a.py"}, {"a.py": [(1, 1)]}, normalize) is False

    # normal end line, and loop-back branches
    result4 = {
        "locations": [{"physicalLocation": {"artifactLocation": {"uri": "a.py"}, "region": {"startLine": 2, "endLine": 3}}}],
        "codeFlows": [
            "bad",
            {"threadFlows": ["bad", {"locations": ["bad", {"location": "bad"}, {"location": {"physicalLocation": {"artifactLocation": {"uri": "a.py"}, "region": {"startLine": 2}}}}]}, {"locations": [{"location": {"physicalLocation": {"artifactLocation": {"uri": "a.py"}, "region": {"startLine": 2}}}}]}]},
            {"threadFlows": [{"locations": [{"location": {"physicalLocation": {"artifactLocation": {"uri": "a.py"}, "region": {"startLine": 2}}}}]}, {"locations": [{"location": {"physicalLocation": {"artifactLocation": {"uri": "a.py"}, "region": {"startLine": 2}}}}]}]},
        ],
    }
    assert intersects(result4, {"a.py"}, {"a.py": [(2, 2)]}, normalize) is True
    assert intersects(result3, {"a.py", "b.py"}, {"b.py": [(1, 1)]}, normalize) is True
    assert norm("plain.py") == "plain.py"
    lp = diff_scope["_location_path_and_line"]
    assert lp({"physicalLocation": "bad"}, normalize)[0] == ""
