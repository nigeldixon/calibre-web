#!/usr/bin/env python3
import json
import os
import subprocess
import sys
from collections import Counter

raw_path = 'semgrep-results/semgrep.raw.sarif'
out_path = 'semgrep-results/semgrep.sarif'
scope_path = 'semgrep-results/scope.json'
exit_path = 'semgrep-results/scan-exit-code.txt'

try:
    scan_exit = int(open(exit_path, encoding='utf-8').read().strip())
except (OSError, ValueError):
    scan_exit = None


def base_scope(**overrides):
    scope = {
        'scoped': False,
        'reason': 'unknown',
        'changed_count': 0,
        'base': '',
        'head': '',
        'scan_ok': scan_exit == 0,
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
    write_scope(base_scope(reason='no_sarif'))
    write_empty_sarif()
    sys.exit(1)

try:
    with open(raw_path, 'r', encoding='utf-8') as f:
        sarif = json.load(f)
except Exception as exc:
    # Invalid SARIF is a hard failure: the scan did not produce usable output.
    write_scope(base_scope(reason='invalid_sarif', error=str(exc)))
    write_empty_sarif()
    sys.exit(1)

total_all = sum(len(run.get('results', []) or []) for run in sarif.get('runs', []) or [])

event = os.environ.get('EVENT_NAME', '')
base = (os.environ.get('PR_BASE_SHA') or '').strip()
head = (os.environ.get('PR_HEAD_SHA') or '').strip()
base_ref = (os.environ.get('PR_BASE_REF') or '').strip()
changed = None
reason = 'full_scan'

if event in ('pull_request', 'push') and base and head:
    try:
        if base_ref:
            subprocess.run(['git', 'fetch', 'origin', base_ref, '--depth=1'], check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        subprocess.run(['git', 'fetch', 'origin', base, '--depth=1'], check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        subprocess.run(['git', 'fetch', 'origin', head, '--depth=1'], check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        diff = subprocess.run(['git', 'diff', '--name-only', f'{base}...{head}'], check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if diff.returncode != 0:
            diff = subprocess.run(['git', 'diff', '--name-only', base, head], check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if diff.returncode == 0:
            changed = set()
            for line in (diff.stdout or '').splitlines():
                line = line.strip().replace('\\', '/')
                if line:
                    changed.add(line)
                    if line.startswith('./'):
                        changed.add(line[2:])
            reason = 'pr_changed_files'
        else:
            reason = 'diff_failed_full_scan'
    except Exception:
        changed = None
        reason = 'diff_failed_full_scan'


def norm(uri):
    if not uri:
        return ''
    uri = uri.replace('\\', '/')
    while uri.startswith('./'):
        uri = uri[2:]
    return uri


def in_changed(result, changed_set):
    if changed_set is None:
        return True
    locs = list(result.get('locations') or []) + list(result.get('relatedLocations') or [])
    if not locs:
        return False
    for loc in locs:
        phys = (loc or {}).get('physicalLocation') or {}
        art = phys.get('artifactLocation') or {}
        uri = norm(art.get('uri', ''))
        if uri in changed_set:
            return True
    return False


if changed is not None:
    for run in sarif.get('runs', []) or []:
        results = run.get('results', []) or []
        run['results'] = [r for r in results if in_changed(r, changed)]

with open(out_path, 'w', encoding='utf-8') as f:
    json.dump(sarif, f)

notifications = Counter()
for run in sarif.get('runs', []) or []:
    for inv in run.get('invocations') or []:
        for n in inv.get('toolExecutionNotifications') or []:
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
    changed_count=len(changed) if changed is not None else 0,
    base=base,
    head=head,
    total_all=total_all,
    findings=findings,
    warnings=sum(notifications.values()),
    warning_detail=dict(notifications),
))

# A non-zero Semgrep exit code means the scan failed. The SARIF may still
# contain partial results, so keep it for the report but fail the step so the
# upload gate refuses to publish incomplete data to code scanning.
if scan_exit != 0:
    sys.exit(1)
