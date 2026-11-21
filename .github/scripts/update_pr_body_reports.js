const SECTION_START = '<!-- quality-reports:start -->';
const SECTION_END = '<!-- quality-reports:end -->';
const SECTION_HEADER = '\n\n---\n## 📊 Quality reports\n';
const LEGACY_SECTION_HEADER = '\n\n---\n## Quality reports\n';
const SUMMARY_MARKER = '<!-- quality-totals -->';
const REPORT_HEADINGS = {
  codeql: '### 🔍 CodeQL analysis',
  semgrep: '### 🛡️ Semgrep analysis',
  tests: '### 🧪 Tests',
};
const REPORT_MARKERS = [SUMMARY_MARKER, '<!-- codeql-report -->', '<!-- semgrep-report -->', '<!-- coverage-report -->'];
const ISSUES_LABEL = '**Whole-solution issues:** ';
const CHANGED_LABEL = '**In changed files:** ';
const ERRORS_LABEL = '**Analysis errors:** ';
const WARNINGS_LABEL = '**Analysis warnings:** ';
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
  const start = starts.length ? starts[starts.length - 1] : -1;
  const end = start === -1 ? -1 : ends.find((i) => i > start) ?? -1;
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

  const markerIdx = lineMarkerIndices(section, marker)[0] ?? -1;
  if (markerIdx !== -1) {
    let end = section.length;
    for (const m of REPORT_MARKERS) {
      if (m === marker) continue;
      const idx = lineMarkerIndices(section, m).find((i) => i > markerIdx + marker.length) ?? -1;
      if (idx !== -1 && idx < end) end = idx;
    }
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
  REPORT_HEADINGS,
  SUMMARY_MARKER,
  lineMarkerIndices,
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
