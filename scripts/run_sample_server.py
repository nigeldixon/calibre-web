#!/usr/bin/env python3
"""Run Calibre-Web against a temporary copy of the sample fixture library.

Useful for local development/verification without modifying the tracked root library.
The sample library (Dracula, public domain) lives in tests/fixtures/library.
"""
import argparse
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_LIBRARY = ROOT / "tests" / "fixtures" / "library"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1", help="Bind host (use 0.0.0.0 for remote access)")
    ap.add_argument("--port", type=int, default=8083, help="Port to listen on")
    ap.add_argument("--settings", default=str(ROOT / "app.db"), help="Base settings db to copy")
    args = ap.parse_args()

    if not FIXTURE_LIBRARY.exists():
        print(f"Fixture library not found: {FIXTURE_LIBRARY}", file=sys.stderr)
        return 2
    settings_src = Path(args.settings)
    if not settings_src.exists():
        print(f"Settings db not found: {settings_src}", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="calibre-web-sample-") as tmp:
        tmp_path = Path(tmp)
        library_dir = tmp_path / "library"
        shutil.copytree(FIXTURE_LIBRARY, library_dir)
        settings_db = tmp_path / "app.db"
        shutil.copy2(settings_src, settings_db)

        conn = sqlite3.connect(str(settings_db))
        try:
            conn.execute(
                "UPDATE settings SET config_calibre_dir=?, config_port=? WHERE id=1",
                (str(library_dir), args.port),
            )
            conn.commit()
        finally:
            conn.close()

        cmd = [sys.executable, str(ROOT / "cps.py"), "-p", str(settings_db), "-i", args.host]
        print(f"Starting Calibre-Web with sample library on http://{args.host}:{args.port}")
        print(f"Sample data: {library_dir} (temporary copy)")
        proc = subprocess.Popen(cmd, cwd=str(ROOT))

        def _stop(*_):
            try:
                proc.terminate()
            except Exception:
                pass

        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)
        try:
            return proc.wait()
        except KeyboardInterrupt:
            _stop()
            return proc.wait()


if __name__ == "__main__":
    raise SystemExit(main())
