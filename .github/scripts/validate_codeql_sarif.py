#!/usr/bin/env python3
"""Validate CodeQL SARIF before it is uploaded to code scanning."""

import glob
import sys

from sarif_validation import load_sarif, scan_failure, valid_document


def validate(language, results_dir="codeql-results"):
    """Check every SARIF file for one language; raise on invalid or failed scans."""
    files = sorted(glob.glob(f"{results_dir}/{language}/*.sarif"))
    if not files:
        raise ValueError(f"No CodeQL SARIF was produced for {language}")

    for path in files:
        sarif = load_sarif(path)
        if not valid_document(sarif):
            raise ValueError(f"Invalid SARIF {path}: invalid SARIF structure")
        failure = scan_failure(sarif)
        if failure:
            raise ValueError(f"CodeQL scan failed for {language}: {failure}")
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
