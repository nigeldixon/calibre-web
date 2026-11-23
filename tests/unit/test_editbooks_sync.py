"""Regression tests for Kobo sync invalidation in the edit-books flows."""
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy.exc import IntegrityError

from cps import editbooks


def _unwrap(func):
    """Call the original function behind the route/permission decorators."""
    seen = set()
    while hasattr(func, "__wrapped__") and func not in seen:
        seen.add(func)
        func = func.__wrapped__
    return func


def _assert_no_sync_invalidation(calls):
    assert not any(isinstance(call, tuple) and call[0] == "remove" for call in calls)


def test_edit_book_param_marks_edited_book_changed_for_sync(monkeypatch, app):
    calls = []
    book = SimpleNamespace(id=42, tags=[], last_modified=None)
    monkeypatch.setattr(editbooks, "calibre_db", SimpleNamespace(
        get_book=lambda book_id: book,
        create_functions=lambda config: None,
        session=SimpleNamespace(commit=lambda: calls.append("commit")),
    ))
    monkeypatch.setattr(editbooks, "edit_book_tags", lambda value, target: setattr(
        target, "tags", [SimpleNamespace(name=value)]
    ))
    monkeypatch.setattr(editbooks, "kobo_sync_status", SimpleNamespace(
        remove_synced_book=lambda *args, **kwargs: calls.append(("remove", args, kwargs))
    ))

    with app.test_request_context("/"):
        result = _unwrap(editbooks.edit_book_param)("tags", {"pk": [42], "value": "fantasy"})

    assert result["success"] is True
    _assert_no_sync_invalidation(calls)
    assert "commit" in calls
    assert book.last_modified.tzinfo is not None


def test_merge_list_book_marks_target_changed_for_sync(monkeypatch, app, tmp_path):
    calls = []
    library = tmp_path / "library"
    (library / "Target" / "Author").mkdir(parents=True)
    (library / "Source").mkdir(parents=True)
    (library / "Source" / "original.pdf").write_bytes(b"pdf")

    to_book = SimpleNamespace(
        id=1,
        title="Target",
        authors=[SimpleNamespace(name="Author")],
        path="Target/Author",
        data=[SimpleNamespace(format="EPUB")],
        last_modified=None,
    )
    from_book = SimpleNamespace(
        id=2,
        path="Source",
        data=[SimpleNamespace(format="PDF", name="original", uncompressed_size=3)],
    )
    created = []

    class FakeData:
        def __init__(self, *args):
            created.append(args)

    monkeypatch.setattr(editbooks, "db", SimpleNamespace(Data=FakeData))
    monkeypatch.setattr(editbooks, "calibre_db", SimpleNamespace(
        get_book=lambda book_id: {1: to_book, 2: from_book}.get(book_id),
        set_metadata_dirty=lambda book_id: calls.append(("dirty", book_id)),
        session=SimpleNamespace(commit=lambda: calls.append("commit")),
    ))
    monkeypatch.setattr(editbooks, "helper", SimpleNamespace(
        get_valid_filename=lambda value, chars: value,
    ))
    monkeypatch.setattr(editbooks.config, "get_book_path", lambda: str(library))
    monkeypatch.setattr(editbooks, "check_delete_book", lambda ids, message, flag: calls.append(("delete", ids)))
    monkeypatch.setattr(editbooks, "kobo_sync_status", SimpleNamespace(
        remove_synced_book=lambda *args, **kwargs: calls.append(("remove", args, kwargs))
    ))

    with app.test_request_context("/", json={"Merge_books": [1, 2]}):
        response = _unwrap(editbooks.merge_list_book)()

    assert response.status_code == 200
    assert created == [(1, "PDF", 3, "Target - Author")]
    assert ("dirty", 1) in calls
    assert "commit" in calls
    assert ("delete", [2]) in calls
    _assert_no_sync_invalidation(calls)
    assert to_book.last_modified.tzinfo is not None


def test_table_xchange_author_title_marks_book_changed_for_sync(monkeypatch, app):
    calls = []
    book = SimpleNamespace(id=8, title="Old Title", authors=[], last_modified=None)
    monkeypatch.setattr(editbooks, "calibre_db", SimpleNamespace(
        get_book=lambda book_id: book,
        order_authors=lambda books: [SimpleNamespace(name="Old Author")],
        set_metadata_dirty=lambda book_id: calls.append(("dirty", book_id)),
        session=SimpleNamespace(commit=lambda: calls.append("commit"), rollback=lambda: None),
    ))
    monkeypatch.setattr(editbooks, "handle_title_on_edit", lambda book, title: True)
    monkeypatch.setattr(editbooks, "handle_author_on_edit", lambda book, authors: (["New Author"], True))
    monkeypatch.setattr(editbooks, "helper", SimpleNamespace(
        update_dir_structure=lambda *args: None,
    ))
    monkeypatch.setattr(editbooks.config, "config_use_google_drive", False, raising=False)
    monkeypatch.setattr(editbooks.config, "get_book_path", lambda: "/library")
    monkeypatch.setattr(editbooks, "kobo_sync_status", SimpleNamespace(
        remove_synced_book=lambda *args, **kwargs: calls.append(("remove", args, kwargs))
    ))

    with app.test_request_context("/", json={"xchange": [8]}):
        response = _unwrap(editbooks.table_xchange_author_title)()

    assert response.status_code == 200
    _assert_no_sync_invalidation(calls)
    assert ("dirty", 8) in calls
    assert "commit" in calls
    assert book.last_modified.tzinfo is not None


def test_do_edit_book_marks_actual_book_changed_for_sync(monkeypatch, app):
    calls = []
    book = SimpleNamespace(id=17, has_cover=0, title="Book", path="Author/Book", identifiers=[])
    monkeypatch.setattr(editbooks, "calibre_db", SimpleNamespace(
        create_functions=lambda config: None,
        get_filtered_book=lambda book_id, allow_show_archived=True: book,
        set_metadata_dirty=lambda book_id: calls.append(("dirty", book_id)),
        session=SimpleNamespace(
            merge=lambda merged: calls.append(("merge", merged.id)),
            commit=lambda: calls.append("commit"),
            rollback=lambda: None,
        ),
    ))
    monkeypatch.setattr(editbooks, "handle_title_on_edit", lambda book, title: False)
    monkeypatch.setattr(editbooks, "handle_author_on_edit", lambda book, authors: (["Author"], False))
    monkeypatch.setattr(editbooks, "edit_book_ratings", lambda to_save, book: False)
    monkeypatch.setattr(editbooks, "upload_cover", lambda request, book: True)
    monkeypatch.setattr(editbooks, "edit_book_series_index", lambda *args, **kwargs: False)
    monkeypatch.setattr(editbooks, "edit_book_comments", lambda *args, **kwargs: False)
    monkeypatch.setattr(editbooks, "identifier_list", lambda to_save, book: [])
    monkeypatch.setattr(editbooks, "modify_identifiers", lambda *args: (False, None))
    monkeypatch.setattr(editbooks, "edit_book_tags", lambda *args, **kwargs: False)
    monkeypatch.setattr(editbooks, "edit_book_series", lambda *args, **kwargs: False)
    monkeypatch.setattr(editbooks, "edit_book_publisher", lambda *args, **kwargs: False)
    monkeypatch.setattr(editbooks, "edit_book_languages", lambda *args, **kwargs: False)
    monkeypatch.setattr(editbooks, "edit_all_cc_data", lambda *args, **kwargs: False)
    monkeypatch.setattr(editbooks, "render_edit_book", lambda book_id: "rendered")
    monkeypatch.setattr(editbooks.config, "config_use_google_drive", False, raising=False)
    monkeypatch.setattr(editbooks, "kobo_sync_status", SimpleNamespace(
        remove_synced_book=lambda *args, **kwargs: calls.append(("remove", args, kwargs))
    ))

    with app.test_request_context("/", data={"title": "Book", "authors": "Author", "comments": ""}):
        result = editbooks.do_edit_book(17)

    assert result == "rendered"
    _assert_no_sync_invalidation(calls)
    assert ("dirty", 17) in calls
    assert book.last_modified.tzinfo is not None


class _UploadedFormat:
    filename = "book.epub"

    def save(self, path):
        Path(path).write_bytes(b"updated!")


def _patch_upload_dependencies(monkeypatch, calls):
    monkeypatch.setattr(editbooks.config, "get_book_path", lambda: "/library")
    monkeypatch.setattr(editbooks.config, "config_upload_formats", "epub", raising=False)
    monkeypatch.setattr(editbooks.config, "config_check_extensions", False, raising=False)
    monkeypatch.setattr(editbooks.config, "config_rarfile_location", None, raising=False)
    monkeypatch.setattr(editbooks, "current_user", SimpleNamespace(
        name="tester",
        role_upload=lambda: True,
    ))
    monkeypatch.setattr(editbooks, "WorkerThread", SimpleNamespace(add=lambda *args: None))
    monkeypatch.setattr(editbooks, "TaskUpload", lambda *args: "task")
    monkeypatch.setattr(editbooks, "uploader", SimpleNamespace(process=lambda *args, **kwargs: {}))
    monkeypatch.setattr(editbooks, "merge_metadata", lambda *args: None)
    monkeypatch.setattr(editbooks, "url_for", lambda *args, **kwargs: "/book")
    monkeypatch.setattr(editbooks, "resolve_binary_path", lambda *args: "rar")
    monkeypatch.setattr(editbooks, "kobo_sync_status", SimpleNamespace(
        remove_synced_book=lambda *args, **kwargs: calls.append(("remove", args, kwargs))
    ))


def test_upload_book_formats_updates_existing_format_and_marks_book_changed(monkeypatch, app, tmp_path):
    calls = []
    existing_format = SimpleNamespace(uncompressed_size=1)
    monkeypatch.setattr(editbooks, "calibre_db", SimpleNamespace(
        get_book_format=lambda book_id, book_format: existing_format,
        set_metadata_dirty=lambda book_id: calls.append(("dirty", book_id)),
        create_functions=lambda config: calls.append("functions"),
        session=SimpleNamespace(
            add=lambda obj: calls.append(("add", obj)),
            commit=lambda: calls.append("commit"),
            rollback=lambda: calls.append("rollback"),
        ),
    ))
    _patch_upload_dependencies(monkeypatch, calls)
    monkeypatch.setattr(editbooks.config, "get_book_path", lambda: str(tmp_path))
    book = SimpleNamespace(id=23, path="Author/Book", title="Book", has_cover=0)

    with app.test_request_context("/"):
        to_save, error = editbooks.upload_book_formats([_UploadedFormat()], book, book.id)

    assert error is False
    assert existing_format.uncompressed_size == 8
    _assert_no_sync_invalidation(calls)
    assert ("dirty", 23) in calls
    assert "commit" in calls
    assert not any(call[0] == "add" for call in calls if isinstance(call, tuple))
    assert book.last_modified.tzinfo is not None
    assert to_save == {}


def test_upload_book_formats_adds_new_format_and_marks_book_changed(monkeypatch, app, tmp_path):
    calls = []
    created = []

    class FakeData:
        def __init__(self, *args):
            created.append(args)

    monkeypatch.setattr(editbooks, "db", SimpleNamespace(Data=FakeData))
    monkeypatch.setattr(editbooks, "calibre_db", SimpleNamespace(
        get_book_format=lambda book_id, book_format: None,
        set_metadata_dirty=lambda book_id: calls.append(("dirty", book_id)),
        create_functions=lambda config: calls.append("functions"),
        session=SimpleNamespace(
            add=lambda obj: calls.append(("add", obj)),
            commit=lambda: calls.append("commit"),
            rollback=lambda: calls.append("rollback"),
        ),
    ))
    _patch_upload_dependencies(monkeypatch, calls)
    monkeypatch.setattr(editbooks.config, "get_book_path", lambda: str(tmp_path))
    book = SimpleNamespace(id=24, path="Author/Book", title="Book", has_cover=0)

    with app.test_request_context("/"):
        _, error = editbooks.upload_book_formats([_UploadedFormat()], book, book.id)

    assert error is False
    assert created == [(24, "EPUB", 8, "Book")]
    _assert_no_sync_invalidation(calls)
    assert ("dirty", 24) in calls
    assert "commit" in calls
    assert book.last_modified.tzinfo is not None


def test_upload_book_formats_rolls_back_when_replacing_format_fails(monkeypatch, app, tmp_path):
    calls = []
    existing_format = SimpleNamespace(uncompressed_size=1)

    def fail_commit():
        raise IntegrityError("UPDATE", {}, Exception("duplicate"))

    monkeypatch.setattr(editbooks, "calibre_db", SimpleNamespace(
        get_book_format=lambda book_id, book_format: existing_format,
        set_metadata_dirty=lambda book_id: calls.append(("dirty", book_id)),
        create_functions=lambda config: calls.append("functions"),
        session=SimpleNamespace(
            commit=fail_commit,
            rollback=lambda: calls.append("rollback"),
        ),
    ))
    _patch_upload_dependencies(monkeypatch, calls)
    monkeypatch.setattr(editbooks.config, "get_book_path", lambda: str(tmp_path))
    book = SimpleNamespace(id=25, path="Author/Book", title="Book", has_cover=0)

    with app.test_request_context("/"):
        _, error = editbooks.upload_book_formats([_UploadedFormat()], book, book.id)

    assert error is True
    _assert_no_sync_invalidation(calls)
    assert "rollback" in calls
