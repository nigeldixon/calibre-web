# -*- coding: utf-8 -*-

#  This file is part of the Calibre-Web (https://github.com/janeczku/calibre-web)
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation; either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program. If not, see <http://www.gnu.org/licenses/>.

"""TOC-only chapter marks for EPUB and KEPUB files.

This module deliberately does not touch book content. It only appends a new
entry to the EPUB3 ``nav`` document and/or the EPUB2 ``toc.ncx``.
"""

import copy
import os
import posixpath
import re
import stat
import tempfile
import zipfile
from collections import namedtuple
from urllib.parse import unquote, urlsplit

from lxml import etree

from .epub_helper import OPF_NAMESPACE, _safe_parser

CONTAINER_NAMESPACE = 'urn:oasis:names:tc:opendocument:xmlns:container'
XHTML_NAMESPACE = 'http://www.w3.org/1999/xhtml'
EPUB_NAMESPACE = 'http://www.idpf.org/2007/ops'
NCX_NAMESPACE = 'http://www.daisy.org/z3986/2005/ncx/'

OPF_NSMAP = {'pkg': OPF_NAMESPACE}
XHTML_NSMAP = {'x': XHTML_NAMESPACE, 'epub': EPUB_NAMESPACE}
NCX_NSMAP = {'ncx': NCX_NAMESPACE}

XHTML_MEDIA_TYPES = frozenset((
    'application/xhtml+xml',
    'text/xhtml',
    'text/html',
))
NCX_MEDIA_TYPE = 'application/x-dtbncx+xml'

_SCHEME_RE = re.compile(r'^[A-Za-z][A-Za-z0-9+.-]*:')
_WS_RE = re.compile(r'\s+')

ChapterTarget = namedtuple('ChapterTarget', ['href', 'fragment'])


class ChapterLocationError(ValueError):
    """The supplied chapter location cannot be used safely."""


class ChapterTocError(RuntimeError):
    """The book does not contain a usable table of contents."""


def _clean_path(raw_path, opf_dir=None):
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ChapterLocationError("No location supplied")

    path = unquote(raw_path.strip()).replace('\\', '/')
    if not path or path.startswith('/') or _SCHEME_RE.match(path):
        raise ChapterLocationError("Location must be a relative EPUB path")

    parts = []
    for part in posixpath.normpath(path).split('/'):
        if part in ('', '.'):
            continue
        if part == '..':
            raise ChapterLocationError("Location may not leave the book root")
        parts.append(part)

    if opf_dir:
        opf_parts = [item for item in opf_dir.split('/') if item]
        if opf_parts and parts[:len(opf_parts)] == opf_parts:
            parts = parts[len(opf_parts):]

    if not parts:
        raise ChapterLocationError("Location does not name a chapter")
    return '/'.join(parts)


def normalize_location(raw_location, opf_dir=None):
    """Return a safe ``ChapterTarget`` for a Kobo/EPUB location string.

    Kobo adds its own bookmark fragments (for example ``#kobo.1.1`` or
    ``#point(...)``). Those are generated positions inside a chapter and are
    not useful as chapter targets, so they are discarded.
    """
    raw = raw_location if isinstance(raw_location, str) else ''
    path_part, _, fragment = raw.strip().partition('#')
    href = _clean_path(path_part, opf_dir)

    fragment = unquote(fragment.strip())
    if fragment.lower().startswith('kobo.') or fragment.lower().startswith('point('):
        fragment = ''
    elif fragment:
        fragment = ''.join(char for char in fragment if char not in '<>"\'')

    return ChapterTarget(href, fragment)


def target_to_string(target):
    if target.fragment:
        return '{}#{}'.format(target.href, target.fragment)
    return target.href


def _member_path(href, opf_dir):
    split = urlsplit(unquote(href or ''))
    return _clean_relative_member(split.path, opf_dir)


def _clean_relative_member(raw_path, opf_dir):
    path = unquote(raw_path.strip()).replace('\\', '/')
    if not path or path.startswith('/') or _SCHEME_RE.match(path):
        raise ChapterLocationError("Invalid manifest href")

    parts = []
    for part in posixpath.normpath(path).split('/'):
        if part in ('', '.'):
            continue
        if part == '..':
            raise ChapterLocationError("Manifest href leaves the book root")
        parts.append(part)

    if opf_dir:
        parts = [item for item in opf_dir.split('/') if item] + parts
    return '/'.join(parts)


def _text_content(element):
    if element is None:
        return ''
    return _WS_RE.sub(' ', ''.join(element.itertext())).strip()


def _chapter_label(epub_zip, member):
    try:
        tree = etree.fromstring(epub_zip.read(member), parser=_safe_parser)
    except (KeyError, etree.XMLSyntaxError, ValueError):
        return member.rsplit('/', 1)[-1]

    title = tree.xpath('//x:title', namespaces=XHTML_NSMAP)
    if not title:
        title = tree.xpath('//*[local-name()="title"]')
    if title:
        label = _text_content(title[0])
        if label:
            return label[:120]

    headings = tree.xpath(
        '//x:h1|//x:h2|//x:h3|//x:h4|//x:h5|//x:h6', namespaces=XHTML_NSMAP)
    if not headings:
        headings = tree.xpath(
            '//*[local-name()="h1" or local-name()="h2" or local-name()="h3" '
            'or local-name()="h4" or local-name()="h5" or local-name()="h6"]')
    if headings:
        label = _text_content(headings[0])
        if label:
            return label[:120]
    return member.rsplit('/', 1)[-1]


def _find_opf_path(epub_zip):
    container = etree.fromstring(
        epub_zip.read('META-INF/container.xml'), parser=_safe_parser)
    roots = container.xpath(
        '//n:rootfiles/n:rootfile/@full-path',
        namespaces={'n': CONTAINER_NAMESPACE})
    if not roots:
        raise ChapterTocError("EPUB container has no rootfile")
    opf_path = _clean_relative_member(roots[0], None)
    if opf_path not in epub_zip.namelist():
        raise ChapterTocError("EPUB rootfile is missing")
    return opf_path


EpubStructure = namedtuple('EpubStructure', [
    'opf_path', 'opf_dir', 'manifest', 'candidates', 'nav_path', 'ncx_path'])


def read_epub_structure(epub_path):
    """Read manifest metadata and TOC document paths from an EPUB/KEPUB."""
    with zipfile.ZipFile(epub_path) as epub_zip:
        names = set(epub_zip.namelist())
        opf_path = _find_opf_path(epub_zip)
        opf_dir = posixpath.dirname(opf_path)
        opf_tree = etree.fromstring(epub_zip.read(opf_path), parser=_safe_parser)

        manifest = {}
        for item in opf_tree.xpath('//pkg:manifest/pkg:item', namespaces=OPF_NSMAP):
            item_id = item.get('id')
            href = item.get('href')
            if not item_id or not href:
                continue
            try:
                member = _member_path(href, opf_dir)
            except ChapterLocationError:
                continue
            manifest[item_id] = {
                'href': unquote(href),
                'path': member,
                'media_type': (item.get('media-type') or '').strip(),
                'properties': (item.get('properties') or '').split(),
                'in_archive': member in names,
            }

        spine_ids = [
            ref.get('idref') for ref in
            opf_tree.xpath('//pkg:spine/pkg:itemref', namespaces=OPF_NSMAP)
            if ref.get('idref')
        ]

        nav_id = next((
            item_id for item_id, item in manifest.items()
            if 'nav' in item['properties']
            and item['media_type'] in XHTML_MEDIA_TYPES
            and item['in_archive']
        ), None)

        ncx_id = None
        spine = opf_tree.xpath('//pkg:spine', namespaces=OPF_NSMAP)
        if spine:
            ncx_id = spine[0].get('toc')
        if ncx_id not in manifest or manifest[ncx_id]['media_type'] != NCX_MEDIA_TYPE:
            ncx_id = next((
                item_id for item_id, item in manifest.items()
                if item['media_type'] == NCX_MEDIA_TYPE and item['in_archive']
            ), None)

        ordered_ids = spine_ids + [
            item_id for item_id in manifest if item_id not in spine_ids]
        candidates = []
        for item_id in ordered_ids:
            item = manifest.get(item_id)
            if not item or not item['in_archive']:
                continue
            if item['media_type'] not in XHTML_MEDIA_TYPES:
                continue
            if item_id == nav_id:
                continue
            candidates.append({
                'id': item_id,
                'href': item['href'],
                'path': item['path'],
                'label': _chapter_label(epub_zip, item['path']),
            })

        return EpubStructure(
            opf_path=opf_path,
            opf_dir=opf_dir,
            manifest=manifest,
            candidates=candidates,
            nav_path=manifest[nav_id]['path'] if nav_id else None,
            ncx_path=manifest[ncx_id]['path'] if ncx_id else None,
        )


def _localname(element):
    return etree.QName(element).localname


def _namespace(element):
    return etree.QName(element).namespace


def _relative_target(toc_member, target_path, fragment):
    toc_dir = posixpath.dirname(toc_member)
    href = posixpath.relpath(target_path, start=toc_dir or '.')
    if fragment:
        href = '{}#{}'.format(href, fragment)
    return href


def archive_target_path(opf_dir, target):
    if not opf_dir:
        return target.href
    return posixpath.normpath(posixpath.join(opf_dir, target.href))


def _serialize(tree):
    return etree.tostring(
        tree,
        xml_declaration=True,
        encoding='utf-8',
        doctype=tree.getroottree().docinfo.doctype or None,
    )


def add_nav_entry(data, target_href, name):
    """Append a chapter to an EPUB3 nav document, or return ``None``."""
    tree = etree.fromstring(data, parser=_safe_parser)
    navs = tree.xpath(
        '//x:nav[@epub:type="toc"]', namespaces=XHTML_NSMAP)
    if not navs:
        navs = [
            element for element in tree.iter()
            if _localname(element) == 'nav'
            and any(
                _localname(attr) == 'type'
                and value == 'toc'
                for attr, value in element.attrib.items()
            )
        ]
    if not navs:
        return None

    nav = navs[0]
    namespace = _namespace(nav) or XHTML_NAMESPACE
    ordered_lists = [
        child for child in nav if _localname(child) == 'ol']
    if ordered_lists:
        ordered_list = ordered_lists[0]
    else:
        ordered_list = etree.SubElement(
            nav, '{%s}ol' % namespace)

    list_item = etree.SubElement(
        ordered_list, '{%s}li' % namespace)
    anchor = etree.SubElement(
        list_item, '{%s}a' % namespace)
    anchor.set('href', target_href)
    anchor.text = name
    return _serialize(tree)


def add_ncx_entry(data, target_href, name):
    """Append a chapter to an EPUB2 NCX, or return ``None``."""
    tree = etree.fromstring(data, parser=_safe_parser)
    nav_maps = tree.xpath('//ncx:navMap', namespaces=NCX_NSMAP)
    if not nav_maps:
        nav_maps = [
            element for element in tree.iter()
            if _localname(element) == 'navMap']
    if not nav_maps:
        return None

    nav_map = nav_maps[0]
    namespace = _namespace(nav_map) or NCX_NAMESPACE

    play_orders = []
    for value in tree.xpath('//ncx:navPoint/@playOrder', namespaces=NCX_NSMAP):
        if value.isdigit():
            play_orders.append(int(value))
    next_play_order = max(play_orders, default=0) + 1

    used_ids = set(tree.xpath('//*/@id'))
    base_id = 'kobo-chapter'
    new_id = base_id
    suffix = 1
    while new_id in used_ids:
        new_id = '{}-{}'.format(base_id, suffix)
        suffix += 1

    nav_point = etree.SubElement(
        nav_map, '{%s}navPoint' % namespace)
    nav_point.set('id', new_id)
    nav_point.set('playOrder', str(next_play_order))

    nav_label = etree.SubElement(
        nav_point, '{%s}navLabel' % namespace)
    text = etree.SubElement(
        nav_label, '{%s}text' % namespace)
    text.text = name

    content = etree.SubElement(
        nav_point, '{%s}content' % namespace)
    content.set('src', target_href)
    return _serialize(tree)


def _rewrite_zip(epub_path, replacements):
    directory = os.path.dirname(os.path.abspath(epub_path)) or '.'
    handle, temp_path = tempfile.mkstemp(
        prefix='.kobo-chapter-', suffix='.epub', dir=directory)
    os.close(handle)

    try:
        with zipfile.ZipFile(epub_path, 'r') as source:
            with zipfile.ZipFile(temp_path, 'w') as destination:
                destination.comment = source.comment
                infos = source.infolist()

                mimetype_info = next(
                    (info for info in infos if info.filename == 'mimetype'), None)
                if mimetype_info:
                    info = copy.copy(mimetype_info)
                    info.compress_type = zipfile.ZIP_STORED
                    destination.writestr(
                        info, replacements.get('mimetype', source.read(mimetype_info)))

                for original_info in infos:
                    if original_info.filename == 'mimetype':
                        continue
                    info = copy.copy(original_info)
                    data = replacements.get(original_info.filename)
                    if data is None:
                        data = source.read(original_info)
                    destination.writestr(info, data)

        os.chmod(temp_path, stat.S_IMODE(os.stat(epub_path).st_mode))
        os.replace(temp_path, epub_path)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def rewrite_epub_toc(epub_path, name, target, structure=None):
    """Append ``name`` to every TOC representation found in ``epub_path``.

    ``structure`` can be supplied by a caller that has already inspected the
    file, so validation and rewriting use the same manifest view. Returns the
    list of ZIP members that were rewritten.
    """
    structure = structure if structure is not None else read_epub_structure(epub_path)
    replacements = {}
    target_path = archive_target_path(structure.opf_dir, target)

    if structure.nav_path:
        nav_data = add_nav_entry(
            _read_member(epub_path, structure.nav_path),
            _relative_target(structure.nav_path, target_path, target.fragment),
            name,
        )
        if nav_data is not None:
            replacements[structure.nav_path] = nav_data

    if structure.ncx_path:
        ncx_data = add_ncx_entry(
            _read_member(epub_path, structure.ncx_path),
            _relative_target(structure.ncx_path, target_path, target.fragment),
            name,
        )
        if ncx_data is not None:
            replacements[structure.ncx_path] = ncx_data

    if not replacements:
        raise ChapterTocError("No EPUB2 or EPUB3 table of contents found")

    _rewrite_zip(epub_path, replacements)
    return sorted(replacements)


def _read_member(epub_path, member):
    with zipfile.ZipFile(epub_path) as epub_zip:
        return epub_zip.read(member)
