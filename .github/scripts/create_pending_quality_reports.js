const {
  updateReports, SECTION_START, REPORT_DEFS, REPORT_KEYS,
  sectionBounds, reportBlockText, panelBounds, panelIsPending,
} = require('./update_pr_body_reports.js');

const PENDING_MESSAGES = {
  codeql: '⏳ CodeQL analysis is queued/running. Results will appear here when the workflow completes.',
  semgrep: '⏳ Semgrep analysis is queued/running. Results will appear here when the workflow completes.',
  tests: '⏳ Tests are queued/running. Coverage and test results will appear here when the workflow completes.',
};

module.exports = async ({ github, context, core }) => {
  const placeholders = REPORT_KEYS.map((key) => {
    const report = REPORT_DEFS[key];
    return {
      marker: report.marker,
      content: [report.marker, report.heading, '', PENDING_MESSAGES[key]].join('\n'),
    };
  });
  const markers = placeholders.map((r) => r.marker);

  // Backfill runs in another workflow and may win the race. Never
  // overwrite a panel that already holds real results.
  const { data: pr } = await github.rest.pulls.get({
    owner: context.repo.owner, repo: context.repo.repo,
    pull_number: Number(process.env.PR_NUMBER),
  });
  const body = pr.body || '';
  const { start, end } = sectionBounds(body);
  const section = start !== -1 && end !== -1
    ? reportBlockText(body.slice(start + SECTION_START.length, end))
    : '';
  const todo = placeholders.filter((r) => {
    const bounds = panelBounds(section, r.marker, markers);
    if (bounds.start === -1) return true;
    return panelIsPending(section.slice(bounds.start, bounds.end));
  });

  if (todo.length) {
    await updateReports({
      github, context, core,
      pull_number: Number(process.env.PR_NUMBER),
      reports: todo,
    });
  } else {
    core.info('Quality reports already present; skipping placeholders.');
  }
};
