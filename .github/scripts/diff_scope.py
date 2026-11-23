#!/usr/bin/env python3
"""Helpers for scoping SARIF results to changed PR diff lines.

The quality PR comments should focus on findings introduced or touched by a PR,
not every pre-existing finding in a changed file. When a PR diff is available,
a result is in scope if any relevant SARIF location intersects a changed hunk.
If the diff cannot be determined, callers should fall back conservatively to
file-level or unfiltered reporting.
"""

from __future__ import annotations

import re
import subprocess

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def normalize_diff_path(path):
    if not path:
        return ""
    path = path.strip().replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    if path.startswith("a/") or path.startswith("b/"):
        path = path[2:]
    return path


def parse_unified_zero_diff(diff_text):
    """Parse `git diff --unified=0` output into {path: [(start, end), ...]}.

    Ranges are inclusive 1-based line numbers on the new-file side. Deletion-only
    hunks (count == 0) are ignored because they have no new lines to match.
    """
    ranges = {}
    current = None
    for line in (diff_text or "").splitlines():
        if line.startswith("+++ "):
            raw = line[4:].strip()
            if raw == "/dev/null":
                current = None
            else:
                current = normalize_diff_path(raw)
                ranges.setdefault(current, [])
            continue
        if line.startswith("@@ "):
            if not current:
                continue
            match = _HUNK_RE.match(line)
            if not match:
                continue
            start = int(match.group(1))
            count = int(match.group(2) if match.group(2) is not None else "1")
            if count <= 0:
                continue
            ranges[current].append((start, start + count - 1))
    return {path: vals for path, vals in ranges.items() if vals}


def collect_diff_ranges(base_sha, head_sha, base_ref="", fetch=True):
    """Return changed-line ranges for base...head, or None if unavailable.

    None means the diff could not be determined and callers must fall back
    conservatively. An empty dict means the diff was determined but had no
    changed line ranges.
    """
    if not base_sha or not head_sha:
        return None
    try:
        if fetch and base_ref:
            subprocess.run(
                ["git", "fetch", "origin", base_ref, "--depth=1"],
                check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
        if fetch:
            for sha in (base_sha, head_sha):
                subprocess.run(
                    ["git", "fetch", "origin", sha, "--depth=1"],
                    check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                )
        for args in (
            ["git", "diff", "--unified=0", f"{base_sha}...{head_sha}"],
            ["git", "diff", "--unified=0", base_sha, head_sha],
        ):
            proc = subprocess.run(
                args, check=False,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            if proc.returncode == 0:
                return parse_unified_zero_diff(proc.stdout or "")
    except Exception:
        return None
    return None


def _location_path_and_line(loc, normalize_uri):
    if not isinstance(loc, dict):
        return "", None, None
    phys = loc.get("physicalLocation")
    if not isinstance(phys, dict):
        return "", None, None
    art = phys.get("artifactLocation")
    if not isinstance(art, dict):
        art = {}
    uri = normalize_uri(art.get("uri", ""))
    region = phys.get("region")
    if not isinstance(region, dict):
        region = {}
    start = region.get("startLine")
    end = region.get("endLine")
    if not isinstance(start, int):
        start = None
    if not isinstance(end, int):
        end = start
    if start is not None and end is not None and end < start:
        end = start
    return uri, start, end


def _iter_result_locations(result):
    if not isinstance(result, dict):
        return
    for key in ("locations", "relatedLocations"):
        for loc in result.get(key) or []:
            yield loc
    for flow in result.get("codeFlows") or []:
        if not isinstance(flow, dict):
            continue
        for thread in flow.get("threadFlows") or []:
            if not isinstance(thread, dict):
                continue
            for tloc in thread.get("locations") or []:
                if not isinstance(tloc, dict):
                    continue
                loc = tloc.get("location")
                if isinstance(loc, dict):
                    yield loc


def _line_intersects(start, end, ranges):
    for r_start, r_end in ranges:
        if start <= r_end and end >= r_start:
            return True
        if r_start > end:
            break
    return False


def result_intersects_changed_lines(result, changed_files, changed_ranges, normalize_uri):
    """True when a SARIF result should be reported for the PR.

    changed_ranges is a dict from normalized path to inclusive line ranges, or
    None when diff lines could not be determined. When ranges are unavailable,
    fall back conservatively to changed-file membership. When a location is in a
    changed file but has no usable line information, include it conservatively.
    """
    if changed_files is None and changed_ranges is None:
        return True

    saw_changed_file = False
    for loc in _iter_result_locations(result):
        uri, start, end = _location_path_and_line(loc, normalize_uri)
        if not uri:
            continue
        in_changed_file = changed_files is None or uri in changed_files
        if not in_changed_file:
            continue
        saw_changed_file = True
        if changed_ranges is None:
            return True
        file_ranges = changed_ranges.get(uri)
        if not file_ranges:
            # The file was reported as changed, but the diff had no line ranges
            # for it; be conservative and keep file-level behavior.
            return True
        if start is None:
            return True
        if _line_intersects(start, end, file_ranges):
            return True

    if changed_ranges is None:
        return saw_changed_file if changed_files is not None else True
    return False
