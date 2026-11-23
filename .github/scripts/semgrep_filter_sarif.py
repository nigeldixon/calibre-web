#!/usr/bin/env python3
import json
import os
import sys
from collections import Counter

from diff_scope import collect_changed_files, collect_diff_ranges, result_intersects_changed_lines
from sarif_validation import load_sarif, valid_run_shape

raw_path = 'semgrep-results/semgrep.raw.sarif'
out_path = 'semgrep-results/semgrep.sarif'
scope_path = 'semgrep-results/scope.json'
exit_path = 'semgrep-results/scan-exit-code.txt'

try:
    scan_exit = int(open(exit_path, encoding='utf-8').read().strip())
except (OSError, ValueError):
    scan_exit = None


def scan_exit_is_ok():
    # Semgrep uses 1 for "findings were produced", not for a scan failure.
    # Only 0 and 1 are safe to publish to code scanning.
    return scan_exit in (0, 1)


def base_scope(**overrides):
    scope = {
        'scoped': False,
        'reason': 'unknown',
        'changed_count': 0,
        'base': '',
        'head': '',
        'scan_ok': scan_exit_is_ok(),
        'scan_exit': scan_exit,
        'total_all': 0,
        'findings': {'total': 0, 'error': 0, 'warning': 0, 'note': 0},
        'warnings': 0,
        'warning_detail': {},
    }
    scope.update(overrides)
    return scope


def write_scope(scope):
    with open(scope_path, 'w', encoding='utf-8') as f:
        json.dump(scope, f)
    print(json.dumps(scope))


def write_empty_sarif():
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump({"version": "2.1.0", "runs": []}, f)


if not os.path.exists(raw_path):
    # Still write an empty local SARIF so report/artifact steps have a file,
    # but fail so the upload step can refuse to publish it to code scanning.
    write_scope(base_scope(reason='no_sarif', scan_ok=False))
    write_empty_sarif()
    sys.exit(1)

try:
    sarif = load_sarif(raw_path)
except ValueError as exc:
    # Invalid SARIF is a hard failure: the scan did not produce usable output.
    write_scope(base_scope(reason='invalid_sarif', error=str(exc), scan_ok=False))
    write_empty_sarif()
    sys.exit(1)

# Valid JSON is not necessarily valid SARIF. A truncated or malformed scan
# output must not be published: it would clear code-scanning alerts while
# showing a false all-clear.
runs = sarif.get('runs') if isinstance(sarif, dict) else None
if (not isinstance(runs, list) or not runs
        or not all(valid_run_shape(run, strict_descriptor_id=True) for run in runs)):
    write_scope(base_scope(
        reason='invalid_sarif',
        error='SARIF must contain at least one run with a tool object',
        scan_ok=False,
    ))
    write_empty_sarif()
    sys.exit(1)

total_all = sum(len(run.get('results', []) or []) for run in sarif.get('runs', []) or [])

event = os.environ.get('EVENT_NAME', '')
base = (os.environ.get('PR_BASE_SHA') or '').strip()
head = (os.environ.get('PR_HEAD_SHA') or '').strip()
base_ref = (os.environ.get('PR_BASE_REF') or '').strip()
changed = None
changed_ranges = None
reason = 'full_scan'

# Use the same diff-line scoping as the trusted report generator so the
# push path cannot surface pre-existing findings from unchanged lines.
if event in ('pull_request', 'push') and base and head:
    changed_ranges = collect_diff_ranges(base, head, base_ref)
    changed_files = collect_changed_files(base, head, base_ref)
    if changed_ranges:
        # Keep deletion-only files in scope, matching the trusted generator's
        # conservative file-level fallback for files without changed hunks.
        changed = (changed_files or set()) | set(changed_ranges)
        reason = 'pr_changed_lines'
    else:
        # No usable hunks (or no diff at all): fall back conservatively to
        # file-level scoping, or to the full scan if the diff is unavailable.
        changed_ranges = None
        changed = changed_files
        reason = 'pr_changed_files' if changed is not None else 'diff_failed_full_scan'


def norm(uri):
    if not uri:
        return ''
    uri = uri.replace('\\', '/')
    while uri.startswith('./'):
        uri = uri[2:]
    return uri


def in_changed(result, changed_set, ranges=None):
    return result_intersects_changed_lines(result, changed_set, ranges, norm)


if changed is not None:
    for run in sarif.get('runs', []) or []:
        results = run.get('results', []) or []
        run['results'] = [r for r in results if in_changed(r, changed, changed_ranges)]

with open(out_path, 'w', encoding='utf-8') as f:
    json.dump(sarif, f)

notifications = Counter()
exec_errors = 0
for run in sarif.get('runs', []) or []:
    for inv in run.get('invocations') or []:
        for n in inv.get('toolExecutionNotifications') or []:
            if (n.get('level') or '').lower() == 'error':
                # An execution error means the scan is incomplete even when
                # Semgrep exited 0; the upload gate must refuse this SARIF.
                exec_errors += 1
                continue
            key = (n.get('descriptor') or {}).get('id') or (n.get('level') or 'warning')
            notifications[key] += 1

findings = {'total': 0, 'error': 0, 'warning': 0, 'note': 0}
for run in sarif.get('runs', []) or []:
    for r in run.get('results', []) or []:
        findings['total'] += 1
        level = (r.get('level') or 'warning').lower()
        if level == 'error':
            findings['error'] += 1
        elif level in ('note', 'none'):
            findings['note'] += 1
        else:
            findings['warning'] += 1

write_scope(base_scope(
    scoped=changed is not None,
    reason=reason,
    scope_kind=('lines' if changed_ranges else 'files') if changed is not None else None,
    changed_count=len(changed) if changed is not None else 0,
    base=base,
    head=head,
    total_all=total_all,
    findings=findings,
    scan_ok=(scan_exit_is_ok() and exec_errors == 0),
    exec_errors=exec_errors,
    warnings=sum(notifications.values()),
    warning_detail=dict(notifications),
))

# Exit code 1 means findings were produced and is safe to upload. Any other
# non-zero exit code or an error-level execution notification means the scan
# failed. Keep the SARIF for the report but fail the step so the upload gate
# refuses to publish incomplete data to code scanning.
if not scan_exit_is_ok() or exec_errors > 0:
    sys.exit(1)
