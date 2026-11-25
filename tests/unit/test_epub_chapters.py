"""Tests for TOC-only EPUB/KEPUB chapter insertion."""
import zipfile

import pytest
from lxml import etree

from cps import epub_chapters


CONTAINER = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

OPF = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="uid">test-book</dc:identifier>
    <dc:title>Test Book</dc:title>
  </metadata>
  <manifest>
    <item id="chap1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="chap1"/>
  </spine>
</package>
"""

NAV = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Contents</title></head>
  <body>
    <nav epub:type="toc">
      <ol><li><a href="chapter1.xhtml">Chapter 1</a></li></ol>
    </nav>
  </body>
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

CHAPTER = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>Opening Page</title></head>
  <body><h1>Opening Page</h1><p>Comic page.</p></body>
</html>
"""

NO_TOC_OPF = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="uid">test-book</dc:identifier>
  </metadata>
  <manifest>
    <item id="chap1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="chap1"/></spine>
</package>
"""

BAD_MANIFEST_OPF = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="uid">test-book</dc:identifier>
  </metadata>
  <manifest>
    <item id="chap1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
    <item id="no_href" media-type="application/xhtml+xml"/>
    <item id="outside" href="../secret.xhtml" media-type="application/xhtml+xml"/>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
  </manifest>
  <spine>
    <itemref idref="missing"/>
    <itemref idref="chap1"/>
  </spine>
</package>
"""


class _DictZip:
    """Minimal ZipFile stand-in for helpers that only need read/namelist."""

    def __init__(self, members):
        self.members = members

    def read(self, name):
        return self.members[name]

    def namelist(self):
        return sorted(self.members)


def _write_epub(path, *, opf=OPF, nav=NAV, ncx=NCX):
    with zipfile.ZipFile(path, "w") as epub:
        info = zipfile.ZipInfo("mimetype")
        info.compress_type = zipfile.ZIP_STORED
        epub.writestr(info, b"application/epub+zip")
        members = [
            ("META-INF/container.xml", CONTAINER),
            ("OEBPS/content.opf", opf),
        ]
        if nav is not None:
            members.append(("OEBPS/nav.xhtml", nav))
        if ncx is not None:
            members.append(("OEBPS/toc.ncx", ncx))
        members.append(("OEBPS/chapter1.xhtml", CHAPTER))
        for name, data in members:
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED
            epub.writestr(info, data.encode("utf-8"))


def _read(path, member):
    with zipfile.ZipFile(path) as epub:
        return epub.read(member).decode("utf-8")


def test_normalize_location_strips_opf_prefix_and_kobo_fragments():
    target = epub_chapters.normalize_location(
        "OEBPS/chapter1.xhtml#kobo.1.1", "OEBPS")
    assert target.href == "chapter1.xhtml"
    assert target.fragment == ""
    assert epub_chapters.target_to_string(target) == "chapter1.xhtml"


def test_normalize_location_keeps_real_fragments():
    target = epub_chapters.normalize_location(
        "chapter1.xhtml#page12", "OEBPS")
    assert target.href == "chapter1.xhtml"
    assert target.fragment == "page12"
    assert epub_chapters.target_to_string(target) == "chapter1.xhtml#page12"


@pytest.mark.parametrize("location", [
    "../secret.xhtml",
    "/OEBPS/chapter1.xhtml",
    "http://example.test/chapter1.xhtml",
    "#only-a-fragment",
])
def test_normalize_location_rejects_unsafe_paths(location):
    with pytest.raises(epub_chapters.ChapterLocationError):
        epub_chapters.normalize_location(location, "OEBPS")


def test_read_epub_structure_lists_spine_candidates(tmp_path):
    epub_path = tmp_path / "book.epub"
    _write_epub(epub_path)

    structure = epub_chapters.read_epub_structure(str(epub_path))

    assert structure.opf_path == "OEBPS/content.opf"
    assert structure.nav_path == "OEBPS/nav.xhtml"
    assert structure.ncx_path == "OEBPS/toc.ncx"
    assert [item["href"] for item in structure.candidates] == ["chapter1.xhtml"]
    assert structure.candidates[0]["path"] == "OEBPS/chapter1.xhtml"
    assert structure.candidates[0]["label"] == "Opening Page"


def test_rewrite_updates_both_tocs_and_preserves_zip_layout(tmp_path):
    epub_path = tmp_path / "book.epub"
    _write_epub(epub_path)
    target = epub_chapters.normalize_location("chapter1.xhtml#page3", "OEBPS")
    structure = epub_chapters.read_epub_structure(str(epub_path))

    rewritten = epub_chapters.rewrite_epub_toc(
        str(epub_path), "Comic page 3", target, structure=structure)

    assert rewritten == ["OEBPS/nav.xhtml", "OEBPS/toc.ncx"]

    with zipfile.ZipFile(epub_path) as epub:
        infos = epub.infolist()
        assert [info.filename for info in infos] == [
            "mimetype",
            "META-INF/container.xml",
            "OEBPS/content.opf",
            "OEBPS/nav.xhtml",
            "OEBPS/toc.ncx",
            "OEBPS/chapter1.xhtml",
        ]
        assert infos[0].compress_type == zipfile.ZIP_STORED
        assert all(info.compress_type == zipfile.ZIP_DEFLATED
                   for info in infos[1:])
        assert epub.read("mimetype") == b"application/epub+zip"

    nav = etree.fromstring(
        _read(epub_path, "OEBPS/nav.xhtml").encode("utf-8"),
        parser=epub_chapters._safe_parser)
    links = nav.xpath(
        '//x:nav[@epub:type="toc"]//x:a', namespaces=epub_chapters.XHTML_NSMAP)
    assert [link.get("href") for link in links] == [
        "chapter1.xhtml", "chapter1.xhtml#page3"]
    assert [link.text for link in links] == ["Chapter 1", "Comic page 3"]

    ncx = etree.fromstring(
        _read(epub_path, "OEBPS/toc.ncx").encode("utf-8"),
        parser=epub_chapters._safe_parser)
    points = ncx.xpath(
        '//ncx:navPoint', namespaces=epub_chapters.NCX_NSMAP)
    assert len(points) == 2
    assert points[1].get("playOrder") == "2"
    assert points[1].xpath(
        'ncx:navLabel/ncx:text/text()', namespaces=epub_chapters.NCX_NSMAP) == ["Comic page 3"]
    assert points[1].xpath(
        'ncx:content/@src', namespaces=epub_chapters.NCX_NSMAP) == ["chapter1.xhtml#page3"]


def test_rewrite_appends_duplicate_entries(tmp_path):
    epub_path = tmp_path / "book.epub"
    _write_epub(epub_path)
    target = epub_chapters.normalize_location("chapter1.xhtml", "OEBPS")

    epub_chapters.rewrite_epub_toc(str(epub_path), "Same mark", target)
    epub_chapters.rewrite_epub_toc(str(epub_path), "Same mark", target)

    ncx = etree.fromstring(
        _read(epub_path, "OEBPS/toc.ncx").encode("utf-8"),
        parser=epub_chapters._safe_parser)
    labels = ncx.xpath(
        '//ncx:navPoint/ncx:navLabel/ncx:text/text()',
        namespaces=epub_chapters.NCX_NSMAP)
    assert labels == ["Chapter 1", "Same mark", "Same mark"]


def test_normalize_location_rejects_dot_only_path():
    with pytest.raises(epub_chapters.ChapterLocationError,
                       match="does not name a chapter"):
        epub_chapters.normalize_location(".", "OEBPS")


def test_normalize_location_sanitizes_fragment_quotes():
    target = epub_chapters.normalize_location(
        'chapter1.xhtml#page"12', "OEBPS")

    assert target.fragment == "page12"
    assert epub_chapters.target_to_string(target) == "chapter1.xhtml#page12"


@pytest.mark.parametrize("href", [
    "",
    "/outside.xhtml",
    "http://example.test/outside.xhtml",
])
def test_clean_relative_member_rejects_invalid_paths(href):
    with pytest.raises(epub_chapters.ChapterLocationError):
        epub_chapters._clean_relative_member(href, "OEBPS")


def test_clean_relative_member_normalizes_dot_segments():
    assert epub_chapters._clean_relative_member(".", "OEBPS") == "OEBPS"
    assert epub_chapters._clean_relative_member(
        "./chapter.xhtml", "OEBPS") == "OEBPS/chapter.xhtml"
    with pytest.raises(epub_chapters.ChapterLocationError,
                       match="leaves the book root"):
        epub_chapters._clean_relative_member("../secret.xhtml", "OEBPS")


def test_text_content_handles_missing_element():
    assert epub_chapters._text_content(None) == ""


def test_chapter_label_uses_available_text_fallbacks():
    members = {
        "invalid.xhtml": b"<html><head><title>",
        "local-title.xhtml": (
            b"<html><head><title>Local Title</title></head>"
            b"<body><p>Body</p></body></html>"),
        "named-heading.xhtml": (
            '<html xmlns="http://www.w3.org/1999/xhtml">'
            "<body><h1>Named Heading</h1></body></html>").encode("utf-8"),
        "local-heading.xhtml": (
            b"<html><body><h2>Local Heading</h2></body></html>"),
        "plain.xhtml": b"<html><body><p>No heading</p></body></html>",
    }
    archive = _DictZip(members)

    assert epub_chapters._chapter_label(archive, "invalid.xhtml") == "invalid.xhtml"
    assert epub_chapters._chapter_label(archive, "local-title.xhtml") == "Local Title"
    assert epub_chapters._chapter_label(archive, "named-heading.xhtml") == "Named Heading"
    assert epub_chapters._chapter_label(archive, "local-heading.xhtml") == "Local Heading"
    assert epub_chapters._chapter_label(archive, "plain.xhtml") == "plain.xhtml"


def test_find_opf_path_rejects_bad_containers():
    empty_container = (
        '<?xml version="1.0"?>'
        '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        "<rootfiles/></container>"
    )
    with pytest.raises(epub_chapters.ChapterTocError, match="no rootfile"):
        epub_chapters._find_opf_path(_DictZip({
            "META-INF/container.xml": empty_container.encode("utf-8")}))

    missing_root = CONTAINER.replace("OEBPS/content.opf", "OEBPS/missing.opf")
    with pytest.raises(epub_chapters.ChapterTocError,
                       match="rootfile is missing"):
        epub_chapters._find_opf_path(_DictZip({
            "META-INF/container.xml": missing_root.encode("utf-8")}))


def test_read_epub_structure_skips_bad_manifest_items(tmp_path):
    epub_path = tmp_path / "book.epub"
    _write_epub(epub_path, opf=BAD_MANIFEST_OPF)

    structure = epub_chapters.read_epub_structure(str(epub_path))

    assert structure.nav_path == "OEBPS/nav.xhtml"
    assert structure.ncx_path == "OEBPS/toc.ncx"
    assert [item["id"] for item in structure.candidates] == ["chap1"]


def test_rewrite_without_a_toc_raises(tmp_path):
    epub_path = tmp_path / "book.epub"
    _write_epub(epub_path, opf=NO_TOC_OPF, nav=None, ncx=None)
    target = epub_chapters.normalize_location("chapter1.xhtml", "OEBPS")

    with pytest.raises(epub_chapters.ChapterTocError,
                       match="No EPUB2 or EPUB3 table of contents found"):
        epub_chapters.rewrite_epub_toc(str(epub_path), "Page 1", target)


def test_archive_target_path_without_opf_dir():
    target = epub_chapters.normalize_location("chapter1.xhtml")

    assert epub_chapters.archive_target_path("", target) == "chapter1.xhtml"


def test_add_nav_entry_accepts_unprefixed_nav_and_creates_list():
    data = (
        '<?xml version="1.0"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml"><head/><body>'
        '<nav type="toc"></nav></body></html>'
    ).encode("utf-8")

    result = epub_chapters.add_nav_entry(data, "chapter1.xhtml#page1", "Page 1")

    assert result is not None
    tree = etree.fromstring(result, parser=epub_chapters._safe_parser)
    links = tree.xpath(
        "//x:nav//x:a", namespaces=epub_chapters.XHTML_NSMAP)
    assert [link.get("href") for link in links] == ["chapter1.xhtml#page1"]
    assert [link.text for link in links] == ["Page 1"]


def test_add_nav_entry_returns_none_without_nav():
    data = b'<html xmlns="http://www.w3.org/1999/xhtml"><body></body></html>'

    assert epub_chapters.add_nav_entry(data, "chapter1.xhtml", "Page 1") is None


def test_add_ncx_entry_returns_none_without_nav_map():
    data = (
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
        "<head/></ncx>"
    ).encode("utf-8")

    assert epub_chapters.add_ncx_entry(data, "chapter1.xhtml", "Page 1") is None


def test_add_ncx_entry_avoids_existing_ids():
    data = (
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
        '<navMap><navPoint id="kobo-chapter" playOrder="3"/></navMap></ncx>'
    ).encode("utf-8")

    result = epub_chapters.add_ncx_entry(data, "chapter1.xhtml", "Page 1")

    assert result is not None
    tree = etree.fromstring(result, parser=epub_chapters._safe_parser)
    point = tree.xpath(
        "//ncx:navPoint[last()]", namespaces=epub_chapters.NCX_NSMAP)[0]
    assert point.get("id") == "kobo-chapter-1"
    assert point.get("playOrder") == "4"


def test_rewrite_zip_removes_temp_file_on_failure(tmp_path):
    bad_epub = tmp_path / "bad.epub"
    bad_epub.write_bytes(b"not a zip file")

    with pytest.raises(zipfile.BadZipFile):
        epub_chapters._rewrite_zip(str(bad_epub), {})

    assert list(tmp_path.glob(".kobo-chapter-*")) == []


def test_rewrite_zip_ignores_cleanup_unlink_failure(tmp_path, monkeypatch):
    bad_epub = tmp_path / "bad.epub"
    bad_epub.write_bytes(b"not a zip file")

    def fail_unlink(path):
        raise OSError("cannot unlink")

    monkeypatch.setattr(epub_chapters.os, "unlink", fail_unlink)
    with pytest.raises(zipfile.BadZipFile):
        epub_chapters._rewrite_zip(str(bad_epub), {})
