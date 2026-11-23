"""Regression tests for Kobo sync invalidation in background conversion tasks."""
from contextlib import nullcontext
from datetime import datetime, timezone
from types import SimpleNamespace

from cps import helper  # noqa: F401  # breaks the helper/convert import cycle
from cps.tasks import convert


class _FakeQuery:
    def filter(self, *args):
        return self

    def one_or_none(self):
        return None


class _FakeSession:
    def __init__(self, on_commit=None):
        self.commits = []
        self.merged = []
        self.on_commit = on_commit

    def query(self, model):
        return _FakeQuery()

    def merge(self, obj):
        self.merged.append(obj)

    def commit(self):
        self.commits.append(True)
        if self.on_commit:
            self.on_commit()


def test_convert_marks_book_changed_for_kobo_sync(monkeypatch):
    timestamp = datetime(2024, 3, 4, 5, 6, tzinfo=timezone.utc)

    class FixedDateTime:
        @staticmethod
        def now(tz):
            assert tz == timezone.utc
            return timestamp

    monkeypatch.setattr(convert, "datetime", FixedDateTime)

    book = SimpleNamespace(
        id=32,
        path="Author/Book",
        title="Book",
        has_cover=0,
        last_modified=None,
        data=[SimpleNamespace(name="book")],
    )
    dirty = []
    commit_states = []
    session = _FakeSession(on_commit=lambda: commit_states.append(
        (book.last_modified, list(dirty))
    ))
    local_db = SimpleNamespace(
        get_book=lambda book_id: book,
        get_book_format=lambda book_id, book_format: None,
        set_metadata_dirty=lambda book_id: dirty.append(book_id),
        session=session,
    )

    monkeypatch.setattr(convert, "app", SimpleNamespace(app_context=lambda: nullcontext()))
    class FakeData:
        book = "data.book"
        format = "data.format"

        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    monkeypatch.setattr(convert, "db", SimpleNamespace(
        CalibreDB=lambda app: local_db,
        Data=FakeData,
    ))
    monkeypatch.setattr(convert.config, "config_use_google_drive", False, raising=False)
    monkeypatch.setattr(convert.config, "config_kepubifypath", False, raising=False)
    monkeypatch.setattr(convert.config, "config_converterpath", "/fake/converter", raising=False)
    monkeypatch.setattr(convert, "resolve_binary_path", lambda *args: None)

    isfile_results = [False, True]
    monkeypatch.setattr(convert.os.path, "isfile",
                        lambda path: isfile_results.pop(0) if isfile_results else True)
    monkeypatch.setattr(convert.os.path, "exists", lambda path: True)
    monkeypatch.setattr(convert.os.path, "getsize", lambda path: 123)

    task = object.__new__(convert.TaskConvert)
    task.file_path = "/library/book"
    task.book_id = 32
    task.settings = {"old_book_format": "EPUB", "new_book_format": "KEPUB"}
    task.results = {}
    task.title = ""
    handled = []
    task._handleSuccess = lambda: handled.append("success")
    task._handleError = lambda message: handled.append(("error", message))
    task._convert_calibre = lambda *args: (0, None)

    result = task._convert_ebook_format()

    assert result == "book.kepub"
    assert book.last_modified == timestamp
    assert dirty == [32]
    assert commit_states == [(timestamp, [32])]
    assert session.commits == [True]
    assert session.merged[0].uncompressed_size == 123
    assert handled == ["success"]
