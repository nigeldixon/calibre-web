---
name: quality-workflow-review
description: Review or repair GitHub Actions CI/SAST quality-report workflows, including CodeQL and Semgrep PR reports, SARIF validation, PR-body totals, coverage panels, and workflow-script tests.
---

# Quality Workflow Review

Use this skill for work involving the repository's quality-reporting workflows and scripts, especially:

- `.github/workflows/quality_reports.yml`
- `.github/workflows/codeql.yml`
- `.github/workflows/semgrep.yml`
- `.github/workflows/test.yml`
- `.github/scripts/*`
- `tests/workflow_scripts/*`
- PR body sections under `## 📊 Quality reports`

For a PR review, do not modify files unless explicitly asked. For a fix task, make one focused commit per legitimate issue.

## Context to gather

1. Identify the review target: uncommitted diff, explicit commit, branch, or PR number/URL.
2. For PRs, use `gh pr view` and `gh pr diff` plus the current head SHA.
3. Read the full changed files, not only the diff hunks.
4. Read `CONTRIBUTING.md` and nearby workflow/script tests for conventions.
5. Before finalizing, read [the false-positive registry](references/false-positive-registry.md).

## Required invariants

### Fork safety and trust boundaries

- Fork PR reports are published only by a privileged `workflow_run` job.
- Cross-repository detection uses `head.repo.full_name != base.repo.full_name`.
- The publisher checks out trusted workflow scripts and uses structured artifacts.
- PR-controlled markdown, scripts, scope files, or copied PR body text are never trusted for report generation or side effects.
- Resolve backfill runs by base-branch workflow path/ID, not PR-controlled workflow display names.
- Default branch is `develop`; do not hardcode a compare branch for coverage regeneration.

### Report body

Expected headings:

```md
## 📊 Quality reports
### 🔍 CodeQL analysis
### 🛡️ Semgrep analysis
### 🧪 Tests
#### Patch coverage
```

- Legacy `## Quality reports` must canonicalize cleanly.
- Section markers are line-anchored; quoted markers inside report text are not panel boundaries.
- Canonicalization discards injected or stale report content before rebuilding the trusted section.
- `.github/scripts/report_constants.json` is the source of truth for section markers, report markers/headings, and metric labels. `update_pr_body_reports.js` re-exports the derived JS constants and `report_constants.py` loads the same JSON for the Python generators; the parity test in `tests/workflow_scripts/test_report_constants.py` guards agreement.
- Totals are whole-solution totals and are computed from trusted panel data, not untrusted PR body text.
- Never present PR-filtered results as complete.
- A suite with `no results` uses `⚪`, not `✅`.
- Low-coverage icons: `⚠️ Low coverage` for 70–79%, `❌ Low coverage` below 70%, silent small text at 80%+.

### SARIF and analysis jobs

- Invalid, empty, or scan-failed SARIF must hard-fail and must not clear code-scanning alerts.
- CodeQL PR analyses use `upload: 'never'`.
- CodeQL uploads only on non-PR runs, after validation, and only when the analyze step succeeded.
- CodeQL SARIF validation rejects `executionSuccessful: false`.
- Raw Jinja templates are excluded from JavaScript extraction; parseable rendered copies come from `codeql-rendered/`.
- CodeQL init uses `config-file:`, not `config:`.
- CodeQL notification/report handling must tolerate null `artifactLocation` and non-string descriptor IDs.
- Semgrep uploads full raw SARIF, never the PR-filtered copy.
- Semgrep exit code `1` means findings were produced; record it for reporting but normalize the step to success.
- Semgrep notification validation must reject non-string descriptor IDs.

### Tests and coverage

- Workflow-script tests stay under `tests/workflow_scripts/`, with their own CI step and coverage line.
- The coverage line reports statement and branch counts; branch mode is enabled in `tests/workflow_scripts/coverage_support/coveragerc` and `coverage_total()` must surface its `Branch`/`BrPart` columns.
- JS coverage is measured with Node's built-in test runner (`--experimental-test-coverage`, lcov reporter) by `tests/workflow_scripts/test_javascript_suite.py`, which runs the `node:test` files under `tests/workflow_scripts/js/`. The lcov file is uploaded in the `coverage-data` artifact and summarized by `js_coverage_line()`; it counts only `.github/scripts/*.js`, not the test files.
- JS modules need behavior tests, not only `require()`/export checks; `tests/workflow_scripts/js/workflow_modules.test.js` exercises their API calls and written payloads.
- Coverage regenerates from `coverage.xml` plus GitHub `pulls.listFiles` patches.
- Backfilled test reports derive per-suite outcomes from test output via `DERIVE_TEST_OUTCOMES`.
- Standard local test command from the repository root:

```sh
python -m pytest tests/workflow_scripts -q
```

Use an environment with the workflow-script test dependencies installed. Node.js must be on `PATH` for the JavaScript-backed tests; without it those assertions skip silently. CI installs Node 20 with `actions/setup-node` in `.github/workflows/test.yml` — match that version locally. For the exact coverage invocation used by CI, see `.github/workflows/test.yml`.

### Failure comments

- Group comments per suite/failure signature.
- Minimize resolved failures with a clear reason.
- Reopen on recurrence.
- Create a new comment when the failure changes.
- Failure-comment sync must run even when the PR-body publish step fails: the publish step uses `continue-on-error: true` and the following install/sync steps use `always()` in both the `publish` and `backfill` jobs of `quality_reports.yml`.
- Keep the payload guard (`build_test_failure_comments.outcome == 'success'` / `backfill_test_comments.outcome == 'success'`): `sync_test_failure_comments.py` opens the artifact unconditionally, so a bare `always()` on the sync step can fail when no payload was produced.
- The internal-PR path in `test.yml` uses the same publish-then-sync pattern; check both paths when changing either.

## Verification checklist

Before reporting completion:

1. Run the workflow-script test suite.
2. Run the coverage job/check with `tests/workflow_scripts/coverage_support`.
3. Validate all workflow YAML.
4. Check for `TEMPORARY:` markers in `.github/` and `tests/`.
5. Check `git status --short`.
6. Push if asked, then wait for CI runs for the exact head SHA.
7. Verify `gh pr checks <pr>` has no failures.
8. Snapshot and inspect the generated PR body headings and totals.

## Review grading

A finding is actionable only if it is:

- in changed code,
- reachable in the shipped workflow or script invocation,
- not purely self-inflicted by a hand-edited PR body or local-only condition,
- not cosmetic with no reader impact,
- not already dismissed in `references/false-positive-registry.md` without new evidence,
- supported by an exact trigger scenario.

For GitHub Actions, verify step semantics precisely. An ordinary `if:` condition has implicit `success()`; `always()`/`failure()` change that. A step's own `continue-on-error` does not make a later step run after a different prerequisite step failed.

Report each actionable finding with severity, exact `file:line`, trigger, impact, and concrete fix. If none pass the gate, say so plainly.
