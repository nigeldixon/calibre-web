const {
  updateReports, SECTION_START, REPORT_HEADINGS, sectionBounds,
  lineMarkerIndices, reportBlockText,
} = require('./update_pr_body_reports.js');

module.exports = async ({ github, context, core }) => {
  const placeholders = [
    {
      marker: '<!-- codeql-report -->',
      content: [
        '<!-- codeql-report -->',
        REPORT_HEADINGS.codeql,
        '',
        '⏳ CodeQL analysis is queued/running. Results will appear here when the workflow completes.',
      ].join('\n'),
    },
    {
      marker: '<!-- semgrep-report -->',
      content: [
        '<!-- semgrep-report -->',
        REPORT_HEADINGS.semgrep,
        '',
        '⏳ Semgrep analysis is queued/running. Results will appear here when the workflow completes.',
      ].join('\n'),
    },
    {
      marker: '<!-- coverage-report -->',
      content: [
        '<!-- coverage-report -->',
        REPORT_HEADINGS.tests,
        '',
        '⏳ Tests are queued/running. Coverage and test results will appear here when the workflow completes.',
      ].join('\n'),
    },
  ];

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
  const markers = placeholders.map((r) => r.marker);
  const todo = placeholders.filter((r) => {
    const idx = lineMarkerIndices(section, r.marker)[0] ?? -1;
    if (idx === -1) return true;
    let next = section.length;
    for (const m of markers) {
      if (m === r.marker) continue;
      const at = lineMarkerIndices(section, m).find((i) => i > idx) ?? -1;
      if (at !== -1 && at < next) next = at;
    }
    return section.slice(idx, next).includes('⏳');
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
