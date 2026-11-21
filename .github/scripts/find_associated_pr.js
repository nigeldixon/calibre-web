module.exports = async ({ github, context, core }) => {
  const { owner, repo } = context.repo;

  if (context.eventName === 'pull_request' || context.eventName === 'pull_request_target') {
    const pr = context.payload.pull_request;
    if (pr) {
      core.setOutput('number', pr.number.toString());
      core.setOutput('base', pr.base.ref || 'develop');
      core.setOutput('base_ref', pr.base.ref || '');
      core.setOutput('base_sha', pr.base.sha || '');
      core.setOutput('head_sha', pr.head.sha || '');
      return;
    }
  }

  if (context.eventName !== 'push') {
    core.setOutput('number', '');
    core.setOutput('base', 'develop');
    core.setOutput('base_ref', '');
    core.setOutput('base_sha', '');
    core.setOutput('head_sha', '');
    return;
  }

  const branch = context.ref.replace('refs/heads/', '');
  const head = `${owner}:${branch}`;
  const pulls = await github.rest.pulls.list({ owner, repo, head, state: 'open', per_page: 100 });
  if (pulls.data.length > 0) {
    const pr = pulls.data.find((p) => p.head.sha === context.sha) || pulls.data[0];
    core.setOutput('number', pr.number.toString());
    core.setOutput('base', pr.base.ref || 'develop');
    core.setOutput('base_ref', pr.base.ref || '');
    core.setOutput('base_sha', pr.base.sha || '');
    core.setOutput('head_sha', pr.head.sha || '');
  } else {
    core.setOutput('number', '');
    core.setOutput('base', 'develop');
    core.setOutput('base_ref', '');
    core.setOutput('base_sha', '');
    core.setOutput('head_sha', '');
  }
};
