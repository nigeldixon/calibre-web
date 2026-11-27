#!/usr/bin/env python
# -*- coding: utf-8 -*-

#  This file is part of the Calibre-Web (https://github.com/janeczku/calibre-web)
#    Copyright (C) 2018-2019 shavitmichael, OzzieIsaacs
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program. If not, see <http://www.gnu.org/licenses/>.

import base64
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import os
import shutil
import tempfile
import uuid
import zipfile
from time import gmtime, strftime
import json
from urllib.parse import unquote
import requests

from flask import (
    Blueprint,
    request,
    make_response,
    jsonify,
    current_app,
    url_for,
    redirect,
    render_template,
    abort
)
from .cw_login import current_user
from werkzeug.datastructures import Headers
from sqlalchemy import func
from sqlalchemy.sql.expression import and_, or_
from sqlalchemy.exc import IntegrityError, OperationalError, StatementError
from sqlalchemy.orm.exc import StaleDataError

from . import config, logger, kobo_auth, db, calibre_db, helper, shelf as shelf_lib, ub, csrf, kobo_sync_status
from . import isoLanguages, limiter
from .epub import get_epub_layout
from . import epub_chapters
from .constants import COVER_THUMBNAIL_SMALL, COVER_THUMBNAIL_MEDIUM, COVER_THUMBNAIL_LARGE, BASE_DIR
from .helper import get_download_link
from .services import SyncToken as SyncToken
from .web import download_required
from .kobo_auth import requires_kobo_auth, get_auth_token

KOBO_FORMATS = {"KEPUB": ["KEPUB"], "EPUB": ["EPUB3", "EPUB"]}
KOBO_STOREAPI_URL = "https://storeapi.kobo.com"
KOBO_IMAGEHOST_URL = "https://cdn.kobo.com/book-images"

SYNC_ITEM_LIMIT = 100

kobo = Blueprint("kobo", __name__, url_prefix="/kobo/<auth_token>")
kobo_auth.disable_failed_auth_redirect_for_blueprint(kobo)
kobo_auth.register_url_value_preprocessor(kobo)

log = logger.create()


class ChapterDatabaseError(Exception):
    """The chapter files changed but the database update failed."""


@contextmanager
def _restorable_files(paths):
    """Restore ``paths`` from copies if the caller raises before success."""
    created = []
    restorable = []
    try:
        for path in paths:
            directory = os.path.dirname(os.path.abspath(path)) or '.'
            handle, backup_path = tempfile.mkstemp(
                prefix='.kobo-chapter-backup-',
                suffix=os.path.splitext(path)[1],
                dir=directory)
            os.close(handle)
            created.append(backup_path)
            shutil.copy2(path, backup_path)
            restorable.append((path, backup_path))
        yield
    except Exception:
        for path, backup_path in restorable:
            try:
                if os.path.exists(backup_path):
                    os.replace(backup_path, path)
            except OSError as exc:
                log.error("Kobo chapter restore failed for %s: %s", path, exc)
        raise
    finally:
        for backup_path in created:
            try:
                os.unlink(backup_path)
            except OSError:
                pass


def get_store_url_for_current_request():
    # Programmatically modify the current url to point to the official Kobo store
    __, __, request_path_with_auth_token = request.full_path.rpartition("/kobo/")
    __, __, request_path = request_path_with_auth_token.rstrip("?").partition(
        "/"
    )
    return KOBO_STOREAPI_URL + "/" + request_path


CONNECTION_SPECIFIC_HEADERS = [
    "connection",
    "content-encoding",
    "content-length",
    "transfer-encoding",
]


def get_kobo_activated():
    return config.config_kobo_sync


def make_request_to_kobo_store(sync_token=None):
    outgoing_headers = Headers(request.headers)
    outgoing_headers.remove("Host")
    if sync_token:
        sync_token.set_kobo_store_header(outgoing_headers)

    store_response = requests.request(
        method=request.method,
        url=get_store_url_for_current_request(),
        headers=outgoing_headers,
        data=request.get_data(),
        allow_redirects=False,
        timeout=(2, 10)
    )
    log.debug("Content: " + str(store_response.content))
    log.debug("StatusCode: " + str(store_response.status_code))
    return store_response


def redirect_or_proxy_request(auth=False):
    if config.config_kobo_proxy:
        try:
            if request.method == "GET":
                alfa = redirect(get_store_url_for_current_request(), 307)
                return alfa
            else:
                # The Kobo device turns other request types into GET requests on redirects,
                # so we instead proxy to the Kobo store ourselves.
                store_response = make_request_to_kobo_store()

                response_headers = store_response.headers
                for header_key in CONNECTION_SPECIFIC_HEADERS:
                    response_headers.pop(header_key, default=None)

                return make_response(
                    store_response.content, store_response.status_code, response_headers.items()
                )
        except Exception as e:
            log.error("Failed to receive or parse response from Kobo's endpoint: {}".format(e))
            if auth:
                return make_calibre_web_auth_response()
    return make_response(jsonify({}))


def convert_to_kobo_timestamp_string(timestamp):
    try:
        return timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")
    except AttributeError as exc:
        log.debug("Timestamp not valid: {}".format(exc))
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def book_change_filter(sync_token, change_timestamp):
    """Select changes newer than the device token, using the book id for ties."""
    if sync_token.books_last_id > -1:
        return or_(
            change_timestamp > sync_token.books_last_modified,
            and_(change_timestamp == sync_token.books_last_modified,
                 db.Books.id > sync_token.books_last_id),
        )
    return change_timestamp > sync_token.books_last_modified


@kobo.route("/v1/library/sync")
@requires_kobo_auth
def HandleSyncRequest():
    if not current_user.role_download():
        log.info("Users need download permissions for syncing library to Kobo reader")
        return abort(403)
    sync_token = SyncToken.SyncToken.from_headers(request.headers)
    log.info("Kobo library sync request received")
    log.debug("SyncToken: {}".format(sync_token))
    log.debug("Download link format {}".format(get_download_url_for_book('[bookid]', '[bookformat]')))
    if not current_app.wsgi_app.is_proxied:
        log.debug('Kobo: Received unproxied request, changed request port to external server port')

    new_books_last_modified = sync_token.books_last_modified  # needed for sync selected shelfs only
    new_books_last_created = sync_token.books_last_created  # needed to distinguish between new and changed entitlement
    new_reading_state_last_modified = sync_token.reading_state_last_modified

    new_archived_last_modified = datetime.min
    sync_results = []

    # We reload the book database so that the user gets a fresh view of the library
    # in case of external changes (e.g: adding a book through Calibre).
    calibre_db.reconnect_db(config, ub.app_DB_path)

    only_kobo_shelves = current_user.kobo_only_shelves_sync

    # Archive/unarchive changes have their own timestamp. Include it in the
    # book change cursor so a removal is selected even when only the archive
    # row changed since the device's last sync. Older Calibre libraries can
    # have a missing last_modified, so fall back to the book's timestamp.
    book_last_modified = func.coalesce(db.Books.last_modified, db.Books.timestamp)
    archive_change_timestamp = func.coalesce(ub.ArchivedBook.last_modified, book_last_modified)

    if only_kobo_shelves:
        book_change_timestamp = func.max(book_last_modified, archive_change_timestamp,
                                         ub.BookShelf.date_added)
        changed_entries = calibre_db.session.query(db.Books,
                                                   ub.ArchivedBook.last_modified,
                                                   ub.BookShelf.date_added,
                                                   ub.ArchivedBook.is_archived)
        changed_entries = (changed_entries
                           .join(db.Data).outerjoin(ub.ArchivedBook, and_(db.Books.id == ub.ArchivedBook.book_id,
                                                                          ub.ArchivedBook.user_id == current_user.id))
                           .filter(book_change_filter(sync_token, book_change_timestamp))
                           .filter(db.Data.format.in_(KOBO_FORMATS))
                           .filter(calibre_db.common_filters(allow_show_archived=True))
                           .join(ub.BookShelf, db.Books.id == ub.BookShelf.book_id)
                           .join(ub.Shelf)
                           .filter(ub.Shelf.user_id == current_user.id)
                           .filter(ub.Shelf.kobo_sync)
                           .order_by(book_change_timestamp)
                           .order_by(db.Books.id)
                           .order_by(ub.ArchivedBook.last_modified)
                           .distinct())
    else:
        book_change_timestamp = func.max(book_last_modified, archive_change_timestamp)
        changed_entries = calibre_db.session.query(db.Books,
                                                   ub.ArchivedBook.last_modified,
                                                   ub.ArchivedBook.is_archived)
        changed_entries = (changed_entries
                           .join(db.Data).outerjoin(ub.ArchivedBook, and_(db.Books.id == ub.ArchivedBook.book_id,
                                                                          ub.ArchivedBook.user_id == current_user.id))
                           .filter(book_change_filter(sync_token, book_change_timestamp))
                           .filter(calibre_db.common_filters(allow_show_archived=True))
                           .filter(db.Data.format.in_(KOBO_FORMATS))
                           .order_by(book_change_timestamp)
                           .order_by(db.Books.id))

    reading_states_in_new_entitlements = []
    books = changed_entries.limit(SYNC_ITEM_LIMIT).all()
    books_last_id = books[-1].Books.id if books else -1
    log.debug("Books to Sync: {}".format(len(books)))
    for book in books:
        formats = [data.format for data in book.Books.data]
        if 'KEPUB' not in formats and config.config_kepubifypath and 'EPUB' in formats:
            helper.convert_book_format(book.Books.id, config.get_book_path(), 'EPUB', 'KEPUB', current_user.name)

        kobo_reading_state = get_or_create_reading_state(book.Books.id)
        metadata = get_metadata(book.Books)
        entitlement = {
            "BookEntitlement": create_book_entitlement(book.Books, archived=(book.is_archived==True)),
            "BookMetadata": metadata,
        }

        ts_created = book.Books.timestamp.replace(tzinfo=None)

        try:
            ts_created = max(ts_created, book.date_added)
        except AttributeError:
            pass

        is_new = ts_created > sync_token.books_last_created
        archive_timestamp = getattr(book, "last_modified", None)
        archive_changed = bool(
            archive_timestamp and _naive_utc(archive_timestamp) > sync_token.books_last_modified
        )
        binary_changed = book_binary_changed_with_edit(book.Books)
        metadata_only = not is_new and not archive_changed and not binary_changed
        reading_state_changed = kobo_reading_state.last_modified > sync_token.reading_state_last_modified

        if metadata_only:
            # ChangedEntitlement invalidates the downloaded file on the device
            # without updating its metadata. A metadata-only edit must use
            # ChangedProductMetadata instead, so the book stays downloaded.
            sync_results.append({
                "ChangedProductMetadata": {"BookMetadata": _metadata_for_changed_product(metadata)}
            })
            log.debug("Kobo: metadata-only update for book id %s", book.Books.id)
        else:
            if reading_state_changed:
                entitlement["ReadingState"] = get_kobo_reading_state_response(book.Books, kobo_reading_state)
                new_reading_state_last_modified = max(new_reading_state_last_modified, kobo_reading_state.last_modified)
                reading_states_in_new_entitlements.append(book.Books.id)

            if is_new:
                sync_results.append({"NewEntitlement": entitlement})
            elif binary_changed and not book.is_archived:
                # The TOC rewrite changes the file, so the Kobo must fetch it
                # again. Use the Preview/Full pair that makes it re-download
                # in place; a bare ChangedEntitlement only removes the book.
                sync_results.extend(_binary_update_entitlements(entitlement))
            else:
                sync_results.append({"ChangedEntitlement": entitlement})

        # Reading state for a metadata-only update is emitted separately below;
        # a ChangedProductMetadata command must not carry it.

        effective_last_modified = book.Books.last_modified or book.Books.timestamp
        if effective_last_modified:
            new_books_last_modified = max(
                effective_last_modified.replace(tzinfo=None), new_books_last_modified
            )
        archived_timestamp = getattr(book, "last_modified", None)
        if archived_timestamp:
            new_books_last_modified = max(
                new_books_last_modified, archived_timestamp.replace(tzinfo=None)
            )
        try:
            new_books_last_modified = max(
                new_books_last_modified, book.date_added
            )
        except AttributeError:
            pass

        new_books_last_created = max(ts_created, new_books_last_created)
        kobo_sync_status.add_synced_books(book.Books.id)

    for archived in books:
        archived_timestamp = getattr(archived, "last_modified", None)
        if archived.is_archived and archived_timestamp:
            new_archived_last_modified = max(
                new_archived_last_modified, archived_timestamp.replace(tzinfo=None)
            )

    # no. of books returned
    book_count = changed_entries.count()
    # last entry:
    cont_sync = bool(book_count)
    log.debug("Remaining books to Sync: {}".format(book_count))
    # generate reading state data
    changed_reading_states = ub.session.query(ub.KoboReadingState)

    if only_kobo_shelves:
        changed_reading_states = changed_reading_states.join(ub.BookShelf,
                                                             ub.KoboReadingState.book_id == ub.BookShelf.book_id)\
            .join(ub.Shelf)\
            .filter(current_user.id == ub.Shelf.user_id)\
            .filter(ub.Shelf.kobo_sync,
                    or_(
                        ub.KoboReadingState.last_modified > sync_token.reading_state_last_modified,
                        func.datetime(ub.BookShelf.date_added) > sync_token.books_last_modified
                    )).distinct()
    else:
        changed_reading_states = changed_reading_states.filter(
            ub.KoboReadingState.last_modified > sync_token.reading_state_last_modified)

    changed_reading_states = changed_reading_states.filter(
        and_(ub.KoboReadingState.user_id == current_user.id,
             ub.KoboReadingState.book_id.notin_(reading_states_in_new_entitlements)))\
        .order_by(ub.KoboReadingState.last_modified)
    cont_sync |= bool(changed_reading_states.count() > SYNC_ITEM_LIMIT)
    for kobo_reading_state in changed_reading_states.limit(SYNC_ITEM_LIMIT).all():
        book = calibre_db.session.query(db.Books).filter(db.Books.id == kobo_reading_state.book_id).one_or_none()
        if book:
            sync_results.append({
                "ChangedReadingState": {
                    "ReadingState": get_kobo_reading_state_response(book, kobo_reading_state)
                }
            })
            new_reading_state_last_modified = max(new_reading_state_last_modified, kobo_reading_state.last_modified)

    sync_shelves(sync_token, sync_results, only_kobo_shelves)

    # update last created timestamp to distinguish between new and changed entitlements
    if not cont_sync:
        sync_token.books_last_created = new_books_last_created
    sync_token.books_last_modified = new_books_last_modified
    sync_token.books_last_id = books_last_id
    sync_token.archive_last_modified = new_archived_last_modified
    sync_token.reading_state_last_modified = new_reading_state_last_modified

    return generate_sync_response(sync_token, sync_results, cont_sync)


def generate_sync_response(sync_token, sync_results, set_cont=False):
    extra_headers = {}
    if config.config_kobo_proxy and not set_cont:
        # Merge in sync results from the official Kobo store.
        try:
            store_response = make_request_to_kobo_store(sync_token)

            store_sync_results = store_response.json()
            sync_results += store_sync_results
            sync_token.merge_from_store_response(store_response)
            extra_headers["x-kobo-sync"] = store_response.headers.get("x-kobo-sync")
            extra_headers["x-kobo-sync-mode"] = store_response.headers.get("x-kobo-sync-mode")
            extra_headers["x-kobo-recent-reads"] = store_response.headers.get("x-kobo-recent-reads")

        except Exception as ex:
            log.error_or_exception("Failed to receive or parse response from Kobo's sync endpoint: {}".format(ex))
    if set_cont:
        extra_headers["x-kobo-sync"] = "continue"
    sync_token.to_headers(extra_headers)

    # log.debug("Kobo Sync Content: {}".format(sync_results))
    # jsonify decodes the Unicode string different to what kobo expects
    response = make_response(json.dumps(sync_results), extra_headers)
    response.headers["Content-Type"] = "application/json; charset=utf-8"
    return response


@kobo.route("/v1/library/<book_uuid>/metadata")
@requires_kobo_auth
@download_required
def HandleMetadataRequest(book_uuid):
    if not current_app.wsgi_app.is_proxied:
        log.debug('Kobo: Received unproxied request, changed request port to external server port')
    log.info("Kobo library metadata request received for book %s" % book_uuid)
    book = calibre_db.get_book_by_uuid(book_uuid)
    if not book or not book.data:
        log.info("Book %s not found in database", book_uuid)
        return redirect_or_proxy_request()

    metadata = get_metadata(book)
    response = make_response(json.dumps([metadata], ensure_ascii=False))
    response.headers["Content-Type"] = "application/json; charset=utf-8"
    return response


def _book_format_path(book, book_data):
    return os.path.join(config.get_book_path(), book.path,
                        book_data.name + "." + book_data.format.lower())


def _wants_chapter_json():
    return 'application/json' in request.headers.get('Accept', '').lower()


def _chapter_form_url(book_uuid):
    return url_for(
        "kobo.HandleChapterForm",
        auth_token=get_auth_token(),
        book_uuid=book_uuid,
    )


def _chapter_form_response(book_uuid, candidates, name, selected_location,
                           custom_location, book_title="", error=None, status=200,
                           initial_location="", initial_custom_location=""):
    if _wants_chapter_json():
        payload = {
            "success": error is None,
            "book_uuid": book_uuid,
            "candidates": candidates,
        }
        if error:
            payload["error"] = error
        else:
            payload["name"] = name
            payload["location"] = selected_location or custom_location
        return make_response(jsonify(payload), status)

    return make_response(render_template(
        "kobo_chapter_form.html",
        book_uuid=book_uuid,
        candidates=candidates,
        name=name or "",
        selected_location=selected_location or "",
        custom_location=custom_location or "",
        book_title=book_title,
        error=error,
        form_action=_chapter_form_url(book_uuid),
        initial_location=initial_location or "",
        initial_custom_location=initial_custom_location or "",
    ), status)


def _chapter_result_response(book_uuid, ok, message, status=200, **extra):
    if _wants_chapter_json():
        payload = {"success": ok, "message": message, "book_uuid": book_uuid}
        payload.update(extra)
        return make_response(jsonify(payload), status)

    return make_response(render_template(
        "kobo_chapter_result.html",
        ok=ok,
        message=message,
        back_url=_chapter_form_url(book_uuid),
        **extra
    ), status)


def _chapter_target_exists(structure, target):
    target_path = epub_chapters.archive_target_path(structure.opf_dir, target)
    return any(item["path"] == target_path for item in structure.candidates)


def _chapter_check_response(book_uuid, served, book_title="", error=None):
    """Minimal JSON response used by the device launcher as an allow-list."""
    payload = {"book_uuid": book_uuid, "served": bool(served)}
    if book_title:
        payload["title"] = book_title
    if error:
        payload["error"] = error
    return make_response(jsonify(payload), 200)


@csrf.exempt
@kobo.route("/chapters/<book_uuid>", methods=["GET", "POST"])
@requires_kobo_auth
@download_required
def HandleChapterForm(book_uuid):
    """Show and process the Kobo chapter-mark form for one Calibre book."""
    if not (current_user.role_edit() or current_user.role_admin()):
        abort(403)

    check_only = request.method == "GET" and request.args.get("check") == "1"
    device_title = request.args.get("name", "").strip()

    book = calibre_db.get_book_by_uuid(book_uuid)
    if not book:
        log.info("Kobo chapter form: book %s not found in this library", book_uuid)
        if check_only:
            return _chapter_check_response(
                book_uuid, False, device_title, "Book not found in this library.")
        message = "Book not found in this library."
        if device_title:
            message = "'{}' is not in this calibre-web library. Sync it from calibre-web first.".format(
                device_title)
        return _chapter_result_response(book_uuid, False, message, 404)

    book_formats = [
        data for data in book.data if data.format in ("EPUB", "KEPUB")]
    if check_only:
        if book_formats:
            return _chapter_check_response(
                book_uuid, True, getattr(book, "title", "") or device_title)
        return _chapter_check_response(
            book_uuid, False, getattr(book, "title", "") or device_title,
            "This book has no EPUB or KEPUB format.")
    if not book_formats:
        return _chapter_result_response(
            book_uuid, False, "This book has no EPUB or KEPUB format.", 400)

    format_entries = []
    for book_format in book_formats:
        path = _book_format_path(book, book_format)
        try:
            structure = epub_chapters.read_epub_structure(path)
        except Exception as exc:
            log.error("Kobo chapter form: cannot inspect %s: %s", path, exc)
            return _chapter_result_response(
                book_uuid, False, "The book file could not be read.", 400)
        format_entries.append({
            "data": book_format,
            "format": book_format.format,
            "path": path,
            "structure": structure,
        })

    reference = next(
        (entry for entry in format_entries if entry["format"] == "KEPUB"),
        format_entries[0])
    structure = reference["structure"]
    candidates = [{
        "value": candidate["href"],
        "label": candidate["label"],
    } for candidate in structure.candidates]

    if request.method == "GET":
        name = request.args.get("name", "").strip()
        raw_location = request.args.get("location", "").strip()
        selected_location = ""
        custom_location = ""
        if raw_location:
            try:
                target = epub_chapters.normalize_location(
                    raw_location, structure.opf_dir)
                custom_location = epub_chapters.target_to_string(target)
                if _chapter_target_exists(structure, target):
                    selected_location = target.href
            except epub_chapters.ChapterLocationError:
                custom_location = raw_location[:1000]
        return _chapter_form_response(
            book_uuid, candidates, name, selected_location, custom_location,
            book_title=book.title,
            initial_location=selected_location,
            initial_custom_location=custom_location)

    name = request.form.get("name", "").strip()
    selected_location = request.form.get("location", "").strip()
    custom_location = request.form.get("location_custom", "").strip()
    initial_location = request.form.get("initial_location", "").strip()
    initial_custom_location = request.form.get("initial_location_custom", "").strip()
    if not name or len(name) > 200:
        return _chapter_form_response(
            book_uuid, candidates, name[:200], "", "",
            book_title=book.title,
            error="Enter a chapter name (200 characters or fewer).",
            status=400,
            initial_location=initial_location,
            initial_custom_location=initial_custom_location)

    # A prefilled free-text value must not beat a dropdown selection the user
    # actually changed. Only an edited free-text value is an override.
    if custom_location and custom_location != initial_custom_location:
        raw_location = custom_location
    elif selected_location and selected_location != initial_location:
        raw_location = selected_location
    else:
        raw_location = custom_location or selected_location
    if not raw_location or len(raw_location) > 1000:
        return _chapter_form_response(
            book_uuid, candidates, name, "", raw_location[:1000],
            book_title=book.title,
            error="Choose or enter a chapter location.",
            status=400,
            initial_location=initial_location,
            initial_custom_location=initial_custom_location)

    try:
        target = epub_chapters.normalize_location(
            raw_location, structure.opf_dir)
    except epub_chapters.ChapterLocationError:
        return _chapter_form_response(
            book_uuid, candidates, name, "", raw_location[:1000],
            book_title=book.title,
            error="That location is not a valid relative chapter path.",
            status=400,
            initial_location=initial_location,
            initial_custom_location=initial_custom_location)

    for entry in format_entries:
        if not _chapter_target_exists(entry["structure"], target):
            error = "That location is not a chapter in this book."
            if len(format_entries) > 1:
                error = "That location is not a chapter in the {} format.".format(
                    entry["format"])
            return _chapter_form_response(
                book_uuid, candidates, name, "", raw_location[:1000],
                book_title=book.title, error=error, status=400,
                initial_location=initial_location,
                initial_custom_location=initial_custom_location)

    rewritten = []
    try:
        with _restorable_files([entry["path"] for entry in format_entries]):
            for entry in format_entries:
                members = epub_chapters.rewrite_epub_toc(
                    entry["path"], name, target, structure=entry["structure"])
                entry["data"].uncompressed_size = os.path.getsize(entry["path"])
                rewritten.append({
                    "format": entry["format"],
                    "members": members,
                })

            try:
                # Register Calibre's SQLite helpers before flushing the book
                # update; otherwise a fresh process can fail with
                # "no such function: title_sort".
                calibre_db.create_functions(config)
                book.last_modified = datetime.now(timezone.utc)
                calibre_db.set_metadata_dirty(book.id)
                calibre_db.session.commit()
            except (IntegrityError, OperationalError, StaleDataError) as exc:
                calibre_db.session.rollback()
                raise ChapterDatabaseError(str(exc)) from exc
    except ChapterDatabaseError as exc:
        log.error_or_exception("Database error while saving chapter: {}".format(exc))
        return _chapter_result_response(
            book_uuid, False,
            "The book file changed but the database could not be updated.", 500)
    except Exception as exc:
        log.error("Kobo chapter save failed for %s: %s", book_uuid, exc)
        return _chapter_result_response(
            book_uuid, False, "The book could not be updated.", 500)

    location = epub_chapters.target_to_string(target)
    log.info(
        "Kobo chapter %r added to book %s at %s", name, book_uuid, location)
    return _chapter_result_response(
        book_uuid,
        True,
        "Chapter mark saved. The book will update on your next device sync.",
        name=name,
        location=location,
        formats=[item["format"] for item in rewritten],
        rewritten=rewritten,
    )


def get_book_format_binary_version(book, book_data):
    try:
        st = os.stat(_book_format_path(book, book_data))
        return "{}-{}".format(st.st_mtime_ns, st.st_size)
    except Exception:
        return str(book_data.uncompressed_size or 0)


def _naive_utc(timestamp):
    if timestamp is None or timestamp.tzinfo is None:
        return timestamp
    return timestamp.astimezone(timezone.utc).replace(tzinfo=None)


def get_book_binary_change_time(book):
    """Return when a Kobo-readable book file last changed, or None if unknown."""
    formats = [data for data in (getattr(book, "data", None) or [])
               if data.format in KOBO_FORMATS]
    candidates = [data for data in formats if data.format == "KEPUB"] or formats
    latest = None
    for book_data in candidates:
        try:
            st = os.stat(_book_format_path(book, book_data))
        except (OSError, TypeError, AttributeError):
            # No reliable file evidence: the caller must fail closed.
            return None
        changed = datetime.fromtimestamp(st.st_mtime, timezone.utc).replace(tzinfo=None)
        latest = changed if latest is None else max(latest, changed)
    return latest


def book_binary_changed_since(book, sync_timestamp):
    binary_change = get_book_binary_change_time(book)
    if binary_change is None:
        return True
    return binary_change > _naive_utc(sync_timestamp)


def book_binary_changed_with_edit(book):
    """Did the edit that stamped ``last_modified`` also rewrite the book file?"""
    binary_change = get_book_binary_change_time(book)
    effective_last_modified = _naive_utc(book.last_modified or book.timestamp)
    if binary_change is None or effective_last_modified is None:
        return True
    # File writes and the last_modified stamp happen in the same transaction,
    # so the file mtime can be a moment before the database timestamp.
    return binary_change >= effective_last_modified - timedelta(seconds=5)


def get_cover_image_id(book):
    book_uuid = str(book.uuid)
    if getattr(config, "config_use_google_drive", False):
        return book_uuid
    try:
        cover_path = os.path.join(config.get_book_path(), book.path, "cover.jpg")
        st = os.stat(cover_path)
        return "{}__cover{}-{}".format(book_uuid, st.st_mtime_ns, st.st_size)
    except (OSError, TypeError, AttributeError):
        return book_uuid


def normalize_cover_image_id(cover_uuid):
    """Strip the cache-busting suffix before looking up a local cover."""
    if "__cover" not in cover_uuid:
        return cover_uuid
    return cover_uuid.rsplit("__cover", 1)[0]


def _metadata_for_changed_product(metadata):
    """Build the payload nickel expects for a ChangedProductMetadata command."""
    changed = dict(metadata)
    roles = changed.get("ContributorRoles")
    if roles:
        changed["ContributorRoles"] = [
            dict(role, Role="Author") if isinstance(role, dict) else role
            for role in roles
        ]
    return changed


def get_download_url_for_book(book_id, book_format, version=None):
    if version is not None:
        try:
            if hasattr(version, "timestamp"):
                version = int(version.replace(tzinfo=timezone.utc).timestamp()) if version.tzinfo is None else int(version.timestamp())
            else:
                version = str(version)
        except Exception:
            version = None
    if not current_app.wsgi_app.is_proxied:
        if ':' in request.host and not request.host.endswith(']'):
            host = "".join(request.host.split(':')[:-1])
        else:
            host = request.host

        url = "{url_scheme}://{url_base}:{url_port}/kobo/{auth_token}/download/{book_id}/{book_format}".format(
            url_scheme=request.scheme,
            url_base=host,
            url_port=config.config_external_port,
            auth_token=get_auth_token(),
            book_id=book_id,
            book_format=book_format.lower()
        )
        return "{}?v={}".format(url, version) if version is not None else url
    params = dict(
        auth_token=get_auth_token(),
        book_id=book_id,
        book_format=book_format.lower(),
        _external=True,
    )
    if version is not None:
        params["v"] = version
    return url_for("kobo.download_book", **params)


def create_book_entitlement(book, archived):
    book_uuid = str(book.uuid)
    last_modified = book.last_modified or book.timestamp
    created = convert_to_kobo_timestamp_string(book.timestamp)
    return {
        "Accessibility": "Full",
        # Kobo treats a new ActivePeriod as a newly activated entitlement,
        # which clears the downloaded copy. Keep it tied to the book's
        # creation time so updates stay under the same entitlement.
        "ActivePeriod": {"From": created},
        "Created": created,
        "CrossRevisionId": book_uuid,
        "Id": book_uuid,
        "IsRemoved": archived,
        "IsHiddenFromArchive": False,
        "IsLocked": False,
        "LastModified": convert_to_kobo_timestamp_string(last_modified),
        "OriginCategory": "Imported",
        "RevisionId": book_uuid,
        "Status": "Active",
    }


def _binary_update_entitlements(entitlement):
    """Build sync items that re-download a rewritten book under the same id.

    A bare ``ChangedEntitlement`` clears the Kobo's downloaded copy but does
    not fetch the new file. Kobo does auto-download when a ``NewEntitlement``
    arrives with a changed ``Accessibility``. The Preview entitlement clears
    the old file, and the immediately following Full entitlement makes the
    device fetch the rewritten file without adding a second book.
    """
    preview = deepcopy(entitlement)
    preview_entitlement = preview.setdefault("BookEntitlement", {})
    preview_entitlement["Accessibility"] = "Preview"
    preview_entitlement["IsRemoved"] = False
    preview.pop("ReadingState", None)

    full = deepcopy(entitlement)
    full_entitlement = full.setdefault("BookEntitlement", {})
    full_entitlement["Accessibility"] = "Full"
    full_entitlement["IsRemoved"] = False

    return [
        {"ChangedEntitlement": preview},
        {"NewEntitlement": full},
    ]


def current_time():
    return strftime("%Y-%m-%dT%H:%M:%SZ", gmtime())


def get_description(book):
    if not book.comments:
        return None
    return book.comments[0].text


def get_author(book):
    if not book.authors:
        return {"Contributors": None}
    author_list = []
    autor_roles = []
    for author in book.authors:
        autor_roles.append({"Name": author.name})
        author_list.append(author.name)
    return {"ContributorRoles": autor_roles, "Contributors": author_list}


def get_publisher(book):
    if not book.publishers:
        return None
    return book.publishers[0].name


def get_series(book):
    if not book.series:
        return None
    return book.series[0].name


def get_seriesindex(book):
    return book.series_index if isinstance(book.series_index, float) else 1


def get_language(book):
    if not book.languages:
        return 'en'
    return isoLanguages.get(part3=book.languages[0].lang_code).part1


def get_metadata(book):
    download_urls = []
    kepub = [data for data in book.data if data.format == 'KEPUB']

    for book_data in kepub if len(kepub) > 0 else book.data:
        if book_data.format not in KOBO_FORMATS:
            continue
        for kobo_format in KOBO_FORMATS[book_data.format]:
            # log.debug('Id: %s, Format: %s' % (book.id, kobo_format))
            try:
                if get_epub_layout(book, book_data) == 'pre-paginated':
                    kobo_format = 'EPUB3FL'
                download_urls.append(
                    {
                        "Format": kobo_format,
                        "Size": book_data.uncompressed_size,
                        "Url": get_download_url_for_book(book.id, book_data.format,
                                                       get_book_format_binary_version(book, book_data)),
                        # The Kobo forma accepts platforms: (Generic, Android)
                        "Platform": "Generic",
                        # "DrmType": "None", # Not required
                    }
                )
            except (zipfile.BadZipfile, FileNotFoundError) as e:
                log.error(e)

    book_uuid = book.uuid
    metadata = {
        "Categories": ["00000000-0000-0000-0000-000000000001", ],
        # "Contributors": get_author(book),
        "CoverImageId": get_cover_image_id(book),
        "CrossRevisionId": book_uuid,
        "CurrentDisplayPrice": {"CurrencyCode": "USD", "TotalAmount": 0},
        "CurrentLoveDisplayPrice": {"TotalAmount": 0},
        "Description": get_description(book),
        "DownloadUrls": download_urls,
        "EntitlementId": book_uuid,
        "ExternalIds": [],
        "Genre": "00000000-0000-0000-0000-000000000001",
        "IsEligibleForKoboLove": False,
        "IsInternetArchive": False,
        "IsPreOrder": False,
        "IsSocialEnabled": True,
        "Language": get_language(book),
        "PhoneticPronunciations": {},
        "PublicationDate": convert_to_kobo_timestamp_string(book.pubdate),
        "Publisher": {"Imprint": "", "Name": get_publisher(book), },
        "RevisionId": book_uuid,
        "Title": book.title,
        "WorkId": book_uuid,
    }
    metadata.update(get_author(book))

    if get_series(book):
        name = get_series(book)
        try:
            metadata["Series"] = {
                "Name": get_series(book),
                "Number": get_seriesindex(book),        # ToDo Check int() ?
                "NumberFloat": float(get_seriesindex(book)),
                # Get a deterministic id based on the series name.
                "Id": str(uuid.uuid3(uuid.NAMESPACE_DNS, name)),
            }
        except Exception as e:
            print(e)
    return metadata



@csrf.exempt
@kobo.route("/v1/library/tags", methods=["POST", "DELETE"])
@requires_kobo_auth
# Creates a Shelf with the given items, and returns the shelf's uuid.
def HandleTagCreate():
    # catch delete requests, otherwise they are handled in the book delete handler
    if request.method == "DELETE":
        abort(405)
    name, items = None, None
    try:
        shelf_request = request.json
        name = shelf_request["Name"]
        items = shelf_request["Items"]
        if not name:
            raise TypeError
    except (KeyError, TypeError):
        log.debug("Received malformed v1/library/tags request.")
        abort(400, description="Malformed tags POST request. Data has empty 'Name', missing 'Name' or 'Items' field")

    shelf = ub.session.query(ub.Shelf).filter(ub.Shelf.name == name, ub.Shelf.user_id ==
                                              current_user.id).one_or_none()
    if shelf and not shelf_lib.check_shelf_edit_permissions(shelf):
        abort(401, description="User is unauthaurized to create shelf.")

    if not shelf:
        shelf = ub.Shelf(user_id=current_user.id, name=name, uuid=str(uuid.uuid4()))
        ub.session.add(shelf)

    items_unknown_to_calibre = add_items_to_shelf(items, shelf)
    if items_unknown_to_calibre:
        log.debug("Received request to add unknown books to a collection. Silently ignoring items.")
    ub.session_commit()
    return make_response(jsonify(str(shelf.uuid)), 201)


@csrf.exempt
@kobo.route("/v1/library/tags/<tag_id>", methods=["DELETE", "PUT"])
@requires_kobo_auth
def HandleTagUpdate(tag_id):
    shelf = ub.session.query(ub.Shelf).filter(ub.Shelf.uuid == tag_id,
                                              ub.Shelf.user_id == current_user.id).one_or_none()
    if not shelf:
        log.debug("Received Kobo tag update request on a collection unknown to CalibreWeb")
        if config.config_kobo_proxy:
            return redirect_or_proxy_request()
        else:
            abort(404, description="Collection isn't known to CalibreWeb")

    if request.method == "DELETE":
        if not shelf_lib.delete_shelf_helper(shelf):
            abort(401, description="Error deleting Shelf")
    else:
        name = None
        try:
            shelf_request = request.json
            name = shelf_request["Name"]
        except (KeyError, TypeError):
            log.debug("Received malformed v1/library/tags rename request.")
            abort(400, description="Malformed tags POST request. Data is missing 'Name' field")

        shelf.name = name
        ub.session.merge(shelf)
        ub.session_commit()
    return make_response(' ', 200)


# Adds items to the given shelf.
def add_items_to_shelf(items, shelf):
    book_ids_already_in_shelf = set([book_shelf.book_id for book_shelf in shelf.books])
    items_unknown_to_calibre = []
    for item in items:
        try:
            if item["Type"] != "ProductRevisionTagItem":
                items_unknown_to_calibre.append(item)
                continue

            book = calibre_db.get_book_by_uuid(item["RevisionId"])
            if not book:
                items_unknown_to_calibre.append(item)
                continue

            book_id = book.id
            if book_id not in book_ids_already_in_shelf:
                shelf.books.append(ub.BookShelf(book_id=book_id))
        except KeyError:
            items_unknown_to_calibre.append(item)
    return items_unknown_to_calibre


@csrf.exempt
@kobo.route("/v1/library/tags/<tag_id>/items", methods=["POST"])
@requires_kobo_auth
def HandleTagAddItem(tag_id):
    items = None
    try:
        tag_request = request.json
        items = tag_request["Items"]
    except (KeyError, TypeError):
        log.debug("Received malformed v1/library/tags/<tag_id>/items/delete request.")
        abort(400, description="Malformed tags POST request. Data is missing 'Items' field")

    shelf = ub.session.query(ub.Shelf).filter(ub.Shelf.uuid == tag_id,
                                              ub.Shelf.user_id == current_user.id).one_or_none()
    if not shelf:
        log.debug("Received Kobo request on a collection unknown to CalibreWeb")
        abort(404, description="Collection isn't known to CalibreWeb")

    if not shelf_lib.check_shelf_edit_permissions(shelf):
        abort(401, description="User is unauthaurized to edit shelf.")

    items_unknown_to_calibre = add_items_to_shelf(items, shelf)
    if items_unknown_to_calibre:
        log.debug("Received request to add an unknown book to a collection. Silently ignoring item.")

    ub.session.merge(shelf)
    ub.session_commit()
    return make_response('', 201)


@csrf.exempt
@kobo.route("/v1/library/tags/<tag_id>/items/delete", methods=["POST"])
@requires_kobo_auth
def HandleTagRemoveItem(tag_id):
    items = None
    try:
        tag_request = request.json
        items = tag_request["Items"]
    except (KeyError, TypeError):
        log.debug("Received malformed v1/library/tags/<tag_id>/items/delete request.")
        abort(400, description="Malformed tags POST request. Data is missing 'Items' field")

    shelf = ub.session.query(ub.Shelf).filter(ub.Shelf.uuid == tag_id,
                                              ub.Shelf.user_id == current_user.id).one_or_none()
    if not shelf:
        log.debug(
            "Received a request to remove an item from a Collection unknown to CalibreWeb.")
        abort(404, description="Collection isn't known to CalibreWeb")

    if not shelf_lib.check_shelf_edit_permissions(shelf):
        abort(401, description="User is unauthaurized to edit shelf.")

    items_unknown_to_calibre = []
    for item in items:
        try:
            if item["Type"] != "ProductRevisionTagItem":
                items_unknown_to_calibre.append(item)
                continue

            book = calibre_db.get_book_by_uuid(item["RevisionId"])
            if not book:
                items_unknown_to_calibre.append(item)
                continue

            shelf.books.filter(ub.BookShelf.book_id == book.id).delete()
        except KeyError:
            items_unknown_to_calibre.append(item)
    ub.session_commit()

    if items_unknown_to_calibre:
        log.debug("Received request to remove an unknown book to a collecition. Silently ignoring item.")

    return make_response('', 200)


# Add new, changed, or deleted shelves to the sync_results.
# Note: Public shelves that aren't owned by the user aren't supported.
def sync_shelves(sync_token, sync_results, only_kobo_shelves=False):
    new_tags_last_modified = sync_token.tags_last_modified
    # transmit all archived shelfs independent of last sync (why should this matter?)
    for shelf in ub.session.query(ub.ShelfArchive).filter(ub.ShelfArchive.user_id == current_user.id):
        new_tags_last_modified = max(shelf.last_modified, new_tags_last_modified)
        sync_results.append({
            "DeletedTag": {
                "Tag": {
                    "Id": shelf.uuid,
                    "LastModified": convert_to_kobo_timestamp_string(shelf.last_modified)
                }
            }
        })
        ub.session.delete(shelf)
        ub.session_commit()

    extra_filters = []
    if only_kobo_shelves:
        for shelf in ub.session.query(ub.Shelf).filter(
            func.datetime(ub.Shelf.last_modified) > sync_token.tags_last_modified,
            ub.Shelf.user_id == current_user.id,
            not ub.Shelf.kobo_sync
        ):
            sync_results.append({
                "DeletedTag": {
                    "Tag": {
                        "Id": shelf.uuid,
                        "LastModified": convert_to_kobo_timestamp_string(shelf.last_modified)
                    }
                }
            })
        extra_filters.append(ub.Shelf.kobo_sync)

    shelflist = ub.session.query(ub.Shelf).outerjoin(ub.BookShelf).filter(
        or_(func.datetime(ub.Shelf.last_modified) > sync_token.tags_last_modified,
            func.datetime(ub.BookShelf.date_added) > sync_token.tags_last_modified),
        ub.Shelf.user_id == current_user.id,
        *extra_filters
    ).distinct().order_by(func.datetime(ub.Shelf.last_modified).asc())

    for shelf in shelflist:
        if not shelf_lib.check_shelf_view_permissions(shelf):
            continue

        new_tags_last_modified = max(shelf.last_modified, new_tags_last_modified)

        tag = create_kobo_tag(shelf)
        if not tag:
            continue

        if shelf.created > sync_token.tags_last_modified:
            sync_results.append({
                "NewTag": tag
            })
        else:
            sync_results.append({
                "ChangedTag": tag
            })
    sync_token.tags_last_modified = new_tags_last_modified
    ub.session_commit()


# Creates a Kobo "Tag" object from an ub.Shelf object
def create_kobo_tag(shelf):
    tag = {
        "Created": convert_to_kobo_timestamp_string(shelf.created),
        "Id": shelf.uuid,
        "Items": [],
        "LastModified": convert_to_kobo_timestamp_string(shelf.last_modified),
        "Name": shelf.name,
        "Type": "UserTag"
    }
    for book_shelf in shelf.books:
        book = calibre_db.get_book(book_shelf.book_id)
        if not book:
            log.info("Book (id: %s) in BookShelf (id: %s) not found in book database",  book_shelf.book_id, shelf.id)
            continue
        tag["Items"].append(
            {
                "RevisionId": book.uuid,
                "Type": "ProductRevisionTagItem"
            }
        )
    return {"Tag": tag}


@csrf.exempt
@kobo.route("/v1/library/<book_uuid>/state", methods=["GET", "PUT"])
@requires_kobo_auth
def HandleStateRequest(book_uuid):
    book = calibre_db.get_book_by_uuid(book_uuid)
    if not book or not book.data:
        log.info("Book %s not found in database", book_uuid)
        return redirect_or_proxy_request()

    kobo_reading_state = get_or_create_reading_state(book.id)

    if request.method == "GET":
        return jsonify([get_kobo_reading_state_response(book, kobo_reading_state)])
    else:
        update_results_response = {"EntitlementId": book_uuid}

        try:
            request_data = request.json
            request_reading_state = request_data["ReadingStates"][0]

            request_bookmark = request_reading_state["CurrentBookmark"]
            if request_bookmark:
                current_bookmark = kobo_reading_state.current_bookmark
                current_bookmark.progress_percent = request_bookmark["ProgressPercent"]
                current_bookmark.content_source_progress_percent = request_bookmark["ContentSourceProgressPercent"]
                location = request_bookmark["Location"]
                if location:
                    current_bookmark.location_value = location["Value"]
                    current_bookmark.location_type = location["Type"]
                    current_bookmark.location_source = location["Source"]
                update_results_response["CurrentBookmarkResult"] = {"Result": "Success"}

            request_statistics = request_reading_state["Statistics"]
            if request_statistics:
                statistics = kobo_reading_state.statistics
                statistics.spent_reading_minutes = int(request_statistics["SpentReadingMinutes"])
                statistics.remaining_time_minutes = int(request_statistics["RemainingTimeMinutes"])
                update_results_response["StatisticsResult"] = {"Result": "Success"}

            request_status_info = request_reading_state["StatusInfo"]
            if request_status_info:
                book_read = kobo_reading_state.book_read_link
                new_book_read_status = get_ub_read_status(request_status_info["Status"])
                if new_book_read_status == ub.ReadBook.STATUS_IN_PROGRESS \
                        and new_book_read_status != book_read.read_status:
                    book_read.times_started_reading += 1
                    book_read.last_time_started_reading = datetime.now(timezone.utc)
                book_read.read_status = new_book_read_status
                update_results_response["StatusInfoResult"] = {"Result": "Success"}
        except (KeyError, TypeError, ValueError, StatementError):
            log.debug("Received malformed v1/library/<book_uuid>/state request.")
            ub.session.rollback()
            abort(400, description="Malformed request data is missing 'ReadingStates' key")

        ub.session.merge(kobo_reading_state)
        ub.session_commit()
        update_results_response["LastModified"] = convert_to_kobo_timestamp_string(kobo_reading_state.last_modified)
        update_results_response["PriorityTimestamp"] = convert_to_kobo_timestamp_string(kobo_reading_state.priority_timestamp)
        return jsonify({
            "RequestResult": "Success",
            "UpdateResults": [update_results_response],
        })


def get_read_status_for_kobo(ub_book_read):
    enum_to_string_map = {
        None: "ReadyToRead",
        ub.ReadBook.STATUS_UNREAD: "ReadyToRead",
        ub.ReadBook.STATUS_FINISHED: "Finished",
        ub.ReadBook.STATUS_IN_PROGRESS: "Reading",
    }
    return enum_to_string_map[ub_book_read.read_status]


def get_ub_read_status(kobo_read_status):
    string_to_enum_map = {
        None: None,
        "ReadyToRead": ub.ReadBook.STATUS_UNREAD,
        "Finished": ub.ReadBook.STATUS_FINISHED,
        "Reading": ub.ReadBook.STATUS_IN_PROGRESS,
    }
    return string_to_enum_map[kobo_read_status]


def get_or_create_reading_state(book_id):
    book_read = ub.session.query(ub.ReadBook).filter(ub.ReadBook.book_id == book_id,
                                                     ub.ReadBook.user_id == int(current_user.id)).one_or_none()
    if not book_read:
        book_read = ub.ReadBook(user_id=current_user.id, book_id=book_id)
    if not book_read.kobo_reading_state:
        kobo_reading_state = ub.KoboReadingState(user_id=book_read.user_id, book_id=book_id)
        kobo_reading_state.current_bookmark = ub.KoboBookmark()
        kobo_reading_state.statistics = ub.KoboStatistics()
        book_read.kobo_reading_state = kobo_reading_state
    ub.session.add(book_read)
    ub.session_commit()
    return book_read.kobo_reading_state


def get_kobo_reading_state_response(book, kobo_reading_state):
    return {
        "EntitlementId": book.uuid,
        "Created": convert_to_kobo_timestamp_string(book.timestamp),
        "LastModified": convert_to_kobo_timestamp_string(kobo_reading_state.last_modified),
        # AFAICT PriorityTimestamp is always equal to LastModified.
        "PriorityTimestamp": convert_to_kobo_timestamp_string(kobo_reading_state.priority_timestamp),
        "StatusInfo": get_status_info_response(kobo_reading_state.book_read_link),
        "Statistics": get_statistics_response(kobo_reading_state.statistics),
        "CurrentBookmark": get_current_bookmark_response(kobo_reading_state.current_bookmark),
    }


def get_status_info_response(book_read):
    resp = {
        "LastModified": convert_to_kobo_timestamp_string(book_read.last_modified),
        "Status": get_read_status_for_kobo(book_read),
        "TimesStartedReading": book_read.times_started_reading,
    }
    if book_read.last_time_started_reading:
        resp["LastTimeStartedReading"] = convert_to_kobo_timestamp_string(book_read.last_time_started_reading)
    return resp


def get_statistics_response(statistics):
    resp = {
        "LastModified": convert_to_kobo_timestamp_string(statistics.last_modified),
    }
    if statistics.spent_reading_minutes:
        resp["SpentReadingMinutes"] = statistics.spent_reading_minutes
    if statistics.remaining_time_minutes:
        resp["RemainingTimeMinutes"] = statistics.remaining_time_minutes
    return resp


def _clean_progress(value):
    """Return progress as int if it's a whole number, preserving Kobo device expectations."""
    if value is not None and value == int(value):
        return int(value)
    return value


def get_current_bookmark_response(current_bookmark):
    resp = {
        "LastModified": convert_to_kobo_timestamp_string(current_bookmark.last_modified),
    }
    if current_bookmark.progress_percent is not None:
        resp["ProgressPercent"] = _clean_progress(current_bookmark.progress_percent)
    if current_bookmark.content_source_progress_percent is not None:
        resp["ContentSourceProgressPercent"] = _clean_progress(current_bookmark.content_source_progress_percent)
    if current_bookmark.location_value:
        resp["Location"] = {
            "Value": current_bookmark.location_value,
            "Type": current_bookmark.location_type,
            "Source": current_bookmark.location_source,
        }
    return resp


@kobo.route("/<book_uuid>/<width>/<height>/<isGreyscale>/image.jpg", defaults={'Quality': ""})
@kobo.route("/<book_uuid>/<width>/<height>/<Quality>/<isGreyscale>/image.jpg")
@requires_kobo_auth
def HandleCoverImageRequest(book_uuid, width, height, Quality, isGreyscale):
    try:
        if int(height) > 1000:
            resolution = COVER_THUMBNAIL_LARGE
        elif int(height) > 500:
            resolution = COVER_THUMBNAIL_MEDIUM
        else:
            resolution = COVER_THUMBNAIL_SMALL
    except ValueError:
        log.error("Requested height %s of book %s is invalid" % (book_uuid, height))
        resolution = COVER_THUMBNAIL_SMALL
    book_cover = helper.get_book_cover_with_uuid(
        normalize_cover_image_id(book_uuid), resolution=resolution
    )
    if book_cover:
        log.debug("Serving local cover image of book %s" % book_uuid)
        return book_cover

    if not config.config_kobo_proxy:
        log.debug("Returning 404 for cover image of unknown book %s" % book_uuid)
        # additional proxy request make no sense, -> direct return
        return abort(404)

    log.debug("Redirecting request for cover image of unknown book %s to Kobo" % book_uuid)
    return redirect(KOBO_IMAGEHOST_URL +
                    "/{book_uuid}/{width}/{height}/false/image.jpg".format(book_uuid=book_uuid,
                                                                           width=width,
                                                                           height=height), 307)


@kobo.route("")
def TopLevelEndpoint():
    return make_response(jsonify({}))


@csrf.exempt
@kobo.route("/v1/library/<book_uuid>", methods=["DELETE"])
@requires_kobo_auth
def HandleBookDeletionRequest(book_uuid):
    log.info("Kobo book delete request received for book %s" % book_uuid)
    book = calibre_db.get_book_by_uuid(book_uuid)
    if not book:
        log.info("Book %s not found in database", book_uuid)
        return redirect_or_proxy_request()

    book_id = book.id
    is_archived = kobo_sync_status.change_archived_books(book_id, True)
    if is_archived:
        kobo_sync_status.remove_synced_book(book_id)
    return "", 204


# TODO: Implement the following routes
@csrf.exempt
@kobo.route("/v1/library/<dummy>", methods=["DELETE", "GET", "POST"])
@kobo.route("/v1/library/<dummy>/preview", methods=["POST"])
def HandleUnimplementedRequest(dummy=None):
    [limiter.limiter.clear(limit.limit, *limit.request_args) for limit in limiter.current_limits]
    log.debug("Unimplemented Library Request received: %s (request is forwarded to kobo if configured)",
              request.base_url)
    return redirect_or_proxy_request()


# TODO: Implement the following routes
@csrf.exempt
@kobo.route("/v1/user/loyalty/<dummy>", methods=["GET", "POST"])
@kobo.route("/v1/user/profile", methods=["GET", "POST"])
@kobo.route("/v1/user/wishlist", methods=["GET", "POST"])
@kobo.route("/v1/user/recommendations", methods=["GET", "POST"])
@kobo.route("/v1/analytics/<dummy>", methods=["GET", "POST"])
@kobo.route("/v1/assets", methods=["GET"])
def HandleUserRequest(dummy=None):
    [limiter.limiter.clear(limit.limit, *limit.request_args) for limit in limiter.current_limits]
    log.debug("Unimplemented User Request received: %s (request is forwarded to kobo if configured)", request.base_url)
    return redirect_or_proxy_request()


@csrf.exempt
@kobo.route("/v1/user/loyalty/benefits", methods=["GET"])
def handle_benefits():
    [limiter.limiter.clear(limit.limit, *limit.request_args) for limit in limiter.current_limits]
    if config.config_kobo_proxy:
        return redirect_or_proxy_request()
    else:
        return make_response(jsonify({"Benefits": {}}))


@csrf.exempt
@kobo.route("/v1/analytics/gettests", methods=["GET", "POST"])
def handle_getests():
    [limiter.limiter.clear(limit.limit, *limit.request_args) for limit in limiter.current_limits]
    if config.config_kobo_proxy:
        return redirect_or_proxy_request()
    else:
        testkey = request.headers.get("X-Kobo-userkey", "")
        return make_response(jsonify({"Result": "Success", "TestKey": testkey, "Tests": {}}))


@csrf.exempt
@kobo.route("/v1/products/<dummy>/prices", methods=["GET", "POST"])
@kobo.route("/v1/products/<dummy>/recommendations", methods=["GET", "POST"])
@kobo.route("/v1/products/<dummy>/nextread", methods=["GET", "POST"])
@kobo.route("/v1/products/<dummy>/reviews", methods=["GET", "POST"])
@kobo.route("/v1/products/featured/<dummy>", methods=["GET", "POST"])
@kobo.route("/v1/products/featured/", methods=["GET", "POST"])
@kobo.route("/v1/products/books/external/<dummy>", methods=["GET", "POST"])
@kobo.route("/v1/products/books/series/<dummy>", methods=["GET", "POST"])
@kobo.route("/v1/products/books/<dummy>", methods=["GET", "POST"])
@kobo.route("/v1/products/books/<dummy>/", methods=["GET", "POST"])
@kobo.route("/v1/products/dailydeal", methods=["GET", "POST"])
@kobo.route("/v1/products/deals", methods=["GET", "POST"])
@kobo.route("/v1/products", methods=["GET", "POST"])
@kobo.route("/v1/products/<path:dummy>", methods=["GET", "POST"])
@kobo.route("/v1/products/<path:dummy>/", methods=["GET", "POST"])
@kobo.route("/v1/affiliate", methods=["GET", "POST"])
@kobo.route("/v1/deals", methods=["GET", "POST"])
def HandleProductsRequest(dummy=None):
    [limiter.limiter.clear(limit.limit, *limit.request_args) for limit in limiter.current_limits]
    log.debug("Unimplemented Products Request received: %s (request is forwarded to kobo if configured)",
              request.base_url)
    return redirect_or_proxy_request()


def make_calibre_web_auth_response():
    # As described in kobo_auth.py, CalibreWeb doesn't make use practical use of this auth/device API call for
    # authentation (nor for authorization). We return a dummy response just to keep the device happy.
    content = request.get_json()
    AccessToken = base64.b64encode(os.urandom(24)).decode('utf-8')
    RefreshToken = base64.b64encode(os.urandom(24)).decode('utf-8')
    return make_response(
        jsonify(
            {
                "AccessToken": AccessToken,
                "RefreshToken": RefreshToken,
                "TokenType": "Bearer",
                "TrackingId": str(uuid.uuid4()),
                "UserKey": content.get('UserKey', ""),
            }
        )
    )


@csrf.exempt
@kobo.route("/v1/auth/refresh", methods=["POST"])
@kobo.route("/v1/auth/device", methods=["POST"])
@requires_kobo_auth
def HandleAuthRequest():
    log.debug('Kobo Auth request')
    if config.config_kobo_proxy:
        try:
            return redirect_or_proxy_request(auth=True)
        except Exception:
            log.error("Failed to receive or parse response from Kobo's auth endpoint. Falling back to un-proxied mode.")
    return make_calibre_web_auth_response()


@kobo.route("/v1/initialization")
@requires_kobo_auth
def HandleInitRequest():
    log.info('Init')

    kobo_resources = None
    if config.config_kobo_proxy:
        try:
            store_response = make_request_to_kobo_store()
            store_response_json = store_response.json()
            if "Resources" in store_response_json:
                kobo_resources = store_response_json["Resources"]
            else:
                # HACK: This should maybe only happen when we get an "expired token" response?
                # Btw, the below code was copy-pasted from redirect_or_proxy_request()
                response_headers = store_response.headers
                for header_key in CONNECTION_SPECIFIC_HEADERS:
                    response_headers.pop(header_key, default=None)

                return make_response(
                    store_response.content, store_response.status_code, response_headers.items()
                )
        except Exception:
            log.error("Failed to receive or parse response from Kobo's init endpoint. Falling back to un-proxied mode.")
    if not kobo_resources:
        kobo_resources = NATIVE_KOBO_RESOURCES()

    if not current_app.wsgi_app.is_proxied:
        log.debug('Kobo: Received unproxied request, changed request port to external server port')
        if ':' in request.host and not request.host.endswith(']'):
            host = "".join(request.host.split(':')[:-1])
        else:
            host = request.host
        calibre_web_url = "{url_scheme}://{url_base}:{url_port}".format(
            url_scheme=request.scheme,
            url_base=host,
            url_port=config.config_external_port
        )
        log.debug('Kobo: Received unproxied request, changed request url to %s', calibre_web_url)
        kobo_resources["image_host"] = calibre_web_url
        kobo_resources["image_url_quality_template"] = unquote(calibre_web_url +
                                                               url_for("kobo.HandleCoverImageRequest",
                                                                       auth_token=get_auth_token(),
                                                                       book_uuid="{ImageId}",
                                                                       width="{width}",
                                                                       height="{height}",
                                                                       Quality='{Quality}',
                                                                       isGreyscale='isGreyscale'))
        kobo_resources["image_url_template"] = unquote(calibre_web_url +
                                                       url_for("kobo.HandleCoverImageRequest",
                                                               auth_token=get_auth_token(),
                                                               book_uuid="{ImageId}",
                                                               width="{width}",
                                                               height="{height}",
                                                               isGreyscale='false'))
        kobo_resources["library_sync"] = calibre_web_url + url_for("kobo.HandleSyncRequest",
                                                                    auth_token=kobo_auth.get_auth_token())
    else:
        kobo_resources["image_host"] = url_for("web.index", _external=True).strip("/")
        kobo_resources["image_url_quality_template"] = unquote(url_for("kobo.HandleCoverImageRequest",
                                                                       auth_token=get_auth_token(),
                                                                       book_uuid="{ImageId}",
                                                                       width="{width}",
                                                                       height="{height}",
                                                                       Quality='{Quality}',
                                                                       isGreyscale='isGreyscale',
                                                                       _external=True))
        kobo_resources["image_url_template"] = unquote(url_for("kobo.HandleCoverImageRequest",
                                                               auth_token=get_auth_token(),
                                                               book_uuid="{ImageId}",
                                                               width="{width}",
                                                               height="{height}",
                                                               isGreyscale='false',
                                                               _external=True))
        kobo_resources["library_sync"] = url_for("kobo.HandleSyncRequest",
                                                  auth_token=kobo_auth.get_auth_token(),
                                                  _external=True)

    response = make_response(jsonify({"Resources": kobo_resources}))
    response.headers["x-kobo-apitoken"] = "e30="

    return response


@kobo.route("/download/<book_id>/<book_format>")
@requires_kobo_auth
@download_required
def download_book(book_id, book_format):
    return get_download_link(book_id, book_format, "kobo")


def NATIVE_KOBO_RESOURCES():
    kobo_file = os.path.join(BASE_DIR, "kobo_resources.txt")
    try:
        if os.path.isfile(kobo_file):
            with open(kobo_file, "r") as f:
                lines = f.read()
            return json.loads(lines)
    except Exception as e:
        log.error(e)
    return {
        "account_page": "https://www.kobo.com/account/settings",
        "account_page_rakuten": "https://my.rakuten.co.jp/",
        "add_device": "https://storeapi.kobo.com/v1/user/add-device",
        "add_entitlement": "https://storeapi.kobo.com/v1/library/{RevisionIds}",
        "affiliaterequest": "https://storeapi.kobo.com/v1/affiliate",
        "assets": "https://storeapi.kobo.com/v1/assets",
        "audiobook": "https://storeapi.kobo.com/v1/products/audiobooks/{ProductId}",
        "audiobook_detail_page": "https://www.kobo.com/{region}/{language}/audiobook/{slug}",
        "audiobook_landing_page": "https://www.kobo.com/{region}/{language}/audiobooks",
        "audiobook_preview": "https://storeapi.kobo.com/v1/products/audiobooks/{Id}/preview",
        "audiobook_purchase_withcredit": "https://storeapi.kobo.com/v1/store/audiobook/{Id}",
        "audiobook_subscription_orange_deal_inclusion_url": "https://authorize.kobo.com/inclusion",
        "authorproduct_recommendations": "https://storeapi.kobo.com/v1/products/books/authors/recommendations",
        "autocomplete": "https://storeapi.kobo.com/v1/products/autocomplete",
        "blackstone_header": {
            "key": "x-amz-request-payer",
            "value": "requester"
        },
        "book": "https://storeapi.kobo.com/v1/products/books/{ProductId}",
        "book_detail_page": "https://www.kobo.com/{region}/{language}/ebook/{slug}",
        "book_detail_page_rakuten": "http://books.rakuten.co.jp/rk/{crossrevisionid}",
        "book_landing_page": "https://www.kobo.com/ebooks",
        "book_subscription": "https://storeapi.kobo.com/v1/products/books/subscriptions",
        "browse_history": "https://storeapi.kobo.com/v1/user/browsehistory",
        "categories": "https://storeapi.kobo.com/v1/categories",
        "categories_page": "https://www.kobo.com/ebooks/categories",
        "category": "https://storeapi.kobo.com/v1/categories/{CategoryId}",
        "category_featured_lists": "https://storeapi.kobo.com/v1/categories/{CategoryId}/featured",
        "category_products": "https://storeapi.kobo.com/v1/categories/{CategoryId}/products",
        "checkout_borrowed_book": "https://storeapi.kobo.com/v1/library/borrow",
        "client_authd_referral": "https://authorize.kobo.com/api/AuthenticatedReferral/client/v1/getLink",
        "configuration_data": "https://storeapi.kobo.com/v1/configuration",
        "content_access_book": "https://storeapi.kobo.com/v1/products/books/{ProductId}/access",
        "customer_care_live_chat": "https://v2.zopim.com/widget/livechat.html?key=Y6gwUmnu4OATxN3Tli4Av9bYN319BTdO",
        "daily_deal": "https://storeapi.kobo.com/v1/products/dailydeal",
        "deals": "https://storeapi.kobo.com/v1/deals",
        "delete_entitlement": "https://storeapi.kobo.com/v1/library/{Ids}",
        "delete_tag": "https://storeapi.kobo.com/v1/library/tags/{TagId}",
        "delete_tag_items": "https://storeapi.kobo.com/v1/library/tags/{TagId}/items/delete",
        "device_auth": "https://storeapi.kobo.com/v1/auth/device",
        "device_refresh": "https://storeapi.kobo.com/v1/auth/refresh",
        "dictionary_host": "https://ereaderfiles.kobo.com",
        "discovery_host": "https://discovery.kobobooks.com",
        "ereaderdevices": "https://storeapi.kobo.com/v2/products/EReaderDeviceFeeds",
        "eula_page": "https://www.kobo.com/termsofuse?style=onestore",
        "exchange_auth": "https://storeapi.kobo.com/v1/auth/exchange",
        "external_book": "https://storeapi.kobo.com/v1/products/books/external/{Ids}",
        "facebook_sso_page": "https://authorize.kobo.com/signin/provider/Facebook/login?returnUrl=http://kobo.com/",
        "featured_list": "https://storeapi.kobo.com/v1/products/featured/{FeaturedListId}",
        "featured_lists": "https://storeapi.kobo.com/v1/products/featured",
        "free_books_page": {
            "EN": "https://www.kobo.com/{region}/{language}/p/free-ebooks",
            "FR": "https://www.kobo.com/{region}/{language}/p/livres-gratuits",
            "IT": "https://www.kobo.com/{region}/{language}/p/libri-gratuiti",
            "NL": "https://www.kobo.com/{region}/{language}/List/bekijk-het-overzicht-van-gratis-ebooks/QpkkVWnUw8sxmgjSlCbJRg",
            "PT": "https://www.kobo.com/{region}/{language}/p/livros-gratis"
        },
        "fte_feedback": "https://storeapi.kobo.com/v1/products/ftefeedback",
        "funnel_metrics": "https://storeapi.kobo.com/v1/funnelmetrics",
        "get_download_keys": "https://storeapi.kobo.com/v1/library/downloadkeys",
        "get_download_link": "https://storeapi.kobo.com/v1/library/downloadlink",
        "get_tests_request": "https://storeapi.kobo.com/v1/analytics/gettests",
        "giftcard_epd_redeem_url": "https://www.kobo.com/{storefront}/{language}/redeem-ereader",
        "giftcard_redeem_url": "https://www.kobo.com/{storefront}/{language}/redeem",
        "gpb_flow_enabled": "False",
        "help_page": "http://www.kobo.com/help",
        "image_host": "//cdn.kobo.com/book-images/",
        "image_url_quality_template": "https://cdn.kobo.com/book-images/{ImageId}/{Width}/{Height}/{Quality}/{IsGreyscale}/image.jpg",
        "image_url_template": "https://cdn.kobo.com/book-images/{ImageId}/{Width}/{Height}/false/image.jpg",
        "instapaper_enabled": "True",
        "instapaper_env_url": "https://www.instapaper.com/api/kobo",
        "instapaper_link_account_start": "https://authorize.kobo.com/{region}/{language}/linkinstapaper",
        "kobo_audiobooks_credit_redemption": "False",
        "kobo_audiobooks_enabled": "True",
        "kobo_audiobooks_orange_deal_enabled": "False",
        "kobo_audiobooks_subscriptions_enabled": "False",
        "kobo_display_price": "True",
        "kobo_dropbox_link_account_enabled": "False",
        "kobo_google_tax": "False",
        "kobo_googledrive_link_account_enabled": "False",
        "kobo_nativeborrow_enabled": "False",
        "kobo_onedrive_link_account_enabled": "False",
        "kobo_onestorelibrary_enabled": "False",
        "kobo_privacyCentre_url": "https://www.kobo.com/privacy",
        "kobo_redeem_enabled": "True",
        "kobo_shelfie_enabled": "False",
        "kobo_subscriptions_enabled": "True",
        "kobo_superpoints_enabled": "True",
        "kobo_wishlist_enabled": "True",
        "library_book": "https://storeapi.kobo.com/v1/user/library/books/{LibraryItemId}",
        "library_items": "https://storeapi.kobo.com/v1/user/library",
        "library_metadata": "https://storeapi.kobo.com/v1/library/{Ids}/metadata",
        "library_prices": "https://storeapi.kobo.com/v1/user/library/previews/prices",
        "library_search": "https://storeapi.kobo.com/v1/library/search",
        "library_sync": "https://storeapi.kobo.com/v1/library/sync",
        "love_dashboard_page": "https://www.kobo.com/{region}/{language}/kobosuperpoints",
        "love_points_redemption_page": "https://www.kobo.com/{region}/{language}/KoboSuperPointsRedemption?productId={ProductId}",
        "magazine_landing_page": "https://www.kobo.com/emagazines",
        "more_sign_in_options": "https://authorize.kobo.com/signin?returnUrl=http://kobo.com/#allProviders",
        "notebooks": "https://storeapi.kobo.com/api/internal/notebooks",
        "notifications_registration_issue": "https://storeapi.kobo.com/v1/notifications/registration",
        "oauth_host": "https://oauth.kobo.com",
        "password_retrieval_page": "https://www.kobo.com/passwordretrieval.html",
        "personalizedrecommendations": "https://storeapi.kobo.com/v2/users/personalizedrecommendations",
        "pocket_link_account_start": "https://authorize.kobo.com/{region}/{language}/linkpocket",
        "post_analytics_event": "https://storeapi.kobo.com/v1/analytics/event",
        "ppx_purchasing_url": "https://purchasing.kobo.com",
        "privacy_page": "https://www.kobo.com/privacypolicy?style=onestore",
        "product_nextread": "https://storeapi.kobo.com/v1/products/{ProductIds}/nextread",
        "product_prices": "https://storeapi.kobo.com/v1/products/{ProductIds}/prices",
        "product_recommendations": "https://storeapi.kobo.com/v1/products/{ProductId}/recommendations",
        "product_reviews": "https://storeapi.kobo.com/v1/products/{ProductIds}/reviews",
        "products": "https://storeapi.kobo.com/v1/products",
        "productsv2": "https://storeapi.kobo.com/v2/products",
        "provider_external_sign_in_page": "https://authorize.kobo.com/ExternalSignIn/{providerName}?returnUrl=http://kobo.com/",
        "quickbuy_checkout": "https://storeapi.kobo.com/v1/store/quickbuy/{PurchaseId}/checkout",
        "quickbuy_create": "https://storeapi.kobo.com/v1/store/quickbuy/purchase",
        "rakuten_token_exchange": "https://storeapi.kobo.com/v1/auth/rakuten_token_exchange",
        "rating": "https://storeapi.kobo.com/v1/products/{ProductId}/rating/{Rating}",
        "reading_services_host": "https://readingservices.kobo.com",
        "reading_state": "https://storeapi.kobo.com/v1/library/{Ids}/state",
        "redeem_interstitial_page": "https://www.kobo.com",
        "registration_page": "https://authorize.kobo.com/signup?returnUrl=http://kobo.com/",
        "related_items": "https://storeapi.kobo.com/v1/products/{Id}/related",
        "remaining_book_series": "https://storeapi.kobo.com/v1/products/books/series/{SeriesId}",
        "rename_tag": "https://storeapi.kobo.com/v1/library/tags/{TagId}",
        "review": "https://storeapi.kobo.com/v1/products/reviews/{ReviewId}",
        "review_sentiment": "https://storeapi.kobo.com/v1/products/reviews/{ReviewId}/sentiment/{Sentiment}",
        "shelfie_recommendations": "https://storeapi.kobo.com/v1/user/recommendations/shelfie",
        "sign_in_page": "https://authorize.kobo.com/signin?returnUrl=http://kobo.com/",
        "social_authorization_host": "https://social.kobobooks.com:8443",
        "social_host": "https://social.kobobooks.com",
        "store_home": "www.kobo.com/{region}/{language}",
        "store_host": "www.kobo.com",
        "store_newreleases": "https://www.kobo.com/{region}/{language}/List/new-releases/961XUjtsU0qxkFItWOutGA",
        "store_search": "https://www.kobo.com/{region}/{language}/Search?Query={query}",
        "store_top50": "https://www.kobo.com/{region}/{language}/ebooks/Top",
        "subs_landing_page": "https://www.kobo.com/{region}/{language}/plus",
        "subs_management_page": "https://www.kobo.com/{region}/{language}/account/subscriptions",
        "subs_plans_page": "https://www.kobo.com/{region}/{language}/plus/plans",
        "subs_purchase_buy_templated": "https://www.kobo.com/{region}/{language}/Checkoutoption/{ProductId}/{TierId}",
        "tag_items": "https://storeapi.kobo.com/v1/library/tags/{TagId}/Items",
        "tags": "https://storeapi.kobo.com/v1/library/tags",
        "taste_profile": "https://storeapi.kobo.com/v1/products/tasteprofile",
        "terms_of_sale_page": "https://authorize.kobo.com/{region}/{language}/terms/termsofsale",
        "update_accessibility_to_preview": "https://storeapi.kobo.com/v1/library/{EntitlementIds}/preview",
        "use_one_store": "True",
        "user_loyalty_benefits": "https://storeapi.kobo.com/v1/user/loyalty/benefits",
        "user_platform": "https://storeapi.kobo.com/v1/user/platform",
        "user_profile": "https://storeapi.kobo.com/v1/user/profile",
        "user_ratings": "https://storeapi.kobo.com/v1/user/ratings",
        "user_recommendations": "https://storeapi.kobo.com/v1/user/recommendations",
        "user_reviews": "https://storeapi.kobo.com/v1/user/reviews",
        "user_wishlist": "https://storeapi.kobo.com/v1/user/wishlist",
        "userguide_host": "https://ereaderfiles.kobo.com",
        "wishlist_page": "https://www.kobo.com/{region}/{language}/account/wishlist"
    }
