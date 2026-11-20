const SECTION_START = '<!-- quality-reports:start -->';
const SECTION_END = '<!-- quality-reports:end -->';
const SECTION_HEADER = '\n\n---\n## Quality reports\n';
const REPORT_MARKERS = ['<!-- codeql-report -->', '<!-- semgrep-report -->', '<!-- coverage-report -->'];

function ensureSection(body) {
  body = body || '';
  if (body.includes(SECTION_START) && body.includes(SECTION_END)) return body;
  let out = body.replace(/\s*$/, '');
  out += SECTION_HEADER + SECTION_START + '\n' + SECTION_END + '\n';
  return out;
}

function escapeRegExp(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
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

  const otherMarkers = REPORT_MARKERS.filter(m => m !== marker).map(escapeRegExp).join('|');
  // Match up to the next report marker or the end of the managed section content.
  const endBoundary = otherMarkers ? '(?=' + otherMarkers + '|$)' : '(?=$)';
  const re = new RegExp(escapeRegExp(marker) + '[\\s\\S]*?' + endBoundary);

  const replacement = '\n' + content + '\n';
  if (re.test(section)) {
    section = section.replace(re, replacement);
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

module.exports = {
  SECTION_START,
  SECTION_END,
  ensureSection,
  upsertReport,
  updateReport,
  updateReports,
};
