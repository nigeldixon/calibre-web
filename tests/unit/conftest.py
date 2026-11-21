import contextlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / ".github" / "scripts"


@pytest.fixture
def run_quality_script():
    """Run a workflow helper the same way the workflows do: as a subprocess."""

    def _run(name, cwd, env=None):
        child_env = os.environ.copy()
        child_env.update({key: str(value) for key, value in (env or {}).items()})
        return subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / name)],
            cwd=str(cwd),
            env=child_env,
            capture_output=True,
            text=True,
            check=False,
        )

    return _run


@pytest.fixture
def load_quality_script(monkeypatch):
    """Execute a helper up to its first SystemExit and return its globals."""

    def _load(name, cwd, env=None):
        monkeypatch.chdir(cwd)
        for key, value in (env or {}).items():
            monkeypatch.setenv(key, str(value))
        path = SCRIPTS_DIR / name
        namespace = {"__name__": "__quality_script_test__", "__file__": str(path)}
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), namespace)
            except SystemExit:
                pass
        return namespace

    return _load


@pytest.fixture
def write_json():
    def _write(path, data):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    return _write
