# Agent guidance

## Layout and entrypoints

- The app is the Flask package `cps/`. `cps.py` adds the repo root to `sys.path` and calls `cps.main.main()`; `create_app()` in `cps/__init__.py` initializes settings/config/DB globals, and `cps/main.py` registers blueprints.
- Kobo endpoints are conditional: the `cps.kobo` / `cps.kobo_auth` blueprints register only if their optional imports work and `get_kobo_activated()` is true.
- EPUB/KEPUB TOC rewriting lives in `cps/epub_chapters.py`; Kobo HTTP routes live in `cps/kobo.py`; device-side NickelMenu/qndb files are under `device/kobo/` with host-side launcher tests in `tests/unit/test_kobo_device_launcher.py`.

## Setup and runtime

- CI uses Python 3.12; install app deps with `python -m pip install -r requirements.txt`. Test deps used by CI: `pytest pytest-cov diff-cover pytest-playwright playwright==1.65.2 requests defusedxml`.
- Full test setup needs system deps `libmagic1 imagemagick`, Playwright Chromium (`playwright install --with-deps chromium`), Node 20 for JavaScript workflow-script coverage, and `bash` for shell coverage. Without Node, JS assertions skip silently.
- Run locally from the repo root with `python cps.py`; default URL is `http://localhost:8083`.
- `app.db` and `library/metadata.db` are tracked sample/test databases. Browser/functional fixtures copy them into temp dirs; do not edit the tracked copies to change test state.
- Generated coverage and test outputs at the repo root are gitignored and CI deletes them at job start.

## Tests

- `tests/` is the pytest source of truth. `.github/scripts/test_*.py` are CLI helpers invoked as subprocesses, not pytest modules; `pyproject.toml` keeps bare `pytest` from collecting them.
- Focused commands:
  ```sh
  pytest tests/unit -q
  pytest tests/functional -q
  pytest tests/e2e -q
  pytest tests/workflow_scripts -q
  pytest tests/unit tests/functional tests/workflow_scripts -q
  ```
- CI coverage enumerates app modules explicitly. New app modules need `--cov=...` additions to be counted; current unit step includes `--cov=cps.epub_chapters` and functional step appends coverage for `cps.epub_chapters`, `cps.kobo`, and `cps.kobo_auth`.
- E2E/functional browser fixtures set `CALIBRE_WEB_TEST_COVERAGE=1` in CI to collect server-side coverage. They start `cps.py` against a copied fixture library and free port; login defaults are `admin` / `admin123`.
- `tests/conftest.py` stubs `cps.gdriveutils` and, when missing, `jsonschema`. A passing import/test does not prove optional deps are installed.
- In this workspace Playwright Chromium needs `LD_LIBRARY_PATH=/tmp/opencode/libatomic`; verified command: `LD_LIBRARY_PATH=/tmp/opencode/libatomic .venv/bin/pytest tests/e2e/test_cover_editor_browser.py -q`.

## CI and agent skills

- `.github/workflows/test.yml` is the authoritative test/coverage sequence. Internal PRs are covered by push runs; fork PR reports are published by the privileged `workflow_run` job in `quality_reports.yml`. Default compare branch is `develop`.
- Canonical agent skills live in `.agents/skills/`. `.claude/skills/` entries are relative symlinks; edit canonical files only. For `.github/workflows`, `.github/scripts`, PR quality panels, or `tests/workflow_scripts`, load `quality-workflow-review`.
