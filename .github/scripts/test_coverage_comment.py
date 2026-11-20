#!/usr/bin/env python3
import os
import re
import subprocess
import xml.etree.ElementTree as ET

marker = "<!-- coverage-report -->"
xml_path = "coverage.xml"
base_ref = os.environ.get("BASE_REF", "develop").strip() or "develop"

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

def status_icon(outcome):
    if outcome == "success":
        return "✅"
    if outcome == "failure":
        return "❌"
    if outcome in ("skipped", "cancelled"):
        return "⏭️"
    return "⚪"

def extract_summary(path):
    if not os.path.exists(path):
        return "no results"
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception:
        return "no results"
    # Prefer the standard pytest short summary line near the end.
    for line in reversed([ln.strip() for ln in content.splitlines() if ln.strip()]):
        if re.search(r"\b(passed|failed|error|errors|skipped|xfailed|xpassed|deselected|warnings?)\b", line) and re.search(r"\b(in|no tests ran)\b", line):
            return line
        if line.startswith("no tests ran"):
            return line
    for line in reversed([ln.strip() for ln in content.splitlines() if ln.strip()]):
        if re.search(r"\b(passed|failed|error|errors|skipped)\b", line):
            return line
    return "no results"

lines_out = [marker, "## Patch coverage"]

if not os.path.exists(xml_path):
    lines_out += ["", "No coverage data was generated for this run."]
    print("\n".join(lines_out))
    raise SystemExit(0)

# Diff/patch coverage via diff-cover
diff_md_path = "diff-coverage.md"
compare_branch = f"origin/{base_ref}"
diff_pct = None
diff_pct_i = None
diff_total = None
diff_missing = None
diff_md = ""
try:
    candidates = [["diff-cover"], ["python3", "-m", "diff_cover.diff_cover_tool"], ["python", "-m", "diff_cover.diff_cover_tool"]]
    diff_base = None
    for cand in candidates:
        try:
            probe = subprocess.run(cand + ["--help"], capture_output=True, text=True)
            if probe.returncode == 0:
                diff_base = cand
                break
        except Exception:
            pass
    if diff_base is None:
        raise RuntimeError("diff-cover command not available")

    cmd = diff_base + [xml_path, "--compare-branch", compare_branch, "--format", f"markdown:{diff_md_path}"]
    subprocess.run(cmd, capture_output=True, text=True)
    # console summary for parsing
    summary_cmd = diff_base + [xml_path, "--compare-branch", compare_branch]
    res2 = subprocess.run(summary_cmd, capture_output=True, text=True)
    out = (res2.stdout or "") + "\n" + (res2.stderr or "")
    m_cov = re.search(r"Coverage:\s*([0-9]+(?:\.[0-9]+)?)%", out)
    m_total = re.search(r"Total:\s*([0-9]+)\s*lines", out)
    m_miss = re.search(r"Missing:\s*([0-9]+)\s*lines", out)
    if m_cov:
        diff_pct = float(m_cov.group(1))
        diff_pct_i = int(round(diff_pct))
    if m_total:
        diff_total = int(m_total.group(1))
    if m_miss:
        diff_missing = int(m_miss.group(1))
    if os.path.exists(diff_md_path):
        with open(diff_md_path, "r", encoding="utf-8") as f:
            diff_md = f.read().strip()
except Exception:
    diff_md = ""
    diff_pct = None

lines_out += [
    "",
    f"Diff vs `{compare_branch}` (changed/diff lines present in the combined coverage report).",
]
if diff_pct is not None:
    d_color, d_emoji, d_label = pct_to_color(diff_pct)
    lines_out += [
        "",
        f"{badge(diff_pct_i, d_color)} **{diff_pct}%** ({d_label}) {d_emoji}",
        "",
        f"`{bar(diff_pct_i)}` {diff_pct_i}%",
    ]
    if diff_total is not None and diff_missing is not None:
        lines_out.append("")
        lines_out.append(f"**Diff lines:** {diff_total} &nbsp;|&nbsp; **Missing:** {diff_missing} &nbsp;|&nbsp; **Covered:** {max(0, diff_total - diff_missing)}")
else:
    lines_out += [
        "",
        "_Patch coverage could not be computed for this run._",
    ]

if diff_md:
    lines_out += [
        "",
        "<details><summary>Diff coverage details</summary>",
        "",
        diff_md,
        "",
        "</details>",
    ]
print("\n".join(lines_out))
