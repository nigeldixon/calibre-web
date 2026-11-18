import os
import sys
import types
from types import SimpleNamespace

import pytest


# Prevent heavy gdriveutils import side effects when importing cps.helper in unit tests.
if "cps.gdriveutils" not in sys.modules:
    sys.modules["cps.gdriveutils"] = types.ModuleType("cps.gdriveutils")


def _book(book_id=1, path="Author/Book (1)"):
    return SimpleNamespace(id=book_id, path=path)


def test_delete_book_file_removes_book_dir_and_cover_history(tmp_path, monkeypatch):
    from cps import helper

    calibre = tmp_path / "library"
    book_dir = calibre / "Author" / "Book (1)"
    history = book_dir / ".cover_history"
    history.mkdir(parents=True)
    (book_dir / "cover.jpg").write_bytes(b"cover")
    (book_dir / "book.epub").write_bytes(b"epub")
    (history / "cover_20240101T010101000000Z.jpg").write_bytes(b"old")
    author_dir = calibre / "Author"
    # author dir not empty after delete? only this book folder exists
    monkeypatch.setattr(helper, "_", lambda s, *a, **k: s)

    ok, err = helper.delete_book_file(_book(1, "Author/Book (1)"), str(calibre))

    assert ok is True
    assert err is None
    assert not book_dir.exists()
    # author dir removed if empty
    assert not author_dir.exists()


def test_delete_book_file_ignores_oserror_when_removing_subdir(tmp_path, monkeypatch):
    from cps import helper

    calibre = tmp_path / "library"
    book_dir = calibre / "Author" / "Book (1)"
    history = book_dir / ".cover_history"
    history.mkdir(parents=True)
    (book_dir / "cover.jpg").write_bytes(b"cover")
    (history / "cover_old.jpg").write_bytes(b"old")
    monkeypatch.setattr(helper, "_", lambda s, *a, **k: s)

    real_rmdir = os.rmdir

    def flaky_rmdir(path, *args, **kwargs):
        # fail once for history dir to exercise ignored OSError, then allow removal
        if os.path.basename(path) == ".cover_history" and not getattr(flaky_rmdir, "raised", False):
            flaky_rmdir.raised = True
            raise OSError("busy")
        return real_rmdir(path, *args, **kwargs)

    monkeypatch.setattr(helper.os, "rmdir", flaky_rmdir)

    ok, err = helper.delete_book_file(_book(1, "Author/Book (1)"), str(calibre))

    assert ok is True
    assert err is None
    assert not book_dir.exists()


def test_delete_book_file_rmtree_oserror_swallowed_when_path_gone(tmp_path, monkeypatch):
    from cps import helper

    calibre = tmp_path / "library"
    book_dir = calibre / "Author" / "Book (1)"
    book_dir.mkdir(parents=True)
    (book_dir / "cover.jpg").write_bytes(b"cover")
    monkeypatch.setattr(helper, "_", lambda s, *a, **k: s)

    real_rmtree = helper.shutil.rmtree

    def rmtree_remove_then_raise(path):
        if os.path.abspath(path) == os.path.abspath(str(book_dir)):
            if os.path.isdir(path):
                real_rmtree(path)
            raise OSError("gone anyway")
        return real_rmtree(path)

    monkeypatch.setattr(helper.shutil, "rmtree", rmtree_remove_then_raise)

    ok, err = helper.delete_book_file(_book(1, "Author/Book (1)"), str(calibre))

    assert ok is True
    assert err is None
    assert not book_dir.exists()


def test_delete_book_file_rmtree_oserror_reraised_when_path_exists(tmp_path, monkeypatch):
    from cps import helper

    calibre = tmp_path / "library"
    book_dir = calibre / "Author" / "Book (1)"
    book_dir.mkdir(parents=True)
    (book_dir / "cover.jpg").write_bytes(b"cover")
    monkeypatch.setattr(helper, "_", lambda s, *a, **k: s)
    monkeypatch.setattr(helper.log, "error", lambda *a, **k: None)

    def rmtree_fail(path):
        if os.path.abspath(path) == os.path.abspath(str(book_dir)):
            raise OSError("cannot remove")
        raise AssertionError("unexpected path")

    monkeypatch.setattr(helper.shutil, "rmtree", rmtree_fail)

    ok, err = helper.delete_book_file(_book(1, "Author/Book (1)"), str(calibre))

    assert ok is False
    assert err is not None
    assert book_dir.exists()


def test_move_files_on_change_merge_moves_cover_history_and_cleans_source(tmp_path, monkeypatch):
    from cps import helper

    calibre = tmp_path / "library"
    src = calibre / "Old Author" / "Old Book (1)"
    dst = calibre / "New Author" / "New Book (1)"
    src_history = src / ".cover_history"
    dst.mkdir(parents=True)
    src_history.mkdir(parents=True)
    (src / "cover.jpg").write_bytes(b"cover")
    (src / "book.epub").write_bytes(b"epub")
    (src_history / "cover_20240101T010101000000Z.jpg").write_bytes(b"old")
    # pre-existing file in dst should remain
    (dst / "existing.txt").write_bytes(b"keep")

    localbook = SimpleNamespace(id=1, path="Old Author/Old Book (1)")
    monkeypatch.setattr(helper, "_", lambda s, *a, **k: s)
    monkeypatch.setattr(helper.log, "info", lambda *a, **k: None)
    monkeypatch.setattr(helper.log, "debug", lambda *a, **k: None)

    result = helper.move_files_on_change(
        str(calibre),
        "New Author",
        "New Book (1)",
        localbook,
        None,
        None,  # triggers merge/copy branch when dst exists
        str(src),
    )

    assert result is False
    assert localbook.path == "New Author/New Book (1)"
    assert (dst / "cover.jpg").read_bytes() == b"cover"
    assert (dst / "book.epub").read_bytes() == b"epub"
    assert (dst / "existing.txt").read_bytes() == b"keep"
    assert (dst / ".cover_history" / "cover_20240101T010101000000Z.jpg").read_bytes() == b"old"
    assert not src.exists()
    # old author dir removed if empty
    assert not (calibre / "Old Author").exists()


def test_move_files_on_change_merge_ignores_rmdir_oserror_during_cleanup(tmp_path, monkeypatch):
    from cps import helper

    calibre = tmp_path / "library"
    src = calibre / "Author" / "Book (1)"
    dst = calibre / "Author" / "Book (2)"
    src_history = src / ".cover_history"
    dst.mkdir(parents=True)
    src_history.mkdir(parents=True)
    (src / "cover.jpg").write_bytes(b"cover")
    (src_history / "cover_old.jpg").write_bytes(b"old")

    localbook = SimpleNamespace(id=1, path="Author/Book (1)")
    monkeypatch.setattr(helper, "_", lambda s, *a, **k: s)
    monkeypatch.setattr(helper.log, "info", lambda *a, **k: None)
    monkeypatch.setattr(helper.log, "debug", lambda *a, **k: None)

    real_rmdir = os.rmdir
    raised_once = {"v": False}

    def rmdir_fail_once(path, *args, **kwargs):
        # fail cleanup rmdir for source root once to exercise except OSError: pass in cleanup loop
        if os.path.abspath(path) == os.path.abspath(str(src)) and not raised_once["v"]:
            raised_once["v"] = True
            raise OSError("busy")
        return real_rmdir(path, *args, **kwargs)

    monkeypatch.setattr(helper.os, "rmdir", rmdir_fail_once)

    result = helper.move_files_on_change(
        str(calibre), "Author", "Book (2)", localbook, None, None, str(src)
    )

    assert result is False
    assert (dst / ".cover_history" / "cover_old.jpg").exists()
    # cleanup loop ignored first rmdir error for src; src may still exist in that case
    assert raised_once["v"] is True
