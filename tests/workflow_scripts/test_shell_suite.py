import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = sorted((REPO_ROOT / ".github" / "scripts").glob("*.sh"))
COMBINE_SCRIPT = (REPO_ROOT / ".github" / "scripts" / "combine_coverage.sh").resolve()
TRACE_RE = re.compile(r"^\++ (?P<source>[^:]+):(?P<line>\d+):")
CLOSER_KEYWORDS = {"fi", "done", "esac", "}", "else", "then", "do"}


def fake_python(bin_dir, body):
    bin_dir.mkdir(parents=True, exist_ok=True)
    path = bin_dir / "python"
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(0o755)
    return str(bin_dir)


def run_traced(tmp_path, body, files=()):
    bin_dir = fake_python(tmp_path / "bin", body)
    for name, content in files:
        (tmp_path / name).write_text(content, encoding="utf-8")

    trace_path = tmp_path / "trace.log"
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["PS4"] = "+ ${BASH_SOURCE[0]}:${LINENO}: "
    wrapper = (
        f"exec 9>{shlex.quote(str(trace_path))}; "
        f"BASH_XTRACEFD=9 source {shlex.quote(str(COMBINE_SCRIPT))}"
    )
    subprocess.run(
        ["bash", "-xc", wrapper],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return trace_path.read_text(encoding="utf-8")


def trace_hits(trace, scripts):
    hits = {script: set() for script in scripts}
    by_source = {str(script.resolve()): script for script in scripts}
    for line in trace.splitlines():
        match = TRACE_RE.match(line)
        script = by_source.get(match.group("source")) if match else None
        if script:
            hits[script].add(int(match.group("line")))
    return hits


def executable_lines(script):
    result = set()
    continued = False
    for number, raw in enumerate(script.read_text(encoding="utf-8").splitlines(), 1):
        stripped = raw.strip()
        if number == 1 and stripped.startswith("#!"):
            continue
        if continued:
            continued = stripped.endswith("\\")
            continue
        if not stripped or stripped.startswith("#") or stripped in CLOSER_KEYWORDS:
            continue
        result.add(number)
        continued = stripped.endswith("\\")
    return result


def write_lcov(path, executable, hits):
    records = []
    for script, lines in executable.items():
        relative = script.relative_to(REPO_ROOT).as_posix()
        records.extend(
            [
                "TN:",
                f"SF:{relative}",
                f"LF:{len(lines)}",
                f"LH:{len(lines & hits[script])}",
                "end_of_record",
            ]
        )
    path.write_text("\n".join(records + [""]), encoding="utf-8")


def combine_coverage_traces(tmp_path):
    return [
        run_traced(tmp_path / "empty", "exit 0\n"),
        run_traced(
            tmp_path / "success",
            "case \"$*\" in\n"
            "  *combine*) : > .coverage; exit 0;;\n"
            "  *xml*) : > coverage.xml; exit 0;;\n"
            "  *report*) printf 'TOTAL 1 0 100%%\\n'; exit 0;;\n"
            "esac\n"
            "exit 0\n",
            [(".coverage.server1", "one"), (".coverage.extra", "two")],
        ),
        run_traced(
            tmp_path / "combine-failure",
            "printf 'combine failed\\n' >&2\nexit 1\n",
            [(".coverage.server1", "one")],
        ),
        run_traced(
            tmp_path / "report-failure",
            "case \"$*\" in\n"
            "  *combine*) : > .coverage; exit 0;;\n"
            "  *xml*) : > coverage.xml; exit 0;;\n"
            "  *report*) printf 'bad report\\n' >&2; exit 1;;\n"
            "esac\n"
            "exit 0\n",
            [(".coverage.server1", "one")],
        ),
        run_traced(
            tmp_path / "xml-failure",
            "case \"$*\" in\n"
            "  *combine*) : > .coverage; exit 0;;\n"
            "  *xml*) printf 'bad xml\\n' >&2; exit 1;;\n"
            "esac\n"
            "exit 0\n",
            [(".coverage.server1", "one"), ("coverage.xml", "stale")],
        ),
    ]


def test_shell_workflow_scripts_have_line_coverage(tmp_path):
    if not shutil.which("bash"):
        pytest.skip("bash is not installed")
    assert SCRIPTS, "no shell workflow scripts found"

    lcov_path = Path(
        os.environ.get("SHELL_COVERAGE_LCOV_PATH", "coverage-workflow-shell-lcov.info")
    )
    lcov_path.parent.mkdir(parents=True, exist_ok=True)

    executable = {script: executable_lines(script) for script in SCRIPTS}
    hits = {script: set() for script in SCRIPTS}
    for trace in combine_coverage_traces(tmp_path):
        for script, lines in trace_hits(trace, SCRIPTS).items():
            hits[script].update(lines)

    for script, lines in executable.items():
        missed = lines - hits[script]
        assert not missed, (
            f"uncovered shell lines in {script.relative_to(REPO_ROOT)}: {sorted(missed)}"
        )

    write_lcov(lcov_path, executable, hits)
    generated = lcov_path.read_text(encoding="utf-8")
    assert ".github/scripts/" in generated
    for lines in executable.values():
        assert f"LF:{len(lines)}" in generated
        assert f"LH:{len(lines)}" in generated
