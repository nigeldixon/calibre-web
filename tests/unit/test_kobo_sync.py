"""Regression tests for the Kobo sync changes."""
import os
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy.sql.expression import column

from cps import kobo


def _unwrap(func):
    """Call the original function behind the route/auth decorators."""
    seen = set()
    while hasattr(func, "__wrapped__") and func not in seen:
        seen.add(func)
        func = func.__wrapped__
    return func


def test_get_book_format_binary_version_uses_file_mtime_and_size(monkeypatch, tmp_path):
    library = tmp_path / "library"
    book_file = library / "Author" / "Book" / "book.epub"
    book_file.parent.mkdir(parents=True)
    book_file.write_bytes(b"12345")
    os.utime(book_file, (1700000000.25, 1700000000.25))

    monkeypatch.setattr(kobo.config, "get_book_path", lambda: str(library))
    book = SimpleNamespace(path="Author/Book")
    book_data = SimpleNamespace(name="book", format="EPUB", uncompressed_size=999)

    assert kobo.get_book_format_binary_version(book, book_data) == "1700000000-5"


def test_get_book_format_binary_version_falls_back_to_declared_size(monkeypatch, tmp_path):
    monkeypatch.setattr(kobo.config, "get_book_path", lambda: str(tmp_path / "missing"))
    book = SimpleNamespace(path="Author/Book")
    book_data = SimpleNamespace(name="book", format="EPUB", uncompressed_size=4321)

    assert kobo.get_book_format_binary_version(book, book_data) == "4321"

    book_data.uncompressed_size = None
    assert kobo.get_book_format_binary_version(book, book_data) == "0"


def test_get_download_url_for_book_adds_utc_timestamp_for_naive_datetime(monkeypatch, app):
    app.wsgi_app.is_proxied = False
    monkeypatch.setattr(kobo, "get_auth_token", lambda: "token")
    monkeypatch.setattr(kobo.config, "config_external_port", "8081", raising=False)

    timestamp = datetime(2024, 5, 6, 7, 8, 9)
    with app.test_request_context("https://example.com:8443/"):
        url = kobo.get_download_url_for_book(12, "EPUB", timestamp)

    version = int(timestamp.replace(tzinfo=timezone.utc).timestamp())
    assert url == f"https://example.com:8081/kobo/token/download/12/epub?v={version}"


def test_get_download_url_for_book_accepts_aware_datetime_string_and_bad_version(monkeypatch, app):
    app.wsgi_app.is_proxied = False
    monkeypatch.setattr(kobo, "get_auth_token", lambda: "token")
    monkeypatch.setattr(kobo.config, "config_external_port", "8081", raising=False)

    timestamp = datetime(2024, 5, 6, 7, 8, 9, tzinfo=timezone.utc)

    class BrokenVersion:
        @property
        def timestamp(self):
            raise RuntimeError("cannot convert")

    with app.test_request_context("https://example.com/"):
        aware_url = kobo.get_download_url_for_book(12, "KEPUB", timestamp)
        string_url = kobo.get_download_url_for_book(12, "EPUB", "file-v2")
        broken_url = kobo.get_download_url_for_book(12, "EPUB", BrokenVersion())
        plain_url = kobo.get_download_url_for_book(12, "EPUB")

    assert aware_url.endswith(f"?v={int(timestamp.timestamp())}")
    assert string_url.endswith("?v=file-v2")
    assert "?v=" not in broken_url
    assert "?v=" not in plain_url


def test_get_download_url_for_book_passes_version_to_url_for_when_proxied(monkeypatch, app):
    app.wsgi_app.is_proxied = True
    monkeypatch.setattr(kobo, "get_auth_token", lambda: "token")
    captured = {}

    def fake_url_for(endpoint, **params):
        captured.update(endpoint=endpoint, params=params)
        return "proxied-url"

    monkeypatch.setattr(kobo, "url_for", fake_url_for)

    with app.test_request_context("/"):
        result = kobo.get_download_url_for_book(12, "KEPUB", "file-v3")

    assert result == "proxied-url"
    assert captured == {
        "endpoint": "kobo.download_book",
        "params": {
            "auth_token": "token",
            "book_id": 12,
            "book_format": "kepub",
            "_external": True,
            "v": "file-v3",
        },
    }


def test_get_metadata_uses_binary_version_for_download_urls(monkeypatch):
    book = SimpleNamespace(
        id=9,
        uuid="book-uuid",
        title="A Book",
        pubdate=datetime(2020, 1, 1, tzinfo=timezone.utc),
        data=[SimpleNamespace(name="book", format="EPUB", uncompressed_size=100)],
        languages=[],
    )
    url_calls = []

    monkeypatch.setattr(kobo, "get_epub_layout", lambda book, data: "reflowable")
    monkeypatch.setattr(kobo, "get_book_format_binary_version", lambda book, data: "1700000000-100")

    def fake_download_url(book_id, book_format, version=None):
        url_calls.append((book_id, book_format, version))
        return f"download/{book_format}?v={version}"

    monkeypatch.setattr(kobo, "get_download_url_for_book", fake_download_url)
    monkeypatch.setattr(kobo, "get_description", lambda book: "Description")
    monkeypatch.setattr(kobo, "get_publisher", lambda book: {"Name": "Publisher"})
    monkeypatch.setattr(kobo, "get_author", lambda book: {"Authors": []})
    monkeypatch.setattr(kobo, "get_series", lambda book: None)

    metadata = kobo.get_metadata(book)

    assert url_calls == [
        (9, "EPUB", "1700000000-100"),
        (9, "EPUB", "1700000000-100"),
    ]
    assert metadata["DownloadUrls"] == [
        {
            "Format": kobo_format,
            "Size": 100,
            "Url": "download/EPUB?v=1700000000-100",
            "Platform": "Generic",
        }
        for kobo_format in ("EPUB3", "EPUB")
    ]


def test_selected_shelf_sync_includes_books_changed_since_sync_token(monkeypatch, app):
    app.wsgi_app.is_proxied = True
    token = SimpleNamespace(
        books_last_modified=datetime(2024, 1, 1),
        books_last_created=datetime(2024, 1, 1),
        reading_state_last_modified=datetime(2024, 1, 1),
    )
    monkeypatch.setattr(
        kobo,
        "SyncToken",
        SimpleNamespace(SyncToken=SimpleNamespace(from_headers=lambda headers: token)),
    )
    monkeypatch.setattr(kobo, "current_user", SimpleNamespace(
        role_download=lambda: True,
        id=7,
        kobo_only_shelves_sync=True,
        name="tester",
    ))
    monkeypatch.setattr(kobo, "get_download_url_for_book", lambda *args, **kwargs: "download-url")
    monkeypatch.setattr(kobo, "sync_shelves", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        kobo,
        "generate_sync_response",
        lambda sync_token, results, set_cont=False: "sync-response",
    )

    filters = []

    class FakeQuery:
        def query(self, *args):
            return self

        def join(self, *args):
            return self

        def outerjoin(self, *args):
            return self

        def filter(self, *args):
            filters.extend(args)
            return self

        def order_by(self, *args):
            return self

        def distinct(self):
            return self

        def limit(self, count):
            return self

        def all(self):
            return []

        def count(self):
            return 0

        def first(self):
            return None

        def one_or_none(self):
            return None

        def __iter__(self):
            return iter(())

    query = FakeQuery()
    session = SimpleNamespace(query=lambda *args, **kwargs: query)
    monkeypatch.setattr(kobo, "calibre_db", SimpleNamespace(
        reconnect_db=lambda *args, **kwargs: None,
        session=session,
        common_filters=lambda **kwargs: column("common_filter"),
    ))
    monkeypatch.setattr(kobo, "db", SimpleNamespace(
        Books=SimpleNamespace(
            id=column("books.id"),
            last_modified=column("books.last_modified"),
            data=column("books.data"),
        ),
        Data=SimpleNamespace(format=column("books.format")),
    ))
    monkeypatch.setattr(kobo, "ub", SimpleNamespace(
        app_DB_path="app.db",
        session=session,
        KoboSyncedBooks=SimpleNamespace(
            book_id=column("synced.book_id"),
            user_id=column("synced.user_id"),
        ),
        ArchivedBook=SimpleNamespace(
            book_id=column("archived.book_id"),
            user_id=column("archived.user_id"),
            last_modified=column("archived.last_modified"),
            is_archived=column("archived.is_archived"),
        ),
        BookShelf=SimpleNamespace(
            book_id=column("shelf.book_id"),
            date_added=column("shelf.date_added"),
        ),
        Shelf=SimpleNamespace(
            user_id=column("user_shelf.user_id"),
            kobo_sync=column("user_shelf.kobo_sync"),
        ),
        KoboReadingState=SimpleNamespace(
            book_id=column("reading.book_id"),
            user_id=column("reading.user_id"),
            last_modified=column("reading.last_modified"),
        ),
    ))

    with app.test_request_context("/kobo/token/v1/library/sync"):
        result = _unwrap(kobo.HandleSyncRequest)()

    assert result == "sync-response"
    version_filters = [str(expression) for expression in filters if "books.last_modified" in str(expression)]
    assert version_filters, "expected a date filter covering book modifications"
    assert any("shelf.date_added" in expression for expression in version_filters)
