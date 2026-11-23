const fs = require('fs');
const {
  SECTION_START, REPORT_DEFS, REPORT_KEYS,
  sectionBounds, reportBlockText, panelBounds, panelIsPending,
} = require('./update_pr_body_reports.js');

module.exports = async ({ github, context, core }) => {
  const pr = context.payload.pull_request;
  const { data: current } = await github.rest.pulls.get({
    owner: context.repo.owner, repo: context.repo.repo,
    pull_number: pr.number,
  });
  const body = current.body || '';
  const { start, end } = sectionBounds(body);
  const section = start !== -1 && end !== -1
    ? reportBlockText(body.slice(start + SECTION_START.length, end))
    : '';
  const markers = REPORT_KEYS.map((key) => REPORT_DEFS[key].marker);
  const needsBackfill = (marker) => {
    const bounds = panelBounds(section, marker, markers);
    if (bounds.start === -1) return true;
    return panelIsPending(section.slice(bounds.start, bounds.end));
  };

  const configs = REPORT_KEYS.map((key) => {
    const report = REPORT_DEFS[key];
    return {
      key, name: report.label, marker: report.marker,
      prefix: report.artifactPrefix, path: report.workflowPath,
    };
  });
  // Workflow display names come from the pushed branch and can be
  // changed by the PR. Resolve IDs from the base branch's workflow
  // paths so a renamed workflow cannot impersonate a trusted one.
  const workflows = await github.paginate(github.rest.actions.listRepoWorkflows, {
    owner: context.repo.owner, repo: context.repo.repo, per_page: 100,
  });
  const { data: runsData } = await github.rest.actions.listWorkflowRunsForRepo({
    owner: context.repo.owner, repo: context.repo.repo,
    head_sha: pr.head.sha, event: 'push', status: 'completed', per_page: 100,
  });
  const runs = runsData.workflow_runs || [];

  for (const cfg of configs) {
    const upper = cfg.key.toUpperCase();
    const wanted = needsBackfill(cfg.marker);
    core.setOutput(`${cfg.key}_needed`, wanted ? 'true' : 'false');
    if (!wanted) continue;
    const workflow = workflows.find((w) => w.path === cfg.path);
    const candidates = workflow
      ? runs.filter((r) => r.workflow_id === workflow.id)
      : [];
    if (!workflow) {
      core.info(`No base-branch workflow registered at ${cfg.path}.`);
    }
    // The API returns newest runs first. Prefer a successful run so
    // a newer failed re-run cannot hide valid artifacts from it.
    const run = candidates.find((r) => r.conclusion === 'success') || candidates[0];
    if (!run) {
      core.setOutput(`${cfg.key}_run`, '');
      core.setOutput(`${cfg.key}_artifacts`, 'false');
      core.info(`No completed push run of ${cfg.name} for ${pr.head.sha}.`);
      continue;
    }
    core.setOutput(`${cfg.key}_run`, run.id.toString());
    core.setOutput(`${cfg.key}_conclusion`, run.conclusion || '');
    const { data: artifacts } = await github.rest.actions.listWorkflowRunArtifacts({
      owner: context.repo.owner, repo: context.repo.repo,
      run_id: run.id, per_page: 100,
    });
    const names = (artifacts.artifacts || []).map((a) => a.name);
    const ok = names.some((n) => n.startsWith(cfg.prefix));
    core.setOutput(`${cfg.key}_artifacts`, ok ? 'true' : 'false');
    core.info(`${cfg.name} run ${run.id}: artifacts ${ok ? 'available' : 'missing'}.`);
  }

  const files = await github.paginate(github.rest.pulls.listFiles, {
    owner: context.repo.owner, repo: context.repo.repo,
    pull_number: pr.number, per_page: 100,
  });
  fs.writeFileSync('changed-files.txt', files.map((f) => f.filename).join('\n') + '\n');
  fs.writeFileSync('changed-files.json', JSON.stringify(files, null, 2));
  core.setOutput('pr_number', pr.number.toString());
  core.setOutput('base_sha', pr.base.sha || '');
  core.setOutput('head_sha', pr.head.sha || '');
  core.setOutput('base_ref', pr.base.ref || '');
};
