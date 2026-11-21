#!/usr/bin/env python3
"""Collect test suite outcomes into a structured artifact for PR reports."""
import json
import os
import re

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
FAILURE_RE = re.compile(r"^(FAILED|ERROR)\s+(\S+)\s*(?:-\s*(.*))?$")
SUMMARY_WORDS = re.compile(
    r"\b(passed|failed|error|errors|skipped|xfailed|xpassed|deselected|warnings?)\b"
)
SUMMARY_TAIL = re.compile(r"\b(in|no tests ran)\b")


SUITES = [
    ("workflow_script_tests", "Workflow script tests", "WORKFLOW_SCRIPT_OUTCOME", "workflow-script-test-output.txt"),
    ("unit_tests", "Unit (in-process)", "UNIT_OUTCOME", "unit-test-output.txt"),
    ("e2e_tests", "Playwright E2E", "E2E_OUTCOME", "e2e-test-output.txt"),
    ("functional_tests", "HTTP functional", "FUNCTIONAL_OUTCOME", "functional-test-output.txt"),
]


def read_lines(path):
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return [ANSI_RE.sub("", line).rstrip() for line in f]
    except OSError:
        return []


def summary_of(lines):
    stripped = [line.strip() for line in lines if line.strip()]
    for line in reversed(stripped):
        if SUMMARY_WORDS.search(line) and SUMMARY_TAIL.search(line):
            return line
        if line.startswith("no tests ran"):
            return line
    for line in reversed(stripped):
        if SUMMARY_WORDS.search(line):
            return line
    return "no results"


def failures_of(lines, outcome):
    if outcome not in ("failure", "cancelled"):
        return []
    failures = []
    for line in lines:
        match = FAILURE_RE.match(line.strip())
        if not match:
            continue
        kind, nodeid, message = match.groups()
        failures.append({
            "kind": kind,
            "nodeid": nodeid,
            "message": (message or "").strip(),
        })
    return failures


def collect():
    suites = []
    for key, name, outcome_env, default_output in SUITES:
        output = os.environ.get(f"{key.upper()}_OUTPUT", "").strip() or default_output
        lines = read_lines(output)
        outcome = os.environ.get(outcome_env, "").strip()
        suites.append({
            "key": key,
            "name": name,
            "outcome": outcome,
            "summary": summary_of(lines),
            "failures": failures_of(lines, outcome),
        })
    return {
        "run_url": os.environ.get("RUN_URL", "").strip(),
        "suites": suites,
    }


if __name__ == "__main__":
    output_path = os.environ.get("TEST_RESULTS_JSON", "test-results.json").strip() or "test-results.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(collect(), f, indent=2)
