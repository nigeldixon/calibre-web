#!/usr/bin/env python3
import json
import os

marker = '<!-- semgrep-report -->'
sarif_path = 'semgrep-results/semgrep.sarif'
scope_path = 'semgrep-results/scope.json'
raw_sarif_path = os.environ.get('RAW_SARIF_PATH', 'semgrep-results/semgrep.raw.sarif').strip()
scan_exit_path = os.environ.get('SCAN_EXIT_PATH', 'semgrep-results/scan-exit-code.txt').strip()
changed_files_file = os.environ.get('CHANGED_FILES_FILE', '').strip()


def load_json(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def norm_uri(uri):
    if not uri:
        return ''
    uri = uri.replace('\\', '/')
    while uri.startswith('./'):
        uri = uri[2:]
    return uri


def result_in_changed(result, changed):
    if changed is None:
        return True
    locs = list(result.get('locations') or []) + list(result.get('relatedLocations') or [])
    if not locs:
        return False
    for loc in locs:
        phys = (loc or {}).get('physicalLocation') or {}
        art = phys.get('artifactLocation') or {}
        if norm_uri(art.get('uri', '')) in changed:
            return True
    return False


def count_findings(sarif):
    totals = {'results': 0, 'error': 0, 'warning': 0, 'note': 0}
    for run in (sarif or {}).get('runs', []) or []:
        for r in run.get('results', []) or []:
            totals['results'] += 1
            level = (r.get('level') or 'warning').lower()
            if level == 'error':
                totals['error'] += 1
            elif level in ('note', 'none'):
                totals['note'] += 1
            else:
                totals['warning'] += 1
    return totals


def count_notifications(sarif):
    detail = {}
    for run in (sarif or {}).get('runs', []) or []:
        for inv in run.get('invocations') or []:
            for n in inv.get('toolExecutionNotifications') or []:
                key = (n.get('descriptor') or {}).get('id') or (n.get('level') or 'warning')
                detail[key] = detail.get(key, 0) + 1
    return detail


def read_scan_exit():
    try:
        with open(scan_exit_path, 'r', encoding='utf-8') as f:
            return int(f.read().strip())
    except Exception:
        return None


# Trusted path: rebuild the summary from raw SARIF and a changed-file list
# supplied by the privileged workflow_run publisher.
if changed_files_file:
    changed = set()
    try:
        with open(changed_files_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = norm_uri(line.strip())
                if line:
                    changed.add(line)
    except Exception:
        changed = None

    raw_exists = os.path.exists(raw_sarif_path)
    raw = load_json(raw_sarif_path)
    if raw is None:
        sarif = None
        scope = {
            'scoped': False,
            'reason': 'invalid_sarif' if raw_exists else 'no_sarif',
            'changed_count': len(changed) if changed is not None else 0,
            'scan_ok': False, 'scan_exit': read_scan_exit(), 'total_all': 0,
        }
    else:
        total_all = sum(len(run.get('results', []) or []) for run in raw.get('runs', []) or [])
        runs = []
        for run in raw.get('runs', []) or []:
            filtered = dict(run)
            filtered['results'] = [r for r in (run.get('results') or []) if result_in_changed(r, changed)]
            runs.append(filtered)
        sarif = {'version': raw.get('version', '2.1.0'), 'runs': runs}
        notification_detail = count_notifications(raw)
        scan_exit = read_scan_exit()
        scope = {
            'scoped': changed is not None,
            'reason': 'pr_changed_files' if changed is not None else 'full_scan',
            'changed_count': len(changed) if changed is not None else 0,
            'base': os.environ.get('PR_BASE_SHA', '').strip(),
            'head': os.environ.get('PR_HEAD_SHA', '').strip(),
            'scan_ok': scan_exit == 0,
            'scan_exit': scan_exit,
            'total_all': total_all,
            'warnings': sum(notification_detail.values()),
            'warning_detail': notification_detail,
        }
else:
    # Legacy path used by the push workflow in the repository itself.
    sarif = load_json(sarif_path)
    scope = {'scoped': False, 'reason': 'unknown', 'scan_ok': True, 'total_all': 0,
             'warnings': 0, 'warning_detail': {}}
    loaded_scope = load_json(scope_path)
    if loaded_scope:
        scope = loaded_scope

totals = count_findings(sarif)

analyze_outcome = os.environ.get('ANALYZE_RESULT', '')
scan_failed = scope.get('scan_ok') is False or scope.get('reason') in ('no_sarif', 'invalid_sarif')
job_failed = analyze_outcome not in ('', 'success')
warnings = scope.get('warnings', 0)

errors = 0
if scan_failed or job_failed:
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
    scope_line = f"_Scope: {scope.get('changed_count', 0)} changed file(s) in this PR"
    if base and head:
        scope_line += f" (`{base}...{head}`)"
    lines += ['', scope_line + '._']

print('\n'.join(lines))
