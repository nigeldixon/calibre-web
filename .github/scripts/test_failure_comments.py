#!/usr/bin/env python3
"""Build grouped test-failure comments from a test-results artifact."""
import hashlib
import json
import os


MARKER_PREFIX = "<!-- test-failure:"
UNPARSED_FAILURE = {
    "nodeid": "suite outcome",
    "kind": "FAILURE",
    "message": "Suite failed without reported test failures",
}


def marker_for(suite_key):
    digest = hashlib.sha256(f"suite:{suite_key}".encode("utf-8")).hexdigest()[:12]
    return f"{MARKER_PREFIX}{digest} -->"


def failure_marker_for(suite_key, failures):
    """Identify a specific failure set so a new issue gets a new comment."""
    parts = [str(suite_key)]
    for failure in sorted(
        failures,
        key=lambda f: (
            str(f.get("nodeid", "")),
            str(f.get("kind", "FAILED")),
            str(f.get("message", "")),
        ),
    ):
        parts.extend(
            [
                str(failure.get("nodeid", "")),
                str(failure.get("kind", "FAILED")),
                " ".join(str(failure.get("message", "")).split()),
            ]
        )
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:12]
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
    marker = failure_marker_for(suite["key"], failures)
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
        outcome = str(suite.get("outcome", "")).strip().lower()
        unparsed_failure = outcome in ("failure", "cancelled") and not failures
        marker = (
            failure_marker_for(suite.get("key", ""), failures)
            if failures
            else failure_marker_for(suite.get("key", ""), [UNPARSED_FAILURE])
            if unparsed_failure
            else marker_for(suite.get("key", ""))
        )
        suites.append(
            {
                "key": suite.get("key", ""),
                "name": suite.get("name", "Tests"),
                "outcome": outcome,
                "marker": marker,
                "legacy_markers": [
                    legacy_marker_for(suite.get("key", ""), failure["nodeid"])
                    for failure in failures
                ],
                "unparsed_failure": unparsed_failure,
                "failure_count": len(failures) or (1 if unparsed_failure else 0),
            }
        )
        if failures:
            comments.append(comment_for(suite, failures, results.get("run_url", "")))
        elif unparsed_failure:
            comments.append(comment_for(suite, [UNPARSED_FAILURE], results.get("run_url", "")))
    return {"prefix": MARKER_PREFIX, "suites": suites, "comments": comments}


if __name__ == "__main__":
    results_path = os.environ.get("TEST_RESULTS_JSON", "test-results.json").strip() or "test-results.json"
    output_path = os.environ.get("TEST_FAILURE_COMMENTS_JSON", "test-failure-comments.json").strip() or "test-failure-comments.json"
    with open(results_path, "r", encoding="utf-8") as f:
        results = json.load(f)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(build_payload(results), f, indent=2)
