"""Functional coverage for the JSON actually returned to a Kobo device."""
import json
import os
import zipfile
from datetime import datetime, timezone
from types import SimpleNamespace

from flask import Flask
from flask_babel import Babel
from sqlalchemy.sql.expression import column

from cps import kobo, kobo_auth, web
from cps.kobo import kobo as kobo_blueprint


def _unwrap(func):
    """Call the original function behind the route/login decorators."""
    seen = set()
    while hasattr(func, "__wrapped__") and func not in seen:
        seen.add(func)
        func = func.__wrapped__
    return func


def _write_epub(path, layout="reflowable"):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as epub:
        epub.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xml:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
""",
        )
        epub.writestr(
            "OEBPS/content.opf",
            f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="uid">book-uuid</dc:identifier>
    <dc:title>A Book</dc:title>
    <meta property="rendition:layout">{layout}</meta>
  </metadata>
  <manifest/>
  <spine/>
</package>
""",
        )
    os.utime(path, (1700000000, 1700000000))
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


def _make_book():
    return SimpleNamespace(
        id=9,
        uuid="book-uuid",
        title="A Book",
        pubdate=datetime(2020, 1, 1, tzinfo=timezone.utc),
        timestamp=datetime(2020, 1, 1, tzinfo=timezone.utc),
        last_modified=datetime(2024, 1, 3, tzinfo=timezone.utc),
        path="Author/Book",
        data=[SimpleNamespace(name="book", format="EPUB", uncompressed_size=1234)],
        languages=[],
        comments=[],
        publishers=[],
        authors=[],
        series=[],
        series_index=1.0,
    )


class FakeRemoteAuthToken:
    auth_token = "auth_token"
    token_type = "token_type"
    user_id = "user_id"

    def __init__(self):
        self.user_id = None
        self.expiration = None
        self.auth_token = None
        self.token_type = None


class AuthQuery:
    def __init__(self, store, model):
        self.store = store
        self.model = model

    def join(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        if self.model is self.store.remote_token_cls:
            return self.store.tokens[0] if self.store.tokens else None
        if self.model is self.store.user_cls:
            return self.store.user if self.store.tokens else None
        return None


class TokenStore:
    def __init__(self):
        self.remote_token_cls = FakeRemoteAuthToken
        self.user_cls = type("User", (), {})
        self.user = SimpleNamespace(id=7, name="tester")
        self.tokens = []

    def query(self, model):
        return AuthQuery(self, model)

    def add(self, token):
        self.tokens.append(token)


class SyncQuery:
    """Small stand-in for the SQLAlchemy queries used by the sync endpoint."""

    def __init__(self, rows=(), count=None):
        self.rows = list(rows)
        self._count = len(self.rows) if count is None else count

    def join(self, *args, **kwargs):
        return self

    def outerjoin(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
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

    def __len__(self):
        return len(self.rows)


class ProxyAwareWsgiApp:
    def __init__(self, wrapped, is_proxied):
        self.wrapped = wrapped
        self.is_proxied = is_proxied

    def __call__(self, environ, start_response):
        return self.wrapped(environ, start_response)


def _make_kobo_app():
    application = Flask(__name__)
    application.config.update(TESTING=True, SECRET_KEY="functional-test-secret")
    Babel(application)
    application.wsgi_app = ProxyAwareWsgiApp(application.wsgi_app, False)
    application.register_blueprint(kobo_blueprint)
    return application


def _install_auth_fakes(monkeypatch, store, rendered):
    fake_ub = SimpleNamespace(
        session=store,
        session_commit=lambda: None,
        RemoteAuthToken=FakeRemoteAuthToken,
        User=store.user_cls,
    )
    monkeypatch.setattr(kobo_auth, "ub", fake_ub)
    monkeypatch.setattr(
        kobo_auth,
        "current_user",
        SimpleNamespace(id=7, name="tester", role_admin=lambda: True),
    )
    monkeypatch.setattr(kobo_auth, "login_user", lambda user: True)
    monkeypatch.setattr(
        kobo_auth,
        "limiter",
        SimpleNamespace(
            current_limits=[],
            limiter=SimpleNamespace(clear=lambda *args, **kwargs: None),
        ),
    )
    monkeypatch.setattr(web, "current_user", SimpleNamespace(role_download=lambda: True))

    def render(token_template, **kwargs):
        rendered.update(kwargs)
        return kwargs["auth_token"]

    monkeypatch.setattr(kobo_auth, "render_title_template", render)


def _generate_token(application):
    with application.test_request_context("/kobo_auth/generate_auth_token/7"):
        return _unwrap(kobo_auth.generate_auth_token)(7)


def test_kobo_metadata_payload_uses_generated_token_and_binary_version(monkeypatch, tmp_path):
    library = tmp_path / "library"
    book_file_size, book_file_mtime_ns = _write_epub(library / "Author" / "Book" / "book.epub")
    book = _make_book()
    store = TokenStore()
    rendered = {}
    _install_auth_fakes(monkeypatch, store, rendered)

    fake_calibre_db = SimpleNamespace(
        session=SimpleNamespace(query=lambda *args, **kwargs: SyncQuery()),
        get_book_by_uuid=lambda book_uuid: book,
    )
    monkeypatch.setattr(kobo, "calibre_db", fake_calibre_db)
    monkeypatch.setattr(kobo_auth, "calibre_db", fake_calibre_db)
    monkeypatch.setattr(kobo.config, "get_book_path", lambda: str(library), raising=False)
    monkeypatch.setattr(kobo.config, "config_external_port", "8081", raising=False)

    application = _make_kobo_app()
    client = application.test_client()

    unauthenticated = client.get("/kobo/not-a-token/v1/library/book-uuid/metadata")
    assert unauthenticated.status_code == 401

    token = _generate_token(application)
    assert token == rendered["auth_token"]
    assert len(token) == 32
    assert store.tokens[0].auth_token == token

    response = client.get(f"/kobo/{token}/v1/library/book-uuid/metadata")

    assert response.status_code == 200
    assert response.headers["Content-Type"] == "application/json; charset=utf-8"
    payload = json.loads(response.get_data(as_text=True))
    assert len(payload) == 1
    metadata = payload[0]
    assert metadata["Title"] == "A Book"
    assert metadata["EntitlementId"] == "book-uuid"
    assert metadata["PublicationDate"] == "2020-01-01T00:00:00Z"

    version = f"{book_file_mtime_ns}-{book_file_size}"
    expected_url = f"http://localhost:8081/kobo/{token}/download/9/epub?v={version}"
    assert metadata["DownloadUrls"] == [
        {
            "Format": kobo_format,
            "Size": 1234,
            "Url": expected_url,
            "Platform": "Generic",
        }
        for kobo_format in ("EPUB3", "EPUB")
    ]


def test_kobo_sync_payload_includes_book_changed_in_selected_shelf(monkeypatch, tmp_path):
    library = tmp_path / "library"
    book_file_size, book_file_mtime_ns = _write_epub(library / "Author" / "Book" / "book.epub")
    book = _make_book()
    changed_row = SimpleNamespace(
        Books=book,
        is_archived=False,
        date_added=datetime(2024, 1, 2),
    )

    store = TokenStore()
    rendered = {}
    _install_auth_fakes(monkeypatch, store, rendered)
    monkeypatch.setattr(
        kobo_auth,
        "calibre_db",
        SimpleNamespace(session=SimpleNamespace(query=lambda *args, **kwargs: SyncQuery())),
    )
    monkeypatch.setattr(kobo.config, "get_book_path", lambda: str(library), raising=False)
    monkeypatch.setattr(kobo.config, "config_external_port", "8081", raising=False)
    monkeypatch.setattr(kobo.config, "config_kobo_proxy", False, raising=False)
    monkeypatch.setattr(kobo.config, "config_kepubifypath", False, raising=False)

    application = _make_kobo_app()
    token = _generate_token(application)

    fake_db = SimpleNamespace(
        Books=SimpleNamespace(
            id=column("books.id"),
            last_modified=column("books.last_modified"),
            timestamp=column("books.timestamp"),
        ),
        Data=SimpleNamespace(format=column("books.format")),
    )
    fake_kobo_ub = SimpleNamespace(
        app_DB_path="app.db",
        session=SimpleNamespace(),
        KoboSyncedBooks=SimpleNamespace(
            user_id=column("synced.user_id"), book_id=column("synced.book_id")
        ),
        ArchivedBook=SimpleNamespace(
            book_id=column("archived.book_id"),
            user_id=column("archived.user_id"),
            last_modified=column("archived.last_modified"),
            is_archived=column("archived.is_archived"),
        ),
        BookShelf=SimpleNamespace(
            book_id=column("shelf.book_id"), date_added=column("shelf.date_added")
        ),
        Shelf=SimpleNamespace(
            user_id=column("user_shelf.user_id"), kobo_sync=column("user_shelf.kobo_sync")
        ),
        KoboReadingState=SimpleNamespace(
            book_id=column("reading.book_id"),
            user_id=column("reading.user_id"),
            last_modified=column("reading.last_modified"),
        ),
    )

    def user_query(model):
        if model is fake_kobo_ub.KoboSyncedBooks:
            return SyncQuery(count=1)
        return SyncQuery()

    fake_kobo_ub.session.query = user_query

    changed_query = SyncQuery(rows=[changed_row])
    synced_ids_query = SyncQuery()
    empty_query = SyncQuery()

    class CalibreSession:
        def query(self, *models):
            if models and models[0] is fake_kobo_ub.KoboSyncedBooks.book_id:
                return synced_ids_query
            if len(models) == 1 and models[0] is fake_db.Books:
                return empty_query
            return changed_query

    monkeypatch.setattr(kobo, "db", fake_db)
    monkeypatch.setattr(kobo, "ub", fake_kobo_ub)
    monkeypatch.setattr(
        kobo,
        "calibre_db",
        SimpleNamespace(
            reconnect_db=lambda *args, **kwargs: None,
            common_filters=lambda **kwargs: column("common_filter"),
            session=CalibreSession(),
        ),
    )
    monkeypatch.setattr(
        kobo,
        "current_user",
        SimpleNamespace(
            id=7,
            name="tester",
            kobo_only_shelves_sync=True,
            role_download=lambda: True,
        ),
    )

    sync_token = SimpleNamespace(
        books_last_modified=datetime(2024, 1, 1),
        books_last_created=datetime(2024, 1, 1),
        books_last_id=-1,
        reading_state_last_modified=datetime(2024, 1, 1),
        to_headers=lambda headers: headers.update({"x-kobo-sync-test": "applied"}),
    )
    monkeypatch.setattr(
        kobo,
        "SyncToken",
        SimpleNamespace(
            SyncToken=SimpleNamespace(from_headers=lambda headers: sync_token)
        ),
        raising=False,
    )
    monkeypatch.setattr(
        kobo,
        "get_or_create_reading_state",
        lambda book_id: SimpleNamespace(last_modified=datetime.min),
    )
    monkeypatch.setattr(kobo, "sync_shelves", lambda *args, **kwargs: None)
    monkeypatch.setattr(kobo.kobo_sync_status, "add_synced_books", lambda book_id: None)

    response = application.test_client().get(f"/kobo/{token}/v1/library/sync")

    assert response.status_code == 200
    assert response.headers["Content-Type"] == "application/json; charset=utf-8"
    assert response.headers["x-kobo-sync"] == "continue"
    assert response.headers["x-kobo-sync-test"] == "applied"
    payload = json.loads(response.get_data(as_text=True))
    assert len(payload) == 1
    entitlement = payload[0]["NewEntitlement"]
    assert entitlement["BookEntitlement"]["Id"] == "book-uuid"
    assert entitlement["BookMetadata"]["Title"] == "A Book"

    version = f"{book_file_mtime_ns}-{book_file_size}"
    expected_url = f"http://localhost:8081/kobo/{token}/download/9/epub?v={version}"
    assert entitlement["BookMetadata"]["DownloadUrls"][0]["Url"] == expected_url


def test_kobo_sync_payload_refreshes_metadata_without_entitlement(monkeypatch, tmp_path):
    library = tmp_path / "library"
    book_file_size, book_file_mtime_ns = _write_epub(library / "Author" / "Book" / "book.epub")
    book = _make_book()
    book.title = "Retitled Book"
    book.last_modified = datetime(2024, 1, 2, tzinfo=timezone.utc)
    book.authors = [SimpleNamespace(name="New Author")]
    book.series = [SimpleNamespace(name="New Series")]
    book.series_index = 2.0
    changed_row = SimpleNamespace(
        Books=book,
        is_archived=False,
        last_modified=None,
        date_added=datetime(2023, 1, 1),
    )

    store = TokenStore()
    rendered = {}
    _install_auth_fakes(monkeypatch, store, rendered)
    monkeypatch.setattr(
        kobo_auth,
        "calibre_db",
        SimpleNamespace(session=SimpleNamespace(query=lambda *args, **kwargs: SyncQuery())),
    )
    monkeypatch.setattr(kobo.config, "get_book_path", lambda: str(library), raising=False)
    monkeypatch.setattr(kobo.config, "config_external_port", "8081", raising=False)
    monkeypatch.setattr(kobo.config, "config_kobo_proxy", False, raising=False)
    monkeypatch.setattr(kobo.config, "config_kepubifypath", False, raising=False)
    monkeypatch.setattr(kobo.config, "config_use_google_drive", False, raising=False)

    application = _make_kobo_app()
    token = _generate_token(application)

    fake_db = SimpleNamespace(
        Books=SimpleNamespace(
            id=column("books.id"),
            last_modified=column("books.last_modified"),
            timestamp=column("books.timestamp"),
        ),
        Data=SimpleNamespace(format=column("books.format")),
    )
    fake_kobo_ub = SimpleNamespace(
        app_DB_path="app.db",
        session=SimpleNamespace(),
        KoboSyncedBooks=SimpleNamespace(
            user_id=column("synced.user_id"), book_id=column("synced.book_id")
        ),
        ArchivedBook=SimpleNamespace(
            book_id=column("archived.book_id"),
            user_id=column("archived.user_id"),
            last_modified=column("archived.last_modified"),
            is_archived=column("archived.is_archived"),
        ),
        BookShelf=SimpleNamespace(
            book_id=column("shelf.book_id"), date_added=column("shelf.date_added")
        ),
        Shelf=SimpleNamespace(
            user_id=column("user_shelf.user_id"), kobo_sync=column("user_shelf.kobo_sync")
        ),
        KoboReadingState=SimpleNamespace(
            book_id=column("reading.book_id"),
            user_id=column("reading.user_id"),
            last_modified=column("reading.last_modified"),
        ),
    )

    def user_query(model):
        if model is fake_kobo_ub.KoboSyncedBooks:
            return SyncQuery(count=1)
        return SyncQuery()

    fake_kobo_ub.session.query = user_query
    changed_query = SyncQuery(rows=[changed_row])

    class CalibreSession:
        def query(self, *models):
            if models and models[0] is fake_kobo_ub.KoboSyncedBooks.book_id:
                return SyncQuery()
            if len(models) == 1 and models[0] is fake_db.Books:
                return SyncQuery()
            return changed_query

    monkeypatch.setattr(kobo, "db", fake_db)
    monkeypatch.setattr(kobo, "ub", fake_kobo_ub)
    monkeypatch.setattr(
        kobo,
        "calibre_db",
        SimpleNamespace(
            reconnect_db=lambda *args, **kwargs: None,
            common_filters=lambda **kwargs: column("common_filter"),
            session=CalibreSession(),
        ),
    )
    monkeypatch.setattr(
        kobo,
        "current_user",
        SimpleNamespace(
            id=7,
            name="tester",
            kobo_only_shelves_sync=True,
            role_download=lambda: True,
        ),
    )

    sync_token = SimpleNamespace(
        books_last_modified=datetime(2024, 1, 1),
        books_last_created=datetime(2024, 1, 1),
        books_last_id=-1,
        reading_state_last_modified=datetime(2024, 1, 1),
        to_headers=lambda headers: headers.update({"x-kobo-sync-test": "applied"}),
    )
    monkeypatch.setattr(
        kobo,
        "SyncToken",
        SimpleNamespace(
            SyncToken=SimpleNamespace(from_headers=lambda headers: sync_token)
        ),
        raising=False,
    )
    monkeypatch.setattr(
        kobo,
        "get_or_create_reading_state",
        lambda book_id: SimpleNamespace(last_modified=datetime.min),
    )
    monkeypatch.setattr(kobo, "sync_shelves", lambda *args, **kwargs: None)
    monkeypatch.setattr(kobo.kobo_sync_status, "add_synced_books", lambda book_id: None)

    response = application.test_client().get(f"/kobo/{token}/v1/library/sync")

    assert response.status_code == 200
    assert response.headers["Content-Type"] == "application/json; charset=utf-8"
    payload = json.loads(response.get_data(as_text=True))
    assert len(payload) == 1
    assert not any("Entitlement" in key for key in payload[0])

    metadata = payload[0]["ChangedProductMetadata"]["BookMetadata"]
    assert metadata["Title"] == "Retitled Book"
    assert metadata["Series"]["Name"] == "New Series"
    assert metadata["Contributors"] == ["New Author"]
    assert metadata["ContributorRoles"] == [{"Name": "New Author", "Role": "Author"}]

    version = f"{book_file_mtime_ns}-{book_file_size}"
    expected_url = f"http://localhost:8081/kobo/{token}/download/9/epub?v={version}"
    assert metadata["DownloadUrls"][0]["Url"] == expected_url
