#!/usr/bin/env python3
import json
import os

from diff_scope import collect_diff_ranges, result_intersects_changed_lines
from report_constants import load_constants
from sarif_validation import valid_run_shape

C = load_constants()
marker = C['markers']['semgrep']
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


def result_in_changed(result, changed, changed_ranges=None):
    return result_intersects_changed_lines(result, changed, changed_ranges, norm_uri)


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
    warnings = 0
    errors = 0
    for run in (sarif or {}).get('runs', []) or []:
        for inv in run.get('invocations') or []:
            for n in inv.get('toolExecutionNotifications') or []:
                if (n.get('level') or '').lower() == 'error':
                    errors += 1
                    continue
                key = (n.get('descriptor') or {}).get('id') or (n.get('level') or 'warning')
                detail[key] = detail.get(key, 0) + 1
                warnings += 1
    return detail, warnings, errors


def valid_sarif(sarif):
    runs = (sarif or {}).get('runs') if isinstance(sarif, dict) else None
    return isinstance(runs, list) and len(runs) > 0 and all(
        valid_run_shape(run, strict_descriptor_id=True) for run in runs
    )


def read_scan_exit():
    try:
        with open(scan_exit_path, 'r', encoding='utf-8') as f:
            return int(f.read().strip())
    except Exception:
        return None


def scan_exit_is_ok(scan_exit):
    # Semgrep returns 1 when findings were produced; that is a successful scan
    # whose SARIF is safe to report and upload.
    return scan_exit in (0, 1)


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
    if raw is None or not valid_sarif(raw):
        sarif = None
        scope = {
            'scoped': False,
            'reason': 'invalid_sarif' if raw_exists else 'no_sarif',
            'changed_count': len(changed) if changed is not None else 0,
            'scan_ok': False, 'scan_exit': read_scan_exit(), 'total_all': 0,
            'warnings': 0, 'warning_detail': {}, 'exec_errors': 0,
        }
    else:
        base_sha = os.environ.get('PR_BASE_SHA', '').strip()
        head_sha = os.environ.get('PR_HEAD_SHA', '').strip()
        base_ref = os.environ.get('PR_BASE_REF', '').strip()
        changed_ranges = collect_diff_ranges(base_sha, head_sha, base_ref=base_ref)
        if not changed_ranges:
            changed_ranges = None
        scope_reason = 'full_scan'
        if changed is not None:
            scope_reason = 'pr_changed_lines' if changed_ranges is not None else 'pr_changed_files'
        total_all = sum(len(run.get('results', []) or []) for run in raw.get('runs', []) or [])
        runs = []
        for run in raw.get('runs', []) or []:
            filtered = dict(run)
            filtered['results'] = [
                r for r in (run.get('results') or [])
                if result_in_changed(r, changed, changed_ranges)
            ]
            runs.append(filtered)
        sarif = {'version': raw.get('version', '2.1.0'), 'runs': runs}
        notification_detail, warnings, exec_errors = count_notifications(raw)
        scan_exit = read_scan_exit()
        scope = {
            'scoped': changed is not None,
            'reason': scope_reason,
            'scope_kind': 'lines' if changed_ranges is not None else 'files',
            'changed_count': len(changed) if changed is not None else 0,
            'base': base_sha,
            'head': head_sha,
            'scan_ok': scan_exit_is_ok(scan_exit) and exec_errors == 0,
            'scan_exit': scan_exit,
            'exec_errors': exec_errors,
            'total_all': total_all,
            'warnings': warnings,
            'warning_detail': notification_detail,
        }
else:
    # Legacy path used by the push workflow in the repository itself. A
    # missing or unreadable artifact means there are no trustworthy results;
    # report an analysis error rather than a false all-clear.
    sarif_exists = os.path.exists(sarif_path)
    sarif = load_json(sarif_path)
    if sarif is None or not valid_sarif(sarif):
        sarif = None
        scope = {'scoped': False,
                 'reason': 'invalid_sarif' if sarif_exists else 'no_sarif',
                 'scan_ok': False, 'total_all': 0,
                 'warnings': 0, 'warning_detail': {}, 'exec_errors': 0}
    else:
        scope = {'scoped': False, 'reason': 'unknown', 'scan_ok': True, 'total_all': 0,
                 'warnings': 0, 'warning_detail': {}, 'exec_errors': 0}
        loaded_scope = load_json(scope_path)
        if loaded_scope:
            scope = loaded_scope
        else:
            # Missing, empty, or corrupt scope data means the scan status is
            # unknown. Never keep the default scan_ok=True in that case.
            scope = {'scoped': False, 'reason': 'invalid_sarif', 'scan_ok': False,
                     'total_all': 0, 'warnings': 0, 'warning_detail': {}, 'exec_errors': 0}

totals = count_findings(sarif)

analyze_outcome = os.environ.get('ANALYZE_RESULT', '')
scan_failed = (
    scope.get('scan_ok') is False
    or scope.get('reason') in ('no_sarif', 'invalid_sarif')
    or (scope.get('exec_errors') or 0) > 0
)
job_failed = analyze_outcome not in ('', 'success')
warnings = scope.get('warnings', 0)

errors = 0
if scan_failed or job_failed:
    errors = 1


def status_icon(n):
    return '✅' if n == 0 else '⚠️'


analysis_url = os.environ.get('ANALYSIS_URL', '').strip()
analysis_label = os.environ.get('ANALYSIS_LABEL', '').strip() or 'All Semgrep alerts'
run_url = os.environ.get('RUN_URL', '').strip()

full_analysis = []
if run_url:
    full_analysis.append(
        f'**Full analysis:** [Workflow run]({run_url}) — complete results are in the '
        '`semgrep-results` artifact (`semgrep.raw.sarif`).'
    )
if analysis_url:
    full_analysis.append(f'**Code scanning:** [{analysis_label}]({analysis_url})')
full_analysis = '\n'.join(full_analysis)

lines = [marker, C['headings']['semgrep'], '']
if scope.get('scoped'):
    scope_kind = scope.get('scope_kind') or ('lines' if scope.get('reason') == 'pr_changed_lines' else 'files')
    if scope_kind == 'lines':
        lines.append('Automated Semgrep summary for lines changed in this PR (filtered SARIF).')
    else:
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
    f"{C['labels']['issues']}{scope.get('total_all', 0)} · "
    f"{C['labels']['changed']}{totals['results']}",
    f"{C['labels']['errors']}{errors} · {C['labels']['warnings']}{warnings}",
    '',
    f"{C['labels']['analyze']}`{analyze_outcome}`",
]

if not scope.get('scan_ok', True):
    detail = f'exit {scope.get("scan_exit")}'
    if scope.get('exec_errors', 0):
        detail += f', {scope["exec_errors"]} error-level notification(s)'
    lines += ['', f'❌ Semgrep scan failed ({detail}); results may be incomplete.']
if scope.get('reason') == 'no_sarif':
    lines += ['', '_No Semgrep SARIF was produced; the scan did not complete._']
if scope.get('reason') == 'invalid_sarif':
    lines += ['', '_Semgrep SARIF could not be parsed; the scan is marked as failed._']
if warnings:
    details = [f'- {v} × {k}' for k, v in (scope.get('warning_detail') or {}).items()]
    lines += [
        '',
        '<a id="quality-semgrep-warnings-anchor"></a>',
        f'<details><summary id="quality-semgrep-warnings"><a href="#quality-semgrep-warnings-anchor">⚠️ Analysis warnings ({warnings})</a></summary>',
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
