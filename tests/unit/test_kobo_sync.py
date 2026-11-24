"""Regression tests for the Kobo sync changes."""
import os
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy.sql.expression import column

from cps import kobo
from cps.services import SyncToken as SyncTokenService


def _unwrap(func):
    """Call the original function behind the route/auth decorators."""
    seen = set()
    while hasattr(func, "__wrapped__") and func not in seen:
        seen.add(func)
        func = func.__wrapped__
    return func


class RecordingQuery:
    """Query stand-in that records filters and returns a fixed page."""

    def __init__(self, rows=(), count=0, filters=None, orders=None):
        self.rows = list(rows)
        self._count = count
        self.filters = filters if filters is not None else []
        self.orders = orders if orders is not None else []

    def query(self, *args):
        return self

    def join(self, *args):
        return self

    def outerjoin(self, *args):
        return self

    def filter(self, *args):
        self.filters.extend(args)
        return self

    def order_by(self, *args):
        self.orders.extend(args)
        return self

    def distinct(self):
        return self

    def limit(self, count):
        return self

    def all(self):
        return list(self.rows)

    def count(self):
        return self._count

    def first(self):
        return None

    def one_or_none(self):
        return None

    def __iter__(self):
        return iter(self.rows)


def test_get_book_format_binary_version_uses_file_mtime_and_size(monkeypatch, tmp_path):
    library = tmp_path / "library"
    book_file = library / "Author" / "Book" / "book.epub"
    book_file.parent.mkdir(parents=True)
    book_file.write_bytes(b"12345")
    os.utime(book_file, (1700000000.25, 1700000000.25))

    monkeypatch.setattr(kobo.config, "get_book_path", lambda: str(library))
    book = SimpleNamespace(path="Author/Book")
    book_data = SimpleNamespace(name="book", format="EPUB", uncompressed_size=999)

    expected_version = f"{book_file.stat().st_mtime_ns}-5"
    assert kobo.get_book_format_binary_version(book, book_data) == expected_version


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
        books_last_id=-1,
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
    orders = []
    query = RecordingQuery(filters=filters, orders=orders)
    session = SimpleNamespace(query=lambda *args, **kwargs: query)
    monkeypatch.setattr(kobo, "calibre_db", SimpleNamespace(
        reconnect_db=lambda *args, **kwargs: None,
        session=session,
        common_filters=lambda **kwargs: column("common_filter"),
    ))
    monkeypatch.setattr(kobo, "db", SimpleNamespace(
        Books=SimpleNamespace(
            id=column("books.id"),
            timestamp=column("books.timestamp"),
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
    assert all("synced.book_id" not in str(expression) for expression in filters)
    assert any("books.id" in str(expression) for expression in orders)


def test_book_binary_changed_since_uses_file_mtime(monkeypatch, tmp_path):
    library = tmp_path / "library"
    book_file = library / "Author" / "Book" / "book.epub"
    book_file.parent.mkdir(parents=True)
    book_file.write_bytes(b"12345")
    os.utime(book_file, (1700000000, 1700000000))

    monkeypatch.setattr(kobo.config, "get_book_path", lambda: str(library))
    book = SimpleNamespace(
        path="Author/Book",
        data=[SimpleNamespace(name="book", format="EPUB", uncompressed_size=5)],
    )

    assert not kobo.book_binary_changed_since(book, datetime(2024, 1, 1))
    assert kobo.book_binary_changed_since(book, datetime(2023, 1, 1))


def test_book_binary_changed_since_fails_closed_without_file_evidence(monkeypatch, tmp_path):
    monkeypatch.setattr(kobo.config, "get_book_path", lambda: str(tmp_path / "missing"))
    book = SimpleNamespace(
        path="Author/Book",
        data=[SimpleNamespace(name="book", format="EPUB", uncompressed_size=5)],
    )

    assert kobo.book_binary_changed_since(book, datetime(2030, 1, 1))


def test_book_binary_changed_with_edit_distinguishes_metadata_only_writes(monkeypatch, tmp_path):
    library = tmp_path / "library"
    book_file = library / "Author" / "Book" / "book.epub"
    book_file.parent.mkdir(parents=True)
    book_file.write_bytes(b"12345")

    monkeypatch.setattr(kobo.config, "get_book_path", lambda: str(library))
    book = SimpleNamespace(
        path="Author/Book",
        last_modified=datetime(2024, 1, 2, 12, 0, tzinfo=timezone.utc),
        timestamp=datetime(2020, 1, 1, tzinfo=timezone.utc),
        data=[SimpleNamespace(name="book", format="EPUB", uncompressed_size=5)],
    )

    os.utime(book_file, (datetime(2024, 1, 2, 11, 59, 58, tzinfo=timezone.utc).timestamp(),) * 2)
    assert kobo.book_binary_changed_with_edit(book)

    os.utime(book_file, (datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp(),) * 2)
    assert not kobo.book_binary_changed_with_edit(book)


def test_get_cover_image_id_is_versioned_and_normalized(monkeypatch, tmp_path):
    library = tmp_path / "library"
    cover = library / "Author" / "Book" / "cover.jpg"
    cover.parent.mkdir(parents=True)
    cover.write_bytes(b"cover")
    os.utime(cover, (1700000001, 1700000001))

    monkeypatch.setattr(kobo.config, "get_book_path", lambda: str(library))
    monkeypatch.setattr(kobo.config, "config_use_google_drive", False, raising=False)
    book = SimpleNamespace(uuid="cover-uuid", path="Author/Book")

    image_id = kobo.get_cover_image_id(book)
    assert image_id.startswith("cover-uuid__cover")
    assert kobo.normalize_cover_image_id(image_id) == "cover-uuid"


def test_metadata_only_edit_sends_changed_product_metadata_without_entitlement(monkeypatch, app, tmp_path):
    library = tmp_path / "library"
    book_file = library / "Author" / "Book" / "book.epub"
    book_file.parent.mkdir(parents=True)
    book_file.write_bytes(b"12345")
    os.utime(book_file, (1700000000, 1700000000))

    app.wsgi_app.is_proxied = True
    token = SimpleNamespace(
        books_last_modified=datetime(2024, 1, 1),
        books_last_created=datetime(2024, 1, 1),
        books_last_id=-1,
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
        kobo_only_shelves_sync=False,
        name="tester",
    ))
    monkeypatch.setattr(kobo.config, "get_book_path", lambda: str(library))
    monkeypatch.setattr(kobo.config, "config_kepubifypath", False, raising=False)
    monkeypatch.setattr(kobo, "get_download_url_for_book", lambda *args, **kwargs: "download-url")
    monkeypatch.setattr(kobo, "get_or_create_reading_state",
                        lambda book_id: SimpleNamespace(last_modified=datetime.min))
    monkeypatch.setattr(kobo, "get_metadata", lambda book: {
        "Title": "Retitled",
        "RevisionId": book.uuid,
        "ContributorRoles": [{"Name": "New Author"}],
    })
    monkeypatch.setattr(kobo, "sync_shelves", lambda *args, **kwargs: None)
    monkeypatch.setattr(kobo.kobo_sync_status, "add_synced_books", lambda book_id: None)

    sync_results = []
    monkeypatch.setattr(
        kobo,
        "generate_sync_response",
        lambda sync_token, results, set_cont=False: sync_results.extend(results) or "sync-response",
    )

    book = SimpleNamespace(
        id=42,
        uuid="metadata-uuid",
        timestamp=datetime(2020, 1, 1),
        last_modified=datetime(2024, 1, 2),
        path="Author/Book",
        data=[SimpleNamespace(name="book", format="EPUB", uncompressed_size=5)],
    )
    row = SimpleNamespace(
        Books=book,
        is_archived=False,
        last_modified=None,
        date_added=datetime(2020, 1, 1),
    )
    query = RecordingQuery(rows=[row], count=0)
    reading_state_model = SimpleNamespace(
        book_id=column("reading.book_id"),
        user_id=column("reading.user_id"),
        last_modified=column("reading.last_modified"),
    )

    def session_query(*args, **kwargs):
        if args and args[0] is reading_state_model:
            return RecordingQuery()
        return query

    monkeypatch.setattr(kobo, "calibre_db", SimpleNamespace(
        reconnect_db=lambda *args, **kwargs: None,
        session=SimpleNamespace(query=session_query),
        common_filters=lambda **kwargs: column("common_filter"),
    ))
    monkeypatch.setattr(kobo, "db", SimpleNamespace(
        Books=SimpleNamespace(
            id=column("books.id"),
            timestamp=column("books.timestamp"),
            last_modified=column("books.last_modified"),
        ),
        Data=SimpleNamespace(format=column("books.format")),
    ))
    monkeypatch.setattr(kobo, "ub", SimpleNamespace(
        app_DB_path="app.db",
        session=SimpleNamespace(query=lambda *args, **kwargs: RecordingQuery()),
        ArchivedBook=SimpleNamespace(
            book_id=column("archived.book_id"),
            user_id=column("archived.user_id"),
            last_modified=column("archived.last_modified"),
            is_archived=column("archived.is_archived"),
        ),
        KoboReadingState=reading_state_model,
    ))

    with app.test_request_context("/kobo/token/v1/library/sync"):
        result = _unwrap(kobo.HandleSyncRequest)()

    assert result == "sync-response"
    assert len(sync_results) == 1
    assert "ChangedProductMetadata" in sync_results[0]
    assert not any("Entitlement" in key for key in sync_results[0])
    metadata = sync_results[0]["ChangedProductMetadata"]["BookMetadata"]
    assert metadata["Title"] == "Retitled"
    assert metadata["ContributorRoles"] == [{"Name": "New Author", "Role": "Author"}]
    assert token.books_last_modified == datetime(2024, 1, 2)


def test_archive_change_is_selected_from_archive_timestamp(monkeypatch, app):
    app.wsgi_app.is_proxied = True
    token = SimpleNamespace(
        books_last_modified=datetime(2024, 1, 1),
        books_last_created=datetime(2024, 1, 1),
        books_last_id=-1,
        archive_last_modified=datetime(2024, 1, 1),
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
        kobo_only_shelves_sync=False,
        name="tester",
    ))
    monkeypatch.setattr(kobo.config, "config_kepubifypath", False, raising=False)
    monkeypatch.setattr(kobo, "get_download_url_for_book", lambda *args, **kwargs: "download-url")
    monkeypatch.setattr(kobo, "get_or_create_reading_state", lambda book_id: SimpleNamespace(last_modified=datetime.min))
    entitlement_calls = []
    monkeypatch.setattr(kobo, "create_book_entitlement", lambda book, archived: entitlement_calls.append((book.id, archived)) or {})
    monkeypatch.setattr(kobo, "get_metadata", lambda book: {})
    monkeypatch.setattr(kobo, "sync_shelves", lambda *args, **kwargs: None)
    monkeypatch.setattr(kobo.kobo_sync_status, "add_synced_books", lambda book_id: None)
    monkeypatch.setattr(
        kobo,
        "generate_sync_response",
        lambda sync_token, results, set_cont=False: "sync-response",
    )

    book = SimpleNamespace(
        id=42,
        uuid="archived-uuid",
        timestamp=datetime(2020, 1, 1),
        last_modified=datetime(2024, 1, 2),
        data=[SimpleNamespace(format="EPUB")],
    )
    archived_timestamp = datetime(2024, 2, 3, 4, 5, 6, tzinfo=timezone.utc)
    row = SimpleNamespace(Books=book, is_archived=True, last_modified=archived_timestamp)
    filters = []
    orders = []
    query = RecordingQuery(rows=[row], filters=filters, orders=orders)
    reading_state_model = SimpleNamespace(
        book_id=column("reading.book_id"),
        user_id=column("reading.user_id"),
        last_modified=column("reading.last_modified"),
    )

    def session_query(*args, **kwargs):
        if args and args[0] is reading_state_model:
            return RecordingQuery()
        return query

    monkeypatch.setattr(kobo, "calibre_db", SimpleNamespace(
        reconnect_db=lambda *args, **kwargs: None,
        session=SimpleNamespace(query=session_query),
        common_filters=lambda **kwargs: column("common_filter"),
    ))
    monkeypatch.setattr(kobo, "db", SimpleNamespace(
        Books=SimpleNamespace(
            id=column("books.id"),
            timestamp=column("books.timestamp"),
            last_modified=column("books.last_modified"),
            data=column("books.data"),
        ),
        Data=SimpleNamespace(format=column("books.format")),
    ))
    monkeypatch.setattr(kobo, "ub", SimpleNamespace(
        app_DB_path="app.db",
        session=SimpleNamespace(query=lambda *args, **kwargs: RecordingQuery()),
        ArchivedBook=SimpleNamespace(
            book_id=column("archived.book_id"),
            user_id=column("archived.user_id"),
            last_modified=column("archived.last_modified"),
            is_archived=column("archived.is_archived"),
        ),
        KoboReadingState=reading_state_model,
    ))

    with app.test_request_context("/kobo/token/v1/library/sync"):
        result = _unwrap(kobo.HandleSyncRequest)()

    assert result == "sync-response"
    assert entitlement_calls == [(42, True)]
    change_expressions = " ".join(str(expression) for expression in filters + orders)
    assert "archived.last_modified" in change_expressions
    assert "coalesce" in change_expressions.lower()
    expected_timestamp = archived_timestamp.replace(tzinfo=None)
    assert token.books_last_modified == expected_timestamp
    assert token.archive_last_modified == expected_timestamp


def test_null_book_last_modified_is_selected_and_advances_sync_token(monkeypatch, app):
    app.wsgi_app.is_proxied = True
    book_timestamp = datetime(2024, 1, 3, 4, 5, 6, tzinfo=timezone.utc)
    token = SimpleNamespace(
        books_last_modified=datetime(2024, 1, 1),
        books_last_created=datetime(2024, 1, 1),
        books_last_id=-1,
        archive_last_modified=datetime(2024, 1, 1),
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
        kobo_only_shelves_sync=False,
        name="tester",
    ))
    monkeypatch.setattr(kobo.config, "config_kepubifypath", False, raising=False)
    monkeypatch.setattr(kobo, "get_download_url_for_book", lambda *args, **kwargs: "download-url")
    monkeypatch.setattr(kobo, "get_or_create_reading_state",
                        lambda book_id: SimpleNamespace(last_modified=datetime.min))
    monkeypatch.setattr(kobo, "create_book_entitlement", lambda book, archived: {})
    monkeypatch.setattr(kobo, "get_metadata", lambda book: {})
    monkeypatch.setattr(kobo, "sync_shelves", lambda *args, **kwargs: None)
    monkeypatch.setattr(kobo.kobo_sync_status, "add_synced_books", lambda book_id: None)
    monkeypatch.setattr(
        kobo,
        "generate_sync_response",
        lambda sync_token, results, set_cont=False: "sync-response",
    )

    book = SimpleNamespace(
        id=42,
        uuid="legacy-uuid",
        timestamp=book_timestamp,
        last_modified=None,
        data=[SimpleNamespace(format="EPUB")],
    )
    row = SimpleNamespace(Books=book, is_archived=False, last_modified=None)
    filters = []
    orders = []
    query = RecordingQuery(rows=[row], filters=filters, orders=orders)
    reading_state_model = SimpleNamespace(
        book_id=column("reading.book_id"),
        user_id=column("reading.user_id"),
        last_modified=column("reading.last_modified"),
    )

    def session_query(*args, **kwargs):
        if args and args[0] is reading_state_model:
            return RecordingQuery()
        return query

    monkeypatch.setattr(kobo, "calibre_db", SimpleNamespace(
        reconnect_db=lambda *args, **kwargs: None,
        session=SimpleNamespace(query=session_query),
        common_filters=lambda **kwargs: column("common_filter"),
    ))
    monkeypatch.setattr(kobo, "db", SimpleNamespace(
        Books=SimpleNamespace(
            id=column("books.id"),
            timestamp=column("books.timestamp"),
            last_modified=column("books.last_modified"),
            data=column("books.data"),
        ),
        Data=SimpleNamespace(format=column("books.format")),
    ))
    monkeypatch.setattr(kobo, "ub", SimpleNamespace(
        app_DB_path="app.db",
        session=SimpleNamespace(query=lambda *args, **kwargs: RecordingQuery()),
        ArchivedBook=SimpleNamespace(
            book_id=column("archived.book_id"),
            user_id=column("archived.user_id"),
            last_modified=column("archived.last_modified"),
            is_archived=column("archived.is_archived"),
        ),
        KoboReadingState=reading_state_model,
    ))

    with app.test_request_context("/kobo/token/v1/library/sync"):
        result = _unwrap(kobo.HandleSyncRequest)()

    assert result == "sync-response"
    change_expressions = " ".join(str(expression) for expression in filters + orders)
    assert "coalesce" in change_expressions.lower()
    assert "books.timestamp" in change_expressions
    assert token.books_last_modified == book_timestamp.replace(tzinfo=None)
    assert token.books_last_id == 42


def test_create_book_entitlement_uses_timestamp_when_last_modified_missing():
    timestamp = datetime(2020, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    book = SimpleNamespace(uuid="legacy-uuid", timestamp=timestamp, last_modified=None)

    entitlement = kobo.create_book_entitlement(book, archived=False)

    assert entitlement["LastModified"] == "2020-01-02T03:04:05Z"
    assert entitlement["Created"] == "2020-01-02T03:04:05Z"


def test_book_change_filter_uses_id_as_timestamp_tie_breaker():
    token = SimpleNamespace(
        books_last_modified=datetime(2024, 1, 1),
        books_last_id=7,
    )

    paged = str(kobo.book_change_filter(token, column("books.last_modified")))
    assert " OR " in paged.upper()
    assert ">" in paged
    assert "=" in paged
    assert "books.id" in paged

    token.books_last_id = -1
    initial = str(kobo.book_change_filter(token, column("books.last_modified")))
    assert "books.id" not in initial
    assert " OR " not in initial.upper()
    assert "> :" in initial


def test_book_change_filter_keeps_newer_timestamp_with_lower_id(monkeypatch):
    from sqlalchemy import Column, DateTime, Integer, MetaData, Table, create_engine, select

    metadata = MetaData()
    books = Table(
        "books", metadata,
        Column("id", Integer, primary_key=True),
        Column("last_modified", DateTime),
    )
    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(books.insert(), [
            {"id": 100, "last_modified": datetime(2024, 1, 1)},
            {"id": 1, "last_modified": datetime(2024, 1, 10)},
        ])

    monkeypatch.setattr(kobo, "db", SimpleNamespace(Books=SimpleNamespace(id=books.c.id)))
    token = SimpleNamespace(
        books_last_modified=datetime(2024, 1, 1),
        books_last_id=100,
    )

    with engine.connect() as connection:
        ids = connection.execute(
            select(books.c.id).where(kobo.book_change_filter(token, books.c.last_modified))
        ).scalars().all()
    engine.dispose()

    assert ids == [1]


def test_sync_token_round_trips_book_cursor():
    token = SyncTokenService.SyncToken(
        raw_kobo_store_token="raw-store-token",
        books_last_created=datetime(2024, 1, 1),
        books_last_modified=datetime(2024, 1, 2, 3, 4, 5),
        books_last_id=17,
    )
    header = SyncTokenService.SyncToken.SYNC_TOKEN_HEADER

    restored = SyncTokenService.SyncToken.from_headers({header: token.build_sync_token()})
    assert restored.books_last_id == 17
    assert restored.books_last_modified == token.books_last_modified
    assert "17" in str(token)

    token.books_last_id = "not-an-id"
    malformed = SyncTokenService.SyncToken.from_headers({header: token.build_sync_token()})
    assert malformed.books_last_id == -1


def test_whole_library_sync_uses_token_cursor_not_synced_ledger(monkeypatch, app):
    app.wsgi_app.is_proxied = True
    token = SimpleNamespace(
        books_last_modified=datetime(2024, 1, 1),
        books_last_created=datetime(2024, 1, 1),
        books_last_id=4,
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
        kobo_only_shelves_sync=False,
        name="tester",
    ))
    monkeypatch.setattr(kobo.config, "config_kepubifypath", False, raising=False)
    monkeypatch.setattr(kobo, "get_download_url_for_book", lambda *args, **kwargs: "download-url")
    monkeypatch.setattr(kobo, "get_or_create_reading_state", lambda book_id: SimpleNamespace(last_modified=datetime.min))
    monkeypatch.setattr(kobo, "create_book_entitlement", lambda book, archived: {})
    monkeypatch.setattr(kobo, "get_metadata", lambda book: {})
    monkeypatch.setattr(kobo, "sync_shelves", lambda *args, **kwargs: None)
    monkeypatch.setattr(kobo.kobo_sync_status, "add_synced_books", lambda book_id: None)
    monkeypatch.setattr(
        kobo,
        "generate_sync_response",
        lambda sync_token, results, set_cont=False: "sync-response",
    )

    book = SimpleNamespace(
        id=12,
        uuid="book-uuid",
        timestamp=datetime(2024, 1, 2),
        last_modified=datetime(2024, 1, 3),
        data=[SimpleNamespace(format="EPUB")],
    )
    row = SimpleNamespace(Books=book, is_archived=False)
    filters = []
    orders = []
    query = RecordingQuery(rows=[row], filters=filters, orders=orders)
    reading_state_model = SimpleNamespace(
        book_id=column("reading.book_id"),
        user_id=column("reading.user_id"),
        last_modified=column("reading.last_modified"),
    )
    reading_query = RecordingQuery()

    def session_query(*args, **kwargs):
        if args and args[0] is reading_state_model:
            return reading_query
        return query

    session = SimpleNamespace(query=session_query)
    monkeypatch.setattr(kobo, "calibre_db", SimpleNamespace(
        reconnect_db=lambda *args, **kwargs: None,
        session=session,
        common_filters=lambda **kwargs: column("common_filter"),
    ))
    monkeypatch.setattr(kobo, "db", SimpleNamespace(
        Books=SimpleNamespace(
            id=column("books.id"),
            timestamp=column("books.timestamp"),
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
        KoboReadingState=reading_state_model,
    ))

    with app.test_request_context("/kobo/token/v1/library/sync"):
        result = _unwrap(kobo.HandleSyncRequest)()

    assert result == "sync-response"
    assert token.books_last_id == 12
    assert any("books.last_modified" in str(expression) for expression in filters)
    assert any("books.id" in str(expression) for expression in filters)
    assert all("synced.book_id" not in str(expression) for expression in filters)
    assert any("books.last_modified" in str(expression) for expression in orders)
