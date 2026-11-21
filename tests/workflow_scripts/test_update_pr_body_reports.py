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

// A marker quoted inside a report must not be treated as a panel boundary.
const body = 'Intro\n' + START + '\n' +
  '<!-- coverage-report -->\n## Tests\n- bad line <!-- codeql-report -->\n' +
  END + '\n';
const updated = m.upsertReport(body, '<!-- codeql-report -->',
  '<!-- codeql-report -->\n## CodeQL analysis\n');
assert.ok(updated.includes('## CodeQL analysis'), 'new panel missing');
assert.ok(updated.includes('<!-- coverage-report -->\n## Tests'),
  'quoted marker truncated the tests panel');

// Inline label text must not inflate the aggregate totals.
const totals = m.totalsContent(START + '\n<!-- coverage-report -->\n## Tests\n' +
  '- message with **Analysis errors:** 999 inline\n' + END + '\n');
assert.ok(totals.includes('0 analysis errors'), totals);

// Generated lines still contribute to the totals.
const real = m.totalsContent(START + '\n<!-- codeql-report -->\n## CodeQL analysis\n' +
  '**Analysis errors:** 2 · **Analysis warnings:** 1\n' + END + '\n');
assert.ok(real.includes('2 analysis errors'), real);
assert.ok(real.includes('1 analysis warnings'), real);
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
