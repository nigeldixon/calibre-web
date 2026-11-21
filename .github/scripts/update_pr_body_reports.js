const SECTION_START = '<!-- quality-reports:start -->';
const SECTION_END = '<!-- quality-reports:end -->';
const SECTION_HEADER = '\n\n---\n## Quality reports\n';
const SUMMARY_MARKER = '<!-- quality-totals -->';
const REPORT_MARKERS = [SUMMARY_MARKER, '<!-- codeql-report -->', '<!-- semgrep-report -->', '<!-- coverage-report -->'];
const ISSUES_LABEL = '**Whole-solution issues:** ';
const ERRORS_LABEL = '**Analysis errors:** ';
const WARNINGS_LABEL = '**Analysis warnings:** ';

function ensureSection(body) {
  body = body || '';
  if (body.includes(SECTION_START) && body.includes(SECTION_END)) return body;
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

  const startIdx = body.indexOf(SECTION_START);
  const endIdx = body.indexOf(SECTION_END, startIdx);
  if (startIdx === -1 || endIdx === -1) return body;

  const before = body.slice(0, startIdx + SECTION_START.length);
  let section = body.slice(startIdx + SECTION_START.length, endIdx);
  const after = body.slice(endIdx);

  const replacement = '\n' + content + '\n';
  const markerIdx = section.indexOf(marker);
  if (markerIdx !== -1) {
    let end = section.length;
    for (const m of REPORT_MARKERS) {
      if (m === marker) continue;
      const idx = section.indexOf(m, markerIdx + marker.length);
      if (idx !== -1 && idx < end) end = idx;
    }
    section = section.slice(0, markerIdx) + replacement + section.slice(end);
  } else {
    // Append before end of section, keeping a blank line separation if needed
    section = section.replace(/\s*$/, '') + '\n' + replacement;
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
  let pos = 0;
  while ((pos = body.indexOf(label, pos)) !== -1) {
    const start = pos + label.length;
    const end = body.indexOf('\n', start);
    const text = body.slice(start, end === -1 ? body.length : end).trim();
    const n = Number.parseInt(text, 10);
    if (Number.isFinite(n)) total += n;
    pos = start;
  }
  return total;
}

function totalsContent(body) {
  return [
    SUMMARY_MARKER,
    `**Quality total:** ${sumLabel(body, ISSUES_LABEL)} issues found · ` +
      `${sumLabel(body, ERRORS_LABEL)} analysis errors · ` +
      `${sumLabel(body, WARNINGS_LABEL)} analysis warnings`,
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

// Update a report and the aggregate totals in one API write, so concurrent
// workflows cannot leave a stale summary behind.
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
  updateReport,
  updateReports,
  refreshTotals,
  updateReportAndRefreshTotals,
};
