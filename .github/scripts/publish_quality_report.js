const fs = require('fs');
const {
  updateReportAndRefreshTotals, REPORT_DEFS, unavailablePanel,
} = require('./update_pr_body_reports.js');

module.exports = async ({ github, context, core }) => {
  const wr = context.payload.workflow_run;
  const report = REPORT_DEFS[process.env.REPORT_KEY];
  if (!report) {
    core.info(`No report mapping for key ${process.env.REPORT_KEY || '(none)'}; skipping.`);
    return;
  }

  const runUrl = wr.html_url || `${process.env.GITHUB_SERVER_URL}/${process.env.GITHUB_REPOSITORY}/actions/runs/${wr.id}`;
  const trusted = process.env.TRUSTED === 'true';
  const modified = process.env.MODIFIED || '';
  let content = '';

  if (fs.existsSync(report.file) && fs.statSync(report.file).size > 0) {
    content = fs.readFileSync(report.file, 'utf8');
    core.info(`Using regenerated ${report.file} from workflow run ${wr.id}.`);
  } else {
    const reason = trusted
      ? `${report.label} workflow did not produce usable artifacts.`
      : `this PR modified verification-sensitive files (${modified || 'unknown'}), so its artifacts cannot be trusted.`;
    content = unavailablePanel({
      marker: report.marker,
      heading: report.heading,
      labels: report.labels,
      message: `${reason} See [workflow run](${runUrl}).`,
    });
    core.warning(`Publishing fallback for ${report.marker}: ${reason}`);
  }

  await updateReportAndRefreshTotals({
    github, context, core,
    pull_number: Number(process.env.PR_NUMBER),
    marker: report.marker,
    content,
  });
};
