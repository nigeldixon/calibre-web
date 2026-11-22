const fs = require('fs');
const { updateReportAndRefreshTotals, REPORT_HEADINGS } = require('./update_pr_body_reports.js');

module.exports = async ({ github, context, core }) => {
  const pr = context.payload.pull_request;
  const reports = [
    { key: 'codeql', marker: '<!-- codeql-report -->', file: 'codeql-report.md', heading: REPORT_HEADINGS.codeql, labels: true },
    { key: 'semgrep', marker: '<!-- semgrep-report -->', file: 'semgrep-report.md', heading: REPORT_HEADINGS.semgrep, labels: true },
    { key: 'tests', marker: '<!-- coverage-report -->', file: 'coverage-report.md', heading: REPORT_HEADINGS.tests, labels: false },
  ];

  for (const r of reports) {
    const upper = r.key.toUpperCase();
    if (process.env[`${upper}_NEEDED`] !== 'true') continue;
    const runId = process.env[`${upper}_RUN`] || '';
    const runUrl = runId
      ? `${process.env.GITHUB_SERVER_URL}/${process.env.GITHUB_REPOSITORY}/actions/runs/${runId}`
      : pr.html_url;
    let content = '';
    if (fs.existsSync(r.file) && fs.statSync(r.file).size > 0) {
      content = fs.readFileSync(r.file, 'utf8');
      core.info(`Using regenerated ${r.file} for ${r.key}.`);
    } else if (runId) {
      // Artifacts expired or the run failed before uploading them.
      // Re-run once; the normal publisher will write the real report.
      await github.rest.actions.reRunWorkflow({
        owner: context.repo.owner, repo: context.repo.repo,
        run_id: Number(runId),
      });
      core.warning(`Re-running ${r.key} push run ${runId}: no usable artifacts.`);
      continue;
    } else {
      const parts = [
        r.marker,
        r.heading,
        '',
        `❌ Report unavailable: no completed push run of ${r.key} exists for commit ${pr.head.sha}. See [pull request](${runUrl}).`,
      ];
      if (r.labels) {
        parts.push(
          '',
          '**Whole-solution issues:** unknown · **In changed files:** unknown',
          '**Analysis errors:** unknown · **Analysis warnings:** unknown',
          '',
          '**Analyze job:** `failure`'
        );
      }
      content = parts.join('\n');
      core.warning(`No push run found for ${r.key} at ${pr.head.sha}.`);
    }
    await updateReportAndRefreshTotals({
      github, context, core,
      pull_number: Number(process.env.PR_NUMBER),
      marker: r.marker,
      content,
    });
  }
};
