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


def _write_epub(path):
    with zipfile.ZipFile(path, "w") as epub:
        info = zipfile.ZipInfo("mimetype")
        info.compress_type = zipfile.ZIP_STORED
        epub.writestr(info, b"application/epub+zip")
        for name, data in (
            ("META-INF/container.xml", CONTAINER),
            ("OEBPS/content.opf", OPF),
            ("OEBPS/nav.xhtml", NAV),
            ("OEBPS/toc.ncx", NCX),
            ("OEBPS/chapter1.xhtml", CHAPTER),
        ):
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

    rewritten = epub_chapters.rewrite_epub_toc(
        str(epub_path), "Comic page 3", target)

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
