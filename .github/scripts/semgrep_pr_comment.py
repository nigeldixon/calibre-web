#!/usr/bin/env python3
import json
import os

marker = '<!-- semgrep-report -->'
sarif_path = 'semgrep-results/semgrep.sarif'
scope_path = 'semgrep-results/scope.json'

sarif = None
if os.path.exists(sarif_path):
    try:
        with open(sarif_path, 'r', encoding='utf-8') as f:
            sarif = json.load(f)
    except Exception:
        sarif = None

scope = {'scoped': False, 'reason': 'unknown', 'changed_count': 0, 'base': '', 'head': ''}
if os.path.exists(scope_path):
    try:
        with open(scope_path, 'r', encoding='utf-8') as f:
            scope = json.load(f)
    except Exception:
        pass

totals = {'results': 0, 'error': 0, 'warning': 0, 'note': 0}
if sarif is not None:
    for run in sarif.get('runs', []) or []:
        for r in run.get('results', []) or []:
            totals['results'] += 1
            level = (r.get('level') or 'warning').lower()
            if level == 'error':
                totals['error'] += 1
            elif level in ('note', 'none'):
                totals['note'] += 1
            else:
                totals['warning'] += 1

def status_icon(n):
    return '✅' if n == 0 else '⚠️'

lines = [marker, '## Semgrep analysis', '']
if scope.get('scoped'):
    lines.append('Automated Semgrep summary for files changed in this PR (filtered SARIF). See the **Security** tab / code scanning alerts and workflow logs for full details.')
elif scope.get('reason') == 'diff_failed_full_scan':
    lines.append('Automated Semgrep summary (could not determine PR diff; showing unfiltered/full scan results). See the **Security** tab / code scanning alerts and workflow logs for full details.')
elif os.environ.get('GITHUB_EVENT_NAME') == 'schedule':
    lines.append('Automated Semgrep summary (scheduled full scan). See the **Security** tab / code scanning alerts and workflow logs for full details.')
else:
    lines.append('Automated Semgrep summary. See the **Security** tab / code scanning alerts and workflow logs for full details.')

lines += [
    '',
    '| Status | Results | Error | Warning | Note |',
    '|:------:|--------:|------:|--------:|-----:|',
]

if sarif is None:
    lines.append('| ⚪ | - | - | - | - |')
else:
    lines.append(f"| {status_icon(totals['results'])} | {totals['results']} | {totals['error']} | {totals['warning']} | {totals['note']} |")

analyze_outcome = os.environ.get('ANALYZE_RESULT', '')
lines += ['', f'**Analyze job:** `{analyze_outcome}`']

if scope.get('scoped'):
    base = (scope.get('base') or '')[:7]
    head = (scope.get('head') or '')[:7]
    lines += ['', f"_Scope: {scope.get('changed_count', 0)} changed file(s) in this PR (`{base}...{head}`)._"]

if sarif is None and not os.path.exists(sarif_path):
    lines += ['', '_No Semgrep SARIF was found for this run; results may be unavailable._']

print('\n'.join(lines))
