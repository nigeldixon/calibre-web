#!/usr/bin/env python3
"""Render the PR test panel, including patch coverage."""
import json
import os
import re
from defusedxml import ElementTree as SafeET

marker = "<!-- coverage-report -->"
coverage_xml_path = os.environ.get("COVERAGE_XML_PATH", "coverage.xml").strip() or "coverage.xml"
run_url = os.environ.get("RUN_URL", "").strip()
changed_json_path = os.environ.get("CHANGED_FILES_JSON", "").strip()
workflow_coverage_path = os.environ.get("WORKFLOW_COVERAGE_TERM_PATH", "coverage-workflow-scripts-term.txt").strip()
test_results_path = os.environ.get("TEST_RESULTS_JSON", "test-results.json").strip()

DEFAULT_SUITES = [
    ("workflow_script_tests", "Workflow script tests"),
    ("unit_tests", "Unit (in-process)"),
    ("e2e_tests", "Playwright E2E"),
    ("functional_tests", "HTTP functional"),
]


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


def normalize_path(path):
    path = (path or "").replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    marker_prefix = "/calibre-web/"
    if marker_prefix in path:
        # CI workspace paths can contain the repository name twice
        # (/home/runner/work/calibre-web/calibre-web/...). Strip at the last
        # occurrence so the result matches GitHub's changed-file names.
        path = path.rsplit(marker_prefix, 1)[1]
    return path


def coverage_by_file(path):
    by_file = {}
    if not os.path.exists(path):
        return by_file
    try:
        root = SafeET.parse(path).getroot()
    except Exception:
        return by_file
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
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    if isinstance(data, dict):
        data = data.get("files", [])
    return data if isinstance(data, list) else []


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
                return f"{parts[1]} statements · {parts[2]} missed · {parts[-1]} covered"
            return " ".join(parts)
    return lines[-1] if lines else None


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


def workflow_coverage_line():
    total = coverage_total(workflow_coverage_path)
    if not total:
        return None
    marker = coverage_marker(total)
    if marker:
        return f"**Workflow script coverage:** {marker} `{total}`"
    return f"<small>Workflow script coverage: `{total}`</small>"


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


def status_icon(outcome):
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
            f"| {status_icon(suite['outcome'])} | {suite['name']} | "
            f"`{str(suite['summary']).replace('|', chr(92) + '|')}` | {len(suite['failures'])} |"
        )
    return lines


def failure_details(suites):
    lines = []
    for suite in suites:
        failures = suite["failures"]
        if not failures:
            continue
        lines += ["", f"<details><summary>❌ {suite['name']} failures ({len(failures)})</summary>", ""]
        for failure in failures:
            message = str(failure.get("message", "")).replace("\n", " ").strip()
            suffix = f" — {message}" if message else ""
            lines.append(f"- `{failure.get('nodeid', 'unknown test')}`{suffix}")
        lines += ["", "</details>"]
    return lines


def coverage_section():
    out = ["### Patch coverage", ""]

    if not os.path.exists(coverage_xml_path):
        out += ["No coverage data was generated for this run."]
        extra = workflow_coverage_line()
        if extra:
            out += ["", extra]
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
            if not filename:
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
            out += ["", "<details><summary>Patch coverage details</summary>", ""] + details + ["", "</details>"]
        else:
            out += ["", "_Patch coverage could not be computed for this run._"]
    else:
        # Patch coverage needs the PR's changed-file patches. They are supplied by the
        # workflow for both push runs and the privileged fork-PR publisher, so there is
        # no hardcoded compare branch and no env-derived git subprocess argument.
        out += ["", "_Patch coverage could not be computed for this run._"]

    extra = workflow_coverage_line()
    if extra:
        out += ["", extra]
    return out


def main():
    suites = load_test_results()
    lines = [marker, "## Tests", ""]
    if run_url:
        lines += [f"**Full analysis:** [Workflow run]({run_url})", ""]
    lines += ["Automated test summary for files changed in this PR.", ""]
    lines += test_table(suites)
    lines += failure_details(suites)
    lines += ["", ""] + coverage_section()
    print("\n".join(lines))


if __name__ == "__main__":
    main()
