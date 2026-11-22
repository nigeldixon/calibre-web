#!/usr/bin/env python3
"""Structural SARIF checks shared by the workflow scripts.

These helpers only inspect shape; each caller keeps its own policy for what to
do with invalid files, findings, and execution notifications.
"""

import json


def load_sarif(path):
    """Parse a SARIF file, raising ValueError when it is unreadable or malformed."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception as exc:
        raise ValueError(f"Invalid SARIF {path}: {exc}") from exc


def valid_message(message):
    if message is None:
        return True
    if not isinstance(message, dict):
        return False
    text = message.get("text")
    return text is None or isinstance(text, str)


def valid_artifact_location(art):
    if art is None:
        return True
    if not isinstance(art, dict):
        return False
    uri = art.get("uri", "")
    return uri is None or isinstance(uri, str)


def valid_location(loc):
    if not isinstance(loc, dict):
        return False
    phys = loc.get("physicalLocation")
    if phys is not None and not isinstance(phys, dict):
        return False
    return valid_artifact_location((phys or {}).get("artifactLocation"))


def valid_result_shape(result):
    if not isinstance(result, dict):
        return False
    level = result.get("level")
    if level is not None and not isinstance(level, str):
        return False
    if not valid_message(result.get("message")):
        return False
    for key in ("locations", "relatedLocations"):
        locs = result.get(key)
        if locs is not None and (not isinstance(locs, list) or not all(
                valid_location(loc) for loc in locs)):
            return False
    return True


def valid_notification_shape(notification, strict_descriptor_id=True):
    """Check a notification, optionally requiring string descriptor IDs.

    Semgrep's own reporting treats non-string IDs as malformed, while CodeQL
    emits notification IDs that are not always strings. Callers choose the
    policy instead of sharing one stricter rule.
    """
    if not isinstance(notification, dict):
        return False
    level = notification.get("level")
    if level is not None and not isinstance(level, str):
        return False
    if not valid_message(notification.get("message")):
        return False
    descriptor = notification.get("descriptor")
    if descriptor is not None:
        if not isinstance(descriptor, dict):
            return False
        descriptor_id = descriptor.get("id")
        if (strict_descriptor_id and descriptor_id is not None
                and not isinstance(descriptor_id, str)):
            return False
    locs = notification.get("locations")
    return locs is None or (isinstance(locs, list) and all(
        valid_location(loc) for loc in locs))


def valid_run_shape(run, strict_descriptor_id=True):
    if (not isinstance(run, dict)
            or not isinstance(run.get("tool"), dict)
            or not run["tool"]
            or not isinstance(run.get("results", []), list)):
        return False
    if not all(valid_result_shape(result) for result in run.get("results", []) or []):
        return False
    invocations = run.get("invocations", []) or []
    if not isinstance(invocations, list):
        return False
    for invocation in invocations:
        if not isinstance(invocation, dict):
            return False
        notifications = invocation.get("toolExecutionNotifications", []) or []
        if (not isinstance(notifications, list)
                or not all(valid_notification_shape(
                    n, strict_descriptor_id=strict_descriptor_id
                ) for n in notifications)):
            return False
    return True


def valid_document(sarif, strict_descriptor_id=True):
    """True when a document contains at least one structurally valid run."""
    runs = sarif.get("runs") if isinstance(sarif, dict) else None
    return isinstance(runs, list) and bool(runs) and all(
        valid_run_shape(run, strict_descriptor_id=strict_descriptor_id)
        for run in runs)


def scan_failure(sarif):
    """Return a scan-failure reason, or None when the invocations succeeded."""
    if not isinstance(sarif, dict):
        return None
    for run in sarif.get("runs", []) or []:
        for invocation in run.get("invocations", []) or []:
            if invocation.get("executionSuccessful") is False:
                return "executionSuccessful=false"
            for notification in invocation.get("toolExecutionNotifications", []) or []:
                if str(notification.get("level", "")).lower() == "error":
                    message = notification.get("message") or {}
                    return ((message.get("text") if isinstance(message, dict) else None)
                            or "error notification")
    return None
