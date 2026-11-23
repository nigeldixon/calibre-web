"""Tests for Kobo archive state changes visible to token-based sync."""
from datetime import datetime, timezone
from types import SimpleNamespace

from cps import kobo_sync_status


class _FirstResultQuery:
    """Query stand-in that records filters and returns one row."""

    def __init__(self, row):
        self.row = row
        self.filters = []

    def filter(self, *args):
        self.filters.extend(args)
        return self

    def first(self):
        return self.row


def test_change_archived_books_marks_book_modified_for_sync(monkeypatch):
    timestamp = datetime(2024, 2, 3, 4, 5, 6, tzinfo=timezone.utc)
    old_timestamp = datetime(2024, 1, 1, tzinfo=timezone.utc)

    class FixedDateTime:
        @staticmethod
        def now(tz):
            assert tz == timezone.utc
            return timestamp

    monkeypatch.setattr(kobo_sync_status, "datetime", FixedDateTime)
    monkeypatch.setattr(kobo_sync_status, "current_user", SimpleNamespace(id=7))

    book = SimpleNamespace(last_modified=old_timestamp)
    archived_book = SimpleNamespace(
        user_id=7,
        book_id=12,
        is_archived=False,
        last_modified=old_timestamp,
    )
    archived_query = _FirstResultQuery(archived_book)
    book_query = _FirstResultQuery(book)
    merged = []
    calibre_commits = []
    ub_commits = []

    monkeypatch.setattr(kobo_sync_status, "db", SimpleNamespace(
        Books=SimpleNamespace(id="books.id"),
    ))
    monkeypatch.setattr(kobo_sync_status, "calibre_db", SimpleNamespace(
        session=SimpleNamespace(
            query=lambda model: book_query,
            commit=lambda: calibre_commits.append(True),
        ),
    ))
    monkeypatch.setattr(kobo_sync_status, "ub", SimpleNamespace(
        ArchivedBook=SimpleNamespace(user_id="archived.user_id", book_id="archived.book_id"),
        session=SimpleNamespace(
            query=lambda model: archived_query,
            merge=merged.append,
        ),
        session_commit=lambda message=None: ub_commits.append(message),
    ))

    result = kobo_sync_status.change_archived_books(12, True, "archived by test")

    assert result is True
    assert archived_book.is_archived is True
    assert archived_book.last_modified == timestamp
    assert book.last_modified == timestamp
    assert merged == [archived_book]
    assert calibre_commits == [True]
    assert ub_commits == ["archived by test"]
