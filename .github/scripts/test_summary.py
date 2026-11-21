#!/usr/bin/env python3
import os
import re
import subprocess

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
    for line in reversed([ln.strip() for ln in content.splitlines() if ln.strip()]):
        if re.search(r"\b(passed|failed|error|errors|skipped|xfailed|xpassed|deselected|warnings?)\b", line) and re.search(r"\b(in|no tests ran)\b", line):
            return line
        if line.startswith("no tests ran"):
            return line
    for line in reversed([ln.strip() for ln in content.splitlines() if ln.strip()]):
        if re.search(r"\b(passed|failed|error|errors|skipped)\b", line):
            return line
    return "no results"

suites = [
    ("Workflow script tests", os.environ.get("WORKFLOW_SCRIPT_OUTCOME", ""), "workflow-script-test-output.txt"),
    ("Unit (in-process)", os.environ.get("UNIT_OUTCOME", ""), "unit-test-output.txt"),
    ("Playwright E2E", os.environ.get("E2E_OUTCOME", ""), "e2e-test-output.txt"),
    ("HTTP functional", os.environ.get("FUNCTIONAL_OUTCOME", ""), "functional-test-output.txt"),
]
print("## Test results")
print()
print("| Status | Suite | Tests (pass/fail summary) |")
print("|:------:|-------|----------------------------|")
for name, outcome, path in suites:
    print(f"| {status_icon(outcome)} | {name} | `{extract_summary(path).replace('|', '\\|')}` |")
print()
print("## Test coverage (combined)")
print()
print("Combined coverage from in-process unit tests and server-side functional/E2E runs when coverage data is available.")
print()
print("```text")
if os.path.exists("coverage-term.txt"):
    try:
        with open("coverage-term.txt", "r", encoding="utf-8", errors="replace") as f:
            txt = f.read().rstrip()
        print(txt if txt else "No coverage data generated.")
    except Exception:
        print("No coverage data generated.")
elif os.path.exists(".coverage"):
    try:
        r = subprocess.run(
            ["python", "-m", "coverage", "report", "-m", "--include=*/cps/*", "--omit=*/tests/*"],
            capture_output=True,
            text=True,
        )
        print((r.stdout or r.stderr or "No coverage data generated.").rstrip())
    except Exception:
        print("No coverage data generated.")
else:
    print("No coverage data generated.")
print("```")
