import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]

JS_TEST = r"""
const assert = require('assert');
const m = require('./.github/scripts/update_pr_body_reports.js');
const START = m.SECTION_START;
const END = m.SECTION_END;
const H = m.REPORT_HEADINGS;

assert.ok(m.SECTION_HEADER.includes('## 📊 Quality reports'), m.SECTION_HEADER);
assert.strictEqual(H.codeql, '### 🔍 CodeQL analysis');
assert.strictEqual(H.semgrep, '### 🛡️ Semgrep analysis');
assert.strictEqual(H.tests, '### 🧪 Tests');

// A marker quoted inside a report must not be treated as a panel boundary.
const body = 'Intro\n' + START + '\n' +
  '<!-- coverage-report -->\n' + H.tests + '\n- bad line <!-- codeql-report -->\n' +
  END + '\n';
const updated = m.upsertReport(body, '<!-- codeql-report -->',
  '<!-- codeql-report -->\n' + H.codeql + '\n');
assert.ok(updated.includes(H.codeql), 'new panel missing');
assert.ok(updated.includes('<!-- coverage-report -->\n' + H.tests),
  'quoted marker truncated the tests panel');

// Inline label text must not inflate the aggregate totals.
const totals = m.totalsContent(START + '\n<!-- coverage-report -->\n' + H.tests + '\n' +
  '- message with **Analysis errors:** 999 inline\n' + END + '\n');
assert.ok(totals.includes('0 analysis errors'), totals);

// Generated lines still contribute to the totals.
const real = m.totalsContent(START + '\n<!-- codeql-report -->\n' + H.codeql + '\n' +
  '**Analysis errors:** 2 · **Analysis warnings:** 1\n' + END + '\n');
assert.ok(real.includes('2 analysis errors'), real);
assert.ok(real.includes('1 analysis warnings'), real);

// Standalone injected section markers before the real section are discarded.
const injected = 'Fake\n' + START + '\nattacker\n' + END + '\nReal\n' +
  START + '\n<!-- coverage-report -->\n' + H.tests + '\n' + END + '\nTail\n';
const canonical = m.canonicalizeSection(injected);
assert.strictEqual(canonical.split(START).length - 1, 1, 'duplicate section starts');
assert.strictEqual(canonical.split(END).length - 1, 1, 'duplicate section ends');
assert.ok(canonical.includes('<!-- coverage-report -->\n' + H.tests), canonical);
assert.ok(!canonical.includes('attacker'), canonical);

// Duplicate report markers inside the section collapse to the last one.
const duplicated = START + '\n<!-- codeql-report -->\nold\n' +
  '<!-- semgrep-report -->\nkeep\n<!-- codeql-report -->\nnew\n' + END + '\n';
const cleaned = m.canonicalizeSection(duplicated);
assert.strictEqual(cleaned.split('<!-- codeql-report -->').length - 1, 1, cleaned);
assert.ok(cleaned.includes('<!-- codeql-report -->\nnew'), cleaned);

// Metric-looking text outside a generated report block cannot affect totals.
const outside = START + '\n**Analysis errors:** 999\n' +
  '<!-- coverage-report -->\n' + H.tests + '\n' + END + '\n';
const outsideTotals = m.totalsContent(outside);
assert.ok(outsideTotals.includes('0 analysis errors'), outsideTotals);

// A body with no section markers is rebuilt from clean text, including the
// pre-emoji header used by older PR bodies.
const rebuilt = m.canonicalizeSection('Intro' + m.SECTION_HEADER + 'stale\n');
assert.strictEqual(rebuilt.split(m.SECTION_HEADER).length - 1, 1, rebuilt);
assert.ok(!rebuilt.includes('stale'), rebuilt);
const legacy = m.canonicalizeSection('Intro' + m.LEGACY_SECTION_HEADER + 'stale\n');
assert.ok(legacy.includes(m.SECTION_HEADER), legacy);
assert.ok(!legacy.includes('stale'), legacy);
"""


def test_report_markers_and_totals_are_line_anchored():
    if not shutil.which("node"):
        pytest.skip("node is not installed")

    proc = subprocess.run(
        ["node", "-e", JS_TEST],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
