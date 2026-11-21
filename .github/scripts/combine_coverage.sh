#!/usr/bin/env bash
set -euo pipefail
shopt -s nullglob
# Remove any generated reports checked into the branch. Otherwise a PR could
# supply a fake coverage.xml that survives when no test suite produces data.
rm -f coverage.xml coverage-term.txt diff-coverage.md coverage-term.tmp

# Combine parallel server-side coverage data (from functional/E2E) into .coverage.
# Deduplicate because .coverage.server* also matches .coverage.*.
declare -A seen=()
server_cov=()
for file in .coverage.server* .coverage.*; do
  [ -e "$file" ] || continue
  if [ -z "${seen[$file]+x}" ]; then
    seen[$file]=1
    server_cov+=("$file")
  fi
done
if [ ${#server_cov[@]} -gt 0 ]; then
  if ! python -m coverage combine --append "${server_cov[@]}"; then
    echo "Could not combine coverage data." >&2
    exit 1
  fi
fi

if [ -f .coverage ]; then
  if ! python -m coverage xml -o coverage.xml; then
    rm -f coverage.xml
    echo "Could not write coverage XML." >&2
    exit 1
  fi
  # Write to a temporary file first: a failed report command must not leave
  # its stderr in coverage-term.txt where it would be shown as app coverage.
  if ! python -m coverage report -m --include="*/cps/*" --omit="*/tests/*" > coverage-term.tmp 2>&1; then
    rm -f coverage-term.tmp
    echo "Could not build the coverage report." >&2
    exit 1
  fi
  mv coverage-term.tmp coverage-term.txt
fi
