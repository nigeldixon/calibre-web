const SECTION_START = '<!-- quality-reports:start -->';
const SECTION_END = '<!-- quality-reports:end -->';
const SECTION_HEADER = '\n\n---\n## Quality reports\n';
const SUMMARY_MARKER = '<!-- quality-totals -->';
const REPORT_MARKERS = [SUMMARY_MARKER, '<!-- codeql-report -->', '<!-- semgrep-report -->', '<!-- coverage-report -->'];
const ISSUES_LABEL = '**Whole-solution issues:** ';
const ERRORS_LABEL = '**Analysis errors:** ';
const WARNINGS_LABEL = '**Analysis warnings:** ';

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

function ensureSection(body) {
  body = body || '';
  if (lineMarkerIndices(body, SECTION_START).length && lineMarkerIndices(body, SECTION_END).length) return body;
  let out = body.replace(/\s*$/, '');
  out += SECTION_HEADER + SECTION_START + '\n' + SECTION_END + '\n';
  return out;
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
  // Only generated lines beginning with the label contribute to the totals;
  // quoted text inside an expandable failure detail cannot inflate them.
  for (const line of body.split(/\r?\n/)) {
    const text = line.trim();
    if (!text.startsWith(label)) continue;
    const n = Number.parseInt(text.slice(label.length).trim(), 10);
    if (Number.isFinite(n)) total += n;
  }
  return total;
}

function totalsContent(body) {
  const start = lineMarkerIndices(body, SECTION_START)[0] ?? -1;
  const end = start === -1 ? -1 : lineMarkerIndices(body, SECTION_END).find((i) => i > start) ?? -1;
  const section = start !== -1 && end !== -1 ? body.slice(start, end) : '';
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
  SUMMARY_MARKER,
  ensureSection,
  upsertReport,
  sumLabel,
  totalsContent,
  updateReport,
  updateReports,
  refreshTotals,
  updateReportAndRefreshTotals,
};
