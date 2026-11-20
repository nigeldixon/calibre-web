#!/usr/bin/env python3
import json
import os
import subprocess
import sys

raw_path = 'semgrep-results/semgrep.raw.sarif'
out_path = 'semgrep-results/semgrep.sarif'
scope_path = 'semgrep-results/scope.json'

if not os.path.exists(raw_path):
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump({"version": "2.1.0", "runs": []}, f)
    with open(scope_path, 'w', encoding='utf-8') as f:
        json.dump({'scoped': False, 'reason': 'no_sarif', 'changed_count': 0, 'base': '', 'head': ''}, f)
    sys.exit(0)

with open(raw_path, 'r', encoding='utf-8') as f:
    sarif = json.load(f)

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

scope = {
    'scoped': changed is not None,
    'reason': reason,
    'changed_count': (len(changed) if changed is not None else 0),
    'base': base,
    'head': head,
}
with open(scope_path, 'w', encoding='utf-8') as f:
    json.dump(scope, f)
print(json.dumps(scope))
