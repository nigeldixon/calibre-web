#!/usr/bin/env python3
"""Build the desired per-test failure comments from a test-results artifact."""
import hashlib
import json
import os


MARKER_PREFIX = "<!-- test-failure:"


def marker_for(suite_key, nodeid):
    digest = hashlib.sha1(f"{suite_key}:{nodeid}".encode("utf-8")).hexdigest()[:12]
    return f"{MARKER_PREFIX}{digest} -->"


def comment_for(suite, failure, run_url):
    marker = marker_for(suite["key"], failure["nodeid"])
    message = str(failure.get("message", "")).replace("\n", " ").strip()
    lines = [
        marker,
        f"## ❌ Test failure: {suite['name']}",
        "",
        f"`{failure['nodeid']}` failed.",
        "",
        f"- **Outcome:** `{failure.get('kind', 'FAILED')}`",
        f"- **Suite:** `{suite.get('summary', 'no results')}`",
    ]
    if message:
        lines.append(f"- **Message:** {message}")
    if run_url:
        lines += ["", f"[Full workflow run]({run_url})"]
    return {"marker": marker, "body": "\n".join(lines).strip()}


def build_payload(results):
    comments = []
    for suite in results.get("suites", []) or []:
        if not isinstance(suite, dict):
            continue
        for failure in suite.get("failures", []) or []:
            if not isinstance(failure, dict) or not failure.get("nodeid"):
                continue
            comments.append(comment_for(suite, failure, results.get("run_url", "")))
    return {"prefix": MARKER_PREFIX, "comments": comments}


if __name__ == "__main__":
    results_path = os.environ.get("TEST_RESULTS_JSON", "test-results.json").strip() or "test-results.json"
    output_path = os.environ.get("TEST_FAILURE_COMMENTS_JSON", "test-failure-comments.json").strip() or "test-failure-comments.json"
    with open(results_path, "r", encoding="utf-8") as f:
        results = json.load(f)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(build_payload(results), f, indent=2)
