const fs = require('fs');
const { REPORT_DEFS, REPORT_KEYS } = require('./update_pr_body_reports.js');

module.exports = async ({ github, context, core }) => {
  const wr = context.payload.workflow_run;
  if (wr.conclusion === 'cancelled' || wr.conclusion === 'skipped') {
    core.setOutput('number', '');
    core.setOutput('trusted', 'false');
    core.info(`Ignoring ${wr.name} run ${wr.id} with conclusion ${wr.conclusion}.`);
    return;
  }
  let pr = null;

  if ((wr.pull_requests || []).length) {
    const number = wr.pull_requests[0].number;
    pr = (await github.rest.pulls.get({
      owner: context.repo.owner, repo: context.repo.repo, pull_number: number,
    })).data;
  } else if (wr.head_repository?.owner?.login && wr.head_branch) {
    const head = `${wr.head_repository.owner.login}:${wr.head_branch}`;
    const { data: pulls } = await github.rest.pulls.list({
      owner: context.repo.owner, repo: context.repo.repo,
      head, state: 'open', per_page: 100,
    });
    const match = pulls.find((p) => p.head.sha === wr.head_sha) || pulls[0];
    if (match) {
      pr = (await github.rest.pulls.get({
        owner: context.repo.owner, repo: context.repo.repo, pull_number: match.number,
      })).data;
    }
  }

  if (!pr) {
    core.setOutput('number', '');
    core.setOutput('trusted', 'false');
    core.info(`No open PR found for ${wr.name} run ${wr.id}; skipping.`);
    return;
  }

  if (pr.head.sha !== wr.head_sha) {
    core.setOutput('number', '');
    core.setOutput('trusted', 'false');
    core.info(`PR #${pr.number} head ${pr.head.sha} does not match run ${wr.head_sha}; skipping stale report.`);
    return;
  }

  const fromFork = pr.head.repo && pr.base.repo &&
    pr.head.repo.full_name !== pr.base.repo.full_name;
  if (!fromFork) {
    core.setOutput('number', '');
    core.setOutput('trusted', 'false');
    core.info(`PR #${pr.number} is not from a different repository; push workflow publishes its reports.`);
    return;
  }

  // Identify the run by its workflow file path, not by wr.name:
  // the name comes from the PR-controlled workflow and can be
  // changed to impersonate a trusted workflow.
  const workflowConfigs = {};
  for (const key of REPORT_KEYS) {
    const report = REPORT_DEFS[key];
    workflowConfigs[report.workflowPath] = { key, required: report.requiredFiles };
  }
  const config = workflowConfigs[wr.path || ''];
  if (!config) {
    core.setOutput('number', '');
    core.setOutput('trusted', 'false');
    core.info(`No trusted workflow mapping for path ${wr.path || '(none)'}; skipping.`);
    return;
  }
  core.setOutput('report_key', config.key);

  core.setOutput('number', pr.number.toString());
  core.setOutput('base_sha', pr.base.sha || '');
  core.setOutput('head_sha', wr.head_sha || '');
  core.setOutput('base_ref', pr.base.ref || '');

  const files = await github.paginate(github.rest.pulls.listFiles, {
    owner: context.repo.owner, repo: context.repo.repo,
    pull_number: pr.number, per_page: 100,
  });
  fs.writeFileSync('changed-files.txt', files.map((f) => f.filename).join('\n') + '\n');
  fs.writeFileSync('changed-files.json', JSON.stringify(files, null, 2));

  const requiredFiles = config.required;
  // Compare against the PR's own base commit, not the repository's
  // default branch: a PR may legitimately target another branch, and
  // comparing to the default branch would report files as "modified"
  // when the PR never touched them.
  const baseRef = pr.base.sha;

  async function fileAt(ref, path) {
    try {
      const { data } = await github.rest.repos.getContent({
        owner: context.repo.owner, repo: context.repo.repo, path, ref,
      });
      return data.type === 'file' ? data.content : null;
    } catch (err) {
      return null;
    }
  }

  const modified = [];
  for (const path of requiredFiles) {
    const atHead = await fileAt(wr.head_sha, path);
    const atBase = await fileAt(baseRef, path);
    if (atHead !== atBase) modified.push(path);
  }

  if (modified.length) {
    core.warning(`PR modifies analysis files: ${modified.join(', ')}; artifacts will not be trusted.`);
    core.setOutput('trusted', 'false');
    core.setOutput('modified', modified.join(', '));
  } else {
    core.setOutput('trusted', 'true');
    core.setOutput('modified', '');
  }
};
