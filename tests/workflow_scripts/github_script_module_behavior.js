// Behavior tests for the workflow modules extracted from github-script steps.
// Run by test_github_script_module_behavior.py; stubs stand in for the GitHub
// API so assertions cover the payloads each module writes, not just wiring.
const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const ROOT = path.resolve(__dirname, '../..');
const SCRIPTS = path.join(ROOT, '.github', 'scripts');
const shared = require(path.join(SCRIPTS, 'update_pr_body_reports.js'));
const { REPORT_DEFS, REPORT_KEYS } = shared;
const moduleFor = (name) => require(path.join(SCRIPTS, `${name}.js`));

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

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
    getContent: async ({ ref }) => {
      checked.push(ref);
      return { data: { type: 'file', content: ref === 'head-sha' ? 'changed' : 'original' } };
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
    'actions.listWorkflowRunsForRepo': () => ({ data: { workflow_runs: [{ id: 1 }] } }),
  });
  await moduleFor('sweep_stale_placeholders')({
    github: stub.github, context: contextFor({}), core,
  });
  assert.strictEqual(stub.calls.filter((c) => c.name === 'pulls.update').length, 0);
  assert.ok(info.some((m) => m.includes('still has running workflows')));

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

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try {
      await fn();
      console.log(`ok - ${name}`);
    } catch (err) {
      failed += 1;
      console.error(`not ok - ${name}\n${err.stack}`);
    }
  }
  console.log(`${tests.length - failed}/${tests.length} module behavior tests passed`);
  if (failed) process.exit(1);
})();
