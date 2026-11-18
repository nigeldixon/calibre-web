# -*- coding: utf-8 -*-

#  This file is part of the Calibre-Web (https://github.com/janeczku/calibre-web)
#    Copyright (C) 2026 OzzieIsaacs
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

import os
import re
import shutil
from datetime import datetime, timezone

HISTORY_DIRNAME = ".cover_history"
COVER_FILENAME = "cover.jpg"
_VERSION_RE = re.compile(r"^cover_\d{8}T\d{12}Z\.jpg$")


def history_dir(book_path):
    return os.path.join(book_path, HISTORY_DIRNAME)


def ensure_history_dir(book_path):
    path = history_dir(book_path)
    os.makedirs(path, exist_ok=True)
    return path


def is_safe_version_name(name):
    if not isinstance(name, str):
        return False
    if not _VERSION_RE.match(name):
        return False
    # extra hardening: no path components
    if os.path.basename(name) != name:
        return False
    if os.path.sep in name or (os.path.altsep and os.path.altsep in name):
        return False
    return True


def _generate_version_name():
    return "cover_{}.jpg".format(datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))


def snapshot_cover(book_path):
    cover_path = os.path.join(book_path, COVER_FILENAME)
    if not os.path.isfile(cover_path):
        return None
    hdir = ensure_history_dir(book_path)
    # ensure uniqueness
    for _ in range(5):
        name = _generate_version_name()
        dest = os.path.join(hdir, name)
        if not os.path.exists(dest):
            break
    else:
        # extremely unlikely; add extra entropy via pid/time counter
        name = "cover_{}_{}.jpg".format(
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"),
            os.getpid(),
        )
        dest = os.path.join(hdir, name)
    shutil.copy2(cover_path, dest)
    return name


def list_cover_versions(book_path):
    hdir = history_dir(book_path)
    if not os.path.isdir(hdir):
        return []
    versions = []
    for entry in os.scandir(hdir):
        if not entry.is_file():
            continue
        if not is_safe_version_name(entry.name):
            continue
        try:
            stat = entry.stat()
        except OSError:
            continue
        versions.append({
            "name": entry.name,
            "size": stat.st_size,
            "mtime": stat.st_mtime,
        })
    versions.sort(key=lambda v: v["name"], reverse=True)
    return versions


def delete_cover_version(book_path, name):
    if not is_safe_version_name(name):
        return False
    hdir = history_dir(book_path)
    target = os.path.join(hdir, name)
    # ensure resolved path stays within history dir
    try:
        hdir_real = os.path.realpath(hdir)
        target_real = os.path.realpath(target)
    except OSError:
        return False
    if os.path.commonpath([hdir_real, target_real]) != hdir_real:
        return False
    if not os.path.isfile(target_real):
        return False
    try:
        os.remove(target_real)
    except OSError:
        return False
    return True


def restore_cover_version(book_path, name):
    if not is_safe_version_name(name):
        return False
    hdir = history_dir(book_path)
    source = os.path.join(hdir, name)
    try:
        hdir_real = os.path.realpath(hdir)
        source_real = os.path.realpath(source)
    except OSError:
        return False
    if os.path.commonpath([hdir_real, source_real]) != hdir_real:
        return False
    if not os.path.isfile(source_real):
        return False
    cover_path = os.path.join(book_path, COVER_FILENAME)
    # preserve current before overwrite
    if os.path.isfile(cover_path):
        snapshot_cover(book_path)
    try:
        shutil.copy2(source_real, cover_path)
    except OSError:
        return False
    return True
