import io
import os
import sys
import types
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from flask import Flask
from sqlalchemy.exc import IntegrityError
from werkzeug.datastructures import FileStorage
from werkzeug.exceptions import Forbidden, NotFound

# Prevent heavy gdriveutils import side effects when importing cps.editbooks in unit tests.
if "cps.gdriveutils" not in sys.modules:
    sys.modules["cps.gdriveutils"] = types.ModuleType("cps.gdriveutils")


def _unwrap(func):
    # peel stacked @wraps decorators (user_login_required/edit_required)
    seen = set()
    while hasattr(func, "__wrapped__") and func not in seen:
        seen.add(func)
        func = func.__wrapped__
    return func


def _make_app():
    app = Flask(__name__)
    app.config["TESTING"] = True
    return app


def _jpeg_bytes():
    # minimal JPEG header/signature accepted by our helper
    return b"\xff\xd8\xff\xe0" + b"JFIF" + b"\x00" * 64 + b"\xff\xd9"


def _file_storage(data, filename="cover.jpg", content_type="image/jpeg"):
    return FileStorage(stream=io.BytesIO(data), filename=filename, content_type=content_type)



def _fake_db(session, set_metadata_dirty=None, create_functions=None):
    return SimpleNamespace(
        session=session,
        create_functions=create_functions or (lambda cfg: None),
        set_metadata_dirty=set_metadata_dirty or (lambda book_id: None),
    )


class DummySession:
    def __init__(self, commit_exc=None):
        self.commit_exc = commit_exc
        self.merged = []
        self.rolled_back = 0
        self.commits = 0

    def merge(self, obj):
        self.merged.append(obj)
        return obj

    def commit(self):
        self.commits += 1
        if self.commit_exc:
            raise self.commit_exc

    def rollback(self):
        self.rolled_back += 1


def test_book_cover_path_uses_config_and_book_path(monkeypatch, tmp_path):
    from cps import editbooks

    monkeypatch.setattr(editbooks.config, "get_book_path", lambda: str(tmp_path / "library"))
    book = SimpleNamespace(path="Author/Book (1)")
    assert editbooks._book_cover_path(book) == str(tmp_path / "library" / "Author" / "Book (1)" / "cover.jpg")


def test_get_book_for_cover_edit_forbidden_without_upload_role(monkeypatch):
    from cps import editbooks

    monkeypatch.setattr(editbooks, "current_user", SimpleNamespace(role_upload=lambda: False))
    with pytest.raises(Forbidden):
        editbooks._get_book_for_cover_edit(1)


def test_get_book_for_cover_edit_404_when_book_missing(monkeypatch):
    from cps import editbooks

    monkeypatch.setattr(editbooks, "current_user", SimpleNamespace(role_upload=lambda: True))
    monkeypatch.setattr(editbooks, "calibre_db", SimpleNamespace(get_filtered_book=lambda book_id: None))
    with pytest.raises(NotFound):
        editbooks._get_book_for_cover_edit(123)


def test_get_book_for_cover_edit_returns_book(monkeypatch):
    from cps import editbooks

    book = SimpleNamespace(id=2, path="Bram Stoker/Dracula (2)")
    monkeypatch.setattr(editbooks, "current_user", SimpleNamespace(role_upload=lambda: True))
    monkeypatch.setattr(editbooks, "calibre_db", SimpleNamespace(get_filtered_book=lambda book_id: book))
    assert editbooks._get_book_for_cover_edit(2) is book


def test_save_edited_cover_bytes_jpeg_write_success(tmp_path):
    from cps import editbooks

    cover_path = tmp_path / "cover.jpg"
    data = _jpeg_bytes()
    assert editbooks._save_edited_cover_bytes(str(cover_path), data) is True
    assert cover_path.read_bytes() == data


def test_save_edited_cover_bytes_jpeg_write_oserror_returns_false(monkeypatch, tmp_path):
    from cps import editbooks

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("builtins.open", boom)
    assert editbooks._save_edited_cover_bytes(str(tmp_path / "cover.jpg"), _jpeg_bytes()) is False


def test_save_edited_cover_bytes_non_jpeg_returns_false_when_unconvertible(monkeypatch, tmp_path):
    from cps import editbooks

    # force both Wand and Pillow conversion paths to be unavailable/fail
    import cps.cover as cover_mod

    monkeypatch.setattr(cover_mod, "use_IM", False)
    # ensure Pillow import fails
    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "PIL" or name.startswith("PIL."):
            raise ImportError("no pillow")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    assert editbooks._save_edited_cover_bytes(str(tmp_path / "cover.jpg"), b"notjpeg") is False


def test_save_edited_cover_bytes_non_jpeg_wand_exception_then_pillow_success(monkeypatch, tmp_path):
    from cps import editbooks
    import cps.cover as cover_mod

    monkeypatch.setattr(cover_mod, "use_IM", True)

    class BoomImage:
        def __init__(self, blob=None):
            raise RuntimeError("wand boom")

    import types
    wand_mod = types.ModuleType("wand")
    wand_image_mod = types.ModuleType("wand.image")
    wand_image_mod.Image = BoomImage
    monkeypatch.setitem(__import__("sys").modules, "wand", wand_mod)
    monkeypatch.setitem(__import__("sys").modules, "wand.image", wand_image_mod)

    # Pillow fallback success
    class FakePILImageObj:
        def convert(self, mode):
            return self

        def save(self, path, format=None):
            with open(path, "wb") as f:
                f.write(b"jpeg-from-pillow")

    class FakePILImageModule:
        @staticmethod
        def open(stream):
            return FakePILImageObj()

    pil_mod = types.ModuleType("PIL")
    pil_image_mod = FakePILImageModule()
    pil_pkg = types.ModuleType("PIL")
    pil_pkg.Image = pil_image_mod
    monkeypatch.setitem(__import__("sys").modules, "PIL", pil_pkg)
    monkeypatch.setitem(__import__("sys").modules, "PIL.Image", pil_image_mod)

    cover_path = tmp_path / "cover.jpg"
    assert editbooks._save_edited_cover_bytes(str(cover_path), b"notjpeg") is True
    assert cover_path.read_bytes() == b"jpeg-from-pillow"


def test_save_edited_cover_bytes_non_jpeg_pillow_exception_returns_false(monkeypatch, tmp_path):
    from cps import editbooks
    import cps.cover as cover_mod

    monkeypatch.setattr(cover_mod, "use_IM", False)

    class BadPILImageObj:
        def convert(self, mode):
            return self

        def save(self, path, format=None):
            raise RuntimeError("pillow save fail")

    class BadPILImageModule:
        @staticmethod
        def open(stream):
            return BadPILImageObj()

    pil_pkg = types.ModuleType("PIL")
    pil_pkg.Image = BadPILImageModule()
    monkeypatch.setitem(__import__("sys").modules, "PIL", pil_pkg)
    monkeypatch.setitem(__import__("sys").modules, "PIL.Image", pil_pkg.Image)

    assert editbooks._save_edited_cover_bytes(str(tmp_path / "cover.jpg"), b"notjpeg") is False


def test_edit_cover_makedirs_oserror_returns_500(monkeypatch, tmp_path):
    from cps import editbooks
    monkeypatch.setattr(editbooks, "_", lambda s, *a, **k: s)

    book_dir = tmp_path / "book"
    book = SimpleNamespace(id=2, path="book", has_cover=0, last_modified=datetime.now(timezone.utc))
    app = _make_app()
    edit_cover = _unwrap(editbooks.edit_cover)

    monkeypatch.setattr(editbooks, "_get_book_for_cover_edit", lambda book_id: book)
    monkeypatch.setattr(editbooks.config, "get_book_path", lambda: str(tmp_path))
    monkeypatch.setattr(editbooks.log, "error_or_exception", lambda ex: None)

    def boom(*args, **kwargs):
        raise OSError("nope")

    monkeypatch.setattr(os, "makedirs", boom)

    with app.test_request_context(
        "/ajax/editcover/2",
        method="POST",
        data={"cover": _file_storage(_jpeg_bytes())},
        content_type="multipart/form-data",
    ):
        resp, status = edit_cover(2)
        assert status == 500
        assert resp.get_json()["success"] is False


def test_edit_cover_snapshot_exception_nonfatal_and_save_succeeds(monkeypatch, tmp_path):
    from cps import editbooks
    monkeypatch.setattr(editbooks, "_", lambda s, *a, **k: s)

    book_dir = tmp_path / "book"
    book_dir.mkdir()
    book = SimpleNamespace(id=2, path="book", has_cover=0, last_modified=datetime.now(timezone.utc))
    app = _make_app()
    edit_cover = _unwrap(editbooks.edit_cover)
    db_session = DummySession()

    monkeypatch.setattr(editbooks, "_get_book_for_cover_edit", lambda book_id: book)
    monkeypatch.setattr(editbooks.config, "get_book_path", lambda: str(tmp_path))
    monkeypatch.setattr(editbooks.log, "error_or_exception", lambda ex: None)
    monkeypatch.setattr(editbooks.cover_history, "snapshot_cover", lambda p: (_ for _ in ()).throw(RuntimeError("snap fail")))
    monkeypatch.setattr(editbooks, "_save_edited_cover_bytes", lambda path, data, mimetype=None: True)
    monkeypatch.setattr(editbooks, "calibre_db", _fake_db(db_session))
    monkeypatch.setattr(editbooks.kobo_sync_status, "remove_synced_book", lambda book_id: None)
    monkeypatch.setattr(editbooks.helper, "replace_cover_thumbnail_cache", lambda book_id: None)
    monkeypatch.setattr(editbooks, "url_for", lambda *a, **k: "/cover")

    with app.test_request_context(
        "/ajax/editcover/2",
        method="POST",
        data={"cover": _file_storage(_jpeg_bytes())},
        content_type="multipart/form-data",
    ):
        resp = edit_cover(2)
        data = resp.get_json()
        assert data["success"] is True
        assert db_session.commits == 1


def test_edit_cover_save_returns_false_returns_500(monkeypatch, tmp_path):
    from cps import editbooks
    monkeypatch.setattr(editbooks, "_", lambda s, *a, **k: s)

    book_dir = tmp_path / "book"
    book_dir.mkdir()
    book = SimpleNamespace(id=2, path="book", has_cover=0, last_modified=datetime.now(timezone.utc))
    app = _make_app()
    edit_cover = _unwrap(editbooks.edit_cover)

    monkeypatch.setattr(editbooks, "_get_book_for_cover_edit", lambda book_id: book)
    monkeypatch.setattr(editbooks.config, "get_book_path", lambda: str(tmp_path))
    monkeypatch.setattr(editbooks.log, "error_or_exception", lambda ex: None)
    monkeypatch.setattr(editbooks.cover_history, "snapshot_cover", lambda p: None)
    monkeypatch.setattr(editbooks, "_save_edited_cover_bytes", lambda path, data, mimetype=None: False)

    with app.test_request_context(
        "/ajax/editcover/2",
        method="POST",
        data={"cover": _file_storage(_jpeg_bytes())},
        content_type="multipart/form-data",
    ):
        resp, status = edit_cover(2)
        assert status == 500
        assert resp.get_json()["error"]


def test_edit_cover_save_raises_returns_500(monkeypatch, tmp_path):
    from cps import editbooks
    monkeypatch.setattr(editbooks, "_", lambda s, *a, **k: s)

    book_dir = tmp_path / "book"
    book_dir.mkdir()
    book = SimpleNamespace(id=2, path="book", has_cover=0, last_modified=datetime.now(timezone.utc))
    app = _make_app()
    edit_cover = _unwrap(editbooks.edit_cover)

    monkeypatch.setattr(editbooks, "_get_book_for_cover_edit", lambda book_id: book)
    monkeypatch.setattr(editbooks.config, "get_book_path", lambda: str(tmp_path))
    monkeypatch.setattr(editbooks.log, "error_or_exception", lambda ex: None)
    monkeypatch.setattr(editbooks.cover_history, "snapshot_cover", lambda p: None)

    def boom(*a, **k):
        raise RuntimeError("save boom")

    monkeypatch.setattr(editbooks, "_save_edited_cover_bytes", boom)

    with app.test_request_context(
        "/ajax/editcover/2",
        method="POST",
        data={"cover": _file_storage(_jpeg_bytes())},
        content_type="multipart/form-data",
    ):
        resp, status = edit_cover(2)
        assert status == 500
        assert resp.get_json()["success"] is False


def test_edit_cover_db_error_returns_500_and_rolls_back(monkeypatch, tmp_path):
    from cps import editbooks
    monkeypatch.setattr(editbooks, "_", lambda s, *a, **k: s)

    book_dir = tmp_path / "book"
    book_dir.mkdir()
    book = SimpleNamespace(id=2, path="book", has_cover=0, last_modified=datetime.now(timezone.utc))
    app = _make_app()
    edit_cover = _unwrap(editbooks.edit_cover)
    db_session = DummySession(commit_exc=IntegrityError("stmt", "params", "orig"))

    monkeypatch.setattr(editbooks, "_get_book_for_cover_edit", lambda book_id: book)
    monkeypatch.setattr(editbooks.config, "get_book_path", lambda: str(tmp_path))
    monkeypatch.setattr(editbooks.log, "error_or_exception", lambda ex: None)
    monkeypatch.setattr(editbooks.cover_history, "snapshot_cover", lambda p: None)
    monkeypatch.setattr(editbooks, "_save_edited_cover_bytes", lambda path, data, mimetype=None: True)
    monkeypatch.setattr(editbooks, "calibre_db", _fake_db(db_session))
    monkeypatch.setattr(editbooks.kobo_sync_status, "remove_synced_book", lambda book_id: None)

    with app.test_request_context(
        "/ajax/editcover/2",
        method="POST",
        data={"cover": _file_storage(_jpeg_bytes())},
        content_type="multipart/form-data",
    ):
        resp, status = edit_cover(2)
        assert status == 500
        assert resp.get_json()["success"] is False
        assert db_session.rolled_back == 1


def test_edit_cover_thumbnail_cache_exception_nonfatal(monkeypatch, tmp_path):
    from cps import editbooks
    monkeypatch.setattr(editbooks, "_", lambda s, *a, **k: s)

    book_dir = tmp_path / "book"
    book_dir.mkdir()
    book = SimpleNamespace(id=2, path="book", has_cover=0, last_modified=datetime.now(timezone.utc))
    app = _make_app()
    edit_cover = _unwrap(editbooks.edit_cover)
    db_session = DummySession()

    monkeypatch.setattr(editbooks, "_get_book_for_cover_edit", lambda book_id: book)
    monkeypatch.setattr(editbooks.config, "get_book_path", lambda: str(tmp_path))
    monkeypatch.setattr(editbooks.log, "error_or_exception", lambda ex: None)
    monkeypatch.setattr(editbooks.cover_history, "snapshot_cover", lambda p: None)
    monkeypatch.setattr(editbooks, "_save_edited_cover_bytes", lambda path, data, mimetype=None: True)
    monkeypatch.setattr(editbooks, "calibre_db", _fake_db(db_session))
    monkeypatch.setattr(editbooks.kobo_sync_status, "remove_synced_book", lambda book_id: None)
    monkeypatch.setattr(editbooks.helper, "replace_cover_thumbnail_cache", lambda book_id: (_ for _ in ()).throw(RuntimeError("cache fail")))
    monkeypatch.setattr(editbooks, "url_for", lambda *a, **k: "/cover")

    with app.test_request_context(
        "/ajax/editcover/2",
        method="POST",
        data={"cover": _file_storage(_jpeg_bytes())},
        content_type="multipart/form-data",
    ):
        resp = edit_cover(2)
        assert resp.get_json()["success"] is True


def test_restore_cover_history_db_error_returns_500_and_rolls_back(monkeypatch, tmp_path):
    from cps import editbooks
    monkeypatch.setattr(editbooks, "_", lambda s, *a, **k: s)

    book = SimpleNamespace(id=2, path="book", has_cover=1, last_modified=datetime.now(timezone.utc))
    app = _make_app()
    restore = _unwrap(editbooks.restore_cover_history)
    db_session = DummySession(commit_exc=IntegrityError("stmt", "params", "orig"))

    monkeypatch.setattr(editbooks, "_get_book_for_cover_edit", lambda book_id: book)
    monkeypatch.setattr(editbooks.config, "get_book_path", lambda: str(tmp_path))
    monkeypatch.setattr(editbooks.log, "error_or_exception", lambda ex: None)
    monkeypatch.setattr(editbooks.cover_history, "restore_cover_version", lambda book_dir, version: True)
    monkeypatch.setattr(editbooks, "calibre_db", _fake_db(db_session))
    monkeypatch.setattr(editbooks.kobo_sync_status, "remove_synced_book", lambda book_id: None)

    with app.test_request_context("/ajax/coverhistory/2/ver/restore", method="POST"):
        resp, status = restore(2, "cover_20240101T010101000000Z.jpg")
        assert status == 500
        assert resp.get_json()["success"] is False
        assert db_session.rolled_back == 1


def test_restore_cover_history_thumbnail_cache_exception_nonfatal(monkeypatch, tmp_path):
    from cps import editbooks
    monkeypatch.setattr(editbooks, "_", lambda s, *a, **k: s)

    book = SimpleNamespace(id=2, path="book", has_cover=1, last_modified=datetime.now(timezone.utc))
    app = _make_app()
    restore = _unwrap(editbooks.restore_cover_history)
    db_session = DummySession()

    monkeypatch.setattr(editbooks, "_get_book_for_cover_edit", lambda book_id: book)
    monkeypatch.setattr(editbooks.config, "get_book_path", lambda: str(tmp_path))
    monkeypatch.setattr(editbooks.log, "error_or_exception", lambda ex: None)
    monkeypatch.setattr(editbooks.cover_history, "restore_cover_version", lambda book_dir, version: True)
    monkeypatch.setattr(editbooks, "calibre_db", _fake_db(db_session))
    monkeypatch.setattr(editbooks.kobo_sync_status, "remove_synced_book", lambda book_id: None)
    monkeypatch.setattr(editbooks.helper, "replace_cover_thumbnail_cache", lambda book_id: (_ for _ in ()).throw(RuntimeError("cache fail")))
    monkeypatch.setattr(editbooks, "url_for", lambda *a, **k: "/cover")

    with app.test_request_context("/ajax/coverhistory/2/ver/restore", method="POST"):
        resp = restore(2, "cover_20240101T010101000000Z.jpg")
        assert resp.get_json()["success"] is True


def test_save_edited_cover_bytes_non_jpeg_wand_success(monkeypatch, tmp_path):
    from cps import editbooks
    import cps.cover as cover_mod

    monkeypatch.setattr(cover_mod, "use_IM", True)

    saved = {}

    class GoodImage:
        def __init__(self, blob=None):
            self.blob = blob
            self.format = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def transform_colorspace(self, space):
            saved["colorspace"] = space

        def save(self, filename=None):
            saved["filename"] = filename
            saved["format"] = self.format
            with open(filename, "wb") as f:
                f.write(b"jpeg-from-wand")

    wand_mod = types.ModuleType("wand")
    wand_image_mod = types.ModuleType("wand.image")
    wand_image_mod.Image = GoodImage
    monkeypatch.setitem(sys.modules, "wand", wand_mod)
    monkeypatch.setitem(sys.modules, "wand.image", wand_image_mod)

    cover_path = tmp_path / "cover.jpg"
    assert editbooks._save_edited_cover_bytes(str(cover_path), b"pngdata") is True
    assert cover_path.read_bytes() == b"jpeg-from-wand"
    assert saved["format"] == "jpeg"
    assert saved["colorspace"] == "srgb"


def test_save_edited_cover_bytes_non_jpeg_outer_exception_returns_false(monkeypatch, tmp_path):
    from cps import editbooks

    # make `from .cover import use_IM` raise to hit outer except Exception: pass
    class BadCover(types.ModuleType):
        @property
        def use_IM(self):
            raise RuntimeError("boom")

    bad = BadCover("cps.cover")
    # property on instance doesn't work; use __getattr__
    class BadCoverMod(types.ModuleType):
        def __getattr__(self, name):
            if name == "use_IM":
                raise RuntimeError("boom")
            raise AttributeError(name)

    monkeypatch.setitem(sys.modules, "cps.cover", BadCoverMod("cps.cover"))

    assert editbooks._save_edited_cover_bytes(str(tmp_path / "cover.jpg"), b"notjpeg") is False


def test_edit_cover_returns_400_when_no_file(monkeypatch, tmp_path):
    from cps import editbooks

    book = SimpleNamespace(id=2, path="book", has_cover=0, last_modified=datetime.now(timezone.utc))
    app = _make_app()
    edit_cover = _unwrap(editbooks.edit_cover)

    monkeypatch.setattr(editbooks, "_get_book_for_cover_edit", lambda book_id: book)
    monkeypatch.setattr(editbooks, "_", lambda s, *a, **k: s)

    with app.test_request_context("/ajax/editcover/2", method="POST", data={}):
        resp, status = edit_cover(2)
        assert status == 400
        assert resp.get_json()["success"] is False


def test_edit_cover_returns_400_when_empty_file_data(monkeypatch, tmp_path):
    from cps import editbooks

    book = SimpleNamespace(id=2, path="book", has_cover=0, last_modified=datetime.now(timezone.utc))
    app = _make_app()
    edit_cover = _unwrap(editbooks.edit_cover)

    monkeypatch.setattr(editbooks, "_get_book_for_cover_edit", lambda book_id: book)
    monkeypatch.setattr(editbooks, "_", lambda s, *a, **k: s)

    with app.test_request_context(
        "/ajax/editcover/2",
        method="POST",
        data={"cover": _file_storage(b"", filename="cover.jpg")},
        content_type="multipart/form-data",
    ):
        resp, status = edit_cover(2)
        assert status == 400
        assert resp.get_json()["success"] is False


def test_list_cover_history_success(monkeypatch, tmp_path):
    from cps import editbooks

    book = SimpleNamespace(id=2, path="book")
    app = _make_app()
    list_history = _unwrap(editbooks.list_cover_history)

    monkeypatch.setattr(editbooks, "_get_book_for_cover_edit", lambda book_id: book)
    monkeypatch.setattr(editbooks.config, "get_book_path", lambda: str(tmp_path))
    monkeypatch.setattr(editbooks.cover_history, "list_cover_versions", lambda p: [{"name": "cover_20240101T010101000000Z.jpg"}])

    with app.test_request_context("/ajax/coverhistory/2", method="GET"):
        resp = list_history(2)
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["versions"]) == 1


def test_delete_cover_history_post_invalid_action_returns_400(monkeypatch, tmp_path):
    from cps import editbooks

    book = SimpleNamespace(id=2, path="book")
    app = _make_app()
    delete_history = _unwrap(editbooks.delete_cover_history)

    monkeypatch.setattr(editbooks, "_get_book_for_cover_edit", lambda book_id: book)
    monkeypatch.setattr(editbooks, "_", lambda s, *a, **k: s)

    with app.test_request_context(
        "/ajax/coverhistory/2/ver.jpg",
        method="POST",
        data={"action": "nope"},
    ):
        resp, status = delete_history(2, "ver.jpg")
        assert status == 400
        assert resp.get_json()["success"] is False


def test_delete_cover_history_not_found_returns_404(monkeypatch, tmp_path):
    from cps import editbooks

    book = SimpleNamespace(id=2, path="book")
    app = _make_app()
    delete_history = _unwrap(editbooks.delete_cover_history)

    monkeypatch.setattr(editbooks, "_get_book_for_cover_edit", lambda book_id: book)
    monkeypatch.setattr(editbooks.config, "get_book_path", lambda: str(tmp_path))
    monkeypatch.setattr(editbooks.cover_history, "delete_cover_version", lambda p, v: False)
    monkeypatch.setattr(editbooks, "_", lambda s, *a, **k: s)

    with app.test_request_context("/ajax/coverhistory/2/ver.jpg", method="DELETE"):
        resp, status = delete_history(2, "ver.jpg")
        assert status == 404
        assert resp.get_json()["success"] is False


def test_delete_cover_history_success(monkeypatch, tmp_path):
    from cps import editbooks

    book = SimpleNamespace(id=2, path="book")
    app = _make_app()
    delete_history = _unwrap(editbooks.delete_cover_history)

    monkeypatch.setattr(editbooks, "_get_book_for_cover_edit", lambda book_id: book)
    monkeypatch.setattr(editbooks.config, "get_book_path", lambda: str(tmp_path))
    monkeypatch.setattr(editbooks.cover_history, "delete_cover_version", lambda p, v: True)

    with app.test_request_context("/ajax/coverhistory/2/ver.jpg", method="DELETE"):
        resp = delete_history(2, "ver.jpg")
        assert resp.get_json()["success"] is True


def test_restore_cover_history_not_found_returns_404(monkeypatch, tmp_path):
    from cps import editbooks

    book = SimpleNamespace(id=2, path="book")
    app = _make_app()
    restore = _unwrap(editbooks.restore_cover_history)

    monkeypatch.setattr(editbooks, "_get_book_for_cover_edit", lambda book_id: book)
    monkeypatch.setattr(editbooks.config, "get_book_path", lambda: str(tmp_path))
    monkeypatch.setattr(editbooks.cover_history, "restore_cover_version", lambda p, v: False)
    monkeypatch.setattr(editbooks, "_", lambda s, *a, **k: s)

    with app.test_request_context("/ajax/coverhistory/2/ver.jpg/restore", method="POST"):
        resp, status = restore(2, "ver.jpg")
        assert status == 404
        assert resp.get_json()["success"] is False


def test_edit_cover_returns_400_when_google_drive_enabled(monkeypatch):
    from cps import editbooks

    app = _make_app()
    edit_cover = _unwrap(editbooks.edit_cover)

    monkeypatch.setattr(editbooks.config, "config_use_google_drive", True, raising=False)
    monkeypatch.setattr(editbooks, "_", lambda s, *a, **k: s)

    with app.test_request_context("/ajax/editcover/2", method="POST", data={}):
        resp, status = edit_cover(2)
        assert status == 400
        data = resp.get_json()
        assert data["success"] is False
        assert "Google Drive" in data["error"]


def test_list_cover_history_returns_400_when_google_drive_enabled(monkeypatch):
    from cps import editbooks

    app = _make_app()
    list_history = _unwrap(editbooks.list_cover_history)

    monkeypatch.setattr(editbooks.config, "config_use_google_drive", True, raising=False)
    monkeypatch.setattr(editbooks, "_", lambda s, *a, **k: s)

    with app.test_request_context("/ajax/coverhistory/2", method="GET"):
        resp, status = list_history(2)
        assert status == 400
        data = resp.get_json()
        assert data["success"] is False
        assert "Google Drive" in data["error"]


def test_delete_cover_history_returns_400_when_google_drive_enabled(monkeypatch):
    from cps import editbooks

    app = _make_app()
    delete_history = _unwrap(editbooks.delete_cover_history)

    monkeypatch.setattr(editbooks.config, "config_use_google_drive", True, raising=False)
    monkeypatch.setattr(editbooks, "_", lambda s, *a, **k: s)

    with app.test_request_context("/ajax/coverhistory/2/ver.jpg", method="DELETE"):
        resp, status = delete_history(2, "ver.jpg")
        assert status == 400
        data = resp.get_json()
        assert data["success"] is False
        assert "Google Drive" in data["error"]


def test_restore_cover_history_returns_400_when_google_drive_enabled(monkeypatch):
    from cps import editbooks

    app = _make_app()
    restore = _unwrap(editbooks.restore_cover_history)

    monkeypatch.setattr(editbooks.config, "config_use_google_drive", True, raising=False)
    monkeypatch.setattr(editbooks, "_", lambda s, *a, **k: s)

    with app.test_request_context("/ajax/coverhistory/2/ver.jpg/restore", method="POST"):
        resp, status = restore(2, "ver.jpg")
        assert status == 400
        data = resp.get_json()
        assert data["success"] is False
        assert "Google Drive" in data["error"]
