#!/usr/bin/env python3
import glob
import json
import os
import subprocess

marker = "<!-- codeql-report -->"
sarif_files = sorted(glob.glob("codeql-results/**/*.sarif", recursive=True))
status_files = sorted(glob.glob("codeql-results/**/status.txt", recursive=True))
analyze_outcomes = os.environ.get("ANALYZE_RESULT", "")
expected_langs = ["javascript-typescript", "python"]


def lang_from_path(path):
    for part in path.replace("\\", "/").split("/"):
        if part.startswith("codeql-sarif-"):
            return part[len("codeql-sarif-"):]
    return None


lang_outcome = {}
for status_file in status_files:
    lang = lang_from_path(status_file)
    if not lang:
        continue
    try:
        with open(status_file, "r", encoding="utf-8") as f:
            lang_outcome[lang] = f.read().strip()
    except Exception:
        lang_outcome[lang] = "unknown"

base_sha = os.environ.get("PR_BASE_SHA", "").strip()
head_sha = os.environ.get("PR_HEAD_SHA", "").strip()
base_ref = os.environ.get("PR_BASE_REF", "").strip()
changed_files_file = os.environ.get("CHANGED_FILES_FILE", "").strip()
changed_files = None
scope_note = "Automated CodeQL summary for this PR (generated from analysis SARIF)."

if changed_files_file:
    try:
        with open(changed_files_file, "r", encoding="utf-8") as f:
            changed_files = set()
            for line in f:
                line = line.strip().replace("\\", "/")
                while line.startswith("./"):
                    line = line[2:]
                if line:
                    changed_files.add(line)
        scope_note = "Automated CodeQL summary for files changed in this PR (generated from analysis SARIF)."
    except Exception:
        changed_files = None
elif base_sha and head_sha:
    try:
        if base_ref:
            subprocess.run(["git", "fetch", "origin", base_ref, "--depth=1"], check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        subprocess.run(["git", "fetch", "origin", base_sha, "--depth=1"], check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        subprocess.run(["git", "fetch", "origin", head_sha, "--depth=1"], check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        diff = subprocess.run(
            ["git", "diff", "--name-only", f"{base_sha}...{head_sha}"],
            check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        if diff.returncode == 0:
            changed_files = set()
            for line in (diff.stdout or "").splitlines():
                line = line.strip().replace("\\", "/")
                while line.startswith("./"):
                    line = line[2:]
                if line:
                    changed_files.add(line)
            scope_note = "Automated CodeQL summary for files changed in this PR (filtered from analysis SARIF)."
        else:
            diff2 = subprocess.run(
                ["git", "diff", "--name-only", base_sha, head_sha],
                check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            if diff2.returncode == 0:
                changed_files = set()
                for line in (diff2.stdout or "").splitlines():
                    line = line.strip().replace("\\", "/")
                    if line:
                        changed_files.add(line)
                scope_note = "Automated CodeQL summary for files changed in this PR (filtered from analysis SARIF)."
    except Exception:
        changed_files = None


def normalize_uri(uri):
    if not uri:
        return ""
    uri = uri.replace("\\", "/")
    while uri.startswith("./"):
        uri = uri[2:]
    return uri


def notification_detail(n):
    uri = ''
    for loc in (n.get('locations') or []):
        found = normalize_uri((loc.get('physicalLocation') or {}).get('artifactLocation', {}).get('uri', ''))
        if found:
            uri = found
            break
    label = (n.get('descriptor') or {}).get('id') or (n.get('level') or 'analysis error')
    text = ' '.join(((n.get('message') or {}).get('text') or '').split())[:160]
    prefix = f'`{uri}` — ' if uri else ''
    return f'- {prefix}{label}: {text}'


def result_in_changed_files(result, changed):
    if changed is None:
        return True
    for loc in (result.get("locations") or []):
        phys = (loc or {}).get("physicalLocation") or {}
        art = phys.get("artifactLocation") or {}
        uri = normalize_uri(art.get("uri", ""))
        if uri in changed:
            return True
    for loc in (result.get("relatedLocations") or []):
        phys = (loc or {}).get("physicalLocation") or {}
        art = phys.get("artifactLocation") or {}
        uri = normalize_uri(art.get("uri", ""))
        if uri in changed:
            return True
    return False


def empty_info(read_error=False):
    return {
        "results": 0, "error": 0, "warning": 0, "note": 0,
        "parse_error": True, "read_error": read_error,
        "exec_errors": 0, "exec_warnings": 0,
    }


by_lang = {}
total_all = 0
analysis_warnings = 0
error_details = []
warning_details = []


def record_error(detail):
    # Keep the reported count and the expandable detail list in sync. A count
    # without a matching line made the panel claim more errors than it showed.
    error_details.append(detail if detail.startswith("- ") else f"- {detail}")


if not sarif_files:
    record_error("No CodeQL SARIF artifacts were produced for this run.")
    for lang in expected_langs:
        by_lang[lang] = empty_info()

for path in sarif_files:
    lang = lang_from_path(path) or "unknown"
    try:
        with open(path, "r", encoding="utf-8") as f:
            sarif = json.load(f)
    except Exception:
        by_lang[lang] = empty_info(read_error=True)
        record_error(f"`{path}`: SARIF could not be parsed.")
        continue

    info = {
        "results": 0, "error": 0, "warning": 0, "note": 0,
        "parse_error": False, "read_error": False,
        "exec_errors": 0, "exec_warnings": 0,
    }
    for run in sarif.get("runs", []) or []:
        total_all += len(run.get("results", []) or [])
        for inv in run.get("invocations") or []:
            for n in inv.get("toolExecutionNotifications") or []:
                level = (n.get("level") or "").lower()
                if level == "error":
                    info["exec_errors"] += 1
                    record_error(notification_detail(n))
                elif level == "warning":
                    info["exec_warnings"] += 1
                    analysis_warnings += 1
                    warning_details.append(notification_detail(n))
        for r in run.get("results", []) or []:
            if not result_in_changed_files(r, changed_files):
                continue
            info["results"] += 1
            level = (r.get("level") or "warning").lower()
            if level in ("error",):
                info["error"] += 1
            elif level in ("warning",):
                info["warning"] += 1
            elif level in ("note", "none"):
                info["note"] += 1
            else:
                info["warning"] += 1
    by_lang[lang] = info

if sarif_files:
    for lang in expected_langs:
        if lang not in by_lang:
            by_lang[lang] = empty_info()
            # In a partial run this expected language produced no SARIF. Record
            # it here, because the failed-language loop below treats the
            # parse_error flag as an already-recorded failure.
            record_error(f"No SARIF artifact was produced for `{lang}`.")

failed_langs = [lang for lang, outcome in lang_outcome.items() if outcome not in ("", "success")]
job_failure_recorded = False
for lang in failed_langs:
    # A failed language already represents the job failure, whether or not it
    # added its own detail line (SARIF errors may have done that already).
    job_failure_recorded = True
    info = by_lang.get(lang)
    # A missing, unreadable, or error-notification-bearing SARIF file already
    # contributed an analysis error; only record otherwise-uncounted failures.
    if info is None:
        record_error(f"CodeQL analyze failed for `{lang}` and no SARIF was available.")
    elif not (info.get("parse_error") or info.get("read_error") or info.get("exec_errors")):
        record_error(f"CodeQL analyze failed for `{lang}`.")
# The aggregate job result is independent of the per-language status files:
# a job can fail while every language status says success. Record it unless a
# language-specific failure has already been recorded.
job_failed = analyze_outcomes not in ("", "success")
if job_failed and not job_failure_recorded:
    record_error(f"CodeQL analyze job reported `{analyze_outcomes}`.")

analysis_errors = len(error_details)


def status_icon(count):
    return "✅" if count == 0 else "⚠️"


analysis_url = os.environ.get("ANALYSIS_URL", "").strip()
run_url = os.environ.get("RUN_URL", "").strip()
full_analysis_links = []
if analysis_url:
    full_analysis_links.append(f"[All CodeQL alerts]({analysis_url})")
if run_url:
    full_analysis_links.append(f"[Workflow run]({run_url})")
full_analysis = "**Full analysis:** " + " · ".join(full_analysis_links) if full_analysis_links else ""

total = sum(v.get("results", 0) for v in by_lang.values() if not v.get("parse_error"))

lines = [
    marker,
    "## CodeQL analysis",
    "",
    scope_note,
    "",
]
if full_analysis:
    lines += [full_analysis, ""]

lines += [
    "| Status | Language | Results | Error | Warning | Note |",
    "|:------:|----------|--------:|------:|--------:|-----:|",
]

for lang in sorted(by_lang):
    info = by_lang[lang]
    if info.get("parse_error"):
        lines.append(f"| ⚪ | `{lang}` | - | - | - | - |")
    else:
        icon = status_icon(info["results"])
        lines.append(
            f"| {icon} | `{lang}` | {info['results']} | {info['error']} | {info['warning']} | {info['note']} |"
        )

lines += [
    "",
    f"**Whole-solution issues:** {total_all} · **In changed files:** {total}",
    f"**Analysis errors:** {analysis_errors} · **Analysis warnings:** {analysis_warnings}",
    "",
    f"**Analyze job:** `{analyze_outcomes}`",
]

if error_details:
    lines += ["", f'<details><summary>❌ Analysis errors ({analysis_errors})</summary>', ""]
    lines += error_details
    lines += ["", "</details>"]

if warning_details:
    lines += ["", f'<details><summary>⚠️ Analysis warnings ({analysis_warnings})</summary>', ""]
    lines += warning_details
    lines += ["", "</details>"]

if any(v.get("read_error") for v in by_lang.values()):
    lines += ["", "❌ A CodeQL SARIF file could not be parsed; the analysis is marked as failed."]

if failed_langs:
    lines += ["", "❌ CodeQL analyze failed for: " + ", ".join(f"`{l}`" for l in failed_langs) + "."]
elif job_failed:
    lines += ["", f"❌ CodeQL analyze job reported `{analyze_outcomes}`."]

if changed_files is not None:
    lines += [
        "",
        f"_Scope: {len(changed_files)} changed file(s) in this PR (`{base_sha[:7]}...{head_sha[:7]}`)._",
    ]
elif base_sha and head_sha:
    lines += [
        "",
        "_Scope: could not determine PR diff; showing all SARIF results._",
    ]

if not sarif_files:
    lines += [
        "",
        "_No SARIF artifacts were found for this run; results may be unavailable (analysis may have failed or not produced output)._",
    ]

print("\n".join(lines))
