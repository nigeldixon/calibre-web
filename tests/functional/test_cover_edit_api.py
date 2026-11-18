import io
import os
import re
import time

import pytest
import requests


BASE_URL = os.environ.get("CALIBRE_WEB_URL", "http://127.0.0.1:8083")
BOOK_ID = int(os.environ.get("CALIBRE_WEB_TEST_BOOK_ID", "2"))
BOOK_DIR = os.environ.get(
    "CALIBRE_WEB_TEST_BOOK_DIR",
    "library/Bram Stoker/Dracula (2)",
)
COVER_PATH = os.path.join(BOOK_DIR, "cover.jpg")
HISTORY_DIR = os.path.join(BOOK_DIR, ".cover_history")


def _server_available():
    try:
        r = requests.get(BASE_URL + "/login", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


@pytest.fixture(scope="session", autouse=True)
def require_server():
    if not _server_available():
        pytest.skip(f"Calibre-Web server not available at {BASE_URL}")


@pytest.fixture(scope="session")
def admin_session():
    s = requests.Session()
    r = s.get(BASE_URL + "/login")
    r.raise_for_status()
    m = re.search(r'name="csrf_token" value="([^"]+)"', r.text)
    assert m, "csrf token not found on login page"
    csrf = m.group(1)
    r = s.post(
        BASE_URL + "/login",
        data={"username": "admin", "password": "admin123", "csrf_token": csrf, "submit": ""},
        allow_redirects=True,
    )
    assert r.status_code == 200
    # fetch a page to ensure session/csrf usable; reuse token
    s.csrf_token = csrf
    return s


@pytest.fixture(scope="session")
def original_cover_bytes():
    with open(COVER_PATH, "rb") as f:
        return f.read()


@pytest.fixture(autouse=True)
def clean_history(original_cover_bytes):
    # ensure known baseline before each test
    os.makedirs(HISTORY_DIR, exist_ok=True)
    # remove any leftover history files
    for name in os.listdir(HISTORY_DIR):
        try:
            os.remove(os.path.join(HISTORY_DIR, name))
        except OSError:
            pass
    # restore original cover
    with open(COVER_PATH, "wb") as f:
        f.write(original_cover_bytes)
    yield
    # cleanup after test
    if os.path.isdir(HISTORY_DIR):
        for name in os.listdir(HISTORY_DIR):
            try:
                os.remove(os.path.join(HISTORY_DIR, name))
            except OSError:
                pass
    with open(COVER_PATH, "wb") as f:
        f.write(original_cover_bytes)


def auth_headers(session):
    return {"X-CSRFToken": getattr(session, "csrf_token", "")}


def test_cover_history_requires_login():
    # fresh session without login
    s = requests.Session()
    r = s.get(BASE_URL + f"/ajax/coverhistory/{BOOK_ID}", allow_redirects=False)
    # should redirect to login or 401/403 depending; must not return success json
    assert r.status_code in (302, 401, 403)
    if r.headers.get("Content-Type", "").startswith("application/json"):
        data = r.json()
        assert not data.get("success", False)


def test_edit_cover_requires_login():
    s = requests.Session()
    files = {"cover": ("cover.jpg", io.BytesIO(b"\xff\xd8\xff\xdbFAKE"), "image/jpeg")}
    r = s.post(BASE_URL + f"/ajax/editcover/{BOOK_ID}", files=files, allow_redirects=False)
    assert r.status_code in (302, 400, 401, 403)


@pytest.mark.parametrize("endpoint", [
    "GET /ajax/coverhistory/{book_id}",
    "POST /ajax/editcover/{book_id}",
    "POST /ajax/coverhistory/{book_id}/cover_20990101T010101000000Z.jpg",
    "POST /ajax/coverhistory/{book_id}/cover_20990101T010101000000Z.jpg/restore",
])
def test_cover_endpoints_404_for_unknown_book(admin_session, original_cover_bytes, endpoint):
    method, path = endpoint.split(" ", 1)
    url = BASE_URL + path.format(book_id=99999999)
    if method == "GET":
        r = admin_session.get(url, headers=auth_headers(admin_session))
    elif path.endswith("/editcover/{book_id}".format(book_id=99999999)):
        files = {"cover": ("cover.jpg", io.BytesIO(original_cover_bytes), "image/jpeg")}
        r = admin_session.post(url, files=files, headers=auth_headers(admin_session))
    else:
        r = admin_session.post(url, headers=auth_headers(admin_session), data={"action": "delete"})
    assert r.status_code == 404


def test_list_cover_history_empty(admin_session):
    r = admin_session.get(BASE_URL + f"/ajax/coverhistory/{BOOK_ID}")
    assert r.status_code == 200
    data = r.json()
    assert data["success"] is True
    assert data["versions"] == []


def test_edit_cover_saves_and_snapshots_previous(admin_session, original_cover_bytes):
    # upload edited cover (same bytes are fine, endpoint should still snapshot previous)
    files = {"cover": ("cover.jpg", io.BytesIO(original_cover_bytes), "image/jpeg")}
    r = admin_session.post(
        BASE_URL + f"/ajax/editcover/{BOOK_ID}",
        files=files,
        headers=auth_headers(admin_session),
    )
    assert r.status_code == 200
    data = r.json()
    assert data["success"] is True
    assert "cover_url" in data and f"/cover/{BOOK_ID}/" in data["cover_url"]
    assert "last_modified" in data

    # current cover still valid jpeg bytes
    with open(COVER_PATH, "rb") as f:
        assert f.read() == original_cover_bytes

    # previous cover snapshotted
    r = admin_session.get(BASE_URL + f"/ajax/coverhistory/{BOOK_ID}")
    assert r.status_code == 200
    versions = r.json()["versions"]
    assert len(versions) == 1
    name = versions[0]["name"]
    assert name.startswith("cover_") and name.endswith(".jpg")
    snap_path = os.path.join(HISTORY_DIR, name)
    assert os.path.isfile(snap_path)
    with open(snap_path, "rb") as f:
        assert f.read() == original_cover_bytes


def test_edit_cover_rejects_missing_file(admin_session):
    r = admin_session.post(
        BASE_URL + f"/ajax/editcover/{BOOK_ID}",
        data={},
        headers=auth_headers(admin_session),
    )
    assert r.status_code == 400
    data = r.json()
    assert data["success"] is False


def test_edit_cover_rejects_empty_file(admin_session):
    files = {"cover": ("cover.jpg", io.BytesIO(b""), "image/jpeg")}
    r = admin_session.post(
        BASE_URL + f"/ajax/editcover/{BOOK_ID}",
        files=files,
        headers=auth_headers(admin_session),
    )
    assert r.status_code == 400
    data = r.json()
    assert data["success"] is False


def test_edit_cover_cache_bust_and_last_modified(admin_session, original_cover_bytes):
    files = {"cover": ("cover.jpg", io.BytesIO(original_cover_bytes), "image/jpeg")}
    r = admin_session.post(
        BASE_URL + f"/ajax/editcover/{BOOK_ID}",
        files=files,
        headers=auth_headers(admin_session),
    )
    assert r.status_code == 200
    data = r.json()
    assert data["success"] is True
    assert f"/cover/{BOOK_ID}/" in data["cover_url"]
    assert "c=" in data["cover_url"]
    assert "last_modified" in data and "T" in data["last_modified"]


def test_edit_cover_multiple_saves_create_ordered_history(admin_session, original_cover_bytes):
    # create three snapshots via three saves
    names_seen = []
    for i in range(3):
        files = {"cover": ("cover.jpg", io.BytesIO(original_cover_bytes), "image/jpeg")}
        r = admin_session.post(BASE_URL + f"/ajax/editcover/{BOOK_ID}", files=files, headers=auth_headers(admin_session))
        assert r.status_code == 200 and r.json()["success"] is True
        versions = admin_session.get(BASE_URL + f"/ajax/coverhistory/{BOOK_ID}").json()["versions"]
        assert len(versions) == i + 1
        names_seen = [v["name"] for v in versions]
        # newest-first by name (timestamp-based names)
        assert names_seen == sorted(names_seen, reverse=True)
        # small delay to ensure distinct timestamps when resolution is coarse in some envs
        time.sleep(0.01)

    # all snapshot files exist and match original bytes
    for name in names_seen:
        p = os.path.join(HISTORY_DIR, name)
        assert os.path.isfile(p)
        with open(p, "rb") as f:
            assert f.read() == original_cover_bytes


def test_delete_cover_history_version(admin_session, original_cover_bytes):
    # create a version
    files = {"cover": ("cover.jpg", io.BytesIO(original_cover_bytes), "image/jpeg")}
    r = admin_session.post(BASE_URL + f"/ajax/editcover/{BOOK_ID}", files=files, headers=auth_headers(admin_session))
    assert r.json()["success"] is True
    versions = admin_session.get(BASE_URL + f"/ajax/coverhistory/{BOOK_ID}").json()["versions"]
    assert len(versions) == 1
    name = versions[0]["name"]

    r = admin_session.post(
        BASE_URL + f"/ajax/coverhistory/{BOOK_ID}/{name}",
        data={"action": "delete"},
        headers=auth_headers(admin_session),
    )
    assert r.status_code == 200
    assert r.json()["success"] is True
    versions = admin_session.get(BASE_URL + f"/ajax/coverhistory/{BOOK_ID}").json()["versions"]
    assert versions == []
    assert not os.path.exists(os.path.join(HISTORY_DIR, name))


@pytest.mark.parametrize("name", [
    "../cover.jpg",
    "cover.jpg",
    ".hidden",
    "subdir/cover_20240101T010101000000Z.jpg",
])
def test_delete_cover_history_rejects_unsafe_name(admin_session, name):
    # use requests with path; some names may be normalized by server/client, but must be safe
    url = BASE_URL + f"/ajax/coverhistory/{BOOK_ID}/" + name
    r = admin_session.post(url, data={"action": "delete"}, headers=auth_headers(admin_session))
    # route/validation should reject; must not delete actual cover
    assert r.status_code in (400, 404)
    assert os.path.isfile(COVER_PATH)


def test_delete_cover_history_missing_version_returns_404(admin_session):
    r = admin_session.post(
        BASE_URL + f"/ajax/coverhistory/{BOOK_ID}/cover_20990101T010101000000Z.jpg",
        data={"action": "delete"},
        headers=auth_headers(admin_session),
    )
    assert r.status_code == 404
    assert r.json()["success"] is False


def test_delete_cover_history_via_delete_method(admin_session, original_cover_bytes):
    files = {"cover": ("cover.jpg", io.BytesIO(original_cover_bytes), "image/jpeg")}
    r = admin_session.post(BASE_URL + f"/ajax/editcover/{BOOK_ID}", files=files, headers=auth_headers(admin_session))
    assert r.json()["success"] is True
    name = admin_session.get(BASE_URL + f"/ajax/coverhistory/{BOOK_ID}").json()["versions"][0]["name"]

    r = admin_session.delete(
        BASE_URL + f"/ajax/coverhistory/{BOOK_ID}/{name}",
        headers=auth_headers(admin_session),
    )
    assert r.status_code == 200
    assert r.json()["success"] is True
    assert admin_session.get(BASE_URL + f"/ajax/coverhistory/{BOOK_ID}").json()["versions"] == []


def test_delete_cover_history_rejects_invalid_action(admin_session, original_cover_bytes):
    files = {"cover": ("cover.jpg", io.BytesIO(original_cover_bytes), "image/jpeg")}
    r = admin_session.post(BASE_URL + f"/ajax/editcover/{BOOK_ID}", files=files, headers=auth_headers(admin_session))
    assert r.json()["success"] is True
    name = admin_session.get(BASE_URL + f"/ajax/coverhistory/{BOOK_ID}").json()["versions"][0]["name"]

    r = admin_session.post(
        BASE_URL + f"/ajax/coverhistory/{BOOK_ID}/{name}",
        data={"action": "restore"},
        headers=auth_headers(admin_session),
    )
    assert r.status_code == 400
    assert r.json()["success"] is False
    # version should still exist
    versions = admin_session.get(BASE_URL + f"/ajax/coverhistory/{BOOK_ID}").json()["versions"]
    assert [v["name"] for v in versions] == [name]


def test_restore_cover_history_version(admin_session, original_cover_bytes):
    # create first snapshot of original
    files = {"cover": ("cover.jpg", io.BytesIO(original_cover_bytes), "image/jpeg")}
    r = admin_session.post(BASE_URL + f"/ajax/editcover/{BOOK_ID}", files=files, headers=auth_headers(admin_session))
    assert r.json()["success"] is True
    versions = admin_session.get(BASE_URL + f"/ajax/coverhistory/{BOOK_ID}").json()["versions"]
    assert len(versions) == 1
    old_name = versions[0]["name"]

    # change current cover to different bytes
    new_bytes = original_cover_bytes[:1024] + b"XYZCHANGED"
    if new_bytes[:3] != b"\xff\xd8\xff":
        new_bytes = b"\xff\xd8\xff" + new_bytes[3:]
    files = {"cover": ("cover.jpg", io.BytesIO(new_bytes), "image/jpeg")}
    r = admin_session.post(BASE_URL + f"/ajax/editcover/{BOOK_ID}", files=files, headers=auth_headers(admin_session))
    assert r.json()["success"] is True
    with open(COVER_PATH, "rb") as f:
        assert f.read() == new_bytes

    # restore old version
    r = admin_session.post(
        BASE_URL + f"/ajax/coverhistory/{BOOK_ID}/{old_name}/restore",
        headers=auth_headers(admin_session),
    )
    assert r.status_code == 200
    data = r.json()
    assert data["success"] is True
    assert "cover_url" in data and f"/cover/{BOOK_ID}/" in data["cover_url"]
    assert "c=" in data["cover_url"]

    with open(COVER_PATH, "rb") as f:
        assert f.read() == original_cover_bytes

    # restoring should have snapshotted the (then-current new_bytes) cover; old_name still exists too
    versions = admin_session.get(BASE_URL + f"/ajax/coverhistory/{BOOK_ID}").json()["versions"]
    names = [v["name"] for v in versions]
    assert old_name in names
    # find a snapshot containing new_bytes
    found_new = False
    for v in versions:
        with open(os.path.join(HISTORY_DIR, v["name"]), "rb") as f:
            if f.read() == new_bytes:
                found_new = True
                break
    assert found_new


def test_restore_cover_history_missing_version_returns_404(admin_session):
    r = admin_session.post(
        BASE_URL + f"/ajax/coverhistory/{BOOK_ID}/cover_20990101T010101000000Z.jpg/restore",
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
def test_restore_cover_history_rejects_unsafe_name(admin_session, name):
    url = BASE_URL + f"/ajax/coverhistory/{BOOK_ID}/" + name + "/restore"
    r = admin_session.post(url, headers=auth_headers(admin_session))
    assert r.status_code in (400, 404)
    if r.headers.get("Content-Type", "").startswith("application/json"):
        assert r.json().get("success") is False
    assert os.path.isfile(COVER_PATH)
