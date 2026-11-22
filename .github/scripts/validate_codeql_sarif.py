#!/usr/bin/env python3
"""Validate CodeQL SARIF before it is uploaded to code scanning."""

import glob
import json
import sys


def validate(language, results_dir="codeql-results"):
    """Check every SARIF file for one language; raise on invalid or failed scans."""
    files = sorted(glob.glob(f"{results_dir}/{language}/*.sarif"))
    if not files:
        raise ValueError(f"No CodeQL SARIF was produced for {language}")

    for path in files:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                sarif = json.load(handle)
        except Exception as exc:
            raise ValueError(f"Invalid SARIF {path}: {exc}") from exc

        runs = sarif.get("runs") if isinstance(sarif, dict) else None
        if not isinstance(runs, list) or not runs:
            raise ValueError(f"Invalid SARIF {path}: no runs")
        for run in runs:
            if (not isinstance(run, dict)
                    or not isinstance(run.get("tool"), dict)
                    or not run["tool"]):
                raise ValueError(f"Invalid SARIF {path}: missing tool")
            results = run.get("results", [])
            if not isinstance(results, list):
                raise ValueError(f"Invalid SARIF {path}: results is not a list")
            invocations = run.get("invocations", []) or []
            if not isinstance(invocations, list):
                raise ValueError(f"Invalid SARIF {path}: invocations is not a list")
            for invocation in invocations:
                if not isinstance(invocation, dict):
                    raise ValueError(f"Invalid SARIF {path}: invalid invocation")
                if invocation.get("executionSuccessful") is False:
                    raise ValueError(
                        f"CodeQL scan failed for {language}: executionSuccessful=false"
                    )
                notifications = invocation.get("toolExecutionNotifications", []) or []
                if not isinstance(notifications, list):
                    raise ValueError(f"Invalid SARIF {path}: notifications is not a list")
                for notification in notifications:
                    if not isinstance(notification, dict):
                        raise ValueError(f"Invalid SARIF {path}: invalid notification")
                    if str(notification.get("level", "")).lower() == "error":
                        raise ValueError(
                            f"CodeQL scan failed for {language}: "
                            f"{notification.get('message', {}).get('text', 'error notification')}"
                        )
    return len(files)


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) not in (1, 2):
        print("Usage: validate_codeql_sarif.py <language> [results-dir]", file=sys.stderr)
        return 2
    language = args[0]
    results_dir = args[1] if len(args) == 2 else "codeql-results"
    try:
        count = validate(language, results_dir)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Validated {count} CodeQL SARIF file(s) for {language}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
