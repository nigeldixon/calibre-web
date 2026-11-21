#!/usr/bin/env python3
"""Build grouped test-failure comments from a test-results artifact."""
import hashlib
import json
import os


MARKER_PREFIX = "<!-- test-failure:"


def marker_for(suite_key):
    digest = hashlib.sha256(f"suite:{suite_key}".encode("utf-8")).hexdigest()[:12]
    return f"{MARKER_PREFIX}{digest} -->"


def legacy_marker_for(suite_key, nodeid):
    """Return the marker used by the old one-comment-per-failure format."""
    digest = hashlib.sha256(f"{suite_key}:{nodeid}".encode("utf-8")).hexdigest()[:12]
    return f"{MARKER_PREFIX}{digest} -->"


def failure_bullet(failure):
    message = str(failure.get("message", "")).replace("\n", " ").strip()
    outcome = str(failure.get("kind", "FAILED"))
    suffix = f" — {message}" if message else ""
    return f"- `{failure['nodeid']}` — `{outcome}`{suffix}"


def comment_for(suite, failures, run_url):
    marker = marker_for(suite["key"])
    lines = [
        marker,
        f"## ❌ Test failures: {suite['name']} ({len(failures)})",
        "",
    ] + [failure_bullet(failure) for failure in failures]
    if run_url:
        lines += ["", f"**Full analysis:** [Workflow run]({run_url})"]
    return {"marker": marker, "body": "\n".join(lines).strip()}


def build_payload(results):
    comments = []
    suites = []
    for suite in results.get("suites", []) or []:
        if not isinstance(suite, dict):
            continue
        failures = [
            failure
            for failure in suite.get("failures", []) or []
            if isinstance(failure, dict) and failure.get("nodeid")
        ]
        suites.append(
            {
                "key": suite.get("key", ""),
                "name": suite.get("name", "Tests"),
                "marker": marker_for(suite.get("key", "")),
                "legacy_markers": [
                    legacy_marker_for(suite.get("key", ""), failure["nodeid"])
                    for failure in failures
                ],
                "failure_count": len(failures),
            }
        )
        if failures:
            comments.append(comment_for(suite, failures, results.get("run_url", "")))
    return {"prefix": MARKER_PREFIX, "suites": suites, "comments": comments}


if __name__ == "__main__":
    results_path = os.environ.get("TEST_RESULTS_JSON", "test-results.json").strip() or "test-results.json"
    output_path = os.environ.get("TEST_FAILURE_COMMENTS_JSON", "test-failure-comments.json").strip() or "test-failure-comments.json"
    with open(results_path, "r", encoding="utf-8") as f:
        results = json.load(f)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(build_payload(results), f, indent=2)
