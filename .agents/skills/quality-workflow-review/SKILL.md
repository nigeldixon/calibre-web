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
- Each report's `requiredFiles` covers every PR-controlled input that can change its artifacts: CodeQL workflow, config, template renderer, and SARIF validators; Semgrep workflow, filter, validator, and ignore files; Tests workflow, report scripts, coverage-support config, and `pyproject.toml`. A modified analysis input makes fork artifacts untrusted.
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
- Canonicalization discards injected or stale report content before rebuilding the trusted section. `ensureSection()` always yields one complete marked section, and `sectionBounds()` only selects a start with a following end; `upsertReport()` and `stripPriorSections()` rely on those invariants instead of defensive nullish fallbacks.
- `updateReportAndRefreshTotals()` uses an explicit retry counter: conflicts throw on the final attempt, while three consecutive concurrent body changes return `retries_exhausted`.
- `.github/scripts/report_constants.json` is the source of truth for section markers, report markers/headings, and metric labels. `update_pr_body_reports.js` re-exports the derived JS constants and `report_constants.py` loads the same JSON for the Python generators; the parity test in `tests/workflow_scripts/test_report_constants.py` guards agreement.
- Totals are whole-solution totals and are computed from trusted panel data, not untrusted PR body text. A labelled panel with non-numeric metrics or a pending whole-line `⏳` placeholder makes the total a qualified lower bound (`at least ...`) with an explicit unavailable-report count; it must never be presented as a complete total. The exported `panelIsPending()` whole-line check is shared by totals, pending-placeholder creation, backfill resolution, and the stale sweep; substring `⏳` checks misclassify completed panels whose failure details quote the marker.
- The stale-placeholder sweep treats any workflow run whose status is not `completed` as active, including queued, requested, waiting, pending, and action-required runs.
- Never present PR-filtered results as complete.
- A suite with `no results` uses `⚪`, not `✅`.
- Low-coverage icons: `⚠️ Low coverage` for 70–79%, `❌ Low coverage` below 70%, silent small text at 80%+. Each language uses the lower of its line and branch percentages (inferred branches for shell), not coverage.py's combined percentage; the workflow-coverage line shows the most severe marker present.

### SARIF and analysis jobs

- Invalid, empty, or scan-failed SARIF must hard-fail and must not clear code-scanning alerts.
- CodeQL PR analyses use `upload: 'never'`.
- CodeQL uploads only on non-PR runs, after validation, and only when the analyze step succeeded.
- Internal push publishers must publish failed analysis panels, not leave the previous commit's report in place. The CodeQL generator uses `continue-on-error: true` because it exits 1 after printing a complete failure panel; the publisher uses `always()` plus an `unavailablePanel()` fallback. Removing only an `outcome == 'success'` check is insufficient because an ordinary `if:` retains implicit `success()`.
- CodeQL SARIF validation rejects `executionSuccessful: false`.
- Raw Jinja templates are excluded from JavaScript extraction; parseable rendered copies come from `codeql-rendered/`.
- CodeQL init uses `config-file:`, not `config:`.
- CodeQL notification/report handling must tolerate null `artifactLocation` and non-string descriptor IDs; CodeQL callers pass `strict_descriptor_id=False` to the shared validator.
- Semgrep uploads full raw SARIF, never the PR-filtered copy.
- Semgrep exit code `1` means findings were produced; record it for reporting but normalize the step to success.
- Semgrep notification validation must reject non-string descriptor IDs; Semgrep callers pass `strict_descriptor_id=True` explicitly.

### Tests and coverage

- Workflow-script tests stay under `tests/workflow_scripts/`, with their own CI step and coverage line.
- Generated coverage, test-result, and report artifacts from local CI reproductions are listed in `.gitignore`.
- Python, JavaScript, and shell workflow-script coverage appear on one adjacent line in the same `covered/total (percent)` format: `Workflow script coverage: Python \`...\` · JavaScript \`...\` · Shell \`...\``. `coverage_stats()` surfaces coverage.py's `Branch`/`BrPart` columns, enabled in `tests/workflow_scripts/coverage_support/coveragerc`; `python_coverage_detail()`, `js_coverage_detail()`, and `shell_coverage_detail()` format the three languages, and `coverage_summary_line()` combines them.
- Python coverage must enumerate every `.github/scripts/*.py`, including files no test imports: `coveragerc` uses `source = ${COVERAGE_SCRIPTS_DIR}` (resolved by `coverage_support/sitecustomize.py` and exported by the `test.yml` step), and `[report] include` limits the output to workflow scripts. The workflow-script tests job reports coverage in the PR quality panel but does not fail the job on a coverage threshold when the tests themselves pass; low coverage should be visible in the report, not hidden behind a bare step failure.
- JS coverage is measured with Node's built-in test runner (`--experimental-test-coverage`, lcov reporter) by `tests/workflow_scripts/test_javascript_suite.py`, which runs the `node:test` files under `tests/workflow_scripts/js/`. The lcov file is uploaded in the `coverage-data` artifact and counted by `js_coverage_total()`; it includes only `.github/scripts/*.js`, not the test files. The suite asserts every `.github/scripts/*.js` appears with full line and branch coverage, so an untested new script cannot hide behind a high total.
- Shell line and branch coverage are derived from bash `xtrace` control flow by `tests/workflow_scripts/test_shell_suite.py`; it exercises `.github/scripts/*.sh`, writes `coverage-workflow-shell-lcov.info`, and is summarized by `shell_coverage_detail()`. Branches cover `if`, loops, and `&&`/`||` decisions; outcomes are inferred from the traced line sequence because bash has no native branch counters, so the report must label them `inferred branches`. New shell scripts must be added to that suite or its full-coverage assertion will fail.
- JS modules need behavior tests, not only `require()`/export checks; `tests/workflow_scripts/js/workflow_modules.test.js` exercises their API calls and written payloads.
- Coverage regenerates from `coverage.xml` plus GitHub `pulls.listFiles` patches.
- No-data fallbacks must say “No application coverage data …”; generic “No coverage data …” wording is ambiguous next to the workflow-coverage line.
- Backfilled test reports derive per-suite outcomes from test output via `DERIVE_TEST_OUTCOMES`.
- Standard local test command from the repository root:

```sh
python -m pytest tests/workflow_scripts -q
```

Use an environment with the workflow-script test dependencies installed. Node.js must be on `PATH` for the JavaScript-backed tests; without it those assertions skip silently. Bash must also be on `PATH` for the shell coverage suite. CI installs Node 20 with `actions/setup-node` in `.github/workflows/test.yml` — match that version locally. For the exact coverage invocation used by CI, see `.github/workflows/test.yml`.

### Failure comments

- Group comments per suite/failure signature.
- Minimize resolved failures with a clear reason.
- A suite that fails without parseable `FAILED`/`ERROR` lines gets an explicit active comment and must not resolve existing failure comments; resolution requires positive suite-success evidence.
- Reopen on recurrence.
- Create a new comment when the failure changes.
- Failure-comment sync must run even when the PR-body publish step fails: the publish step uses `continue-on-error: true` and the following install/sync steps use `always()` in both the `publish` and `backfill` jobs of `quality_reports.yml`.
- The fork publish step also requires `steps.pr.outcome == 'success'`; a failed resolver can leave partial outputs but must not cause a fallback report with an invented trust explanation.
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
