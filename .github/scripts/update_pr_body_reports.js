const C = require('./report_constants.json');

const SECTION_START = C.sectionStart;
const SECTION_END = C.sectionEnd;
const SECTION_HEADER = C.sectionHeader;
const LEGACY_SECTION_HEADER = C.legacySectionHeader;
const SUMMARY_MARKER = C.summaryMarker;
const REPORT_HEADINGS = C.headings;
const ISSUES_LABEL = C.labels.issues;
const CHANGED_LABEL = C.labels.changed;
const ERRORS_LABEL = C.labels.errors;
const WARNINGS_LABEL = C.labels.warnings;
const ANALYZE_LABEL = C.labels.analyze;
const REPORT_DEFS = {
  codeql: {
    key: 'codeql', marker: C.markers.codeql, heading: C.headings.codeql,
    file: 'codeql-report.md', label: 'CodeQL', labels: true,
    artifactPrefix: 'codeql-sarif-', workflowPath: '.github/workflows/codeql.yml',
    requiredFiles: ['.github/workflows/codeql.yml'],
  },
  semgrep: {
    key: 'semgrep', marker: C.markers.semgrep, heading: C.headings.semgrep,
    file: 'semgrep-report.md', label: 'Semgrep', labels: true,
    artifactPrefix: 'semgrep-results', workflowPath: '.github/workflows/semgrep.yml',
    requiredFiles: [
      '.github/workflows/semgrep.yml',
      '.github/scripts/semgrep_filter_sarif.py',
    ],
  },
  tests: {
    key: 'tests', marker: C.markers.tests, heading: C.headings.tests,
    file: 'coverage-report.md', label: 'Tests', labels: false,
    artifactPrefix: 'coverage-data', workflowPath: '.github/workflows/test.yml',
    requiredFiles: [
      '.github/workflows/test.yml',
      '.github/scripts/collect_test_results.py',
      '.github/scripts/combine_coverage.sh',
      '.github/scripts/sync_test_failure_comments.py',
      '.github/scripts/test_coverage_comment.py',
      '.github/scripts/test_failure_comments.py',
    ],
  },
};
const REPORT_KEYS = C.reportKeys;
const REPORT_MARKERS = [SUMMARY_MARKER, ...REPORT_KEYS.map((key) => C.markers[key])];
// Generated metric lines start with one of these labels and may contain
// several of them (for example errors and warnings share a line).
const METRIC_LABELS = [ISSUES_LABEL, CHANGED_LABEL, ERRORS_LABEL, WARNINGS_LABEL];

// Markers embedded in report text (for example a test message copied into a
// failure bullet) must never be mistaken for a panel boundary. Only whole
// lines count as structural markers.
function lineMarkerIndices(body, marker) {
  const indices = [];
  let pos = 0;
  while ((pos = body.indexOf(marker, pos)) !== -1) {
    const before = pos === 0 ? '\n' : body[pos - 1];
    const after = body[pos + marker.length] || '\n';
    if (before === '\n' && (after === '\n' || after === '\r')) indices.push(pos);
    pos += marker.length;
  }
  return indices;
}

// Return the bounds of a single report panel. Only whole-line markers count,
// so a marker quoted inside a report cannot truncate another panel. Callers
// may pass a narrower marker set when only some panels can be present.
function panelBounds(section, marker, markers) {
  const start = lineMarkerIndices(section, marker)[0] ?? -1;
  if (start === -1) return { start: -1, end: -1 };
  let end = section.length;
  for (const m of (markers || REPORT_MARKERS)) {
    if (m === marker) continue;
    const at = lineMarkerIndices(section, m).find((i) => i > start + marker.length) ?? -1;
    if (at !== -1 && at < end) end = at;
  }
  return { start, end };
}

// Build the explicit fallback panel used when a workflow produced no usable
// report. Analysis panels also carry the metric labels that refreshTotals()
// parses, so an unavailable report contributes "unknown", never a zero.
function unavailablePanel({ marker, heading, labels, message }) {
  const parts = [marker, heading, '', `❌ Report unavailable: ${message}`];
  if (labels) {
    parts.push(
      '',
      `${ISSUES_LABEL}unknown · ${CHANGED_LABEL}unknown`,
      `${ERRORS_LABEL}unknown · ${WARNINGS_LABEL}unknown`,
      '',
      `${ANALYZE_LABEL}\`failure\``,
    );
  }
  return parts.join('\n');
}

function stripQualityMarkers(text) {
  return (text || '')
    .split(/\r?\n/)
    .filter((line) => line !== SECTION_START && line !== SECTION_END)
    .join('\n')
    .split(SECTION_HEADER)
    .join('')
    .split(LEGACY_SECTION_HEADER)
    .join('');
}

function dedupeReportMarkers(section) {
  const lines = (section || '').split(/\r?\n/);
  const lastIndex = {};
  lines.forEach((line, i) => {
    if (REPORT_MARKERS.includes(line)) lastIndex[line] = i;
  });
  const kept = [];
  let skipping = false;
  lines.forEach((line, i) => {
    if (REPORT_MARKERS.includes(line)) {
      // Drop the whole earlier panel, not just its marker: its metric lines
      // would otherwise be attributed to the preceding panel by
      // reportBlockText() and inflate the aggregate totals.
      skipping = lastIndex[line] !== i;
    }
    if (!skipping) kept.push(line);
  });
  return kept.join('\n');
}

// Rebuild exactly one quality section from the last complete pair of section
// markers. Standalone injected pairs before it are discarded, and duplicate
// report markers inside it are collapsed to the last occurrence.
function sectionBounds(body) {
  const starts = lineMarkerIndices(body || '', SECTION_START);
  const ends = lineMarkerIndices(body || '', SECTION_END);
  let start = -1;
  let end = -1;
  // A trailing unmatched START marker (for example, quoted in the PR text)
  // must not hide the genuine report section.
  for (const candidate of starts) {
    const candidateEnd = ends.find((i) => i > candidate) ?? -1;
    if (candidateEnd === -1) continue;
    start = candidate;
    end = candidateEnd;
  }
  return { start, end };
}

// Remove complete section pairs that appear before the section we are keeping.
// Their contents are untrusted leftovers, not part of the PR description or
// the generated report, and must not survive canonicalization.
function stripPriorSections(body, keepStart) {
  const starts = lineMarkerIndices(body, SECTION_START);
  const ends = lineMarkerIndices(body, SECTION_END);
  let cleaned = '';
  let cursor = 0;
  for (const start of starts) {
    if (start >= keepStart) break;
    const end = ends.find((i) => i > start) ?? -1;
    if (end === -1 || end >= keepStart) continue;
    cleaned += body.slice(cursor, start);
    cursor = end + SECTION_END.length;
  }
  return cleaned + body.slice(cursor);
}

function qualityHeaderIndex(body) {
  const current = body.indexOf(SECTION_HEADER);
  const legacy = body.indexOf(LEGACY_SECTION_HEADER);
  if (current === -1) return legacy;
  if (legacy === -1) return current;
  return Math.min(current, legacy);
}

function canonicalizeSection(body) {
  body = body || '';
  const originalBounds = sectionBounds(body);
  if (originalBounds.start === -1 || originalBounds.end === -1) {
    // Without section markers, anything from a generated header onward is a
    // stale report body. Keep the PR's own text before that header only.
    const headerAt = qualityHeaderIndex(body);
    const prefix = headerAt === -1 ? body : body.slice(0, headerAt);
    return stripQualityMarkers(prefix).replace(/\s*$/, '') + SECTION_HEADER + SECTION_START + '\n' + SECTION_END + '\n';
  }
  const cleanedBody = stripPriorSections(body, originalBounds.start);
  const { start, end } = sectionBounds(cleanedBody);
  const section = dedupeReportMarkers(cleanedBody.slice(start + SECTION_START.length, end));
  const before = stripQualityMarkers(cleanedBody.slice(0, start)).replace(/\s*$/, '');
  const after = stripQualityMarkers(cleanedBody.slice(end + SECTION_END.length)).replace(/^\s*/, '');
  return before + SECTION_HEADER + SECTION_START + section + SECTION_END + (after ? '\n' + after : '\n');
}

function ensureSection(body) {
  return canonicalizeSection(body || '');
}

function upsertReport(body, marker, content) {
  body = ensureSection(body || '');
  content = (content || '').replace(/\s*$/, '');
  if (!content.startsWith(marker)) {
    content = marker + '\n' + content;
  }

  const startIdx = lineMarkerIndices(body, SECTION_START)[0] ?? -1;
  const endIdx = startIdx === -1 ? -1 : lineMarkerIndices(body, SECTION_END).find((i) => i > startIdx) ?? -1;
  if (startIdx === -1 || endIdx === -1) return body;

  const before = body.slice(0, startIdx + SECTION_START.length);
  let section = body.slice(startIdx + SECTION_START.length, endIdx);
  const after = body.slice(endIdx);

  const { start: markerIdx, end } = panelBounds(section, marker);
  if (markerIdx !== -1) {
    // Normalize the whitespace before this report to exactly one blank line.
    // Otherwise every update adds another newline and the PR body grows.
    const prefix = section.slice(0, markerIdx).replace(/\s*$/, '\n\n');
    const suffix = section.slice(end);
    section = prefix + content + '\n' + (suffix ? '\n' + suffix : '');
  } else {
    // Append before end of section, keeping a blank line separation
    section = section.replace(/\s*$/, '') + '\n\n' + content + '\n';
  }

  // Normalize spacing before section end marker
  section = section.replace(/\s*$/, '\n');
  return before + section + after;
}

async function updateReport({ github, context, core, owner, repo, pull_number, marker, content }) {
  owner = owner || context.repo.owner;
  repo = repo || context.repo.repo;
  const pullNumber = Number(pull_number);
  if (!pullNumber) {
    core.info('No pull request number provided; skipping PR body report update.');
    return { updated: false, reason: 'no_pr' };
  }

  for (let attempt = 0; attempt < 3; attempt++) {
    const { data: pr } = await github.rest.pulls.get({ owner, repo, pull_number: pullNumber });
    const currentBody = pr.body || '';
    const newBody = upsertReport(currentBody, marker, content);
    if (newBody === currentBody) {
      core.info(`PR #${pullNumber} body already up to date for ${marker}.`);
      return { updated: false, reason: 'unchanged' };
    }
    try {
      await github.rest.pulls.update({ owner, repo, pull_number: pullNumber, body: newBody });
      core.info(`Updated PR #${pullNumber} body report section for ${marker}.`);
      return { updated: true };
    } catch (err) {
      const status = err && err.status;
      if (attempt < 2 && (status === 409 || status === 422)) {
        core.info(`Retrying PR body update after conflict/error (${status})...`);
        continue;
      }
      throw err;
    }
  }
  return { updated: false, reason: 'retries_exhausted' };
}

async function updateReports({ github, context, core, owner, repo, pull_number, reports }) {
  // reports: [{marker, content}, ...] applied sequentially (each re-fetches body)
  let result = { updated: false };
  for (const r of (reports || [])) {
    result = await updateReport({ github, context, core, owner, repo, pull_number, marker: r.marker, content: r.content });
  }
  return result;
}

function sumLabel(body, label) {
  let total = 0;
  // Only trusted metric lines contribute. A failure bullet can quote label
  // text inline, but it does not start with a generated metric label.
  for (const rawLine of body.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!METRIC_LABELS.some((prefix) => line.startsWith(prefix))) continue;
    let pos = 0;
    while ((pos = line.indexOf(label, pos)) !== -1) {
      const start = pos + label.length;
      const value = line.slice(start).split('·')[0].trim();
      const n = Number.parseInt(value, 10);
      if (Number.isFinite(n)) total += n;
      pos = start;
    }
  }
  return total;
}

// Only generated report panels contribute to totals. A metric-looking line
// elsewhere in the body (or in an unrelated paragraph) is ignored.
function reportBlockText(section) {
  const markers = [];
  for (const marker of REPORT_MARKERS) {
    const at = lineMarkerIndices(section, marker)[0] ?? -1;
    if (at !== -1) markers.push({ marker, at });
  }
  markers.sort((a, b) => a.at - b.at);
  return markers
    .map((item, i) => section.slice(
      item.at,
      i + 1 < markers.length ? markers[i + 1].at : section.length,
    ))
    .join('\n');
}

function totalsContent(body) {
  const start = lineMarkerIndices(body, SECTION_START)[0] ?? -1;
  const end = start === -1 ? -1 : lineMarkerIndices(body, SECTION_END).find((i) => i > start) ?? -1;
  const section = start !== -1 && end !== -1 ? reportBlockText(body.slice(start, end)) : '';
  return [
    SUMMARY_MARKER,
    `**Quality total:** ${sumLabel(section, ISSUES_LABEL)} issues found · ` +
      `${sumLabel(section, ERRORS_LABEL)} analysis errors · ` +
      `${sumLabel(section, WARNINGS_LABEL)} analysis warnings`,
  ].join('\n');
}

async function refreshTotals({ github, context, core, owner, repo, pull_number }) {
  owner = owner || context.repo.owner;
  repo = repo || context.repo.repo;
  const pullNumber = Number(pull_number);
  if (!pullNumber) {
    core.info('No pull request number provided; skipping quality totals update.');
    return { updated: false, reason: 'no_pr' };
  }

  const { data: pr } = await github.rest.pulls.get({ owner, repo, pull_number: pullNumber });
  return updateReport({
    github, context, core, owner, repo, pull_number,
    marker: SUMMARY_MARKER,
    content: totalsContent(pr.body || ''),
  });
}

// Update a report and the aggregate totals in one API write. GitHub has no
// conditional PR update, so re-read immediately before writing and retry if
// another workflow changed the body in the meantime.
async function updateReportAndRefreshTotals({ github, context, core, owner, repo, pull_number, marker, content }) {
  owner = owner || context.repo.owner;
  repo = repo || context.repo.repo;
  const pullNumber = Number(pull_number);
  if (!pullNumber) {
    core.info('No pull request number provided; skipping PR body update.');
    return { updated: false, reason: 'no_pr' };
  }

  for (let attempt = 0; attempt < 3; attempt++) {
    const { data: pr } = await github.rest.pulls.get({ owner, repo, pull_number: pullNumber });
    const currentBody = pr.body || '';
    let newBody = upsertReport(currentBody, marker, content);
    newBody = upsertReport(newBody, SUMMARY_MARKER, totalsContent(newBody));

    const { data: latest } = await github.rest.pulls.get({ owner, repo, pull_number: pullNumber });
    if ((latest.body || '') !== currentBody) {
      core.info(`PR #${pullNumber} body changed during ${marker} update; reapplying.`);
      continue;
    }

    if (newBody === currentBody) {
      core.info(`PR #${pullNumber} body already up to date for ${marker}.`);
      return { updated: false, reason: 'unchanged' };
    }
    try {
      await github.rest.pulls.update({ owner, repo, pull_number: pullNumber, body: newBody });
      core.info(`Updated PR #${pullNumber} report ${marker} and quality totals.`);
      return { updated: true };
    } catch (err) {
      const status = err && err.status;
      if (attempt < 2 && (status === 409 || status === 422)) {
        core.info(`Retrying PR body update after conflict/error (${status})...`);
        continue;
      }
      throw err;
    }
  }
  return { updated: false, reason: 'retries_exhausted' };
}

module.exports = {
  SECTION_START,
  SECTION_END,
  SECTION_HEADER,
  LEGACY_SECTION_HEADER,
  REPORT_DEFS,
  REPORT_KEYS,
  REPORT_MARKERS,
  REPORT_HEADINGS,
  SUMMARY_MARKER,
  ISSUES_LABEL,
  CHANGED_LABEL,
  ERRORS_LABEL,
  WARNINGS_LABEL,
  ANALYZE_LABEL,
  METRIC_LABELS,
  lineMarkerIndices,
  panelBounds,
  unavailablePanel,
  ensureSection,
  sectionBounds,
  canonicalizeSection,
  reportBlockText,
  upsertReport,
  sumLabel,
  totalsContent,
  updateReport,
  updateReports,
  refreshTotals,
  updateReportAndRefreshTotals,
};
