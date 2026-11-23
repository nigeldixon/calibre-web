const { test } = require('node:test');
const assert = require('node:assert');
const path = require('path');

const ROOT = path.resolve(__dirname, '../../..');

test('JS exports match shared constants', () => {
const C = require(path.join(ROOT, '.github', 'scripts', 'report_constants.json'));
const m = require(path.join(ROOT, '.github', 'scripts', 'update_pr_body_reports.js'));

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
});
