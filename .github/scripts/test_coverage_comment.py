#!/usr/bin/env python3
"""Render the PR test panel, including patch coverage."""
import json
import os
import re
from defusedxml import ElementTree as SafeET

from application_changes import application_changed_files, is_application_file, normalize_path
from application_changes import load_changed_files as _load_changed_files
from report_constants import load_constants

C = load_constants()
marker = C["markers"]["tests"]


class CoverageDataError(Exception):
    """Raised when a coverage report exists but cannot be trusted."""


coverage_xml_path = os.environ.get("COVERAGE_XML_PATH", "coverage.xml").strip() or "coverage.xml"
run_url = os.environ.get("RUN_URL", "").strip()
changed_json_path = os.environ.get("CHANGED_FILES_JSON", "").strip()
workflow_coverage_path = os.environ.get("WORKFLOW_COVERAGE_TERM_PATH", "coverage-workflow-scripts-term.txt").strip()
js_coverage_path = os.environ.get("WORKFLOW_JS_COVERAGE_LCOV_PATH", "coverage-workflow-js-lcov.info").strip()
shell_coverage_path = os.environ.get("WORKFLOW_SHELL_COVERAGE_LCOV_PATH", "coverage-workflow-shell-lcov.info").strip()
test_results_path = os.environ.get("TEST_RESULTS_JSON", "test-results.json").strip()

DEFAULT_SUITES = [
    ("workflow_script_tests", "Workflow script tests"),
    ("unit_tests", "Unit (in-process)"),
    ("e2e_tests", "Playwright E2E"),
    ("functional_tests", "HTTP functional"),
]

WORKFLOW_CHANGE_PREFIXES = (".github/workflows/",)


def pct_to_color(p):
    if p >= 90:
        return "brightgreen", "🟢", "Good"
    if p >= 80:
        return "green", "🟢", "Good"
    if p >= 70:
        return "yellow", "🟡", "Fair"
    if p >= 50:
        return "orange", "🟠", "Poor"
    return "red", "🔴", "Poor"


def bar(p, width=20):
    filled = int(round((p / 100.0) * width))
    filled = max(0, min(width, filled))
    return "█" * filled + "░" * (width - filled)


def badge(pct_i, color):
    return f"![coverage](https://img.shields.io/badge/coverage-{pct_i}%25-{color})"


def coverage_by_file(path):
    by_file = {}
    if not os.path.exists(path):
        return by_file
    try:
        root = SafeET.parse(path).getroot()
    except Exception as exc:
        raise CoverageDataError(str(exc)) from exc
    for cls in root.iter("class"):
        filename = normalize_path(cls.get("filename", ""))
        if not filename:
            continue
        lines_el = cls.find("lines")
        if lines_el is None:
            continue
        for line in lines_el.findall("line"):
            try:
                number = int(line.get("number", "0"))
                hits = int(line.get("hits", "0") or 0)
            except (TypeError, ValueError):
                continue
            if number > 0:
                by_file.setdefault(filename, {})[number] = hits
    return by_file


def added_lines_from_patch(patch):
    added = set()
    new_line = None
    for raw in (patch or "").splitlines():
        if raw.startswith("@@"):
            match = re.search(r"\+(\d+)(?:,(\d+))?", raw)
            new_line = int(match.group(1)) if match else None
            continue
        # The '+++ b/path' header appears before the first hunk, so
        # new_line is None already covers it. An added source line can
        # legitimately start with '++' (e.g. '+++counter;').
        if new_line is None:
            continue
        if raw.startswith("+"):
            added.add(new_line)
            new_line += 1
        elif raw.startswith("-") or raw.startswith("\\"):
            continue
        else:
            new_line += 1
    return added


def load_changed_files(path):
    return _load_changed_files(path) or []


def is_workflow_change(filename):
    filename = normalize_path(filename)
    return any(filename.startswith(prefix) for prefix in WORKFLOW_CHANGE_PREFIXES)


def workflow_changed_files(path):
    """Return workflow-related paths, or None when changed files are unknown."""
    if not path:
        return None
    files = _load_changed_files(path)
    if files is None:
        return None
    changed = set()
    for item in files:
        if not isinstance(item, dict):
            continue
        filename = normalize_path(item.get("filename", ""))
        if is_workflow_change(filename):
            changed.add(filename)
    return sorted(changed)


def coverage_total(path):
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = [ln.strip() for ln in f.read().splitlines() if ln.strip()]
    except Exception:
        return None
    for line in reversed(lines):
        if line.startswith("TOTAL"):
            parts = line.split()
            if len(parts) >= 4 and parts[-1].endswith("%"):
                if len(parts) >= 6:
                    # Branch mode adds total and partial branch counts.
                    return (
                        f"{parts[1]} statements · {parts[2]} missed · "
                        f"{parts[3]} branches · {parts[4]} partial · {parts[-1]} covered"
                    )
                return f"{parts[1]} statements · {parts[2]} missed · {parts[-1]} covered"
            return " ".join(parts)
    return lines[-1] if lines else None


def coverage_stats(path):
    """Parse a coverage TOTAL line into counts used by the shared detail format."""
    total = coverage_total(path)
    if not total:
        return None
    match = re.search(
        r"(\d+) statements · (\d+) missed"
        r"(?: · (\d+) branches · (\d+) partial)?"
        r" · (\d+(?:\.\d+)?)% covered",
        total,
    )
    if not match:
        return None
    return {
        "statements": int(match.group(1)),
        "missed": int(match.group(2)),
        "branches": int(match.group(3) or 0),
        "partial": int(match.group(4) or 0),
        "percent": float(match.group(5)),
    }


def coverage_marker(total):
    match = re.search(r"(\d+(?:\.\d+)?)\s*%", total or "")
    if not match:
        return ""
    percent = float(match.group(1))
    if percent >= 80:
        return ""
    if percent >= 70:
        return "⚠️ Low coverage"
    return "❌ Low coverage"


def python_coverage_detail():
    stats = coverage_stats(workflow_coverage_path)
    if not stats:
        return None
    statements = stats["statements"]
    covered = statements - stats["missed"]
    lines_pct = 100.0 * covered / statements if statements else 0.0
    parts = [f"{covered}/{statements} statements ({lines_pct:.0f}%)"]
    worst = lines_pct
    if stats["branches"]:
        branches = stats["branches"]
        branch_covered = branches - stats["partial"]
        branches_pct = 100.0 * branch_covered / branches
        parts.append(f"{branch_covered}/{branches} branches ({branches_pct:.0f}%)")
        worst = min(worst, branches_pct)
    return {
        "detail": " · ".join(parts),
        "marker": coverage_marker(f"{worst}%"),
    }


def lcov_coverage_total(path, suffix):
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            records = f.read().split("end_of_record")
    except Exception:
        return None

    totals = {"lines": 0, "hit_lines": 0, "branches": 0, "hit_branches": 0}
    for record in records:
        lines = record.splitlines()
        source = next((ln[3:] for ln in lines if ln.startswith("SF:")), "")
        if ".github/scripts/" not in source or not source.endswith(suffix):
            continue

        def value(prefix):
            for ln in lines:
                if ln.startswith(prefix):
                    return int(ln.split(":", 1)[1])
            return 0

        totals["lines"] += value("LF:")
        totals["hit_lines"] += value("LH:")
        totals["branches"] += value("BRF:")
        totals["hit_branches"] += value("BRH:")

    return totals if totals["lines"] else None


def js_coverage_total(path):
    return lcov_coverage_total(path, ".js")


def shell_coverage_total(path):
    return lcov_coverage_total(path, ".sh")


def js_coverage_detail():
    totals = js_coverage_total(js_coverage_path)
    if not totals:
        return None
    lines_pct = 100.0 * totals["hit_lines"] / totals["lines"]
    branches_pct = 100.0 * totals["hit_branches"] / totals["branches"] if totals["branches"] else 0.0
    detail = (
        f"{totals['hit_lines']}/{totals['lines']} lines ({lines_pct:.0f}%) · "
        f"{totals['hit_branches']}/{totals['branches']} branches ({branches_pct:.0f}%)"
    )
    worst = min(lines_pct, branches_pct) if totals["branches"] else lines_pct
    return {"detail": detail, "marker": coverage_marker(f"{worst}%")}


def shell_coverage_detail():
    totals = shell_coverage_total(shell_coverage_path)
    if not totals:
        return None
    lines_pct = 100.0 * totals["hit_lines"] / totals["lines"]
    branches_pct = 100.0 * totals["hit_branches"] / totals["branches"] if totals["branches"] else 0.0
    detail = (
        f"{totals['hit_lines']}/{totals['lines']} lines ({lines_pct:.0f}%) · "
        f"{totals['hit_branches']}/{totals['branches']} inferred branches ({branches_pct:.0f}%)"
    )
    worst = min(lines_pct, branches_pct) if totals["branches"] else lines_pct
    return {"detail": detail, "marker": coverage_marker(f"{worst}%")}


def coverage_summary_line():
    entries = [
        ("Python", python_coverage_detail()),
        ("JavaScript", js_coverage_detail()),
        ("Shell", shell_coverage_detail()),
    ]
    present = [(label, entry) for label, entry in entries if entry]
    if not present:
        return None

    details = [f"{label} `{entry['detail']}`" for label, entry in present]
    # The sort puts ❌ before ⚠️. Show only the most severe marker so mixed
    # severity bands cannot render contradictory labels in the panel heading.
    markers = sorted(
        {entry["marker"] for _, entry in present if entry["marker"]},
        key=lambda marker: 0 if marker.startswith("❌") else 1,
    )
    summary = "Workflow script coverage"
    body = " · ".join(details)
    if markers:
        return f"**{summary} — {markers[0]}:** {body}"
    return f"<small>{summary}: {body}</small>"


def coverage_extras():
    line = coverage_summary_line()
    if not line:
        return []

    workflow_files = workflow_changed_files(changed_json_path)
    # Unknown changed-file data stays expanded: it would be unsafe to hide
    # coverage for a workflow change we cannot rule out. A known list without
    # workflow-definition changes collapses the line so unrelated PRs only
    # see it on demand.
    if workflow_files == []:
        return [
            "",
            "<details><summary id="quality-workflow-script-coverage">Workflow script coverage</summary>",
            "",
            line,
            "",
            "</details>",
        ]
    return ["", line]


def load_test_results():
    try:
        with open(test_results_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = None
    suites = (data or {}).get("suites")
    if not isinstance(suites, list):
        suites = []
    by_key = {}
    for suite in suites:
        if isinstance(suite, dict) and suite.get("key"):
            by_key[suite["key"]] = suite
    result = []
    for key, name in DEFAULT_SUITES:
        suite = by_key.get(key, {})
        result.append({
            "key": key,
            "name": name,
            "outcome": suite.get("outcome", ""),
            "summary": suite.get("summary", "no results"),
            "failures": suite.get("failures") or [],
        })
    global run_url
    if not run_url:
        run_url = (data or {}).get("run_url", "").strip()
    return result


def status_icon(outcome, summary=""):
    if str(summary).strip() == "no results":
        return "⚪"
    if outcome == "success":
        return "✅"
    if outcome in ("failure", "cancelled"):
        return "❌"
    if outcome in ("skipped",):
        return "⏭️"
    return "⚪"


def test_table(suites):
    lines = [
        "| Status | Suite | Tests | Failures |",
        "|:------:|-------|-------|---------:|",
    ]
    for suite in suites:
        lines.append(
            f"| {status_icon(suite['outcome'], suite['summary'])} | {suite['name']} | "
            f"`{str(suite['summary']).replace('|', chr(92) + '|')}` | {len(suite['failures'])} |"
        )
    return lines


def failure_details(suites):
    lines = []
    for suite in suites:
        failures = suite["failures"]
        if not failures:
            continue
        lines += ["", f"<a id=\"quality-failures-{suite['name'].lower().replace(' ','-')}-anchor\"></a>", f"<details><summary id=\"quality-failures-{suite['name'].lower().replace(' ','-')}\">❌ {suite['name']} failures ({len(failures)})</summary>", ""]
        for failure in failures:
            message = str(failure.get("message", "")).replace("\n", " ").strip()
            suffix = f" — {message}" if message else ""
            lines.append(f"- `{failure.get('nodeid', 'unknown test')}`{suffix}")
        lines += ["", "</details>"]
    return lines


def coverage_section():
    out = ["#### Patch coverage", ""]

    if not os.path.exists(coverage_xml_path):
        app_files = application_changed_files(changed_json_path) if changed_json_path else None
        out += ["❌ No application coverage data was generated for this run." if app_files
                else "No application coverage data was generated for this run."]
        if app_files:
            out += ["", "Application files changed in this PR:"]
            out += [f"- `{filename}`" for filename in app_files[:20]]
            remaining = len(app_files) - 20
            if remaining > 0:
                out += [f"- ... and {remaining} more"]
        elif app_files is None:
            out += ["", "Application changes could not be determined from the available data."]
        else:
            out += ["", "No application files changed in this PR."]
        out += coverage_extras()
        return out

    # Trusted path: compute patch coverage from coverage XML + GitHub's file patches.
    # This is used by the privileged workflow_run publisher and needs no git checkout.
    if changed_json_path:
        coverage = coverage_by_file(coverage_xml_path)
        per_file = []
        total = 0
        covered = 0

        for item in load_changed_files(changed_json_path):
            if item.get("status") == "removed":
                continue
            filename = normalize_path(item.get("filename", ""))
            if not is_application_file(filename):
                continue
            added = sorted(added_lines_from_patch(item.get("patch", "")))
            file_cov = coverage.get(filename, {})
            relevant = sorted(n for n in added if n in file_cov)
            if not relevant:
                # A new Python module that was never imported has no entry in
                # the coverage XML at all. Count its added lines as uncovered
                # instead of silently dropping them from the denominator.
                if filename.endswith(".py") and added and filename not in coverage:
                    total += len(added)
                    per_file.append((filename, 0, len(added), added))
                continue
            missing = [n for n in relevant if file_cov[n] <= 0]
            file_total = len(relevant)
            file_covered = file_total - len(missing)
            total += file_total
            covered += file_covered
            per_file.append((filename, file_covered, file_total, missing))

        out += ["", "Diff vs changed files in this PR."]
        if total:
            diff_pct = 100.0 * covered / total
            diff_pct_i = int(round(diff_pct))
            d_color, d_emoji, d_label = pct_to_color(diff_pct)
            out += [
                "",
                f"{badge(diff_pct_i, d_color)} **{diff_pct:.1f}%** ({d_label}) {d_emoji}",
                "",
                f"`{bar(diff_pct_i)}` {diff_pct_i}%",
                "",
                f"**Diff lines:** {total} &nbsp;|&nbsp; **Missing:** {total - covered} &nbsp;|&nbsp; **Covered:** {covered}",
            ]
            details = []
            for filename, file_covered, file_total, missing in sorted(per_file):
                if missing:
                    details.append(f"- `{filename}`: {file_covered}/{file_total} covered; missing lines {', '.join(map(str, missing[:20]))}")
                else:
                    details.append(f"- `{filename}`: {file_covered}/{file_total} covered")
            out += ["", "<details><summary id="quality-patch-coverage-details">Patch coverage details</summary>", ""] + details + ["", "</details>"]
        else:
            out += ["", "_Patch coverage could not be computed for this run._"]
    else:
        # Patch coverage needs the PR's changed-file patches. They are supplied by the
        # workflow for both push runs and the privileged fork-PR publisher, so there is
        # no hardcoded compare branch and no env-derived git subprocess argument.
        out += ["", "_Patch coverage could not be computed for this run._"]

    out += coverage_extras()
    return out


def main():
    suites = load_test_results()
    lines = [marker, C["headings"]["tests"], ""]
    if run_url:
        lines += [f"**Full analysis:** [Workflow run]({run_url})", ""]
    lines += ["Automated test summary for files changed in this PR.", ""]
    lines += test_table(suites)
    lines += failure_details(suites)
    try:
        lines += ["", ""] + coverage_section()
    except CoverageDataError as exc:
        lines += [
            "",
            "",
            "#### Patch coverage",
            "",
            "❌ Coverage XML could not be parsed; patch coverage is unavailable for this run.",
            f"`{coverage_xml_path}`: {exc}",
        ]
        print("\n".join(lines))
        raise SystemExit(1)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
