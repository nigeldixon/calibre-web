"""Functional coverage for the Kobo chapter-mark form endpoint."""
import json
import os
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask, g
from flask_babel import Babel
from lxml import etree
from sqlalchemy.exc import IntegrityError

from cps import kobo, kobo_auth, web
from cps.kobo import kobo as kobo_blueprint

TEMPLATE_FOLDER = Path(__file__).parents[2] / "cps" / "templates"

CHAPTER = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>Opening Page</title></head>
  <body><h1>Opening Page</h1></body>
</html>
"""

NAV = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Contents</title></head>
  <body><nav epub:type="toc"><ol><li><a href="chapter1.xhtml">Chapter 1</a></li></ol></nav></body>
</html>
"""

NCX = """<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head><meta name="dtb:uid" content="test-book"/></head>
  <docTitle><text>Test Book</text></docTitle>
  <navMap>
    <navPoint id="nav1" playOrder="1">
      <navLabel><text>Chapter 1</text></navLabel>
      <content src="chapter1.xhtml"/>
    </navPoint>
  </navMap>
</ncx>
"""


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
        requested = g.get("auth_token")
        valid = (
            self.store.tokens
            and requested == self.store.tokens[0].auth_token
        )
        if self.model is self.store.remote_token_cls:
            return self.store.tokens[0] if valid else None
        if self.model is self.store.user_cls:
            return self.store.user if valid else None
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


class ProxyAwareWsgiApp:
    def __init__(self, wrapped, is_proxied):
        self.wrapped = wrapped
        self.is_proxied = is_proxied

    def __call__(self, environ, start_response):
        return self.wrapped(environ, start_response)


def _unwrap(func):
    seen = set()
    while hasattr(func, "__wrapped__") and func not in seen:
        seen.add(func)
        func = func.__wrapped__
    return func


def _make_app():
    application = Flask(
        __name__, template_folder=str(TEMPLATE_FOLDER))
    application.config.update(TESTING=True, SECRET_KEY="chapter-test-secret")
    Babel(application)
    application.wsgi_app = ProxyAwareWsgiApp(application.wsgi_app, False)
    application.register_blueprint(kobo_blueprint)
    return application


def _write_epub(path, chapter_href="chapter1.xhtml"):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as epub:
        mimetype = zipfile.ZipInfo("mimetype")
        mimetype.compress_type = zipfile.ZIP_STORED
        epub.writestr(mimetype, b"application/epub+zip")
        epub.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
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
    <dc:identifier id="uid">chapter-book-uuid</dc:identifier>
    <dc:title>Chapter Book</dc:title>
  </metadata>
  <manifest>
    <item id="chap1" href="{chapter_href}" media-type="application/xhtml+xml"/>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
  </manifest>
  <spine toc="ncx"><itemref idref="chap1"/></spine>
</package>
""".format(chapter_href=chapter_href),
        )
        nav = NAV.replace("chapter1.xhtml", chapter_href)
        epub.writestr("OEBPS/nav.xhtml", nav)
        epub.writestr("OEBPS/toc.ncx", NCX)
        epub.writestr("OEBPS/{}".format(chapter_href), CHAPTER)


def _add_second_chapter(path):
    with zipfile.ZipFile(path) as source:
        members = [(info, source.read(info.filename)) for info in source.infolist()]
    with zipfile.ZipFile(path, "w") as destination:
        for info, data in members:
            if info.filename == "OEBPS/content.opf":
                data = data.decode()
                data = data.replace(
                    '<item id="nav"',
                    '<item id="chap2" href="chapter2.xhtml" media-type="application/xhtml+xml"/>\n'
                    '    <item id="nav"')
                data = data.replace(
                    '<itemref idref="chap1"/>',
                    '<itemref idref="chap1"/><itemref idref="chap2"/>')
                data = data.encode()
            destination.writestr(info, data)
        destination.writestr("OEBPS/chapter2.xhtml", CHAPTER)


def _last_ncx_src(path):
    with zipfile.ZipFile(path) as epub:
        ncx = etree.fromstring(
            epub.read("OEBPS/toc.ncx"), parser=kobo.epub_chapters._safe_parser)
    return ncx.xpath(
        '//ncx:navPoint[last()]/ncx:content/@src',
        namespaces=kobo.epub_chapters.NCX_NSMAP)[0]


def _install_fakes(monkeypatch, store, library, book, db_state):
    monkeypatch.setattr(
        kobo_auth,
        "ub",
        SimpleNamespace(
            session=store,
            session_commit=lambda: None,
            RemoteAuthToken=FakeRemoteAuthToken,
            User=store.user_cls,
        ),
    )
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
    monkeypatch.setattr(
        web, "current_user", SimpleNamespace(role_download=lambda: True))
    monkeypatch.setattr(
        kobo, "current_user", SimpleNamespace(
            role_edit=lambda: True, role_admin=lambda: False))
    monkeypatch.setattr(
        kobo_auth,
        "render_title_template",
        lambda template, **kwargs: kwargs["auth_token"],
    )

    def commit():
        db_state["commits"] += 1
        db_state["events"].append("commit")

    def rollback():
        db_state["rollbacks"] += 1

    def set_metadata_dirty(book_id):
        db_state["dirty"].append(book_id)

    def create_functions(config):
        db_state["events"].append("functions")

    empty_join = SimpleNamespace(all=lambda: [])
    fake_calibre_db = SimpleNamespace(
        get_book_by_uuid=lambda book_uuid: book,
        session=SimpleNamespace(
            commit=commit,
            rollback=rollback,
            query=lambda *args, **kwargs: SimpleNamespace(
                join=lambda *join_args, **join_kwargs: empty_join),
        ),
        set_metadata_dirty=set_metadata_dirty,
        create_functions=create_functions,
    )
    monkeypatch.setattr(kobo, "calibre_db", fake_calibre_db)
    monkeypatch.setattr(kobo_auth, "calibre_db", fake_calibre_db)
    monkeypatch.setattr(
        kobo.config, "get_book_path", lambda: str(library), raising=False)


def _prepare(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "CALIBRE_WEB_CHAPTER_MARKER_DIR", str(tmp_path / "chapter-markers"))
    library = tmp_path / "library"
    path = library / "Author" / "Book" / "book.epub"
    _write_epub(path)
    book = SimpleNamespace(
        id=23,
        uuid="chapter-book-uuid",
        title="Chapter Book",
        path="Author/Book",
        last_modified=datetime(2020, 1, 1, tzinfo=timezone.utc),
        data=[SimpleNamespace(
            name="book", format="EPUB", uncompressed_size=999)],
    )
    store = TokenStore()
    db_state = {"commits": 0, "rollbacks": 0, "dirty": [], "events": []}
    _install_fakes(monkeypatch, store, library, book, db_state)
    application = _make_app()
    with application.test_request_context("/kobo_auth/generate_auth_token/7"):
        token = _unwrap(kobo_auth.generate_auth_token)(7)
    return application, token, path, book, db_state


def test_chapter_form_requires_valid_token(monkeypatch, tmp_path):
    application, _, _, _, _ = _prepare(monkeypatch, tmp_path)
    client = application.test_client()

    response = client.get("/kobo/not-a-token/chapters/chapter-book-uuid")

    assert response.status_code == 401


def test_chapter_check_reports_served_book(monkeypatch, tmp_path):
    application, token, _, _, _ = _prepare(monkeypatch, tmp_path)
    client = application.test_client()

    response = client.get(
        f"/kobo/{token}/chapters/chapter-book-uuid?check=1"
        "&name=Chapter+Book&location=chapter1.xhtml")

    assert response.status_code == 200
    assert json.loads(response.get_data(as_text=True)) == {
        "book_uuid": "chapter-book-uuid",
        "served": True,
        "title": "Chapter Book",
    }


def test_chapter_check_reports_missing_book_without_html(monkeypatch, tmp_path):
    application, token, _, _, _ = _prepare(monkeypatch, tmp_path)
    monkeypatch.setattr(
        kobo.calibre_db, "get_book_by_uuid", lambda book_uuid: None)
    client = application.test_client()

    response = client.get(
        f"/kobo/{token}/chapters/missing-book?check=1&name=Test+Chapters")

    assert response.status_code == 200
    payload = json.loads(response.get_data(as_text=True))
    assert payload["served"] is False
    assert payload["title"] == "Test Chapters"
    assert payload["error"] == "Book not found in this library."


def test_chapter_form_get_prefills_detected_location(monkeypatch, tmp_path):
    application, token, _, _, _ = _prepare(monkeypatch, tmp_path)
    client = application.test_client()

    response = client.get(
        f"/kobo/{token}/chapters/chapter-book-uuid"
        "?name=Page+3&location=OEBPS%2Fchapter1.xhtml%23kobo.1.1")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Add chapter mark" in body
    assert 'value="Page 3"' in body
    assert '<option value="chapter1.xhtml" selected>' in body
    assert 'id="location_custom" name="location_custom"' in body
    assert 'value="chapter1.xhtml"' in body
    assert 'name="initial_location" value="chapter1.xhtml"' in body
    assert 'name="initial_location_custom" value="chapter1.xhtml"' in body


def test_chapter_form_passes_device_session_id_to_hidden_field(monkeypatch, tmp_path):
    application, token, _, _, _ = _prepare(monkeypatch, tmp_path)
    client = application.test_client()

    response = client.get(
        f"/kobo/{token}/chapters/chapter-book-uuid"
        "?client=device-session-1")

    assert response.status_code == 200
    assert 'name="client" value="device-session-1"' in response.get_data(as_text=True)


def test_prefixed_prefilled_location_does_not_override_changed_dropdown(monkeypatch, tmp_path):
    application, token, path, _, _ = _prepare(monkeypatch, tmp_path)
    _add_second_chapter(path)
    client = application.test_client()

    # Nickel submits the visible free-text field as prefilled by the launcher.
    # An OPF-prefixed path is equivalent to the normalized hidden value and
    # must not beat a dropdown choice the user actually changed.
    response = client.post(
        f"/kobo/{token}/chapters/chapter-book-uuid",
        data={
            "name": "Selected chapter",
            "location": "chapter2.xhtml",
            "location_custom": "OEBPS/chapter1.xhtml",
            "initial_location": "chapter1.xhtml",
            "initial_location_custom": "chapter1.xhtml",
        },
    )

    assert response.status_code == 200
    assert _last_ncx_src(path) == "chapter2.xhtml"


def test_changed_dropdown_overrides_prefilled_custom_location(monkeypatch, tmp_path):
    application, token, path, _, _ = _prepare(monkeypatch, tmp_path)
    _add_second_chapter(path)
    client = application.test_client()

    get_response = client.get(
        f"/kobo/{token}/chapters/chapter-book-uuid"
        "?name=Page+1&location=OEBPS%2Fchapter1.xhtml")
    assert get_response.status_code == 200

    response = client.post(
        f"/kobo/{token}/chapters/chapter-book-uuid",
        data={
            "name": "Selected chapter",
            "location": "chapter2.xhtml",
            "location_custom": "chapter1.xhtml",
            "initial_location": "chapter1.xhtml",
            "initial_location_custom": "chapter1.xhtml",
        },
    )

    assert response.status_code == 200
    assert "chapter2.xhtml" in response.get_data(as_text=True)
    assert _last_ncx_src(path) == "chapter2.xhtml"


def test_edited_custom_location_still_overrides_dropdown(monkeypatch, tmp_path):
    application, token, path, _, _ = _prepare(monkeypatch, tmp_path)
    _add_second_chapter(path)
    client = application.test_client()

    response = client.post(
        f"/kobo/{token}/chapters/chapter-book-uuid",
        data={
            "name": "Custom location",
            "location": "chapter1.xhtml",
            "location_custom": "chapter2.xhtml",
            "initial_location": "chapter1.xhtml",
            "initial_location_custom": "chapter1.xhtml",
        },
    )

    assert response.status_code == 200
    assert _last_ncx_src(path) == "chapter2.xhtml"


def test_device_style_filename_matches_both_format_layouts(monkeypatch, tmp_path):
    application, token, epub_path, book, _ = _prepare(monkeypatch, tmp_path)
    kepub_path = epub_path.with_suffix(".kepub")
    _write_epub(kepub_path, chapter_href="Text/chapter1.xhtml")
    book.data.append(SimpleNamespace(
        name="book", format="KEPUB", uncompressed_size=999))
    client = application.test_client()

    response = client.post(
        f"/kobo/{token}/chapters/chapter-book-uuid",
        data={
            "name": "Device location",
            "location_custom": "OEBPS/chapter1.xhtml#kobo.1.1",
        },
    )

    assert response.status_code == 200
    assert "Chapter mark saved" in response.get_data(as_text=True)
    assert _last_ncx_src(epub_path) == "chapter1.xhtml"
    assert _last_ncx_src(kepub_path) == "Text/chapter1.xhtml"


def test_chapter_post_rewrites_toc_and_marks_binary_change(monkeypatch, tmp_path):
    application, token, path, book, db_state = _prepare(monkeypatch, tmp_path)
    client = application.test_client()

    response = client.post(
        f"/kobo/{token}/chapters/chapter-book-uuid",
        data={"name": "Comic page 3", "location": "chapter1.xhtml"},
    )

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Chapter saved" in body
    assert "Comic page 3" in body
    assert "next device sync" in body

    with zipfile.ZipFile(path) as epub:
        ncx = etree.fromstring(
            epub.read("OEBPS/toc.ncx"), parser=kobo.epub_chapters._safe_parser)
    labels = ncx.xpath(
        '//ncx:navPoint/ncx:navLabel/ncx:text/text()',
        namespaces=kobo.epub_chapters.NCX_NSMAP)
    assert labels == ["Chapter 1", "Comic page 3"]

    assert book.data[0].uncompressed_size == os.path.getsize(path)
    assert book.last_modified > datetime(2020, 1, 1, tzinfo=timezone.utc)
    assert db_state["dirty"] == [23]
    assert db_state["commits"] == 1
    assert db_state["events"] == ["functions", "commit"]
    assert kobo.book_binary_changed_with_edit(book) is True


def test_chapter_post_rewrites_both_epub_and_kepub(monkeypatch, tmp_path):
    application, token, path, book, db_state = _prepare(monkeypatch, tmp_path)
    kepub_path = path.with_suffix(".kepub")
    kepub_path.write_bytes(path.read_bytes())
    book.data.append(SimpleNamespace(
        name="book", format="KEPUB", uncompressed_size=999))
    client = application.test_client()

    response = client.post(
        f"/kobo/{token}/chapters/chapter-book-uuid",
        data={"name": "Both formats", "location": "chapter1.xhtml"},
    )

    assert response.status_code == 200
    assert "EPUB, KEPUB" in response.get_data(as_text=True)
    for book_path in (path, kepub_path):
        with zipfile.ZipFile(book_path) as epub:
            ncx = etree.fromstring(
                epub.read("OEBPS/toc.ncx"), parser=kobo.epub_chapters._safe_parser)
        labels = ncx.xpath(
            '//ncx:navPoint/ncx:navLabel/ncx:text/text()',
            namespaces=kobo.epub_chapters.NCX_NSMAP)
        assert labels == ["Chapter 1", "Both formats"]
        data = next(item for item in book.data
                    if item.format == ("KEPUB" if book_path == kepub_path else "EPUB"))
        assert data.uncompressed_size == os.path.getsize(book_path)
    assert db_state["commits"] == 1


def test_chapter_post_can_return_json(monkeypatch, tmp_path):
    application, token, _, _, _ = _prepare(monkeypatch, tmp_path)
    client = application.test_client()

    response = client.post(
        f"/kobo/{token}/chapters/chapter-book-uuid",
        headers={"Accept": "application/json"},
        data={"name": "Comic page 4", "location_custom": "chapter1.xhtml#page4"},
    )

    assert response.status_code == 200
    payload = json.loads(response.get_data(as_text=True))
    assert payload["success"] is True
    assert payload["name"] == "Comic page 4"
    assert payload["location"] == "chapter1.xhtml#page4"
    assert payload["formats"] == ["EPUB"]


def test_chapter_post_rejects_unknown_location(monkeypatch, tmp_path):
    application, token, path, _, db_state = _prepare(monkeypatch, tmp_path)
    client = application.test_client()

    response = client.post(
        f"/kobo/{token}/chapters/chapter-book-uuid",
        data={"name": "Bad", "location_custom": "../secret.xhtml"},
    )

    assert response.status_code == 400
    assert "not a valid relative chapter path" in response.get_data(as_text=True)
    assert db_state["commits"] == 0
    with zipfile.ZipFile(path) as epub:
        original = epub.read("OEBPS/toc.ncx").decode("utf-8")
    assert "Bad" not in original


def test_chapter_post_json_reports_safe_location_error(monkeypatch, tmp_path):
    application, token, _, _, _ = _prepare(monkeypatch, tmp_path)
    client = application.test_client()

    response = client.post(
        f"/kobo/{token}/chapters/chapter-book-uuid",
        headers={"Accept": "application/json"},
        data={"name": "Bad", "location_custom": "../secret.xhtml"},
    )

    assert response.status_code == 400
    payload = json.loads(response.get_data(as_text=True))
    assert payload["success"] is False
    assert payload["error"] == "That location is not a valid relative chapter path."


def test_chapter_form_get_can_return_json(monkeypatch, tmp_path):
    application, token, _, _, _ = _prepare(monkeypatch, tmp_path)
    client = application.test_client()

    response = client.get(
        f"/kobo/{token}/chapters/chapter-book-uuid"
        "?name=Page+3&location=OEBPS%2Fchapter1.xhtml%23kobo.1.1",
        headers={"Accept": "application/json"},
    )

    assert response.status_code == 200
    payload = json.loads(response.get_data(as_text=True))
    assert payload["success"] is True
    assert payload["name"] == "Page 3"
    assert payload["location"] == "chapter1.xhtml"
    assert [item["value"] for item in payload["candidates"]] == ["chapter1.xhtml"]


def test_chapter_form_get_keeps_invalid_prefill_for_editing(monkeypatch, tmp_path):
    application, token, _, _, _ = _prepare(monkeypatch, tmp_path)
    client = application.test_client()

    response = client.get(
        f"/kobo/{token}/chapters/chapter-book-uuid"
        "?name=Page+3&location=..%2Fsecret.xhtml")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'value="../secret.xhtml"' in body
    assert '<option value="chapter1.xhtml" selected>' not in body


@pytest.mark.parametrize("data, error", [
    ({"name": "", "location": "chapter1.xhtml"}, "Enter a chapter name"),
    ({"name": "No location"}, "Choose or enter a chapter location."),
    ({"name": "Ghost", "location": "chapter2.xhtml"},
     "not a chapter in this book"),
])
def test_chapter_post_validates_form_input(monkeypatch, tmp_path, data, error):
    application, token, _, _, db_state = _prepare(monkeypatch, tmp_path)
    client = application.test_client()

    response = client.post(
        f"/kobo/{token}/chapters/chapter-book-uuid", data=data)

    assert response.status_code == 400
    assert error in response.get_data(as_text=True)
    assert db_state["commits"] == 0


def test_chapter_post_form_error_can_return_json(monkeypatch, tmp_path):
    application, token, _, _, _ = _prepare(monkeypatch, tmp_path)
    client = application.test_client()

    response = client.post(
        f"/kobo/{token}/chapters/chapter-book-uuid",
        headers={"Accept": "application/json"},
        data={"name": "", "location": "chapter1.xhtml"},
    )

    assert response.status_code == 400
    payload = json.loads(response.get_data(as_text=True))
    assert payload["success"] is False
    assert payload["error"] == "Enter a chapter name (200 characters or fewer)."


def test_chapter_form_reports_missing_book(monkeypatch, tmp_path):
    application, token, _, _, _ = _prepare(monkeypatch, tmp_path)
    monkeypatch.setattr(
        kobo.calibre_db, "get_book_by_uuid", lambda book_uuid: None)
    client = application.test_client()

    response = client.get(f"/kobo/{token}/chapters/chapter-book-uuid")

    assert response.status_code == 404
    assert "Book not found in this library." in response.get_data(as_text=True)


def test_chapter_form_reports_book_without_epub_format(monkeypatch, tmp_path):
    application, token, _, book, _ = _prepare(monkeypatch, tmp_path)
    book.data = []
    client = application.test_client()

    response = client.get(f"/kobo/{token}/chapters/chapter-book-uuid")

    assert response.status_code == 400
    assert "This book has no EPUB or KEPUB format." in response.get_data(as_text=True)


def test_chapter_form_reports_unreadable_book(monkeypatch, tmp_path):
    application, token, _, book, _ = _prepare(monkeypatch, tmp_path)
    book.path = "Missing/Author"
    client = application.test_client()

    response = client.get(f"/kobo/{token}/chapters/chapter-book-uuid")

    assert response.status_code == 400
    assert "The book file could not be read." in response.get_data(as_text=True)


def test_chapter_post_reports_rewrite_failure(monkeypatch, tmp_path):
    application, token, _, _, db_state = _prepare(monkeypatch, tmp_path)

    def fail_rewrite(path, name, target):
        raise RuntimeError("zip failure")

    monkeypatch.setattr(kobo.epub_chapters, "rewrite_epub_toc", fail_rewrite)
    client = application.test_client()

    response = client.post(
        f"/kobo/{token}/chapters/chapter-book-uuid",
        data={"name": "Page 1", "location": "chapter1.xhtml"},
    )

    assert response.status_code == 500
    assert "The book could not be updated." in response.get_data(as_text=True)
    assert db_state["commits"] == 0


def test_chapter_post_reports_database_failure(monkeypatch, tmp_path):
    application, token, path, _, db_state = _prepare(monkeypatch, tmp_path)
    original = path.read_bytes()

    def fail_commit():
        raise IntegrityError("UPDATE", {}, Exception("db failure"))

    monkeypatch.setattr(kobo.calibre_db.session, "commit", fail_commit)
    client = application.test_client()

    response = client.post(
        f"/kobo/{token}/chapters/chapter-book-uuid",
        data={"name": "Page 1", "location": "chapter1.xhtml"},
    )

    assert response.status_code == 500
    assert "the database could not be updated" in response.get_data(as_text=True)
    assert db_state["rollbacks"] == 1
    assert db_state["commits"] == 0
    assert path.read_bytes() == original
    assert list(tmp_path.glob("**/.kobo-chapter-backup-*")) == []


def test_chapter_form_requires_edit_rights(monkeypatch, tmp_path):
    application, token, path, _, db_state = _prepare(monkeypatch, tmp_path)
    monkeypatch.setattr(
        kobo, "current_user", SimpleNamespace(
            role_edit=lambda: False, role_admin=lambda: False))
    original = path.read_bytes()
    client = application.test_client()

    response = client.post(
        f"/kobo/{token}/chapters/chapter-book-uuid",
        data={"name": "Page 1", "location": "chapter1.xhtml"},
    )

    assert response.status_code == 403
    assert path.read_bytes() == original
    assert db_state["commits"] == 0


def test_chapter_post_rejects_location_missing_from_other_format(monkeypatch, tmp_path):
    application, token, epub_path, book, db_state = _prepare(monkeypatch, tmp_path)
    kepub_path = epub_path.with_suffix(".kepub")
    _write_epub(epub_path, chapter_href="page1.xhtml")
    _write_epub(kepub_path)
    book.data.append(SimpleNamespace(
        name="book", format="KEPUB", uncompressed_size=999))
    original_epub = epub_path.read_bytes()
    original_kepub = kepub_path.read_bytes()
    client = application.test_client()

    response = client.post(
        f"/kobo/{token}/chapters/chapter-book-uuid",
        data={"name": "Both formats", "location": "chapter1.xhtml"},
    )

    assert response.status_code == 400
    assert "not a chapter in the EPUB format" in response.get_data(as_text=True)
    assert epub_path.read_bytes() == original_epub
    assert kepub_path.read_bytes() == original_kepub
    assert db_state["commits"] == 0


def test_restorable_files_logs_restore_failure(monkeypatch, tmp_path):
    path = tmp_path / "book.epub"
    path.write_bytes(b"original")

    def fail_replace(source, destination):
        raise OSError("cannot restore")

    monkeypatch.setattr(kobo.os, "replace", fail_replace)

    with pytest.raises(RuntimeError, match="boom"):
        with kobo._restorable_files([path]):
            raise RuntimeError("boom")

    assert path.read_bytes() == b"original"


def test_restorable_files_ignores_cleanup_failure(monkeypatch, tmp_path):
    path = tmp_path / "book.epub"
    path.write_bytes(b"original")

    def fail_unlink(backup_path):
        raise OSError("cannot unlink")

    monkeypatch.setattr(kobo.os, "unlink", fail_unlink)

    with kobo._restorable_files([path]):
        pass

    assert path.read_bytes() == b"original"


def test_chapter_post_restores_formats_after_later_failure(monkeypatch, tmp_path):
    application, token, epub_path, book, db_state = _prepare(monkeypatch, tmp_path)
    kepub_path = epub_path.with_suffix(".kepub")
    kepub_path.write_bytes(epub_path.read_bytes())
    book.data.append(SimpleNamespace(
        name="book", format="KEPUB", uncompressed_size=999))
    originals = {path: path.read_bytes() for path in (epub_path, kepub_path)}
    original_rewrite = kobo.epub_chapters.rewrite_epub_toc
    calls = []

    def rewrite_then_fail(path, name, target, structure=None):
        calls.append(path)
        if len(calls) == 2:
            raise RuntimeError("zip failure")
        return original_rewrite(path, name, target, structure=structure)

    monkeypatch.setattr(kobo.epub_chapters, "rewrite_epub_toc", rewrite_then_fail)
    client = application.test_client()

    response = client.post(
        f"/kobo/{token}/chapters/chapter-book-uuid",
        data={"name": "Both formats", "location": "chapter1.xhtml"},
    )

    assert response.status_code == 500
    assert "The book could not be updated." in response.get_data(as_text=True)
    assert len(calls) == 2
    assert all(path.read_bytes() == data for path, data in originals.items())
    assert db_state["commits"] == 0
    assert list(tmp_path.glob("**/.kobo-chapter-backup-*")) == []


def test_successful_post_records_consumable_device_marker(monkeypatch, tmp_path):
    application, token, _, _, _ = _prepare(monkeypatch, tmp_path)
    client = application.test_client()

    response = client.post(
        f"/kobo/{token}/chapters/chapter-book-uuid",
        data={
            "name": "Page 12",
            "location": "chapter1.xhtml",
            "client": "device-session-1",
        },
    )
    assert response.status_code == 200

    result = client.get(
        f"/kobo/{token}/chapters/chapter-book-uuid/result"
        "?client=device-session-1")

    assert result.status_code == 200
    assert result.headers["Content-Type"].startswith("text/plain")
    assert result.get_data(as_text=True) == (
        "saved=1\nname=Page 12\nlocation=chapter1.xhtml\n")

    again = client.get(
        f"/kobo/{token}/chapters/chapter-book-uuid/result"
        "?client=device-session-1")
    assert again.get_data(as_text=True) == "saved=0\n"


def test_device_result_json_and_invalid_client(monkeypatch, tmp_path):
    application, token, _, _, _ = _prepare(monkeypatch, tmp_path)
    client = application.test_client()

    client.post(
        f"/kobo/{token}/chapters/chapter-book-uuid",
        data={
            "name": "JSON mark",
            "location": "chapter1.xhtml",
            "client": "device-session-2",
        },
    )
    response = client.get(
        f"/kobo/{token}/chapters/chapter-book-uuid/result"
        "?client=device-session-2",
        headers={"Accept": "application/json"},
    )

    assert response.status_code == 200
    assert json.loads(response.get_data(as_text=True)) == {
        "book_uuid": "chapter-book-uuid",
        "saved": True,
        "name": "JSON mark",
        "location": "chapter1.xhtml",
    }

    invalid = client.get(
        f"/kobo/{token}/chapters/chapter-book-uuid/result"
        "?client=bad%2Fclient")
    assert invalid.get_data(as_text=True) == "saved=0\n"
