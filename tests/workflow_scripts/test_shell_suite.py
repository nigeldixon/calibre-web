import os
import re
import shlex
import shutil
import subprocess
from collections import namedtuple
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = sorted((REPO_ROOT / ".github" / "scripts").glob("*.sh"))
COMBINE_SCRIPT = (REPO_ROOT / ".github" / "scripts" / "combine_coverage.sh").resolve()
EVENT_RE = re.compile(
    r"^\++ (?P<source>[^:]+):(?P<line>\d+): (?P<command>.*)$"
)
CLOSER_KEYWORDS = {"fi", "done", "esac", "}", "else", "then", "do"}

Event = namedtuple("Event", "line command")
BranchPoint = namedtuple(
    "BranchPoint", "line kind body body_lines after end false_target"
)


def fake_python(bin_dir, body):
    bin_dir.mkdir(parents=True, exist_ok=True)
    path = bin_dir / "python"
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(0o755)
    return str(bin_dir)


def run_traced(tmp_path, body, files=(), symlinks=()):
    bin_dir = fake_python(tmp_path / "bin", body)
    for name, content in files:
        (tmp_path / name).write_text(content, encoding="utf-8")
    for name, target in symlinks:
        (tmp_path / name).symlink_to(target)

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


def trace_events(trace, scripts):
    events = {script: [] for script in scripts}
    by_source = {str(script.resolve()): script for script in scripts}
    for line in trace.splitlines():
        match = EVENT_RE.match(line)
        script = by_source.get(match.group("source")) if match else None
        if script:
            events[script].append(Event(int(match.group("line")), match.group("command").strip()))
    return events


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


def next_executable(executable, after, before=None):
    candidates = [
        number
        for number in executable
        if number > after and (before is None or number < before)
    ]
    return min(candidates) if candidates else None


def matching_close(source, start, opener, closer):
    depth = 0
    for number in range(start, len(source) + 1):
        stripped = source[number - 1].strip()
        if stripped == opener or stripped.startswith(f"{opener} "):
            depth += 1
        elif stripped == closer:
            depth -= 1
            if depth == 0:
                return number
    return None


def branch_points(script, executable):
    source = script.read_text(encoding="utf-8").splitlines()
    points = {}
    loops = []

    for number in sorted(executable):
        stripped = source[number - 1].strip()
        if stripped.startswith("if "):
            opener, closer, kind = "if", "fi", "if"
        elif stripped.split(" ", 1)[0] in {"for", "until", "while"}:
            opener, closer, kind = stripped.split(" ", 1)[0], "done", "loop"
        else:
            continue

        end = matching_close(source, number, opener, closer)
        body = next_executable(executable, number, end)
        body_lines = {line for line in executable if number < line < end}
        point = BranchPoint(number, kind, body, body_lines, next_executable(executable, end), end, None)
        points[number] = point
        if kind == "loop":
            loops.append(point)

    for number in sorted(executable):
        if number in points:
            continue
        stripped = source[number - 1].strip()
        if " || " not in stripped and " && " not in stripped:
            continue
        enclosing = [point for point in loops if point.body <= number <= point.end]
        false_target = enclosing[-1].line if enclosing else next_executable(executable, number)
        points[number] = BranchPoint(
            number, "logic", None, set(), next_executable(executable, number), None, false_target
        )

    return points


def classify_branch_events(points, events):
    counts = {line: [0, 0] for line in points}

    for point in points.values():
        if point.kind != "loop":
            continue
        for index, event in enumerate(events):
            following = events[index + 1] if index + 1 < len(events) else None
            following_line = following.line if following else None
            if event.line == point.line and following_line in point.body_lines:
                counts[point.line][0] += 1
            elif event.line in point.body_lines and following_line == point.after:
                # Bash traces the for line only when another iteration starts;
                # leaving the body for the next statement is the exit outcome.
                counts[point.line][1] += 1

    for index, event in enumerate(events):
        point = points.get(event.line)
        if not point or point.kind == "loop":
            continue

        following = events[index + 1] if index + 1 < len(events) else None
        following_line = following.line if following else None

        if point.kind == "logic":
            # A short-circuit continuation is traced as a second event on the
            # same source line; ignore it so each decision is counted once.
            if index > 0 and events[index - 1].line == point.line:
                continue
            counts[point.line][1 if following_line == point.line else 0] += 1
            continue

        if following_line in point.body_lines:
            counts[point.line][0] += 1
        elif following is None or following_line == point.after or following_line > point.end:
            counts[point.line][1] += 1
    return counts


def write_lcov(path, executable, hits, branches):
    records = []
    for script, lines in executable.items():
        relative = script.relative_to(REPO_ROOT).as_posix()
        branch_counts = branches[script]
        records.extend(
            [
                "TN:",
                f"SF:{relative}",
                f"LF:{len(lines)}",
                f"LH:{len(lines & hits[script])}",
            ]
        )
        for line, counts in sorted(branch_counts.items()):
            for outcome, count in enumerate(counts):
                records.append(f"BRDA:{line},0,{outcome},{count}")
        records.extend(
            [
                f"BRF:{sum(len(counts) for counts in branch_counts.values())}",
                f"BRH:{sum(1 for counts in branch_counts.values() for count in counts if count)}",
                "end_of_record",
            ]
        )
    path.write_text("\n".join(records + [""]), encoding="utf-8")


def combine_coverage_traces(tmp_path):
    return [
        run_traced(tmp_path / "empty", "exit 0\n"),
        run_traced(
            tmp_path / "broken-symlink",
            "exit 0\n",
            symlinks=[(".coverage.broken", "/does/not/exist")],
        ),
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


def test_shell_workflow_scripts_have_line_and_branch_coverage(tmp_path):
    if not shutil.which("bash"):
        pytest.skip("bash is not installed")
    assert SCRIPTS, "no shell workflow scripts found"

    lcov_path = Path(
        os.environ.get("SHELL_COVERAGE_LCOV_PATH", "coverage-workflow-shell-lcov.info")
    )
    lcov_path.parent.mkdir(parents=True, exist_ok=True)

    executable = {script: executable_lines(script) for script in SCRIPTS}
    points = {script: branch_points(script, executable[script]) for script in SCRIPTS}
    scenario_events = [
        trace_events(trace, SCRIPTS) for trace in combine_coverage_traces(tmp_path)
    ]

    hits = {script: set() for script in SCRIPTS}
    branches = {
        script: {line: [0, 0] for line in points[script]} for script in SCRIPTS
    }
    for events_by_script in scenario_events:
        for script, events in events_by_script.items():
            hits[script].update(event.line for event in events)
            for line, counts in classify_branch_events(points[script], events).items():
                for index, count in enumerate(counts):
                    branches[script][line][index] += count

    for script, lines in executable.items():
        missed_lines = lines - hits[script]
        assert not missed_lines, (
            f"uncovered shell lines in {script.relative_to(REPO_ROOT)}: {sorted(missed_lines)}"
        )
        missed_branches = [
            line for line, counts in branches[script].items() if 0 in counts
        ]
        assert not missed_branches, (
            f"uncovered shell branches in {script.relative_to(REPO_ROOT)}: {missed_branches}"
        )

    write_lcov(lcov_path, executable, hits, branches)
    generated = lcov_path.read_text(encoding="utf-8")
    assert ".github/scripts/" in generated
    for lines in executable.values():
        assert f"LF:{len(lines)}" in generated
        assert f"LH:{len(lines)}" in generated
    for branch_counts in branches.values():
        total = sum(len(counts) for counts in branch_counts.values())
        assert f"BRF:{total}" in generated
        assert f"BRH:{total}" in generated
