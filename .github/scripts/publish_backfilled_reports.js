const fs = require('fs');
const {
  updateReportAndRefreshTotals, REPORT_DEFS, REPORT_KEYS, unavailablePanel,
} = require('./update_pr_body_reports.js');

module.exports = async ({ github, context, core }) => {
  const pr = context.payload.pull_request;

  for (const key of REPORT_KEYS) {
    const report = REPORT_DEFS[key];
    const upper = key.toUpperCase();
    if (process.env[`${upper}_NEEDED`] !== 'true') continue;
    const runId = process.env[`${upper}_RUN`] || '';
    const runUrl = runId
      ? `${process.env.GITHUB_SERVER_URL}/${process.env.GITHUB_REPOSITORY}/actions/runs/${runId}`
      : pr.html_url;
    let content = '';
    if (fs.existsSync(report.file) && fs.statSync(report.file).size > 0) {
      content = fs.readFileSync(report.file, 'utf8');
      core.info(`Using regenerated ${report.file} for ${key}.`);
    } else if (runId) {
      // Artifacts expired or the run failed before uploading them.
      // Re-run once; the normal publisher will write the real report.
      await github.rest.actions.reRunWorkflow({
        owner: context.repo.owner, repo: context.repo.repo,
        run_id: Number(runId),
      });
      core.warning(`Re-running ${key} push run ${runId}: no usable artifacts.`);
      continue;
    } else {
      content = unavailablePanel({
        marker: report.marker,
        heading: report.heading,
        labels: report.labels,
        message: `no completed push run of ${key} exists for commit ${pr.head.sha}. See [pull request](${runUrl}).`,
      });
      core.warning(`No push run found for ${key} at ${pr.head.sha}.`);
    }
    await updateReportAndRefreshTotals({
      github, context, core,
      pull_number: Number(process.env.PR_NUMBER),
      marker: report.marker,
      content,
    });
  }
};
