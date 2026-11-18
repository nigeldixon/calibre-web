import os
from pathlib import Path

import pytest


def test_history_dir_name_inside_book_path(tmp_path):
    from cps import cover_history

    book_dir = tmp_path / "Author" / "Book (1)"
    book_dir.mkdir(parents=True)
    hdir = cover_history.history_dir(str(book_dir))
    assert hdir == str(book_dir / ".cover_history")


def test_ensure_history_dir_creates_directory(tmp_path):
    from cps import cover_history

    book_dir = tmp_path / "book"
    book_dir.mkdir()
    hdir = cover_history.ensure_history_dir(str(book_dir))
    assert os.path.isdir(hdir)
    assert hdir == str(book_dir / ".cover_history")


def test_snapshot_cover_copies_current_cover_into_history(tmp_path):
    from cps import cover_history

    book_dir = tmp_path / "book"
    book_dir.mkdir()
    cover = book_dir / "cover.jpg"
    cover.write_bytes(b"\xff\xd8\xff\xdbFAKEJPEG")

    version = cover_history.snapshot_cover(str(book_dir))
    assert version is not None
    assert version.endswith(".jpg")
    assert (book_dir / ".cover_history").is_dir()
    saved = Path(book_dir / ".cover_history" / version)
    assert saved.is_file()
    assert saved.read_bytes() == b"\xff\xd8\xff\xdbFAKEJPEG"
    # original still present
    assert cover.read_bytes() == b"\xff\xd8\xff\xdbFAKEJPEG"


def test_snapshot_cover_returns_none_when_no_cover(tmp_path):
    from cps import cover_history

    book_dir = tmp_path / "book"
    book_dir.mkdir()
    assert cover_history.snapshot_cover(str(book_dir)) is None


def test_list_cover_versions_sorted_newest_first(tmp_path):
    from cps import cover_history

    book_dir = tmp_path / "book"
    book_dir.mkdir()
    hdir = book_dir / ".cover_history"
    hdir.mkdir()
    (hdir / "cover_20240101T010101000000Z.jpg").write_bytes(b"a")
    (hdir / "cover_20240301T010101000000Z.jpg").write_bytes(b"c")
    (hdir / "cover_20240201T010101000000Z.jpg").write_bytes(b"b")

    versions = cover_history.list_cover_versions(str(book_dir))
    names = [v["name"] for v in versions]
    assert names == [
        "cover_20240301T010101000000Z.jpg",
        "cover_20240201T010101000000Z.jpg",
        "cover_20240101T010101000000Z.jpg",
    ]
    for v in versions:
        assert "name" in v and "size" in v and "mtime" in v


def test_list_cover_versions_empty_when_no_history(tmp_path):
    from cps import cover_history

    book_dir = tmp_path / "book"
    book_dir.mkdir()
    assert cover_history.list_cover_versions(str(book_dir)) == []


def test_delete_cover_version_removes_file(tmp_path):
    from cps import cover_history

    book_dir = tmp_path / "book"
    book_dir.mkdir()
    hdir = book_dir / ".cover_history"
    hdir.mkdir()
    vf = hdir / "cover_20240101T010101000000Z.jpg"
    vf.write_bytes(b"a")

    assert cover_history.delete_cover_version(str(book_dir), vf.name) is True
    assert not vf.exists()


@pytest.mark.parametrize("name", [
    "../cover.jpg",
    "..\\cover.jpg",
    "/etc/passwd",
    "subdir/cover_20240101T010101000000Z.jpg",
    "cover.jpg",
    "",
    ".hidden",
])
def test_delete_cover_version_rejects_unsafe_names(tmp_path, name):
    from cps import cover_history

    book_dir = tmp_path / "book"
    book_dir.mkdir()
    hdir = book_dir / ".cover_history"
    hdir.mkdir()
    # create a decoy file outside/inside as needed not required
    assert cover_history.delete_cover_version(str(book_dir), name) is False


def test_delete_cover_version_returns_false_for_missing(tmp_path):
    from cps import cover_history

    book_dir = tmp_path / "book"
    book_dir.mkdir()
    hdir = book_dir / ".cover_history"
    hdir.mkdir()
    assert cover_history.delete_cover_version(str(book_dir), "cover_20990101T010101000000Z.jpg") is False


def test_is_safe_version_name_accepts_generated_pattern():
    from cps import cover_history

    assert cover_history.is_safe_version_name("cover_20240101T010101000000Z.jpg") is True


def test_restore_cover_version_promotes_history_to_current(tmp_path):
    from cps import cover_history

    book_dir = tmp_path / "book"
    book_dir.mkdir()
    current = book_dir / "cover.jpg"
    current.write_bytes(b"current")
    hdir = book_dir / ".cover_history"
    hdir.mkdir()
    old = hdir / "cover_20240101T010101000000Z.jpg"
    old.write_bytes(b"old")

    # restore should copy old over cover.jpg and snapshot previous current
    result = cover_history.restore_cover_version(str(book_dir), old.name)
    assert result is True
    assert current.read_bytes() == b"old"
    # previous current should have been snapshotted (at least one history file remains/added)
    files = list(hdir.iterdir())
    assert old.exists()  # kept in history
    assert any(f.read_bytes() == b"current" for f in files)
