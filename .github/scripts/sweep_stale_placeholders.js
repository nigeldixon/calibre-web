const {
  updateReportAndRefreshTotals, SECTION_START, REPORT_DEFS, REPORT_KEYS,
  REPORT_MARKERS, sectionBounds, reportBlockText, panelBounds, panelIsPending,
  unavailablePanel,
} = require('./update_pr_body_reports.js');

module.exports = async ({ github, context, core }) => {
  const { owner, repo } = context.repo;
  const reports = REPORT_KEYS.map((key) => REPORT_DEFS[key]);
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
      owner, repo, head_sha: pr.head.sha, per_page: 100,
    });
    const pending = (active.data.workflow_runs || []).find((run) => run.status !== 'completed');
    if (pending) {
      core.info(`PR #${pr.number} still has ${pending.status || 'active'} workflows; skipping.`);
      continue;
    }

    // Backfill runs are triggered by pull_request_target, so their head_sha is the
    // base branch, not the PR head. Check them separately to avoid failing pending
    // placeholders while a backfill is still generating reports.
    const backfills = await github.rest.actions.listWorkflowRuns({
      owner, repo, workflow_id: 'quality_reports.yml',
      event: 'pull_request_target', per_page: 100,
    });
    const pendingBackfill = (backfills.data.workflow_runs || []).find((run) => {
      if (run.status === 'completed') return false;
      const pulls = Array.isArray(run.pull_requests) ? run.pull_requests : [];
      return pulls.length === 0 || pulls.some((p) => p.number === pr.number);
    });
    if (pendingBackfill) {
      core.info(`PR #${pr.number} still has ${pendingBackfill.status || 'active'} backfill; skipping.`);
      continue;
    }

    for (const report of reports) {
      const bounds = panelBounds(section, report.marker, REPORT_MARKERS);
      if (bounds.start === -1) continue;
      if (!panelIsPending(section.slice(bounds.start, bounds.end))) continue;

      const content = unavailablePanel({
        marker: report.marker,
        heading: report.heading,
        labels: report.labels,
        message: `no analysis completed for commit ${pr.head.sha}. Re-run the workflow or push a new commit.`,
      });
      await updateReportAndRefreshTotals({
        github, context, core,
        pull_number: pr.number,
        marker: report.marker,
        content,
      });
      core.warning(`Replaced stale placeholder in PR #${pr.number}: ${report.marker}`);
    }
  }
};
