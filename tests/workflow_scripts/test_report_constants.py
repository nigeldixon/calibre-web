import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / ".github" / "scripts"
CONSTANTS_PATH = SCRIPTS_DIR / "report_constants.json"
GENERATORS = {
    "codeql_pr_comment.py": "codeql",
    "semgrep_pr_comment.py": "semgrep",
    "test_coverage_comment.py": "tests",
}

JS_PARITY = r"""
const assert = require('assert');
const C = require('./.github/scripts/report_constants.json');
const m = require('./.github/scripts/update_pr_body_reports.js');

assert.strictEqual(m.SECTION_START, C.sectionStart);
assert.strictEqual(m.SECTION_END, C.sectionEnd);
assert.strictEqual(m.SECTION_HEADER, C.sectionHeader);
assert.strictEqual(m.LEGACY_SECTION_HEADER, C.legacySectionHeader);
assert.strictEqual(m.SUMMARY_MARKER, C.summaryMarker);
assert.deepStrictEqual(m.REPORT_HEADINGS, C.headings);
assert.deepStrictEqual(m.REPORT_KEYS, C.reportKeys);
assert.deepStrictEqual(
  m.REPORT_MARKERS,
  [C.summaryMarker, ...C.reportKeys.map((key) => C.markers[key])],
);
assert.strictEqual(m.ISSUES_LABEL, C.labels.issues);
assert.strictEqual(m.CHANGED_LABEL, C.labels.changed);
assert.strictEqual(m.ERRORS_LABEL, C.labels.errors);
assert.strictEqual(m.WARNINGS_LABEL, C.labels.warnings);
assert.strictEqual(m.ANALYZE_LABEL, C.labels.analyze);

for (const key of C.reportKeys) {
  assert.strictEqual(m.REPORT_DEFS[key].marker, C.markers[key]);
  assert.strictEqual(m.REPORT_DEFS[key].heading, C.headings[key]);
}

// The shared helpers must use the same labels and whole-line markers.
const panel = m.unavailablePanel({
  marker: C.markers.codeql,
  heading: C.headings.codeql,
  labels: true,
  message: 'boom.',
});
assert.ok(panel.startsWith(C.markers.codeql), panel);
assert.ok(panel.includes(C.labels.issues), panel);
assert.ok(panel.includes(C.labels.analyze), panel);
const section = C.markers.codeql + '\nA\n' + C.markers.tests + '\nB\n';
const bounds = m.panelBounds(section, C.markers.codeql);
assert.strictEqual(section.slice(bounds.start, bounds.end), C.markers.codeql + '\nA\n');
"""


def load_json_constants():
    return json.loads(CONSTANTS_PATH.read_text(encoding="utf-8"))


def test_python_loader_matches_json():
    spec = importlib.util.spec_from_file_location(
        "report_constants", SCRIPTS_DIR / "report_constants.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.load_constants() == load_json_constants()


def test_js_exports_match_shared_constants():
    if not shutil.which("node"):
        pytest.skip("node is not installed")

    proc = subprocess.run(
        ["node", "-e", JS_PARITY],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize("name,key", GENERATORS.items())
def test_generators_do_not_hardcode_shared_constants(name, key):
    text = (SCRIPTS_DIR / name).read_text(encoding="utf-8")
    constants = load_json_constants()
    assert "load_constants()" in text
    assert constants["markers"][key] not in text
    assert constants["headings"][key] not in text
    if key != "tests":
        for label in constants["labels"].values():
            assert label not in text
