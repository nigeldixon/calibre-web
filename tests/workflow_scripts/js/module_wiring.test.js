const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '../../..');
const MODULE_WORKFLOWS = {
  resolve_workflow_run_pr: ['quality_reports.yml'],
  publish_quality_report: ['quality_reports.yml'],
  resolve_push_runs: ['quality_reports.yml'],
  publish_backfilled_reports: ['quality_reports.yml'],
  sweep_stale_placeholders: ['quality_reports.yml'],
  create_pending_quality_reports: ['codeql.yml'],
};

for (const [moduleName, workflows] of Object.entries(MODULE_WORKFLOWS)) {
  test(`${moduleName} is loaded by its workflows`, () => {
    const wrapper = `require('./.github/scripts/${moduleName}.js')`;
    for (const workflow of workflows) {
      const source = fs.readFileSync(
        path.join(ROOT, '.github', 'workflows', workflow), 'utf8'
      );
      assert.ok(source.includes(wrapper), `${moduleName} missing in ${workflow}`);
    }
  });

  test(`${moduleName} exports an async function`, () => {
    const exported = require(
      path.join(ROOT, '.github', 'scripts', `${moduleName}.js`)
    );
    assert.strictEqual(typeof exported, 'function');
    assert.strictEqual(exported.constructor.name, 'AsyncFunction');
  });
}

const findAssociatedPr = require(
  path.join(ROOT, '.github', 'scripts', 'find_associated_pr.js')
);

function outputCollector() {
  const outputs = {};
  return {
    outputs,
    core: { setOutput: (key, value) => { outputs[key] = String(value); } },
  };
}

test('find_associated_pr uses the pull_request payload when present', async () => {
  const { outputs, core } = outputCollector();
  await findAssociatedPr({
    github: {},
    core,
    context: {
      repo: { owner: 'acme', repo: 'web' },
      eventName: 'pull_request',
      payload: {
        pull_request: {
          number: 7,
          base: { ref: 'develop', sha: 'base-sha' },
          head: { sha: 'head-sha' },
        },
      },
    },
  });
  assert.deepStrictEqual(outputs, {
    number: '7', base: 'develop', base_ref: 'develop',
    base_sha: 'base-sha', head_sha: 'head-sha',
  });
});

test('find_associated_pr leaves outputs empty on unhandled events and stale pushes', async () => {
  const empty = { number: '', base: 'develop', base_ref: '', base_sha: '', head_sha: '' };
  for (const eventName of ['schedule', 'workflow_run']) {
    const { outputs, core } = outputCollector();
    await findAssociatedPr({
      github: {}, core,
      context: { repo: { owner: 'acme', repo: 'web' }, eventName, payload: {} },
    });
    assert.deepStrictEqual(outputs, empty);
  }

  const { outputs, core } = outputCollector();
  const github = {
    rest: {
      pulls: {
        list: async () => ({
          data: [{ number: 8, head: { sha: 'old-sha' }, base: { ref: 'develop' } }],
        }),
      },
    },
  };
  await findAssociatedPr({
    github, core,
    context: {
      repo: { owner: 'acme', repo: 'web' },
      eventName: 'push', ref: 'refs/heads/topic', sha: 'head-sha', payload: {},
    },
  });
  assert.deepStrictEqual(outputs, empty);
});

test('find_associated_pr matches a push to its open PR by head SHA', async () => {
  const { outputs, core } = outputCollector();
  const github = {
    rest: {
      pulls: {
        list: async ({ head }) => {
          assert.strictEqual(head, 'acme:topic');
          return {
            data: [
              { number: 8, head: { sha: 'old-sha' }, base: { ref: 'develop' } },
              { number: 9, head: { sha: 'head-sha' }, base: { ref: 'release', sha: 'base-sha' } },
            ],
          };
        },
      },
    },
  };
  await findAssociatedPr({
    github, core,
    context: {
      repo: { owner: 'acme', repo: 'web' },
      eventName: 'push', ref: 'refs/heads/topic', sha: 'head-sha', payload: {},
    },
  });
  assert.deepStrictEqual(outputs, {
    number: '9', base: 'release', base_ref: 'release',
    base_sha: 'base-sha', head_sha: 'head-sha',
  });
});
