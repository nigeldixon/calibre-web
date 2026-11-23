// Behavior tests for the workflow modules extracted from github-script steps.
// Run through node:test by test_javascript_suite.py; stubs stand in for GitHub
// API so assertions cover the payloads each module writes, not just wiring.
const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const ROOT = path.resolve(__dirname, '../../..');
const SCRIPTS = path.join(ROOT, '.github', 'scripts');
const shared = require(path.join(SCRIPTS, 'update_pr_body_reports.js'));
const { REPORT_DEFS, REPORT_KEYS } = shared;
const moduleFor = (name) => require(path.join(SCRIPTS, `${name}.js`));

const { test } = require('node:test');

function makeCore() {
  const outputs = {};
  const info = [];
  const warning = [];
  return {
    outputs,
    info,
    warning,
    core: {
      setOutput(key, value) { outputs[key] = String(value); },
      info(message) { info.push(message); },
      warning(message) { warning.push(message); },
    },
  };
}

const DEFAULTS = {
  'pulls.get': () => ({ data: { body: '', number: 7, html_url: 'https://pr/7' } }),
  'pulls.list': () => ({ data: [] }),
  'pulls.listFiles': () => [],
  'pulls.update': () => ({}),
  'actions.listRepoWorkflows': () => [],
  'actions.listWorkflowRunsForRepo': () => ({ data: { workflow_runs: [] } }),
  'actions.listWorkflowRuns': () => ({ data: { workflow_runs: [] } }),
  'actions.listWorkflowRunArtifacts': () => ({ data: { artifacts: [] } }),
  'actions.reRunWorkflow': () => ({}),
};

function githubStub(handlers = {}) {
  const calls = [];
  const invoke = (name) => async (params) => {
    calls.push({ name, params });
    return (handlers[name] || DEFAULTS[name])(params);
  };
  const github = {
    rest: {
      pulls: {
        get: invoke('pulls.get'),
        list: invoke('pulls.list'),
        listFiles: invoke('pulls.listFiles'),
        update: invoke('pulls.update'),
      },
      actions: {
        listRepoWorkflows: invoke('actions.listRepoWorkflows'),
        listWorkflowRunsForRepo: invoke('actions.listWorkflowRunsForRepo'),
        listWorkflowRuns: invoke('actions.listWorkflowRuns'),
        listWorkflowRunArtifacts: invoke('actions.listWorkflowRunArtifacts'),
        reRunWorkflow: invoke('actions.reRunWorkflow'),
      },
    },
    paginate: async (fn, params) => fn(params),
  };
  return { github, calls };
}

function bodyGithub(initialBody, overrides = {}) {
  let body = initialBody;
  const stub = githubStub({
    'pulls.get': () => ({ data: { body, number: 7, html_url: 'https://pr/7', head: { sha: 'head-sha' } } }),
    'pulls.update': ({ body: next }) => { body = next; return {}; },
    ...overrides,
  });
  return { ...stub, currentBody: () => body };
}

const contextFor = (payload) => ({ repo: { owner: 'acme', repo: 'web' }, payload });

function pullRequest(overrides = {}) {
  return {
    number: 7,
    html_url: 'https://pr/7',
    head: { sha: 'head-sha', repo: { full_name: 'fork/web' } },
    base: { sha: 'base-sha', ref: 'develop', repo: { full_name: 'acme/web' } },
    body: '',
    ...overrides,
  };
}

async function withEnv(values, fn) {
  const previous = {};
  for (const [key, value] of Object.entries(values)) {
    previous[key] = process.env[key];
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
  }
  try {
    return await fn();
  } finally {
    for (const [key, value] of Object.entries(previous)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  }
}

async function inTempDir(fn) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'cw-wf-module-'));
  const previous = process.cwd();
  try {
    process.chdir(dir);
    return await fn(dir);
  } finally {
    process.chdir(previous);
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

const panel = (marker, text) => [marker, text].join('\n');

function bodyWithPanels(texts) {
  let body = '';
  for (const [key, text] of Object.entries(texts)) {
    body = shared.upsertReport(body, REPORT_DEFS[key].marker, text);
  }
  return body;
}

// resolve_workflow_run_pr

test('resolve_workflow_run_pr ignores cancelled runs', async () => {
  const { core, outputs } = makeCore();
  const { github, calls } = githubStub();
  const context = contextFor({ workflow_run: { conclusion: 'cancelled', name: 'Tests', id: 1 } });
  await moduleFor('resolve_workflow_run_pr')({ github, context, core });
  assert.strictEqual(outputs.number, '');
  assert.strictEqual(outputs.trusted, 'false');
  assert.strictEqual(calls.filter((c) => c.name === 'pulls.get').length, 0);
});

test('resolve_workflow_run_pr trusts an unmodified fork workflow', async () => {
  const { core, outputs } = makeCore();
  const pr = pullRequest();
  const { github } = githubStub({
    'pulls.get': () => ({ data: pr }),
    'pulls.listFiles': () => [{ filename: 'cps/a.py' }],
    'repos.getContent': () => ({ data: { type: 'file', content: 'same' } }),
  });
  github.rest.repos = { getContent: github.rest.pulls.get };
  // The real module uses repos.getContent; route it through the stub recorder.
  const calls = [];
  github.rest.repos.getContent = async (params) => {
    calls.push(params);
    return { data: { type: 'file', content: 'same' } };
  };
  const context = contextFor({
    workflow_run: {
      conclusion: 'success', id: 42, head_sha: 'head-sha',
      path: REPORT_DEFS.codeql.workflowPath,
      pull_requests: [{ number: 7 }],
    },
  });
  await inTempDir(async (dir) => {
    await moduleFor('resolve_workflow_run_pr')({ github, context, core });
    assert.strictEqual(outputs.number, '7');
    assert.strictEqual(outputs.trusted, 'true');
    assert.strictEqual(outputs.report_key, 'codeql');
    assert.strictEqual(outputs.base_sha, 'base-sha');
    assert.strictEqual(outputs.head_sha, 'head-sha');
    assert.strictEqual(outputs.base_ref, 'develop');
    assert.strictEqual(fs.readFileSync(path.join(dir, 'changed-files.txt'), 'utf8'), 'cps/a.py\n');
    assert.deepStrictEqual(JSON.parse(fs.readFileSync(path.join(dir, 'changed-files.json'), 'utf8')), [{ filename: 'cps/a.py' }]);
  });
  assert.ok(calls.length > 0);
});

test('resolve_workflow_run_pr marks modified required files untrusted', async () => {
  const { core, outputs, warning } = makeCore();
  const pr = pullRequest();
  const { github } = githubStub({
    'pulls.get': () => ({ data: pr }),
    'pulls.listFiles': () => [],
  });
  const checked = [];
  github.rest.repos = {
    getContent: async ({ path, ref }) => {
      checked.push(ref);
      const changed = path === REPORT_DEFS.codeql.workflowPath && ref === 'head-sha';
      return { data: { type: 'file', content: changed ? 'changed' : 'original' } };
    },
  };
  const context = contextFor({
    workflow_run: {
      conclusion: 'success', id: 43, head_sha: 'head-sha',
      path: REPORT_DEFS.codeql.workflowPath,
      pull_requests: [{ number: 7 }],
    },
  });
  await inTempDir(async () => {
    await moduleFor('resolve_workflow_run_pr')({ github, context, core });
  });
  assert.strictEqual(outputs.number, '7');
  assert.strictEqual(outputs.trusted, 'false');
  assert.strictEqual(outputs.modified, '.github/workflows/codeql.yml');
  assert.ok(checked.includes('base-sha'));
  assert.ok(warning.some((m) => m.includes('artifacts will not be trusted')));
});

test('resolve_workflow_run_pr distrusts modified analysis inputs', async () => {
  for (const key of REPORT_KEYS) {
    for (const changedPath of REPORT_DEFS[key].requiredFiles) {
      const { core, outputs } = makeCore();
      const { github } = githubStub({
        'pulls.get': () => ({ data: pullRequest() }),
      });
      const checked = [];
      github.rest.repos = {
        getContent: async ({ path, ref }) => {
          checked.push(`${ref}:${path}`);
          const changed = path === changedPath && ref === 'head-sha';
          return { data: { type: 'file', content: changed ? 'changed' : 'original' } };
        },
      };
      const context = contextFor({
        workflow_run: {
          conclusion: 'success', id: 44, head_sha: 'head-sha',
          path: REPORT_DEFS[key].workflowPath,
          pull_requests: [{ number: 7 }],
        },
      });
      await inTempDir(async () => {
        await moduleFor('resolve_workflow_run_pr')({ github, context, core });
      });
      const label = `${key}: ${changedPath}`;
      assert.strictEqual(outputs.trusted, 'false', label);
      assert.strictEqual(outputs.modified, changedPath, label);
      assert.ok(checked.includes(`head-sha:${changedPath}`), label);
      assert.ok(checked.includes(`base-sha:${changedPath}`), label);
    }
  }
});

test('resolve_workflow_run_pr skips internal PRs and unknown workflow paths', async () => {
  const internalPR = pullRequest();
  internalPR.head.repo.full_name = 'acme/web';
  const { core: coreA, outputs: outA } = makeCore();
  const stubA = githubStub({ 'pulls.get': () => ({ data: internalPR }) });
  await inTempDir(async () => {
    await moduleFor('resolve_workflow_run_pr')({
      github: stubA.github,
      context: contextFor({
        workflow_run: { conclusion: 'success', id: 1, head_sha: 'head-sha', pull_requests: [{ number: 7 }] },
      }),
      core: coreA,
    });
  });
  assert.strictEqual(outA.number, '');
  assert.strictEqual(outA.trusted, 'false');

  const { core: coreB, outputs: outB } = makeCore();
  const stubB = githubStub({ 'pulls.get': () => ({ data: pullRequest() }) });
  await inTempDir(async () => {
    await moduleFor('resolve_workflow_run_pr')({
      github: stubB.github,
      context: contextFor({
        workflow_run: {
          conclusion: 'success', id: 2, head_sha: 'head-sha',
          path: '.github/workflows/renamed.yml', pull_requests: [{ number: 7 }],
        },
      }),
      core: coreB,
    });
  });
  assert.strictEqual(outB.number, '');
  assert.strictEqual(outB.trusted, 'false');
});

test('resolve_workflow_run_pr skips stale head SHAs and discovers PRs by head ref', async () => {
  const { core: coreA, outputs: outA } = makeCore();
  const stubA = githubStub({ 'pulls.get': () => ({ data: pullRequest() }) });
  await inTempDir(async () => {
    await moduleFor('resolve_workflow_run_pr')({
      github: stubA.github,
      context: contextFor({
        workflow_run: { conclusion: 'success', id: 3, head_sha: 'old-sha', pull_requests: [{ number: 7 }] },
      }),
      core: coreA,
    });
  });
  assert.strictEqual(outA.number, '');

  const { core: coreB, outputs: outB } = makeCore();
  const stubB = githubStub({
    'pulls.get': () => ({ data: pullRequest() }),
    'pulls.list': () => ({ data: [pullRequest()] }),
  });
  await inTempDir(async () => {
    await moduleFor('resolve_workflow_run_pr')({
      github: stubB.github,
      context: contextFor({
        workflow_run: {
          conclusion: 'success', id: 4, head_sha: 'head-sha',
          head_repository: { owner: { login: 'fork' } }, head_branch: 'work',
          path: REPORT_DEFS.semgrep.workflowPath,
        },
      }),
      core: coreB,
    });
  });
  assert.strictEqual(outB.number, '7');
  assert.strictEqual(outB.report_key, 'semgrep');
});

test('resolve_workflow_run_pr skips runs without a discoverable PR', async () => {
  const { core, outputs, info } = makeCore();
  const stub = githubStub({ 'pulls.list': () => ({ data: [] }) });
  const context = contextFor({
    workflow_run: {
      conclusion: 'success', id: 5, head_sha: 'head-sha',
      head_repository: { owner: { login: 'fork' } }, head_branch: 'work',
    },
  });
  await inTempDir(async () => {
    await moduleFor('resolve_workflow_run_pr')({ github: stub.github, context, core });
  });
  assert.strictEqual(outputs.number, '');
  assert.strictEqual(outputs.trusted, 'false');
  assert.ok(info.some((m) => m.includes('No open PR found')));
});

test('resolve_workflow_run_pr falls back to the first discovered PR when SHAs differ', async () => {
  const { core, outputs } = makeCore();
  const listed = pullRequest({ head: { sha: 'listed-sha', repo: { full_name: 'fork/web' } } });
  const stub = githubStub({
    'pulls.list': () => ({ data: [listed] }),
    'pulls.get': () => ({ data: pullRequest() }),
    'pulls.listFiles': () => [],
  });
  stub.github.rest.repos = {
    getContent: async () => ({ data: { type: 'file', content: 'same' } }),
  };
  const context = contextFor({
    workflow_run: {
      conclusion: 'success', id: 6, head_sha: 'head-sha',
      head_repository: { owner: { login: 'fork' } }, head_branch: 'work',
      path: REPORT_DEFS.codeql.workflowPath,
    },
  });
  await inTempDir(async () => {
    await moduleFor('resolve_workflow_run_pr')({ github: stub.github, context, core });
  });
  assert.strictEqual(outputs.number, '7');
  assert.strictEqual(outputs.trusted, 'true');
});

test('resolve_workflow_run_pr skips fork runs with no trusted workflow path', async () => {
  const { core, outputs, info } = makeCore();
  const stub = githubStub({ 'pulls.get': () => ({ data: pullRequest() }) });
  const context = contextFor({
    workflow_run: {
      conclusion: 'success', id: 7, head_sha: 'head-sha',
      pull_requests: [{ number: 7 }],
    },
  });
  await inTempDir(async () => {
    await moduleFor('resolve_workflow_run_pr')({ github: stub.github, context, core });
  });
  assert.strictEqual(outputs.number, '');
  assert.strictEqual(outputs.trusted, 'false');
  assert.ok(info.some((m) => m.includes('path (none)')));
});

test('resolve_workflow_run_pr tolerates missing PR metadata and non-file content', async () => {
  const { core: missingCore, outputs: missingOutputs } = makeCore();
  const missingPR = pullRequest({
    head: { sha: '', repo: { full_name: 'fork/web' } },
    base: { repo: { full_name: 'acme/web' } },
  });
  const missingStub = githubStub({
    'pulls.get': () => ({ data: missingPR }),
    'pulls.listFiles': () => [],
  });
  missingStub.github.rest.repos = {
    getContent: async () => ({ data: { type: 'file', content: 'same' } }),
  };
  const missingContext = contextFor({
    workflow_run: {
      conclusion: 'success', id: 8, head_sha: '',
      pull_requests: [{ number: 7 }],
      path: REPORT_DEFS.codeql.workflowPath,
    },
  });
  await inTempDir(async () => {
    await moduleFor('resolve_workflow_run_pr')({
      github: missingStub.github, context: missingContext, core: missingCore,
    });
  });
  assert.strictEqual(missingOutputs.number, '7');
  assert.strictEqual(missingOutputs.base_sha, '');
  assert.strictEqual(missingOutputs.head_sha, '');
  assert.strictEqual(missingOutputs.base_ref, '');
  assert.strictEqual(missingOutputs.trusted, 'true');

  const { core, outputs } = makeCore();
  const stub = githubStub({
    'pulls.get': () => ({ data: pullRequest() }),
    'pulls.listFiles': () => [],
  });
  stub.github.rest.repos = {
    getContent: async () => ({ data: { type: 'dir' } }),
  };
  const context = contextFor({
    workflow_run: {
      conclusion: 'success', id: 9, head_sha: 'head-sha',
      pull_requests: [{ number: 7 }],
      path: REPORT_DEFS.codeql.workflowPath,
    },
  });
  await inTempDir(async () => {
    await moduleFor('resolve_workflow_run_pr')({ github: stub.github, context, core });
  });
  assert.strictEqual(outputs.trusted, 'true');
  assert.strictEqual(outputs.modified, '');
});

// publish_quality_report

test('publish_quality_report skips unknown report keys', async () => {
  const { core, info } = makeCore();
  const { github, calls } = githubStub();
  await withEnv({ REPORT_KEY: 'bogus', PR_NUMBER: '7' }, async () => {
    await moduleFor('publish_quality_report')({
      github, context: contextFor({ workflow_run: { id: 1 } }), core,
    });
  });
  assert.strictEqual(calls.length, 0);
  assert.ok(info.some((m) => m.includes('No report mapping')));
});

test('publish_quality_report writes explicit fallback panels for missing artifacts', async () => {
  for (const [trusted, modified, expected] of [
    ['true', '', 'workflow did not produce usable artifacts'],
    ['false', '.github/workflows/codeql.yml', 'artifacts cannot be trusted'],
  ]) {
    const { core } = makeCore();
    const stub = bodyGithub('');
    await inTempDir(async () => {
      await withEnv({
        REPORT_KEY: 'codeql', PR_NUMBER: '7', TRUSTED: trusted, MODIFIED: modified,
        GITHUB_SERVER_URL: 'https://github.com', GITHUB_REPOSITORY: 'acme/web',
      }, async () => {
        await moduleFor('publish_quality_report')({
          github: stub.github,
          context: contextFor({ workflow_run: { id: 42, html_url: 'https://run/42' } }),
          core,
        });
      });
    });
    const writes = stub.calls.filter((c) => c.name === 'pulls.update');
    assert.strictEqual(writes.length, 1);
    const body = writes[0].params.body;
    assert.ok(body.includes(REPORT_DEFS.codeql.marker));
    assert.ok(body.includes('❌ Report unavailable'));
    assert.ok(body.includes(expected));
    assert.ok(body.includes('**Whole-solution issues:** unknown'));
    assert.ok(body.includes('https://run/42'));
  }
});

test('publish_quality_report publishes a regenerated report when present', async () => {
  const { core } = makeCore();
  const stub = bodyGithub('');
  const content = panel(REPORT_DEFS.codeql.marker, 'real codeql result');
  await inTempDir(async (dir) => {
    fs.writeFileSync(path.join(dir, REPORT_DEFS.codeql.file), content);
    await withEnv({ REPORT_KEY: 'codeql', PR_NUMBER: '7', TRUSTED: 'true' }, async () => {
      await moduleFor('publish_quality_report')({
        github: stub.github, context: contextFor({ workflow_run: { id: 42 } }), core,
      });
    });
  });
  const write = stub.calls.find((c) => c.name === 'pulls.update');
  assert.ok(write);
  assert.ok(write.params.body.includes('real codeql result'));
  assert.ok(!write.params.body.includes('Report unavailable'));
});

test('publish_quality_report uses fallback text for missing keys and modified paths', async () => {
  const { core: noKeyCore, info } = makeCore();
  const noKeyStub = bodyGithub('');
  await withEnv({ REPORT_KEY: undefined, PR_NUMBER: '7' }, async () => {
    await moduleFor('publish_quality_report')({
      github: noKeyStub.github,
      context: contextFor({ workflow_run: { id: 1 } }),
      core: noKeyCore,
    });
  });
  assert.strictEqual(noKeyStub.calls.filter((c) => c.name === 'pulls.update').length, 0);
  assert.ok(info.some((m) => m.includes('No report mapping for key (none)')));

  const { core } = makeCore();
  const stub = bodyGithub('');
  await inTempDir(async () => {
    await withEnv({
      REPORT_KEY: 'codeql', PR_NUMBER: '7', TRUSTED: 'false', MODIFIED: undefined,
      GITHUB_SERVER_URL: 'https://github.com', GITHUB_REPOSITORY: 'acme/web',
    }, async () => {
      await moduleFor('publish_quality_report')({
        github: stub.github,
        context: contextFor({ workflow_run: { id: 42 } }),
        core,
      });
    });
  });
  const body = stub.calls.find((c) => c.name === 'pulls.update').params.body;
  assert.ok(body.includes('(unknown)'), body);
  assert.ok(body.includes('❌ Report unavailable'), body);
});

// resolve_push_runs

test('resolve_push_runs skips complete panels and writes changed files', async () => {
  const { core, outputs } = makeCore();
  const body = bodyWithPanels({
    codeql: 'real codeql result', semgrep: 'real semgrep result', tests: 'real test result',
  });
  const stub = githubStub({
    'pulls.get': () => ({ data: pullRequest({ body }) }),
    'pulls.listFiles': () => [{ filename: 'cps/b.py' }],
  });
  const context = contextFor({ pull_request: pullRequest({ body }) });
  await inTempDir(async (dir) => {
    await moduleFor('resolve_push_runs')({ github: stub.github, context, core });
    assert.strictEqual(fs.readFileSync(path.join(dir, 'changed-files.txt'), 'utf8'), 'cps/b.py\n');
  });
  for (const key of REPORT_KEYS) assert.strictEqual(outputs[`${key}_needed`], 'false');
  assert.strictEqual(outputs.pr_number, '7');
  assert.strictEqual(stub.calls.filter((c) => c.name === 'actions.listWorkflowRunArtifacts').length, 0);
});

test('resolve_push_runs prefers a successful run identified by workflow path', async () => {
  const { core, outputs } = makeCore();
  const stub = githubStub({
    'pulls.get': () => ({ data: pullRequest() }),
    'pulls.listFiles': () => [],
    'actions.listRepoWorkflows': () => [
      { id: 99, path: 'other.yml' },
      { id: 11, path: REPORT_DEFS.codeql.workflowPath },
    ],
    'actions.listWorkflowRunsForRepo': () => ({
      data: {
        workflow_runs: [
          { id: 22, workflow_id: 11, conclusion: 'failure' },
          { id: 21, workflow_id: 11, conclusion: 'success' },
        ],
      },
    }),
    'actions.listWorkflowRunArtifacts': () => ({ data: { artifacts: [{ name: 'codeql-sarif-python' }] } }),
  });
  await inTempDir(async () => {
    await moduleFor('resolve_push_runs')({
      github: stub.github,
      context: contextFor({ pull_request: pullRequest() }),
      core,
    });
  });
  assert.strictEqual(outputs.codeql_needed, 'true');
  assert.strictEqual(outputs.codeql_run, '21');
  assert.strictEqual(outputs.codeql_conclusion, 'success');
  assert.strictEqual(outputs.codeql_artifacts, 'true');
  const artifactCall = stub.calls.find((c) => c.name === 'actions.listWorkflowRunArtifacts');
  assert.strictEqual(artifactCall.params.run_id, 21);
});

test('resolve_push_runs reports missing workflows without choosing a run', async () => {
  const { core, outputs, info } = makeCore();
  const stub = githubStub({ 'pulls.get': () => ({ data: pullRequest() }) });
  await inTempDir(async () => {
    await moduleFor('resolve_push_runs')({
      github: stub.github,
      context: contextFor({ pull_request: pullRequest() }),
      core,
    });
  });
  assert.strictEqual(outputs.codeql_needed, 'true');
  assert.strictEqual(outputs.codeql_run, '');
  assert.strictEqual(outputs.codeql_artifacts, 'false');
  assert.ok(info.some((m) => m.includes('No base-branch workflow registered')));
});

test('resolve_push_runs handles missing runs, artifacts, and PR SHAs', async () => {
  const { core, outputs, info } = makeCore();
  const pr = pullRequest({
    head: { sha: '' },
    base: { ref: '', sha: '' },
  });
  const stub = githubStub({
    'pulls.get': () => ({ data: pr }),
    'actions.listRepoWorkflows': () => [
      { id: 11, path: REPORT_DEFS.codeql.workflowPath },
    ],
    'actions.listWorkflowRunsForRepo': () => ({ data: {} }),
    'actions.listWorkflowRunArtifacts': () => ({ data: {} }),
  });
  await inTempDir(async () => {
    await moduleFor('resolve_push_runs')({
      github: stub.github,
      context: contextFor({ pull_request: pr }),
      core,
    });
  });
  assert.strictEqual(outputs.codeql_run, '');
  assert.strictEqual(outputs.codeql_artifacts, 'false');
  assert.strictEqual(outputs.base_sha, '');
  assert.strictEqual(outputs.head_sha, '');
  assert.strictEqual(outputs.base_ref, '');
  assert.ok(info.some((m) => m.includes('No completed push run')));
  assert.strictEqual(stub.calls.filter((c) => c.name === 'actions.listWorkflowRunArtifacts').length, 0);
});

test('resolve_push_runs reports runs without a conclusion or artifacts', async () => {
  const { core, outputs, info } = makeCore();
  const stub = githubStub({
    'pulls.get': () => ({ data: pullRequest() }),
    'actions.listRepoWorkflows': () => [
      { id: 11, path: REPORT_DEFS.codeql.workflowPath },
    ],
    'actions.listWorkflowRunsForRepo': () => ({
      data: { workflow_runs: [{ id: 21, workflow_id: 11 }] },
    }),
    'actions.listWorkflowRunArtifacts': () => ({ data: {} }),
  });
  await inTempDir(async () => {
    await moduleFor('resolve_push_runs')({
      github: stub.github,
      context: contextFor({ pull_request: pullRequest() }),
      core,
    });
  });
  assert.strictEqual(outputs.codeql_run, '21');
  assert.strictEqual(outputs.codeql_conclusion, '');
  assert.strictEqual(outputs.codeql_artifacts, 'false');
  assert.ok(info.some((m) => m.includes('artifacts missing')));
});

// publish_backfilled_reports

test('publish_backfilled_reports skips reports that are not needed', async () => {
  const { core } = makeCore();
  const stub = githubStub();
  await inTempDir(async () => {
    await withEnv({ PR_NUMBER: '7' }, async () => {
      await moduleFor('publish_backfilled_reports')({
        github: stub.github, context: contextFor({ pull_request: pullRequest() }), core,
      });
    });
  });
  assert.strictEqual(stub.calls.length, 0);
});

test('publish_backfilled_reports re-runs a run whose artifacts expired', async () => {
  const { core } = makeCore();
  const stub = githubStub();
  await inTempDir(async () => {
    await withEnv({ PR_NUMBER: '7', CODEQL_NEEDED: 'true', CODEQL_RUN: '21' }, async () => {
      await moduleFor('publish_backfilled_reports')({
        github: stub.github, context: contextFor({ pull_request: pullRequest() }), core,
      });
    });
  });
  const rerun = stub.calls.find((c) => c.name === 'actions.reRunWorkflow');
  assert.ok(rerun);
  assert.strictEqual(rerun.params.run_id, 21);
  assert.strictEqual(stub.calls.some((c) => c.name === 'pulls.update'), false);
});

test('publish_backfilled_reports writes a fallback when no run exists', async () => {
  const { core } = makeCore();
  const stub = bodyGithub('');
  await inTempDir(async () => {
    await withEnv({
      PR_NUMBER: '7', SEMGREP_NEEDED: 'true', SEMGREP_RUN: '',
      GITHUB_SERVER_URL: 'https://github.com', GITHUB_REPOSITORY: 'acme/web',
    }, async () => {
      await moduleFor('publish_backfilled_reports')({
        github: stub.github, context: contextFor({ pull_request: pullRequest() }), core,
      });
    });
  });
  const write = stub.calls.find((c) => c.name === 'pulls.update');
  assert.ok(write);
  assert.ok(write.params.body.includes(REPORT_DEFS.semgrep.marker));
  assert.ok(write.params.body.includes('no completed push run of semgrep exists'));
  assert.ok(write.params.body.includes('**Analysis errors:** unknown'));
});

test('publish_backfilled_reports uses regenerated artifacts when present', async () => {
  const { core } = makeCore();
  const stub = bodyGithub('');
  const content = panel(REPORT_DEFS.tests.marker, 'backfilled test result');
  await inTempDir(async (dir) => {
    fs.writeFileSync(path.join(dir, REPORT_DEFS.tests.file), content);
    await withEnv({ PR_NUMBER: '7', TESTS_NEEDED: 'true', TESTS_RUN: '31' }, async () => {
      await moduleFor('publish_backfilled_reports')({
        github: stub.github, context: contextFor({ pull_request: pullRequest() }), core,
      });
    });
  });
  const write = stub.calls.find((c) => c.name === 'pulls.update');
  assert.ok(write);
  assert.ok(write.params.body.includes('backfilled test result'));
});

// sweep_stale_placeholders

test('sweep_stale_placeholders replaces stale panels and skips running PRs', async () => {
  const body = bodyWithPanels({
    codeql: `${REPORT_DEFS.codeql.heading}\n⏳ waiting`,
  });
  const { core, outputs, info } = makeCore();
  const stub = bodyGithub(body, {
    'pulls.list': () => [pullRequest()],
    'actions.listWorkflowRunsForRepo': () => ({ data: { workflow_runs: [{ id: 1, status: 'in_progress' }] } }),
  });
  await moduleFor('sweep_stale_placeholders')({
    github: stub.github, context: contextFor({}), core,
  });
  assert.strictEqual(stub.calls.filter((c) => c.name === 'pulls.update').length, 0);
  assert.ok(info.some((m) => m.includes('in_progress workflows')));

  const activeFree = bodyGithub(body, { 'pulls.list': () => [pullRequest()] });
  const coreFree = makeCore();
  await moduleFor('sweep_stale_placeholders')({
    github: activeFree.github, context: contextFor({}), core: coreFree.core,
  });
  const write = activeFree.calls.find((c) => c.name === 'pulls.update');
  assert.ok(write);
  assert.ok(write.params.body.includes('no analysis completed for commit head-sha'));
  assert.ok(write.params.body.includes('❌ Report unavailable'));
  assert.strictEqual(outputs.number, undefined);
});

test('sweep_stale_placeholders skips queued and awaiting-approval runs', async () => {
  for (const [status, label] of [
    ['queued', 'queued'],
    ['requested', 'requested'],
    ['waiting', 'waiting'],
    ['pending', 'pending'],
    ['action_required', 'action_required'],
    [undefined, 'active'],
  ]) {
    const body = bodyWithPanels({
      codeql: `${REPORT_DEFS.codeql.heading}\n⏳ waiting`,
    });
    const { core, info } = makeCore();
    const stub = bodyGithub(body, {
      'pulls.list': () => [pullRequest()],
      'actions.listWorkflowRunsForRepo': () => ({ data: { workflow_runs: [{ id: 7, status }] } }),
    });
    await moduleFor('sweep_stale_placeholders')({
      github: stub.github, context: contextFor({}), core,
    });
    assert.strictEqual(stub.calls.filter((c) => c.name === 'pulls.update').length, 0);
    assert.ok(info.some((m) => m.includes(`${label} workflows`)));
    const query = stub.calls.find((c) => c.name === 'actions.listWorkflowRunsForRepo');
    assert.strictEqual(query.params.status, undefined);
    assert.strictEqual(query.params.head_sha, 'head-sha');
  }
});

test('sweep_stale_placeholders skips active pull_request_target backfill runs', async () => {
  const body = bodyWithPanels({
    codeql: `${REPORT_DEFS.codeql.heading}\n⏳ waiting`,
  });
  const { core, info } = makeCore();
  const stub = bodyGithub(body, {
    'pulls.list': () => [pullRequest()],
    'actions.listWorkflowRunsForRepo': () => ({ data: { workflow_runs: [] } }),
    'actions.listWorkflowRuns': () => ({
      data: { workflow_runs: [{ id: 9, status: 'in_progress', pull_requests: [{ number: 7 }] }] },
    }),
  });
  await moduleFor('sweep_stale_placeholders')({
    github: stub.github, context: contextFor({}), core,
  });
  assert.strictEqual(stub.calls.filter((c) => c.name === 'pulls.update').length, 0);
  assert.ok(info.some((m) => m.includes('backfill')));
});

test('sweep_stale_placeholders skips empty bodies and complete panels', async () => {
  const emptyStub = bodyGithub(null, {
    'pulls.list': () => [pullRequest()],
    'actions.listWorkflowRunsForRepo': () => ({ data: {} }),
  });
  await moduleFor('sweep_stale_placeholders')({
    github: emptyStub.github, context: contextFor({}), core: makeCore().core,
  });
  assert.strictEqual(emptyStub.calls.filter((c) => c.name === 'pulls.update').length, 0);

  const complete = bodyWithPanels({
    codeql: 'real codeql result', semgrep: 'real semgrep result', tests: 'real test result',
  });
  const completeStub = bodyGithub(complete, {
    'pulls.list': () => [pullRequest()],
    'actions.listWorkflowRunsForRepo': () => ({ data: {} }),
  });
  await moduleFor('sweep_stale_placeholders')({
    github: completeStub.github, context: contextFor({}), core: makeCore().core,
  });
  assert.strictEqual(completeStub.calls.filter((c) => c.name === 'pulls.update').length, 0);
});

// create_pending_quality_reports

test('create_pending_quality_reports adds placeholders only for missing panels', async () => {
  const { core, info } = makeCore();
  const complete = bodyWithPanels({
    codeql: 'real codeql result', semgrep: 'real semgrep result', tests: 'real test result',
  });
  const skipStub = bodyGithub(complete);
  await withEnv({ PR_NUMBER: '7' }, async () => {
    await moduleFor('create_pending_quality_reports')({
      github: skipStub.github, context: contextFor({}), core,
    });
  });
  assert.strictEqual(skipStub.calls.filter((c) => c.name === 'pulls.update').length, 0);
  assert.ok(info.some((m) => m.includes('already present')));

  const fillStub = bodyGithub('');
  await withEnv({ PR_NUMBER: '7' }, async () => {
    await moduleFor('create_pending_quality_reports')({
      github: fillStub.github, context: contextFor({}), core: makeCore().core,
    });
  });
  const writes = fillStub.calls.filter((c) => c.name === 'pulls.update');
  assert.strictEqual(writes.length, REPORT_KEYS.length);
  for (const key of REPORT_KEYS) {
    assert.ok(writes.some((c) => c.params.body.includes(REPORT_DEFS[key].marker)));
  }
  assert.ok(fillStub.currentBody().includes('⏳'));
});

test('inline hourglass in a generated report is not a pending placeholder', async () => {
  const complete = bodyWithPanels({
    codeql: `${REPORT_DEFS.codeql.heading}\n- \`tests/test_emoji.py::test_⏳\` — assert pending`,
    semgrep: 'real semgrep result',
    tests: 'real test result',
  });

  const sweepStub = bodyGithub(complete, {
    'pulls.list': () => [pullRequest()],
  });
  await moduleFor('sweep_stale_placeholders')({
    github: sweepStub.github, context: contextFor({}), core: makeCore().core,
  });
  assert.strictEqual(sweepStub.calls.filter((c) => c.name === 'pulls.update').length, 0);

  const { core, info } = makeCore();
  const pendingStub = bodyGithub(complete);
  await withEnv({ PR_NUMBER: '7' }, async () => {
    await moduleFor('create_pending_quality_reports')({
      github: pendingStub.github, context: contextFor({}), core,
    });
  });
  assert.strictEqual(pendingStub.calls.filter((c) => c.name === 'pulls.update').length, 0);
  assert.ok(info.some((m) => m.includes('already present')));

  const { core: resolveCore, outputs } = makeCore();
  const resolveStub = githubStub({
    'pulls.get': () => ({ data: pullRequest({ body: complete }) }),
    'pulls.listFiles': () => [],
  });
  await inTempDir(async () => {
    await moduleFor('resolve_push_runs')({
      github: resolveStub.github,
      context: contextFor({ pull_request: pullRequest({ body: complete }) }),
      core: resolveCore,
    });
  });
  for (const key of REPORT_KEYS) assert.strictEqual(outputs[`${key}_needed`], 'false');
});

// update_pr_body_reports edge cases

test('report markers accept CRLF and end-of-body markers', () => {
  const marker = shared.SECTION_START;
  assert.deepStrictEqual(shared.lineMarkerIndices(marker, marker), [0]);
  assert.deepStrictEqual(shared.lineMarkerIndices(`${marker}\r\nnext`, marker), [0]);
});

test('canonicalization handles both header styles and overlapping section markers', () => {
  const bothHeaders = 'Intro' + shared.LEGACY_SECTION_HEADER + 'legacy stale\n' +
    'Middle' + shared.SECTION_HEADER + 'current stale\n';
  const canonical = shared.canonicalizeSection(bothHeaders);
  assert.ok(!canonical.includes('legacy stale'), canonical);
  assert.ok(!canonical.includes('current stale'), canonical);
  assert.ok(!canonical.includes('Middle'), canonical);

  const marker = REPORT_DEFS.tests.marker;
  const nested = shared.SECTION_START + '\norphan\n' + shared.SECTION_START + '\n' +
    marker + '\nreal tests\n' + shared.SECTION_END + '\n';
  const cleaned = shared.canonicalizeSection(nested);
  assert.strictEqual(cleaned.split(shared.SECTION_START).length - 1, 1, cleaned);
  assert.ok(cleaned.includes(`${marker}\nreal tests`), cleaned);
  // The unmatched earlier marker has no complete pair to discard, so it is
  // preserved as plain PR text before the canonical section.
  assert.ok(cleaned.includes('orphan'), cleaned);
});

test('upsertReport tolerates missing content and totalsContent tolerates unmarked bodies', () => {
  const marker = REPORT_DEFS.codeql.marker;
  const body = shared.upsertReport('Intro\n', marker, undefined);
  assert.ok(body.includes(marker), body);

  const totals = shared.totalsContent('**Analysis errors:** 999 inline');
  assert.ok(totals.includes('0 issues found · 0 analysis errors · 0 analysis warnings'), totals);

  const unmatched = shared.totalsContent(
    shared.SECTION_START + '\n**Analysis errors:** 5\n'
  );
  assert.ok(unmatched.includes('0 analysis errors'), unmatched);
});

test('updateReport handles absent PR numbers, unchanged bodies, and API conflicts', async () => {
  const { github } = githubStub();
  const context = contextFor({});
  const marker = REPORT_DEFS.codeql.marker;
  const content = panel(marker, 'real codeql result');

  const skipped = await shared.updateReport({
    github, context, core: makeCore().core, pull_number: 0, marker, content,
  });
  assert.deepStrictEqual(skipped, { updated: false, reason: 'no_pr' });

  const unchangedStub = bodyGithub(shared.upsertReport('', marker, content));
  const unchanged = await shared.updateReport({
    github: unchangedStub.github, context, core: makeCore().core,
    pull_number: 7, marker, content,
  });
  assert.deepStrictEqual(unchanged, { updated: false, reason: 'unchanged' });
  assert.strictEqual(unchangedStub.calls.filter((c) => c.name === 'pulls.update').length, 0);

  let attempts = 0;
  const retryStub = bodyGithub('', {
    'pulls.update': () => {
      attempts += 1;
      if (attempts === 1) {
        const err = new Error('conflict');
        err.status = 409;
        throw err;
      }
      return {};
    },
  });
  const retried = await shared.updateReport({
    github: retryStub.github, context, core: makeCore().core,
    pull_number: 7, marker, content,
  });
  assert.deepStrictEqual(retried, { updated: true });
  assert.strictEqual(attempts, 2);

  let exhaustedAttempts = 0;
  const exhaustedStub = bodyGithub('', {
    'pulls.update': () => {
      exhaustedAttempts += 1;
      const err = new Error('conflict');
      err.status = 422;
      throw err;
    },
  });
  await assert.rejects(shared.updateReport({
    github: exhaustedStub.github, context, core: makeCore().core,
    pull_number: 7, marker, content,
  }), /conflict/);
  assert.strictEqual(exhaustedAttempts, 3);

  const fatalStub = bodyGithub('', {
    'pulls.update': () => {
      const err = new Error('forbidden');
      err.status = 500;
      throw err;
    },
  });
  await assert.rejects(shared.updateReport({
    github: fatalStub.github, context, core: makeCore().core,
    pull_number: 7, marker, content,
  }), /forbidden/);
  assert.strictEqual(fatalStub.calls.filter((c) => c.name === 'pulls.update').length, 1);
});

test('updateReports tolerates a missing report list', async () => {
  const { github, calls } = githubStub();
  const result = await shared.updateReports({
    github, context: contextFor({}), core: makeCore().core, pull_number: 7,
  });
  assert.deepStrictEqual(result, { updated: false });
  assert.strictEqual(calls.length, 0);
});

test('refreshTotals skips missing PRs and writes aggregate totals', async () => {
  const { github: emptyGithub, calls: emptyCalls } = githubStub();
  const skipped = await shared.refreshTotals({
    github: emptyGithub, context: contextFor({}), core: makeCore().core, pull_number: 0,
  });
  assert.deepStrictEqual(skipped, { updated: false, reason: 'no_pr' });
  assert.strictEqual(emptyCalls.length, 0);

  const body = bodyWithPanels({
    codeql: panel(REPORT_DEFS.codeql.marker, '**Analysis errors:** 2 · **Analysis warnings:** 1'),
  });
  const stub = bodyGithub(body);
  const result = await shared.refreshTotals({
    github: stub.github, context: contextFor({}), core: makeCore().core,
    owner: 'acme', repo: 'web', pull_number: 7,
  });
  assert.deepStrictEqual(result, { updated: true });

  const nullBodyStub = bodyGithub(null);
  const nullBodyResult = await shared.refreshTotals({
    github: nullBodyStub.github, context: contextFor({}), core: makeCore().core,
    pull_number: 7,
  });
  assert.deepStrictEqual(nullBodyResult, { updated: true });
  assert.ok(nullBodyStub.currentBody().includes(shared.SUMMARY_MARKER));

  const write = stub.calls.find((c) => c.name === 'pulls.update');
  assert.strictEqual(write.params.owner, 'acme');
  assert.strictEqual(write.params.repo, 'web');
  assert.ok(write.params.body.includes(shared.SUMMARY_MARKER), write.params.body);
  assert.strictEqual(write.params.body.split('**Quality total:**').length - 1, 1, write.params.body);
  assert.ok(write.params.body.includes('2 analysis errors'), write.params.body);
  assert.ok(write.params.body.includes('1 analysis warnings'), write.params.body);
  assert.ok(write.params.body.includes(REPORT_DEFS.codeql.marker), write.params.body);
  assert.ok(write.params.body.includes('**Analysis errors:** 2'), write.params.body);
});

test('updateReportAndRefreshTotals handles missing PRs and unchanged bodies', async () => {
  const { github } = githubStub();
  const context = contextFor({});
  const marker = REPORT_DEFS.codeql.marker;
  const content = panel(marker, 'real codeql result');

  const skipped = await shared.updateReportAndRefreshTotals({
    github, context, core: makeCore().core,
    pull_number: 0, marker, content,
  });
  assert.deepStrictEqual(skipped, { updated: false, reason: 'no_pr' });

  let body = shared.upsertReport('', marker, content);
  body = shared.upsertReport(body, shared.SUMMARY_MARKER, shared.totalsContent(body));
  const stub = bodyGithub(body);
  const unchanged = await shared.updateReportAndRefreshTotals({
    github: stub.github, context, core: makeCore().core,
    pull_number: 7, marker, content,
  });
  assert.deepStrictEqual(unchanged, { updated: false, reason: 'unchanged' });
  assert.strictEqual(stub.calls.filter((c) => c.name === 'pulls.update').length, 0);
});

test('updateReportAndRefreshTotals reapplies updates after a concurrent body change', async () => {
  const { core, info } = makeCore();
  let gets = 0;
  const initial = '';
  const changed = 'Another workflow edited the body.';
  const stub = githubStub({
    'pulls.get': () => ({ data: { body: gets++ === 0 ? initial : changed, number: 7 } }),
  });
  const marker = REPORT_DEFS.semgrep.marker;
  const content = panel(marker, 'real semgrep result');

  const result = await shared.updateReportAndRefreshTotals({
    github: stub.github, context: contextFor({}), core,
    pull_number: 7, marker, content,
  });
  assert.deepStrictEqual(result, { updated: true });
  assert.strictEqual(gets, 4);
  assert.ok(info.some((m) => m.includes('body changed during') && m.includes(marker)));
  const write = stub.calls.find((c) => c.name === 'pulls.update');
  assert.ok(write.params.body.includes(changed));
  assert.ok(write.params.body.includes('real semgrep result'));
});

test('updateReportAndRefreshTotals stops after repeated concurrent body changes', async () => {
  const bodies = ['body A', 'body B', 'body B', 'body A', 'body A', 'body B'];
  let gets = 0;
  const stub = githubStub({
    'pulls.get': () => ({ data: { body: bodies[gets++] || '', number: 7 } }),
  });
  const marker = REPORT_DEFS.codeql.marker;
  const content = panel(marker, 'real codeql result');

  const result = await shared.updateReportAndRefreshTotals({
    github: stub.github, context: contextFor({}), core: makeCore().core,
    pull_number: 7, marker, content,
  });
  assert.deepStrictEqual(result, { updated: false, reason: 'retries_exhausted' });
  assert.strictEqual(gets, 6);
  assert.strictEqual(stub.calls.filter((c) => c.name === 'pulls.update').length, 0);
});

test('updateReportAndRefreshTotals retries conflicts and reports exhausted retries', async () => {
  const context = contextFor({});
  const marker = REPORT_DEFS.tests.marker;
  const content = panel(marker, 'real test result');

  let attempts = 0;
  const retryStub = bodyGithub('', {
    'pulls.update': () => {
      attempts += 1;
      if (attempts === 1) {
        const err = new Error('conflict');
        err.status = 409;
        throw err;
      }
      return {};
    },
  });
  const retried = await shared.updateReportAndRefreshTotals({
    github: retryStub.github, context, core: makeCore().core,
    pull_number: 7, marker, content,
  });
  assert.deepStrictEqual(retried, { updated: true });
  assert.strictEqual(attempts, 2);

  let exhaustedAttempts = 0;
  const exhaustedStub = bodyGithub('', {
    'pulls.update': () => {
      exhaustedAttempts += 1;
      const err = new Error('conflict');
      err.status = 422;
      throw err;
    },
  });
  await assert.rejects(shared.updateReportAndRefreshTotals({
    github: exhaustedStub.github, context, core: makeCore().core,
    pull_number: 7, marker, content,
  }), /conflict/);
  assert.strictEqual(exhaustedAttempts, 3);
});
