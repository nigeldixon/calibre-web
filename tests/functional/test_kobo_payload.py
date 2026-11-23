"""Functional coverage for the metadata JSON actually returned to a Kobo device."""
import json
import os
import zipfile
from datetime import datetime, timezone
from types import SimpleNamespace

from flask import Flask
from flask_babel import Babel

from cps import kobo, kobo_auth, web
from cps.kobo import kobo as kobo_blueprint


def _unwrap(func):
    """Call the original function behind the route/login decorators."""
    seen = set()
    while hasattr(func, "__wrapped__") and func not in seen:
        seen.add(func)
        func = func.__wrapped__
    return func


def _write_epub(path):
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
            """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="uid">book-uuid</dc:identifier>
    <dc:title>A Book</dc:title>
    <meta property="rendition:layout">reflowable</meta>
  </metadata>
  <manifest/>
  <spine/>
</package>
""",
        )
    os.utime(path, (1700000000, 1700000000))
    return path.stat().st_size


def test_kobo_metadata_payload_uses_generated_token_and_binary_version(monkeypatch, tmp_path):
    library = tmp_path / "library"
    book_file_size = _write_epub(library / "Author" / "Book" / "book.epub")
    book = SimpleNamespace(
        id=9,
        uuid="book-uuid",
        title="A Book",
        pubdate=datetime(2020, 1, 1, tzinfo=timezone.utc),
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

    class FakeQuery:
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
            return FakeQuery(self, model)

        def add(self, token):
            self.tokens.append(token)

    store = TokenStore()
    fake_ub = SimpleNamespace(
        session=store,
        session_commit=lambda: None,
        RemoteAuthToken=FakeRemoteAuthToken,
        User=store.user_cls,
    )
    empty_calibre_query = SimpleNamespace(
        join=lambda *args, **kwargs: SimpleNamespace(all=lambda: [])
    )
    fake_calibre_db = SimpleNamespace(
        session=SimpleNamespace(query=lambda *args, **kwargs: empty_calibre_query),
        get_book_by_uuid=lambda book_uuid: book,
    )

    monkeypatch.setattr(kobo_auth, "ub", fake_ub)
    monkeypatch.setattr(kobo, "calibre_db", fake_calibre_db)
    monkeypatch.setattr(kobo_auth, "calibre_db", fake_calibre_db)
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
    monkeypatch.setattr(kobo.config, "get_book_path", lambda: str(library), raising=False)
    monkeypatch.setattr(kobo.config, "config_external_port", "8081", raising=False)

    rendered = {}

    def render(token_template, **kwargs):
        rendered.update(kwargs)
        return kwargs["auth_token"]

    monkeypatch.setattr(kobo_auth, "render_title_template", render)

    application = Flask(__name__)
    application.config.update(TESTING=True, SECRET_KEY="functional-test-secret")
    Babel(application)

    original_wsgi_app = application.wsgi_app

    class ProxyAwareWsgiApp:
        def __init__(self, wrapped, is_proxied):
            self.wrapped = wrapped
            self.is_proxied = is_proxied

        def __call__(self, environ, start_response):
            return self.wrapped(environ, start_response)

    application.wsgi_app = ProxyAwareWsgiApp(original_wsgi_app, False)
    application.register_blueprint(kobo_blueprint)
    client = application.test_client()

    unauthenticated = client.get("/kobo/not-a-token/v1/library/book-uuid/metadata")
    assert unauthenticated.status_code == 401

    with application.test_request_context("/kobo_auth/generate_auth_token/7"):
        token = _unwrap(kobo_auth.generate_auth_token)(7)

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

    version = f"1700000000-{book_file_size}"
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
