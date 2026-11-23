#!/usr/bin/env python3
"""Fail when application code changed without application coverage data."""
import os

from application_changes import application_changed_files

changed_json_path = os.environ.get("CHANGED_FILES_JSON", "changed-files.json").strip() or "changed-files.json"
coverage_xml_path = os.environ.get("COVERAGE_XML_PATH", "coverage.xml").strip() or "coverage.xml"


def main():
    if os.path.exists(coverage_xml_path):
        print("Application coverage data is available.")
        return 0

    files = application_changed_files(changed_json_path)
    if files is None:
        print("❌ No application coverage data and the changed-file list is unavailable.")
        return 1
    if not files:
        print("No application coverage data; no application files changed.")
        return 0

    print("❌ Application files changed but no application coverage data was generated:")
    for filename in files[:20]:
        print(f"- {filename}")
    remaining = len(files) - 20
    if remaining > 0:
        print(f"... and {remaining} more")
    print("Add application tests (or regenerate coverage.xml) before merging.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
