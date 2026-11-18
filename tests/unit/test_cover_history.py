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


def test_restore_cover_version_when_no_current_cover(tmp_path):
    from cps import cover_history

    book_dir = tmp_path / "book"
    book_dir.mkdir()
    hdir = book_dir / ".cover_history"
    hdir.mkdir()
    old = hdir / "cover_20240101T010101000000Z.jpg"
    old.write_bytes(b"old-bytes")
    assert not (book_dir / "cover.jpg").exists()

    result = cover_history.restore_cover_version(str(book_dir), old.name)
    assert result is True
    assert (book_dir / "cover.jpg").read_bytes() == b"old-bytes"
    # version file remains
    assert old.is_file() and old.read_bytes() == b"old-bytes"
    # no spurious snapshot of a non-existent current cover created
    files = list(hdir.iterdir())
    assert files == [old]


def test_restore_cover_version_rejects_unsafe_names(tmp_path):
    from cps import cover_history

    book_dir = tmp_path / "book"
    book_dir.mkdir()
    (book_dir / "cover.jpg").write_bytes(b"current")
    hdir = book_dir / ".cover_history"
    hdir.mkdir()
    for name in ["../cover.jpg", "cover.jpg", "", ".hidden", "subdir/cover_20240101T010101000000Z.jpg"]:
        assert cover_history.restore_cover_version(str(book_dir), name) is False
    assert (book_dir / "cover.jpg").read_bytes() == b"current"


def test_restore_cover_version_returns_false_for_missing(tmp_path):
    from cps import cover_history

    book_dir = tmp_path / "book"
    book_dir.mkdir()
    (book_dir / "cover.jpg").write_bytes(b"current")
    hdir = book_dir / ".cover_history"
    hdir.mkdir()
    assert cover_history.restore_cover_version(str(book_dir), "cover_20990101T010101000000Z.jpg") is False
    assert (book_dir / "cover.jpg").read_bytes() == b"current"


def test_list_cover_versions_ignores_invalid_and_non_file_entries(tmp_path):
    from cps import cover_history

    book_dir = tmp_path / "book"
    book_dir.mkdir()
    hdir = book_dir / ".cover_history"
    hdir.mkdir()
    valid = hdir / "cover_20240201T010101000000Z.jpg"
    valid.write_bytes(b"valid-bytes")
    # invalid names
    (hdir / "cover.jpg").write_bytes(b"nope")
    (hdir / "notes.txt").write_bytes(b"nope")
    (hdir / "cover_20240101T010101000000Z.png").write_bytes(b"nope")
    # non-file entry with valid-looking name
    (hdir / "cover_20240301T010101000000Z.jpg").mkdir()

    versions = cover_history.list_cover_versions(str(book_dir))
    assert [v["name"] for v in versions] == [valid.name]
    assert versions[0]["size"] == len(b"valid-bytes")
    assert isinstance(versions[0]["mtime"], (int, float))


def test_is_safe_version_name_rejects_non_string_and_edge_cases():
    from cps import cover_history

    assert cover_history.is_safe_version_name(None) is False
    assert cover_history.is_safe_version_name(123) is False
    assert cover_history.is_safe_version_name(b"cover_20240101T010101000000Z.jpg") is False
    assert cover_history.is_safe_version_name("cover_20240101T010101000000Z.jpg/extra") is False
    assert cover_history.is_safe_version_name("cover_20240101T010101000000Z.jpg\\extra") is False


def test_snapshot_cover_creates_unique_version_files(tmp_path):
    from cps import cover_history

    book_dir = tmp_path / "book"
    book_dir.mkdir()
    (book_dir / "cover.jpg").write_bytes(b"\xff\xd8\xff\xdbA")
    v1 = cover_history.snapshot_cover(str(book_dir))
    (book_dir / "cover.jpg").write_bytes(b"\xff\xd8\xff\xdbB")
    v2 = cover_history.snapshot_cover(str(book_dir))
    assert v1 and v2 and v1 != v2
    hdir = book_dir / ".cover_history"
    assert (hdir / v1).read_bytes() == b"\xff\xd8\xff\xdbA"
    assert (hdir / v2).read_bytes() == b"\xff\xd8\xff\xdbB"


def test_delete_cover_version_rejects_path_traversal_outside_history(tmp_path):
    from cps import cover_history

    book_dir = tmp_path / "book"
    book_dir.mkdir()
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"keep")
    hdir = book_dir / ".cover_history"
    hdir.mkdir()
    # crafted traversal name should be rejected by safety checks
    assert cover_history.delete_cover_version(str(book_dir), "../outside.jpg") is False
    assert outside.read_bytes() == b"keep"



def test_is_safe_version_name_defensive_checks_via_relaxed_pattern(monkeypatch, tmp_path):
    import os
    import re
    from cps import cover_history

    # relax regex so defensive path/basename/separator checks are reachable
    monkeypatch.setattr(cover_history, "_VERSION_RE", re.compile(r".*"))
    assert cover_history.is_safe_version_name("dir/cover_20240101T010101000000Z.jpg") is False
    assert cover_history.is_safe_version_name("cover_20240101T010101000000Z.jpg") is True

    # altsep present in name (while basename == name) triggers separator rejection
    monkeypatch.setattr(cover_history.os.path, "altsep", "Z")
    assert cover_history.is_safe_version_name("cover_20240101T010101000000Z.jpg") is False


def test_snapshot_cover_uses_fallback_when_names_collide(monkeypatch, tmp_path):
    from cps import cover_history

    book_dir = tmp_path / "book"
    book_dir.mkdir()
    (book_dir / "cover.jpg").write_bytes(b"\xff\xd8\xff\xdbX")
    hdir = book_dir / ".cover_history"
    hdir.mkdir()
    fixed = "cover_20240101T010101000000Z.jpg"
    # occupy the fixed name so all 5 attempts in the loop collide
    (hdir / fixed).write_bytes(b"occupied")

    monkeypatch.setattr(cover_history, "_generate_version_name", lambda: fixed)
    monkeypatch.setattr("cps.cover_history.os.getpid", lambda: 12345)

    name = cover_history.snapshot_cover(str(book_dir))
    assert name is not None
    assert name != fixed
    assert name.startswith("cover_20240101T010101000000Z_12345") or name.endswith(".jpg")
    assert (hdir / name).read_bytes() == b"\xff\xd8\xff\xdbX"
    assert (hdir / fixed).read_bytes() == b"occupied"


def test_list_cover_versions_skips_entry_when_stat_raises(monkeypatch, tmp_path):
    import os
    from cps import cover_history

    book_dir = tmp_path / "book"
    book_dir.mkdir()
    hdir = book_dir / ".cover_history"
    hdir.mkdir()
    bad = hdir / "cover_20240201T010101000000Z.jpg"
    good = hdir / "cover_20240101T010101000000Z.jpg"
    bad.write_bytes(b"bad")
    good.write_bytes(b"good")

    real_scandir = os.scandir

    class FakeEntry:
        def __init__(self, name, path):
            self.name = name
            self.path = path
        def is_file(self):
            return True
        def stat(self):
            if self.name == bad.name:
                raise OSError("boom")
            return os.stat(self.path)

    class FakeScandir:
        def __init__(self, path):
            self._it = iter([FakeEntry(bad.name, str(bad)), FakeEntry(good.name, str(good))])
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return False
        def __iter__(self):
            return self._it

    monkeypatch.setattr("cps.cover_history.os.scandir", lambda p: FakeScandir(p))
    versions = cover_history.list_cover_versions(str(book_dir))
    assert [v["name"] for v in versions] == [good.name]


def test_delete_cover_version_returns_false_when_realpath_raises(monkeypatch, tmp_path):
    from cps import cover_history

    book_dir = tmp_path / "book"
    book_dir.mkdir()
    hdir = book_dir / ".cover_history"
    hdir.mkdir()
    vf = hdir / "cover_20240101T010101000000Z.jpg"
    vf.write_bytes(b"x")

    def boom(path):
        raise OSError("boom")

    monkeypatch.setattr("cps.cover_history.os.path.realpath", boom)
    assert cover_history.delete_cover_version(str(book_dir), vf.name) is False


def test_delete_cover_version_rejects_symlink_pointing_outside_history(tmp_path):
    from cps import cover_history

    book_dir = tmp_path / "book"
    book_dir.mkdir()
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"outside")
    hdir = book_dir / ".cover_history"
    hdir.mkdir()
    name = "cover_20240101T010101000000Z.jpg"
    link = hdir / name
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        import pytest
        pytest.skip("symlinks not supported")

    assert cover_history.delete_cover_version(str(book_dir), name) is False
    assert outside.read_bytes() == b"outside"


def test_delete_cover_version_returns_false_when_remove_raises(monkeypatch, tmp_path):
    from cps import cover_history

    book_dir = tmp_path / "book"
    book_dir.mkdir()
    hdir = book_dir / ".cover_history"
    hdir.mkdir()
    vf = hdir / "cover_20240101T010101000000Z.jpg"
    vf.write_bytes(b"x")

    monkeypatch.setattr("cps.cover_history.os.remove", lambda p: (_ for _ in ()).throw(OSError("boom")))
    assert cover_history.delete_cover_version(str(book_dir), vf.name) is False
    assert vf.exists()


def test_restore_cover_version_returns_false_when_realpath_raises(monkeypatch, tmp_path):
    from cps import cover_history

    book_dir = tmp_path / "book"
    book_dir.mkdir()
    (book_dir / "cover.jpg").write_bytes(b"current")
    hdir = book_dir / ".cover_history"
    hdir.mkdir()
    vf = hdir / "cover_20240101T010101000000Z.jpg"
    vf.write_bytes(b"old")

    def boom(path):
        raise OSError("boom")

    monkeypatch.setattr("cps.cover_history.os.path.realpath", boom)
    assert cover_history.restore_cover_version(str(book_dir), vf.name) is False
    assert (book_dir / "cover.jpg").read_bytes() == b"current"


def test_restore_cover_version_rejects_symlink_pointing_outside_history(tmp_path):
    from cps import cover_history

    book_dir = tmp_path / "book"
    book_dir.mkdir()
    (book_dir / "cover.jpg").write_bytes(b"current")
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"outside")
    hdir = book_dir / ".cover_history"
    hdir.mkdir()
    name = "cover_20240101T010101000000Z.jpg"
    link = hdir / name
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        import pytest
        pytest.skip("symlinks not supported")

    assert cover_history.restore_cover_version(str(book_dir), name) is False
    assert (book_dir / "cover.jpg").read_bytes() == b"current"


def test_restore_cover_version_returns_false_when_copy_raises(monkeypatch, tmp_path):
    import os
    import shutil
    from cps import cover_history

    book_dir = tmp_path / "book"
    book_dir.mkdir()
    cover = book_dir / "cover.jpg"
    cover.write_bytes(b"current")
    hdir = book_dir / ".cover_history"
    hdir.mkdir()
    vf = hdir / "cover_20240101T010101000000Z.jpg"
    vf.write_bytes(b"old")

    real_copy2 = shutil.copy2

    def boom(src, dst):
        # allow snapshot of current cover into history; fail restore copy to cover.jpg
        if os.path.normpath(dst) == os.path.normpath(str(cover)):
            raise OSError("boom")
        return real_copy2(src, dst)

    monkeypatch.setattr("cps.cover_history.shutil.copy2", boom)
    assert cover_history.restore_cover_version(str(book_dir), vf.name) is False


def test_restore_cover_version_returns_false_when_snapshot_raises(monkeypatch, tmp_path):
    from cps import cover_history

    book_dir = tmp_path / "book"
    book_dir.mkdir()
    (book_dir / "cover.jpg").write_bytes(b"current")
    hdir = book_dir / ".cover_history"
    hdir.mkdir()
    vf = hdir / "cover_20240101T010101000000Z.jpg"
    vf.write_bytes(b"old")

    def boom(path):
        raise OSError("boom")

    monkeypatch.setattr(cover_history, "snapshot_cover", boom)
    assert cover_history.restore_cover_version(str(book_dir), vf.name) is False
    assert (book_dir / "cover.jpg").read_bytes() == b"current"
