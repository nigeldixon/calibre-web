#!/usr/bin/env bash
set -euo pipefail
shopt -s nullglob
# Combine parallel server-side coverage data (from functional/E2E) into .coverage
server_cov=(.coverage.server* .coverage.*)
if [ ${#server_cov[@]} -gt 0 ]; then
  python -m coverage combine --append "${server_cov[@]}" 2>&1 || true
else
  python -m coverage combine 2>&1 || true
fi
if [ -f .coverage ] || [ ${#server_cov[@]} -gt 0 ]; then
  python -m coverage xml -o coverage.xml || true
  python -m coverage report -m --include="*/cps/*" --omit="*/tests/*" > coverage-term.txt 2>&1 || true
fi
