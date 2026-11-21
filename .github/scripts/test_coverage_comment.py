#!/usr/bin/env python3
import json
import os
import re
from defusedxml import ElementTree as SafeET

marker = "<!-- coverage-report -->"
coverage_xml_path = os.environ.get("COVERAGE_XML_PATH", "coverage.xml").strip() or "coverage.xml"
run_url = os.environ.get("RUN_URL", "").strip()
changed_json_path = os.environ.get("CHANGED_FILES_JSON", "").strip()


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
        path = path.split(marker_prefix, 1)[1]
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


def header():
    out = [marker, "## Patch coverage", ""]
    if run_url:
        out += [f"**Full analysis:** [Workflow run]({run_url})", ""]
    return out


if not os.path.exists(coverage_xml_path):
    print("\n".join(header() + ["No coverage data was generated for this run."]))
    raise SystemExit(0)

lines_out = header()

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
        file_cov = coverage.get(filename, {})
        relevant = sorted(n for n in added_lines_from_patch(item.get("patch", "")) if n in file_cov)
        if not relevant:
            continue
        missing = [n for n in relevant if file_cov[n] <= 0]
        file_total = len(relevant)
        file_covered = file_total - len(missing)
        total += file_total
        covered += file_covered
        per_file.append((filename, file_covered, file_total, missing))

    lines_out += ["", "Diff vs changed files in this PR."]
    if total:
        diff_pct = 100.0 * covered / total
        diff_pct_i = int(round(diff_pct))
        d_color, d_emoji, d_label = pct_to_color(diff_pct)
        lines_out += [
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
        if details:
            lines_out += ["", "<details><summary>Patch coverage details</summary>", ""] + details + ["", "</details>"]
    else:
        lines_out += ["", "_Patch coverage could not be computed for this run._"]

    print("\n".join(lines_out))
    raise SystemExit(0)

# Patch coverage needs the PR's changed-file patches. They are supplied by the
# workflow for both push runs and the privileged fork-PR publisher, so there is
# no hardcoded compare branch and no env-derived git subprocess argument.
lines_out += ["", "_Patch coverage could not be computed for this run._"]
print("\n".join(lines_out))
