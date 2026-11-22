const {
  updateReportAndRefreshTotals, SECTION_START, SUMMARY_MARKER,
  REPORT_HEADINGS, sectionBounds, lineMarkerIndices, reportBlockText,
} = require('./update_pr_body_reports.js');

module.exports = async ({ github, context, core }) => {
  const { owner, repo } = context.repo;
  const reports = [
    { marker: '<!-- codeql-report -->', heading: REPORT_HEADINGS.codeql, labels: true },
    { marker: '<!-- semgrep-report -->', heading: REPORT_HEADINGS.semgrep, labels: true },
    { marker: '<!-- coverage-report -->', heading: REPORT_HEADINGS.tests, labels: false },
  ];
  const allMarkers = [SUMMARY_MARKER, ...reports.map((r) => r.marker)];
  const pulls = await github.paginate(github.rest.pulls.list, {
    owner, repo, state: 'open', per_page: 100, sort: 'updated',
  });

  for (const pr of pulls) {
    const { data: current } = await github.rest.pulls.get({
      owner, repo, pull_number: pr.number,
    });
    const body = current.body || '';
    const { start, end } = sectionBounds(body);
    const section = start === -1 || end === -1 ? ''
      : reportBlockText(body.slice(start + SECTION_START.length, end));
    if (!section) continue;

    const active = await github.rest.actions.listWorkflowRunsForRepo({
      owner, repo, head_sha: pr.head.sha, status: 'in_progress', per_page: 1,
    });
    if ((active.data.workflow_runs || []).length) {
      core.info(`PR #${pr.number} still has running workflows; skipping.`);
      continue;
    }

    for (const report of reports) {
      const idx = lineMarkerIndices(section, report.marker)[0] ?? -1;
      if (idx === -1) continue;
      let next = section.length;
      for (const marker of allMarkers) {
        if (marker === report.marker) continue;
        const at = lineMarkerIndices(section, marker).find((i) => i > idx) ?? -1;
        if (at !== -1 && at < next) next = at;
      }
      if (!section.slice(idx, next).includes('⏳')) continue;

      const parts = [
        report.marker,
        report.heading,
        '',
        `❌ Report unavailable: no analysis completed for commit ${pr.head.sha}. Re-run the workflow or push a new commit.`,
      ];
      if (report.labels) {
        parts.push(
          '',
          '**Whole-solution issues:** unknown · **In changed files:** unknown',
          '**Analysis errors:** unknown · **Analysis warnings:** unknown',
          '',
          '**Analyze job:** `failure`',
        );
      }
      await updateReportAndRefreshTotals({
        github, context, core,
        pull_number: pr.number,
        marker: report.marker,
        content: parts.join('\n'),
      });
      core.warning(`Replaced stale placeholder in PR #${pr.number}: ${report.marker}`);
    }
  }
};
