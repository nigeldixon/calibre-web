const fs = require('fs');
const { updateReportAndRefreshTotals, REPORT_HEADINGS } = require('./update_pr_body_reports.js');

module.exports = async ({ github, context, core }) => {
  const wr = context.payload.workflow_run;
  const reports = {
    codeql: {
      file: 'codeql-report.md',
      marker: '<!-- codeql-report -->',
      heading: REPORT_HEADINGS.codeql,
      label: 'CodeQL',
      labels: true,
    },
    semgrep: {
      file: 'semgrep-report.md',
      marker: '<!-- semgrep-report -->',
      heading: REPORT_HEADINGS.semgrep,
      label: 'Semgrep',
      labels: true,
    },
    tests: {
      file: 'coverage-report.md',
      marker: '<!-- coverage-report -->',
      heading: REPORT_HEADINGS.tests,
      label: 'Tests',
      labels: false,
    },
  };
  const report = reports[process.env.REPORT_KEY];
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
    const parts = [
      report.marker,
      report.heading,
      '',
      `❌ Report unavailable: ${reason} See [workflow run](${runUrl}).`,
    ];
    if (report.labels) {
      parts.push(
        '',
        '**Whole-solution issues:** unknown · **In changed files:** unknown',
        '**Analysis errors:** unknown · **Analysis warnings:** unknown',
        '',
        '**Analyze job:** `failure`'
      );
    }
    content = parts.join('\n');
    core.warning(`Publishing fallback for ${report.marker}: ${reason}`);
  }

  await updateReportAndRefreshTotals({
    github, context, core,
    pull_number: Number(process.env.PR_NUMBER),
    marker: report.marker,
    content,
  });
};
