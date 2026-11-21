import os
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / ".github" / "scripts" / "combine_coverage.sh"


def fake_python(bin_dir, body):
    bin_dir.mkdir(exist_ok=True)
    path = bin_dir / "python"
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(0o755)
    return {"PATH": f"{bin_dir}:{os.environ['PATH']}"}


def run_combine(tmp_path, env=None):
    import subprocess

    child_env = os.environ.copy()
    child_env.update(env or {})
    return subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=str(tmp_path),
        env=child_env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_combine_failure_does_not_produce_coverage_reports(tmp_path):
    (tmp_path / ".coverage.server1").write_text("dummy", encoding="utf-8")
    env = fake_python(
        tmp_path / "bin", "printf 'combine failed\\n' >&2\nexit 1\n"
    )

    proc = run_combine(tmp_path, env)

    assert proc.returncode != 0
    assert not (tmp_path / "coverage-term.txt").exists()
    assert not (tmp_path / "coverage.xml").exists()


def test_report_failure_does_not_leave_stderr_as_coverage(tmp_path):
    (tmp_path / ".coverage.server1").write_text("dummy", encoding="utf-8")
    env = fake_python(
        tmp_path / "bin",
        "case \"$*\" in\n"
        "  *combine*) : > .coverage; exit 0;;\n"
        "  *report*) printf 'ERROR: bad data\\n' >&2; exit 1;;\n"
        "  *xml*) : > coverage.xml; exit 0;;\n"
        "esac\n"
        "exit 0\n",
    )

    proc = run_combine(tmp_path, env)

    assert proc.returncode != 0
    assert not (tmp_path / "coverage-term.txt").exists()
    assert not (tmp_path / "coverage-term.tmp").exists()


def test_successful_combine_deduplicates_and_writes_report(tmp_path):
    (tmp_path / ".coverage.server1").write_text("dummy", encoding="utf-8")
    (tmp_path / "coverage.xml").write_text("stale", encoding="utf-8")
    log = tmp_path / "calls.log"
    env = fake_python(
        tmp_path / "bin",
        f"printf '%s\\n' \"$*\" >> '{log}'\n"
        "case \"$*\" in\n"
        "  *combine*) : > .coverage; exit 0;;\n"
        "  *xml*) : > coverage.xml; exit 0;;\n"
        "  *report*) printf 'TOTAL 100 0 100%%\\n'; exit 0;;\n"
        "esac\n"
        "exit 0\n",
    )

    proc = run_combine(tmp_path, env)

    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / "coverage-term.txt").read_text(encoding="utf-8") == "TOTAL 100 0 100%\n"
    assert not (tmp_path / "coverage-term.tmp").exists()
    calls = log.read_text(encoding="utf-8")
    assert calls.count(".coverage.server1") == 1
