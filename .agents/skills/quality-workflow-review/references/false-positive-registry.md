# False-positive registry

Read this before finalizing quality-workflow reviews. Do not re-raise these findings unless new evidence proves they are reachable in the shipped workflow and have reader impact.

## Fallback panels hardcode `Analyze job: failure`

Those panels are emitted only when report data is unavailable or stale. They already say metrics could not be regenerated, so changing the label to `unknown` or `not_run` does not change the reader's conclusion.

## CRLF legacy-header canonicalization

Previously dismissed as box ticking. The shipped PR body path uses the current heading structure. Add this only if a real CRLF body is observed breaking canonicalization.

## Unclosed START marker before the real section

A hand-edited body with a stray unmatched start marker before the genuine section is self-inflicted and does not represent workflow-generated output.

## Fake complete section pair with missing genuine END

Requires an author to inject a complete fake section and delete the genuine END marker. It is self-inflicted, not produced by the publisher, and should not block review unless a real occurrence is observed.

## Stray START marker inside a section

Also requires a hand-edited PR body marker. The next trusted publisher run rebuilds the section. Not actionable under the agreed standard.

## Unreadable `CHANGED_FILES_FILE`

The script can set `changed = None` if the file cannot be opened, but this is not reachable through the shipped publisher. The file is written in the same `github-script` step that must succeed before the Semgrep regeneration steps run. Those regeneration steps use ordinary `if:` conditions, so implicit `success()` skips them after the writer step fails. The regeneration step's own `continue-on-error` does not override that.

Reopen only if a path is added where report generation runs without the trusted writer succeeding.

## Fallback `Analyze job: failure` in no-run/stale-placeholder cases

Covered by the first entry. The panels say the report is unavailable; the hardcoded status does not create a false all-clear or hide findings.

## Non-standard pytest summary phrasings

The four shipped pytest suites produce standard `-q` summaries. Do not flag `outcome_from_output()` for phrasings those suites cannot emit unless a new suite changes that assumption.
