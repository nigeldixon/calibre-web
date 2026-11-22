const { test } = require('node:test');
const assert = require('node:assert');
const path = require('path');

const ROOT = path.resolve(__dirname, '../../..');

test('report markers and totals are line-anchored', () => {
const m = require(path.join(ROOT, '.github', 'scripts', 'update_pr_body_reports.js'));
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

// An earlier duplicate panel's metrics must not leak into the preceding
// block or survive as stale report text.
const stalePanels = START + '\n' +
  '<!-- codeql-report -->\n' + H.codeql + '\n' +
  '**Analysis errors:** 0 · **Analysis warnings:** 0\n' +
  '<!-- semgrep-report -->\n' + H.semgrep + '\n' +
  '**Analysis errors:** 5 · **Analysis warnings:** 0\n' +
  '<!-- semgrep-report -->\n' + H.semgrep + '\n' +
  '**Analysis errors:** 2 · **Analysis warnings:** 0\n' + END + '\n';
const staleCleaned = m.canonicalizeSection(stalePanels);
assert.ok(!staleCleaned.includes('5 ·'), staleCleaned);
const staleTotals = m.totalsContent(staleCleaned);
assert.ok(staleTotals.includes('2 analysis errors'), staleTotals);
assert.ok(!staleTotals.includes('7 analysis errors'), staleTotals);

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

// A quoted, unmatched start marker after the section must not discard the
// real reports or the PR text that follows them.
const trailingMarker = 'Intro\n\n---\n## 📊 Quality reports\n' + START + '\n' +
  '<!-- coverage-report -->\n' + H.tests + '\nreal report\n' + END + '\n' +
  'Tail text\n' + START + '\n';
const trailingClean = m.canonicalizeSection(trailingMarker);
assert.strictEqual(trailingClean.split(START).length - 1, 1, trailingClean);
assert.ok(trailingClean.includes('real report'), trailingClean);
assert.ok(trailingClean.includes('Tail text'), trailingClean);
});

test('totals qualify unavailable reports instead of counting them as zero', () => {
const m = require(path.join(ROOT, '.github', 'scripts', 'update_pr_body_reports.js'));
const unavailable = m.unavailablePanel({
  marker: m.REPORT_DEFS.codeql.marker,
  heading: m.REPORT_HEADINGS.codeql,
  labels: true,
  message: 'artifacts were not produced',
});
const body = [
  m.SECTION_START,
  unavailable,
  m.REPORT_DEFS.semgrep.marker,
  m.REPORT_HEADINGS.semgrep,
  '**Whole-solution issues:** 122 · **In changed files:** 3',
  '**Analysis errors:** 0 · **Analysis warnings:** 70',
  m.SECTION_END,
].join('\n');

const oneMissing = m.totalsContent(body);
assert.ok(oneMissing.includes('at least 122 issues found'), oneMissing);
assert.ok(oneMissing.includes('at least 0 analysis errors'), oneMissing);
assert.ok(oneMissing.includes('at least 70 analysis warnings'), oneMissing);
assert.ok(oneMissing.includes('1 report unavailable'), oneMissing);
assert.ok(!oneMissing.includes('**Quality total:** 122 issues found'), oneMissing);

const bothMissing = m.totalsContent([
  m.SECTION_START,
  unavailable,
  m.unavailablePanel({
    marker: m.REPORT_DEFS.semgrep.marker,
    heading: m.REPORT_HEADINGS.semgrep,
    labels: true,
    message: 'artifacts expired',
  }),
  m.SECTION_END,
].join('\n'));
assert.ok(bothMissing.includes('at least 0 issues found'), bothMissing);
assert.ok(bothMissing.includes('2 reports unavailable'), bothMissing);
});

test('pending panels qualify totals instead of looking like zero findings', () => {
const m = require(path.join(ROOT, '.github', 'scripts', 'update_pr_body_reports.js'));
const pending = [
  m.REPORT_DEFS.codeql.marker,
  m.REPORT_HEADINGS.codeql,
  '',
  '⏳ CodeQL analysis is queued/running. Results will appear here when the workflow completes.',
].join('\n');
const body = [
  m.SECTION_START,
  pending,
  m.REPORT_DEFS.semgrep.marker,
  m.REPORT_HEADINGS.semgrep,
  '**Whole-solution issues:** 60 · **In changed files:** 2',
  '**Analysis errors:** 0 · **Analysis warnings:** 5',
  m.SECTION_END,
].join('\n');

const totals = m.totalsContent(body);
assert.ok(totals.includes('at least 60 issues found'), totals);
assert.ok(totals.includes('at least 0 analysis errors'), totals);
assert.ok(totals.includes('at least 5 analysis warnings'), totals);
assert.ok(totals.includes('1 report unavailable'), totals);
assert.ok(!totals.includes('**Quality total:** 60 issues found'), totals);

// An hourglass quoted inside a real report must not qualify the totals.
const quoted = m.totalsContent([
  m.SECTION_START,
  m.REPORT_DEFS.tests.marker,
  m.REPORT_HEADINGS.tests,
  '- waiting message says ⏳ but results are complete',
  m.SECTION_END,
].join('\n'));
assert.ok(quoted.includes('**Quality total:** 0 issues found'), quoted);
assert.ok(!quoted.includes('unavailable'), quoted);
});
