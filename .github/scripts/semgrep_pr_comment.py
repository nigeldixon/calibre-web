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

scope = {'scoped': False, 'reason': 'unknown', 'scan_ok': True, 'total_all': 0,
         'warnings': 0, 'warning_detail': {}}
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

analyze_outcome = os.environ.get('ANALYZE_RESULT', '')
scan_failed = scope.get('scan_ok') is False or scope.get('reason') in ('no_sarif', 'invalid_sarif')
job_failed = analyze_outcome not in ('', 'success')
warnings = scope.get('warnings', 0)

errors = 0
if scan_failed:
    errors = 1
elif job_failed:
    errors = 1


def status_icon(n):
    return '✅' if n == 0 else '⚠️'


analysis_url = os.environ.get('ANALYSIS_URL', '').strip()
run_url = os.environ.get('RUN_URL', '').strip()
full_analysis_links = []
if analysis_url:
    full_analysis_links.append(f'[All Semgrep alerts]({analysis_url})')
if run_url:
    full_analysis_links.append(f'[Workflow run]({run_url})')
full_analysis = '**Full analysis:** ' + ' · '.join(full_analysis_links) if full_analysis_links else ''

lines = [marker, '## Semgrep analysis', '']
if scope.get('scoped'):
    lines.append('Automated Semgrep summary for files changed in this PR (filtered SARIF).')
elif scope.get('reason') == 'diff_failed_full_scan':
    lines.append('Automated Semgrep summary (could not determine PR diff; showing unfiltered/full scan results).')
elif os.environ.get('GITHUB_EVENT_NAME') == 'schedule':
    lines.append('Automated Semgrep summary (scheduled full scan).')
else:
    lines.append('Automated Semgrep summary.')

if full_analysis:
    lines += ['', full_analysis]

lines += [
    '',
    '| Status | Results | Error | Warning | Note |',
    '|:------:|--------:|------:|--------:|-----:|',
]

if sarif is None:
    lines.append('| ⚪ | - | - | - | - |')
else:
    lines.append(f"| {status_icon(totals['results'])} | {totals['results']} | {totals['error']} | {totals['warning']} | {totals['note']} |")

lines += [
    '',
    f"**Whole-solution issues:** {scope.get('total_all', 0)} · **In changed files:** {totals['results']}",
    f'**Analysis errors:** {errors} · **Analysis warnings:** {warnings}',
    '',
    f'**Analyze job:** `{analyze_outcome}`',
]

if not scope.get('scan_ok', True):
    lines += ['', f'❌ Semgrep scan failed (exit {scope.get("scan_exit")}); results may be incomplete.']
if scope.get('reason') == 'no_sarif':
    lines += ['', '_No Semgrep SARIF was produced; the scan did not complete._']
if scope.get('reason') == 'invalid_sarif':
    lines += ['', '_Semgrep SARIF could not be parsed; the scan is marked as failed._']
if warnings:
    details = [f'- {v} × {k}' for k, v in (scope.get('warning_detail') or {}).items()]
    lines += [
        '',
        f'<details><summary>⚠️ Analysis warnings ({warnings})</summary>',
        '',
    ] + details + [
        '',
        '</details>',
    ]

if scope.get('scoped'):
    base = (scope.get('base') or '')[:7]
    head = (scope.get('head') or '')[:7]
    lines += ['', f"_Scope: {scope.get('changed_count', 0)} changed file(s) in this PR (`{base}...{head}`)._"]

print('\n'.join(lines))
