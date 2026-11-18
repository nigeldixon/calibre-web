import io
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
import requests


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_LIBRARY = ROOT / "tests" / "fixtures" / "library"
BOOK_ID = int(os.environ.get("CALIBRE_WEB_TEST_BOOK_ID", "2"))
BOOK_REL_PATH = os.environ.get("CALIBRE_WEB_TEST_BOOK_REL", "Bram Stoker/Dracula (2)")
USERNAME = os.environ.get("CALIBRE_WEB_USERNAME", "admin")
PASSWORD = os.environ.get("CALIBRE_WEB_PASSWORD", "admin123")


def _find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_http(url, timeout=40):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            r = requests.get(url, timeout=2)
            if r.status_code == 200:
                return True
        except Exception as e:
            last = e
        time.sleep(0.5)
    if last:
        raise RuntimeError(f"Server not ready: {last}")
    raise RuntimeError("Server not ready")


@pytest.fixture(scope="session")
def live_server(tmp_path_factory):
    """Start a Calibre-Web instance against a temp copy of the fixture library."""
    work = tmp_path_factory.mktemp("cw-func")
    library_dir = work / "library"
    shutil.copytree(FIXTURE_LIBRARY, library_dir)

    settings_src = ROOT / "app.db"
    if not settings_src.exists():
        pytest.skip("app.db not found; cannot start Calibre-Web for functional tests")
    settings_db = work / "app.db"
    shutil.copy2(settings_src, settings_db)

    port = _find_free_port()
    conn = sqlite3.connect(str(settings_db))
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE settings SET config_calibre_dir=?, config_port=? WHERE id=1",
            (str(library_dir), port),
        )
        conn.commit()
    finally:
        conn.close()

    env = os.environ.copy()
    cmd = [sys.executable, str(ROOT / "cps.py"), "-p", str(settings_db), "-i", "127.0.0.1"]
    log_path = work / "cps.log"
    log_f = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=log_f, stderr=subprocess.STDOUT, env=env)
    base_url = f"http://127.0.0.1:{port}"
    book_dir = library_dir / BOOK_REL_PATH
    try:
        _wait_http(base_url + "/login", timeout=40)
    except Exception:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        log_f.close()
        try:
            tail = "\n".join(log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-80:])
        except Exception:
            tail = ""
        pytest.skip(f"Could not start Calibre-Web for functional tests. Log tail:\n{tail}")

    try:
        yield {
            "base_url": base_url,
            "book_id": BOOK_ID,
            "book_dir": book_dir,
            "cover_path": book_dir / "cover.jpg",
            "history_dir": book_dir / ".cover_history",
            "proc": proc,
            "log_path": log_path,
        }
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        log_f.close()


@pytest.fixture(scope="session")
def admin_session(live_server):
    base_url = live_server["base_url"]
    s = requests.Session()
    r = s.get(base_url + "/login")
    r.raise_for_status()
    m = re.search(r'name="csrf_token" value="([^"]+)"', r.text)
    assert m, "csrf token not found on login page"
    csrf = m.group(1)
    r = s.post(
        base_url + "/login",
        data={"username": USERNAME, "password": PASSWORD, "csrf_token": csrf, "submit": ""},
        allow_redirects=True,
    )
    assert r.status_code == 200
    s.csrf_token = csrf
    return s


@pytest.fixture(scope="session")
def original_cover_bytes(live_server):
    return live_server["cover_path"].read_bytes()


@pytest.fixture(autouse=True)
def clean_history(live_server, original_cover_bytes):
    cover_path = live_server["cover_path"]
    history_dir = live_server["history_dir"]
    history_dir.mkdir(parents=True, exist_ok=True)
    for p in history_dir.iterdir():
        if p.is_file():
            try:
                p.unlink()
            except OSError:
                pass
    cover_path.write_bytes(original_cover_bytes)
    yield
    if history_dir.exists():
        for p in history_dir.iterdir():
            if p.is_file():
                try:
                    p.unlink()
                except OSError:
                    pass
    cover_path.write_bytes(original_cover_bytes)


def auth_headers(session):
    return {"X-CSRFToken": getattr(session, "csrf_token", "")}


def _url(live_server, path):
    return live_server["base_url"] + path


def test_cover_history_requires_login(live_server):
    s = requests.Session()
    r = s.get(_url(live_server, f"/ajax/coverhistory/{live_server['book_id']}"), allow_redirects=False)
    assert r.status_code in (302, 401, 403)
    if r.headers.get("Content-Type", "").startswith("application/json"):
        data = r.json()
        assert not data.get("success", False)


def test_edit_cover_requires_login(live_server):
    s = requests.Session()
    files = {"cover": ("cover.jpg", io.BytesIO(b"\xff\xd8\xff\xdbFAKE"), "image/jpeg")}
    r = s.post(_url(live_server, f"/ajax/editcover/{live_server['book_id']}"), files=files, allow_redirects=False)
    assert r.status_code in (302, 400, 401, 403)


@pytest.mark.parametrize("endpoint", [
    "GET /ajax/coverhistory/{book_id}",
    "POST /ajax/editcover/{book_id}",
    "POST /ajax/coverhistory/{book_id}/cover_20990101T010101000000Z.jpg",
    "POST /ajax/coverhistory/{book_id}/cover_20990101T010101000000Z.jpg/restore",
])
def test_cover_endpoints_404_for_unknown_book(live_server, admin_session, original_cover_bytes, endpoint):
    method, path = endpoint.split(" ", 1)
    url = _url(live_server, path.format(book_id=99999999))
    if method == "GET":
        r = admin_session.get(url, headers=auth_headers(admin_session))
    elif path.endswith("/editcover/{book_id}".format(book_id=99999999)):
        files = {"cover": ("cover.jpg", io.BytesIO(original_cover_bytes), "image/jpeg")}
        r = admin_session.post(url, files=files, headers=auth_headers(admin_session))
    else:
        r = admin_session.post(url, headers=auth_headers(admin_session), data={"action": "delete"})
    assert r.status_code == 404


def test_list_cover_history_empty(live_server, admin_session):
    r = admin_session.get(_url(live_server, f"/ajax/coverhistory/{live_server['book_id']}"))
    assert r.status_code == 200
    data = r.json()
    assert data["success"] is True
    assert data["versions"] == []


def test_edit_cover_saves_and_snapshots_previous(live_server, admin_session, original_cover_bytes):
    files = {"cover": ("cover.jpg", io.BytesIO(original_cover_bytes), "image/jpeg")}
    r = admin_session.post(
        _url(live_server, f"/ajax/editcover/{live_server['book_id']}"),
        files=files,
        headers=auth_headers(admin_session),
    )
    assert r.status_code == 200
    data = r.json()
    assert data["success"] is True
    assert "cover_url" in data and f"/cover/{live_server['book_id']}/" in data["cover_url"]
    assert "last_modified" in data

    assert live_server["cover_path"].read_bytes() == original_cover_bytes

    r = admin_session.get(_url(live_server, f"/ajax/coverhistory/{live_server['book_id']}"))
    assert r.status_code == 200
    versions = r.json()["versions"]
    assert len(versions) == 1
    name = versions[0]["name"]
    assert name.startswith("cover_") and name.endswith(".jpg")
    snap_path = live_server["history_dir"] / name
    assert snap_path.is_file()
    assert snap_path.read_bytes() == original_cover_bytes


def test_edit_cover_rejects_missing_file(live_server, admin_session):
    r = admin_session.post(
        _url(live_server, f"/ajax/editcover/{live_server['book_id']}"),
        data={},
        headers=auth_headers(admin_session),
    )
    assert r.status_code == 400
    data = r.json()
    assert data["success"] is False


def test_edit_cover_rejects_empty_file(live_server, admin_session):
    files = {"cover": ("cover.jpg", io.BytesIO(b""), "image/jpeg")}
    r = admin_session.post(
        _url(live_server, f"/ajax/editcover/{live_server['book_id']}"),
        files=files,
        headers=auth_headers(admin_session),
    )
    assert r.status_code == 400
    data = r.json()
    assert data["success"] is False


def test_edit_cover_cache_bust_and_last_modified(live_server, admin_session, original_cover_bytes):
    files = {"cover": ("cover.jpg", io.BytesIO(original_cover_bytes), "image/jpeg")}
    r = admin_session.post(
        _url(live_server, f"/ajax/editcover/{live_server['book_id']}"),
        files=files,
        headers=auth_headers(admin_session),
    )
    assert r.status_code == 200
    data = r.json()
    assert data["success"] is True
    assert f"/cover/{live_server['book_id']}/" in data["cover_url"]
    assert "c=" in data["cover_url"]
    assert "last_modified" in data and "T" in data["last_modified"]


def test_edit_cover_multiple_saves_create_ordered_history(live_server, admin_session, original_cover_bytes):
    names_seen = []
    for i in range(3):
        files = {"cover": ("cover.jpg", io.BytesIO(original_cover_bytes), "image/jpeg")}
        r = admin_session.post(
            _url(live_server, f"/ajax/editcover/{live_server['book_id']}"),
            files=files,
            headers=auth_headers(admin_session),
        )
        assert r.status_code == 200 and r.json()["success"] is True
        versions = admin_session.get(
            _url(live_server, f"/ajax/coverhistory/{live_server['book_id']}")
        ).json()["versions"]
        assert len(versions) == i + 1
        names_seen = [v["name"] for v in versions]
        assert names_seen == sorted(names_seen, reverse=True)
        time.sleep(0.01)

    for name in names_seen:
        p = live_server["history_dir"] / name
        assert p.is_file()
        assert p.read_bytes() == original_cover_bytes


def test_delete_cover_history_version(live_server, admin_session, original_cover_bytes):
    files = {"cover": ("cover.jpg", io.BytesIO(original_cover_bytes), "image/jpeg")}
    r = admin_session.post(
        _url(live_server, f"/ajax/editcover/{live_server['book_id']}"),
        files=files,
        headers=auth_headers(admin_session),
    )
    assert r.json()["success"] is True
    versions = admin_session.get(
        _url(live_server, f"/ajax/coverhistory/{live_server['book_id']}")
    ).json()["versions"]
    assert len(versions) == 1
    name = versions[0]["name"]

    r = admin_session.post(
        _url(live_server, f"/ajax/coverhistory/{live_server['book_id']}/{name}"),
        data={"action": "delete"},
        headers=auth_headers(admin_session),
    )
    assert r.status_code == 200
    assert r.json()["success"] is True
    versions = admin_session.get(
        _url(live_server, f"/ajax/coverhistory/{live_server['book_id']}")
    ).json()["versions"]
    assert versions == []
    assert not (live_server["history_dir"] / name).exists()


@pytest.mark.parametrize("name", [
    "../cover.jpg",
    "cover.jpg",
    ".hidden",
    "subdir/cover_20240101T010101000000Z.jpg",
])
def test_delete_cover_history_rejects_unsafe_name(live_server, admin_session, name):
    url = _url(live_server, f"/ajax/coverhistory/{live_server['book_id']}/" + name)
    r = admin_session.post(url, data={"action": "delete"}, headers=auth_headers(admin_session))
    assert r.status_code in (400, 404)
    assert live_server["cover_path"].is_file()


def test_delete_cover_history_missing_version_returns_404(live_server, admin_session):
    r = admin_session.post(
        _url(live_server, f"/ajax/coverhistory/{live_server['book_id']}/cover_20990101T010101000000Z.jpg"),
        data={"action": "delete"},
        headers=auth_headers(admin_session),
    )
    assert r.status_code == 404
    assert r.json()["success"] is False


def test_delete_cover_history_via_delete_method(live_server, admin_session, original_cover_bytes):
    files = {"cover": ("cover.jpg", io.BytesIO(original_cover_bytes), "image/jpeg")}
    r = admin_session.post(
        _url(live_server, f"/ajax/editcover/{live_server['book_id']}"),
        files=files,
        headers=auth_headers(admin_session),
    )
    assert r.json()["success"] is True
    name = admin_session.get(
        _url(live_server, f"/ajax/coverhistory/{live_server['book_id']}")
    ).json()["versions"][0]["name"]

    r = admin_session.delete(
        _url(live_server, f"/ajax/coverhistory/{live_server['book_id']}/{name}"),
        headers=auth_headers(admin_session),
    )
    assert r.status_code == 200
    assert r.json()["success"] is True
    assert admin_session.get(
        _url(live_server, f"/ajax/coverhistory/{live_server['book_id']}")
    ).json()["versions"] == []


def test_delete_cover_history_rejects_invalid_action(live_server, admin_session, original_cover_bytes):
    files = {"cover": ("cover.jpg", io.BytesIO(original_cover_bytes), "image/jpeg")}
    r = admin_session.post(
        _url(live_server, f"/ajax/editcover/{live_server['book_id']}"),
        files=files,
        headers=auth_headers(admin_session),
    )
    assert r.json()["success"] is True
    name = admin_session.get(
        _url(live_server, f"/ajax/coverhistory/{live_server['book_id']}")
    ).json()["versions"][0]["name"]

    r = admin_session.post(
        _url(live_server, f"/ajax/coverhistory/{live_server['book_id']}/{name}"),
        data={"action": "restore"},
        headers=auth_headers(admin_session),
    )
    assert r.status_code == 400
    assert r.json()["success"] is False
    versions = admin_session.get(
        _url(live_server, f"/ajax/coverhistory/{live_server['book_id']}")
    ).json()["versions"]
    assert [v["name"] for v in versions] == [name]


def test_restore_cover_history_version(live_server, admin_session, original_cover_bytes):
    files = {"cover": ("cover.jpg", io.BytesIO(original_cover_bytes), "image/jpeg")}
    r = admin_session.post(
        _url(live_server, f"/ajax/editcover/{live_server['book_id']}"),
        files=files,
        headers=auth_headers(admin_session),
    )
    assert r.json()["success"] is True
    versions = admin_session.get(
        _url(live_server, f"/ajax/coverhistory/{live_server['book_id']}")
    ).json()["versions"]
    assert len(versions) == 1
    old_name = versions[0]["name"]

    new_bytes = original_cover_bytes[:1024] + b"XYZCHANGED"
    if new_bytes[:3] != b"\xff\xd8\xff":
        new_bytes = b"\xff\xd8\xff" + new_bytes[3:]
    files = {"cover": ("cover.jpg", io.BytesIO(new_bytes), "image/jpeg")}
    r = admin_session.post(
        _url(live_server, f"/ajax/editcover/{live_server['book_id']}"),
        files=files,
        headers=auth_headers(admin_session),
    )
    assert r.json()["success"] is True
    assert live_server["cover_path"].read_bytes() == new_bytes

    r = admin_session.post(
        _url(live_server, f"/ajax/coverhistory/{live_server['book_id']}/{old_name}/restore"),
        headers=auth_headers(admin_session),
    )
    assert r.status_code == 200
    data = r.json()
    assert data["success"] is True
    assert "cover_url" in data and f"/cover/{live_server['book_id']}/" in data["cover_url"]
    assert "c=" in data["cover_url"]
    assert live_server["cover_path"].read_bytes() == original_cover_bytes

    versions = admin_session.get(
        _url(live_server, f"/ajax/coverhistory/{live_server['book_id']}")
    ).json()["versions"]
    names = [v["name"] for v in versions]
    assert old_name in names
    found_new = False
    for v in versions:
        if (live_server["history_dir"] / v["name"]).read_bytes() == new_bytes:
            found_new = True
            break
    assert found_new


def test_restore_cover_history_missing_version_returns_404(live_server, admin_session):
    r = admin_session.post(
        _url(live_server, f"/ajax/coverhistory/{live_server['book_id']}/cover_20990101T010101000000Z.jpg/restore"),
        headers=auth_headers(admin_session),
    )
    assert r.status_code == 404
    assert r.json()["success"] is False


@pytest.mark.parametrize("name", [
    "../cover.jpg",
    "cover.jpg",
    ".hidden",
    "subdir/cover_20240101T010101000000Z.jpg",
])
def test_restore_cover_history_rejects_unsafe_name(live_server, admin_session, name):
    url = _url(live_server, f"/ajax/coverhistory/{live_server['book_id']}/" + name + "/restore")
    r = admin_session.post(url, headers=auth_headers(admin_session))
    assert r.status_code in (400, 404)
    if r.headers.get("Content-Type", "").startswith("application/json"):
        assert r.json().get("success") is False
    assert live_server["cover_path"].is_file()
