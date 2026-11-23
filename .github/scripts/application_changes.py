#!/usr/bin/env python3
"""Shared helpers for deciding whether a PR changes application code."""
import json

APP_PREFIXES = ("cps/",)
APP_FILES = {"cps.py"}


def normalize_path(path):
    path = (path or "").replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    marker_prefix = "/calibre-web/"
    if marker_prefix in path:
        # CI workspace paths can contain the repository name twice
        # (/home/runner/work/calibre-web/calibre-web/...). Strip at the last
        # occurrence so the result matches GitHub's changed-file names.
        path = path.rsplit(marker_prefix, 1)[1]
    return path


def load_changed_files(path):
    """Return the trusted changed-file list, or None when it is unavailable."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    if isinstance(data, dict):
        data = data.get("files")
    if not isinstance(data, list):
        return None
    return data


def is_application_file(filename):
    """Return True for application source paths measured by coverage.xml."""
    filename = normalize_path(filename)
    return filename in APP_FILES or filename.startswith(APP_PREFIXES)


def application_changed_files(path):
    """Return sorted application paths, [] for none, or None if unknown."""
    files = load_changed_files(path)
    if files is None:
        return None
    changed = set()
    for item in files:
        if not isinstance(item, dict) or item.get("status") == "removed":
            continue
        filename = normalize_path(item.get("filename", ""))
        if is_application_file(filename):
            changed.add(filename)
    return sorted(changed)
