"""Host-side tests for the Kobo/NickelMenu chapter launcher.

The real launcher reads Kobo config/database files and calls NickelDBus. These
tests redirect those paths to a temporary directory and put stub ``sqlite3``,
``python`` and ``qndb`` executables on PATH, so the config parsing, URL
construction, database reader selection and error paths can be exercised
without a device.
"""
import os
import shutil
import sqlite3
import struct
import subprocess
import sys
import tarfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import re

REPO_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = REPO_ROOT / "device" / "kobo" / "add_chapter.sh"
TRACKER = REPO_ROOT / "device" / "kobo" / "track_page.sh"
INIT_SCRIPT = REPO_ROOT / "device" / "kobo" / "init.d-calibre-web-chapters"
BUILD_PACKAGE = REPO_ROOT / "device" / "kobo" / "build-kobo-root.sh"
NICKELMENU_CONFIG = REPO_ROOT / "device" / "kobo" / "nickelmenu-config"
BUNDLED_SQLITE = REPO_ROOT / "device" / "kobo" / "sqlite3-armv7"

DEFAULT_ENDPOINT = "http://192.168.1.10:8083/kobo/testtoken/v1/library/1"
DEFAULT_BASE = "http://192.168.1.10:8083/kobo/testtoken"
DEFAULT_UUID = "84cb8856-4bc4-480f-9535-0d16295c5d5e"


def encode(value):
    """Match the launcher's byte-wise, uppercase urlencode helper."""
    return "".join(f"%{byte:02X}" for byte in value.encode("utf-8"))


def write_stub(path, body):
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture
def device(tmp_path):
    root = tmp_path / "device"
    addon = root / ".adds" / "calibre-web-chapters"
    addon.mkdir(parents=True)
    conf = root / ".kobo" / "Kobo" / "Kobo eReader.conf"
    conf.parent.mkdir(parents=True, exist_ok=True)
    conf.write_text(f"api_endpoint={DEFAULT_ENDPOINT}\n", encoding="utf-8")
    db = root / ".kobo" / "KoboReader.sqlite"
    db.parent.mkdir(parents=True, exist_ok=True)
    db.write_text("stub database\n", encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    qndb_log = tmp_path / "qndb.log"
    wget_log = tmp_path / "wget.log"
    sqlite_log = tmp_path / "sqlite.log"
    python_log = tmp_path / "python.log"

    write_stub(
        bin_dir / "sqlite3",
        "\n".join(
            [
                'printf "%s\\n" "$1" >> "$KOBO_SQLITE_LOG"',
                'printf "%s\\n" "$2" >> "$KOBO_SQLITE_LOG"',
                'case "$2" in',
                '    *ContentType=9*) printf "%s\\n" "$KOBO_SQLITE_CHAPTER_ROWS" ;;',
                '    *"FROM content WHERE ContentID LIKE"*) printf "%s\\n" "$KOBO_SQLITE_CHAPTER_ROWS" ;;',
                '    *) printf "%s\\n%s\\n%s\\n" "$FAKE_UUID" "$FAKE_TITLE" "$FAKE_LOCATION" ;;',
                "esac",
                'exit "${SQLITE_EXIT_CODE:-0}"',
            ]
        )
        + "\n",
    )
    write_stub(
        bin_dir / "python",
        "\n".join(
            [
                'printf "%s\\n" "-- python --" >> "$KOBO_PYTHON_LOG"',
                'for arg in "$@"; do',
                '    printf "%s\\n" "$arg" >> "$KOBO_PYTHON_LOG"',
                "done",
                'case "$2" in',
                '    *executescript*) exit 0 ;;',
                '    *) case "$4" in',
                '           *"FROM content WHERE ContentID LIKE"*) printf "%s\\n" "$KOBO_SQLITE_CHAPTER_ROWS" ;;',
                '           *) printf "%s\\n%s\\n%s\\n" "$FAKE_UUID" "$FAKE_TITLE" "$FAKE_LOCATION" ;;',
                '       esac ;;',
                'esac',
            ]
        )
        + "\n",
    )
    write_stub(
        bin_dir / "qndb",
        "\n".join(
            [
                'printf "%s\\n" "-- call --" >> "$KOBO_QNDB_LOG"',
                'for arg in "$@"; do',
                '    printf "%s\\n" "$arg" >> "$KOBO_QNDB_LOG"',
                "done",
                'exit "${QNDB_EXIT_CODE:-0}"',
            ]
        )
        + "\n",
    )
    write_stub(
        bin_dir / "wget",
        "\n".join(
            [
                'for last do :; done',
                'printf "%s\\n" "$last" >> "$KOBO_WGET_LOG"',
                'case "$last" in',
                '    */result?*)',
                '        if [ -n "${KOBO_RESULT_RESPONSE:-}" ]; then printf "%s\\n" "$KOBO_RESULT_RESPONSE"; else printf "saved=0\\n"; fi',
                '        ;;',
                '    *)',
                '        if [ -n "${KOBO_WGET_RESPONSE:-}" ]; then printf "%s\\n" "$KOBO_WGET_RESPONSE"; else printf \'{"served":true}\\n\'; fi',
                '        ;;',
                'esac',
                'exit "${WGET_EXIT_CODE:-0}"',
            ]
        )
        + "\n",
    )

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "KOBO_ADDON_DIR": str(addon),
            "KOBO_EREADER_CONF": str(conf),
            "KOBO_READER_DB": str(db),
            "KOBO_QNDB_LOG": str(qndb_log),
            "KOBO_WGET_LOG": str(wget_log),
            "KOBO_WGET_RESPONSE": "",
            "KOBO_RESULT_RESPONSE": "",
            "WGET_EXIT_CODE": "0",
            "KOBO_RESULT_ATTEMPTS": "0",
            "KOBO_RESULT_INTERVAL": "0",
            "KOBO_SQLITE_LOG": str(sqlite_log),
            "KOBO_PYTHON_LOG": str(python_log),
            "KOBO_SQLITE_SYSTEM_PATHS": "",
            "KOBO_TEST_DB": str(db),
            "KOBO_SQLITE_CHAPTER_ROWS": "",
            "KOBO_TRACKER_EVENT_COUNTER": str(tmp_path / "tracker-event-counter"),
            "KOBO_TRACKER_SLEEP": "0",
            "FAKE_UUID": DEFAULT_UUID,
            "FAKE_TITLE": "Dracula",
            "FAKE_LOCATION": "chapter1.xhtml#kobo.1.1",
        }
    )
    return SimpleNamespace(
        env=env,
        addon=addon,
        conf=conf,
        db=db,
        bin_dir=bin_dir,
        qndb_log=qndb_log,
        wget_log=wget_log,
        sqlite_log=sqlite_log,
        python_log=python_log,
    )


def run_launcher(device, *args):
    return subprocess.run(
        ["sh", str(LAUNCHER), *args],
        env=device.env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def run_tracker(device, *args, timeout=2):
    return subprocess.run(
        ["sh", str(TRACKER), *args],
        env=device.env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def error_log(device):
    path = device.addon / "last-error.log"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def qndb_calls(device):
    if not device.qndb_log.exists():
        return []
    text = device.qndb_log.read_text(encoding="utf-8")
    return [
        block.strip().splitlines()
        for block in text.split("-- call --")
        if block.strip()
    ]


def wget_urls(device):
    if not device.wget_log.exists():
        return []
    return device.wget_log.read_text(encoding="utf-8").splitlines()


def sqlite_calls(device):
    if not device.sqlite_log.exists():
        return []
    calls = []
    for line in device.sqlite_log.read_text(encoding="utf-8").splitlines():
        if line == str(device.db):
            calls.append([line])
        elif calls:
            calls[-1].append(line)
    return calls


def assert_no_browser_call(device):
    assert not any(
        call[:3] == ["-m", "bwmOpenBrowser", "true"] for call in qndb_calls(device)
    )


def assert_browser_url(device, expected_url):
    url = qndb_calls(device)[0][3]
    prefix, separator, client = url.rpartition("&client=")
    assert separator
    assert re.fullmatch(r"[A-Za-z0-9_-]+", client)
    assert prefix == expected_url


def test_launcher_parses_config_and_opens_modal_browser(device):
    # Duplicate endpoint lines and CRLF line endings happen on real devices.
    device.conf.write_bytes(
        b"api_endpoint=" + DEFAULT_ENDPOINT.encode() + b"\r\n"
        b"api_endpoint=http://ignored/kobo/other\r\n"
    )
    device.env["FAKE_TITLE"] = "Dracula & Co / Page 1"
    device.env["FAKE_LOCATION"] = "OEBPS/chapter1.xhtml#kobo.1.1"

    result = run_launcher(device)

    assert result.returncode == 0, result.stderr
    expected_url = (
        f"{DEFAULT_BASE}/chapters/{DEFAULT_UUID}"
        f"?name={encode('Dracula & Co / Page 1')}"
        f"&location={encode('OEBPS/chapter1.xhtml#kobo.1.1')}"
    )
    calls = qndb_calls(device)
    assert calls[0][:3] == ["-m", "bwmOpenBrowser", "true"]
    assert_browser_url(device, expected_url)
    assert calls[1][:3] == ["-m", "mwcToast", "4000"]
    assert calls[1][3] == "Chapter form opened"


def test_launcher_reads_current_book_from_kobo_database(device):
    result = run_launcher(device)

    assert result.returncode == 0, result.stderr
    calls = sqlite_calls(device)
    assert len(calls) == 1
    assert calls[0][0] == str(device.db)
    query = calls[0][1]
    assert "ContentType=6" in query
    assert "COALESCE(NULLIF(Title,''),NULLIF(BookTitle,''),'')" in query
    assert "COALESCE(ChapterIDBookmarked,'')" in query
    assert "CASE WHEN ReadStatus=1 THEN 0 ELSE 1 END" in query
    assert "COALESCE(DateLastRead,'') DESC" in query
    assert "LIMIT 1;" in query


def test_launcher_prefers_bundled_sqlite_binary(device):
    bundled = device.addon / "sqlite3-armv7"
    write_stub(
        bundled,
        "\n".join(
            [
                'printf "bundled\\n" >> "$KOBO_SQLITE_LOG"',
                'printf "%s\\n%s\\n%s\\n" "$FAKE_UUID" "$FAKE_TITLE" "$FAKE_LOCATION"',
            ]
        )
        + "\n",
    )

    result = run_launcher(device)

    assert result.returncode == 0, result.stderr
    assert "bundled" in device.sqlite_log.read_text(encoding="utf-8")


def test_launcher_falls_back_to_python_sqlite3(device):
    (device.bin_dir / "sqlite3").unlink()
    for name in ("sh", "date", "mkdir", "sed", "head", "tr", "od", "rm"):
        source = shutil.which(name, path="/usr/bin:/bin")
        assert source is not None, f"missing host command: {name}"
        (device.bin_dir / name).symlink_to(source)
    device.env["PATH"] = str(device.bin_dir)

    result = run_launcher(device)

    assert result.returncode == 0, error_log(device) or result.stderr
    python_log = device.python_log.read_text(encoding="utf-8")
    assert "-- python --" in python_log
    assert str(device.db) in python_log
    assert "ContentType=6" in python_log
    assert qndb_calls(device)[0][:3] == ["-m", "bwmOpenBrowser", "true"]


def test_launcher_skips_unusable_bundled_sqlite(device):
    write_stub(device.addon / "sqlite3-armv7", "exit 126\n")
    (device.bin_dir / "sqlite3").unlink()
    for name in ("sh", "date", "mkdir", "sed", "head", "tr", "od", "rm"):
        source = shutil.which(name, path="/usr/bin:/bin")
        assert source is not None, f"missing host command: {name}"
        (device.bin_dir / name).symlink_to(source)
    device.env["PATH"] = str(device.bin_dir)

    result = run_launcher(device)

    assert result.returncode == 0, error_log(device) or result.stderr
    assert "-- python --" in device.python_log.read_text(encoding="utf-8")


def test_query_uses_book_title_and_prefers_currently_read_book(device):
    device.db.unlink()
    with sqlite3.connect(device.db) as connection:
        connection.execute(
            "CREATE TABLE content ("
            "ContentID TEXT, Title TEXT, BookTitle TEXT, "
            "ChapterIDBookmarked TEXT, ContentType INTEGER, "
            "ReadStatus INTEGER, DateLastRead TEXT)"
        )
        connection.executemany(
            "INSERT INTO content VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (DEFAULT_UUID, "Dracula", "", "chapter1.xhtml", 6, 1, "2024-01-01"),
                (
                    "11111111-1111-1111-1111-111111111111",
                    "Finished book",
                    "",
                    "chapter9.xhtml",
                    6,
                    2,
                    "2024-02-01",
                ),
                ("chapter-row", "Chapter title", "Dracula", "chapter2.xhtml", 9, 1, "2024-03-01"),
            ],
        )

    (device.bin_dir / "sqlite3").unlink()
    (device.bin_dir / "python").unlink()
    for name in ("sh", "date", "mkdir", "sed", "head", "tr", "od", "rm"):
        source = shutil.which(name, path="/usr/bin:/bin")
        assert source is not None, f"missing host command: {name}"
        (device.bin_dir / name).symlink_to(source)
    (device.bin_dir / "python").symlink_to(sys.executable)
    device.env["PATH"] = str(device.bin_dir)

    result = run_launcher(device, "--debug")

    assert result.returncode == 0, result.stderr
    debug_text = (device.addon / "debug.txt").read_text(encoding="utf-8")
    assert f"uuid:     {DEFAULT_UUID}" in debug_text
    assert "title:    Dracula" in debug_text
    assert "location: chapter1.xhtml" in debug_text


def test_bundled_sqlite_binary_is_32_bit_arm(device):
    data = BUNDLED_SQLITE.read_bytes()

    assert data.startswith(b"\x7fELF\x01\x01\x01")
    assert struct.unpack_from("<H", data, 18)[0] == 40  # EM_ARM
    assert os.access(BUNDLED_SQLITE, os.X_OK)


def test_debug_mode_writes_parsed_values_without_opening_browser(device):
    result = run_launcher(device, "--debug")

    assert result.returncode == 0, result.stderr
    debug_text = (device.addon / "debug.txt").read_text(encoding="utf-8")
    debug_lines = debug_text.splitlines()
    assert debug_lines[0].startswith("arch:     ") and debug_lines[0].split(":", 1)[1].strip()
    assert debug_lines[1].startswith("reader:   ") and debug_lines[1].split(":", 1)[1].strip()
    assert f"endpoint: {DEFAULT_ENDPOINT}" in debug_text
    assert f"base:     {DEFAULT_BASE}" in debug_text
    assert f"uuid:     {DEFAULT_UUID}" in debug_text
    assert "title:    Dracula" in debug_text
    assert "location: chapter1.xhtml#kobo.1.1" in debug_text
    assert (
        f"url:      {DEFAULT_BASE}/chapters/{DEFAULT_UUID}"
        f"?name={encode('Dracula')}"
        f"&location={encode('chapter1.xhtml#kobo.1.1')}"
    ) in debug_text
    assert_no_browser_call(device)


def test_sideloaded_book_is_rejected_but_debug_still_works(device):
    device.env["FAKE_UUID"] = "/mnt/onboard/ebooks/Dracula.epub"

    result = run_launcher(device)

    assert result.returncode == 1
    assert "was not synced from calibre-web" in error_log(device)
    assert "ContentID=/mnt/onboard/ebooks/Dracula.epub" in error_log(device)
    assert_no_browser_call(device)

    debug_result = run_launcher(device, "--debug")
    assert debug_result.returncode == 0
    assert (device.addon / "debug.txt").exists()


@pytest.mark.parametrize(
    ("conf_text", "expected_error"),
    [
        ("# no endpoint configured\n", "No api_endpoint in Kobo eReader.conf"),
        ("api_endpoint=not-a-kobo-url\n", "not a calibre-web Kobo URL"),
    ],
)
def test_missing_or_invalid_endpoint_fails_before_database_read(
    device, conf_text, expected_error
):
    device.conf.write_text(conf_text, encoding="utf-8")

    result = run_launcher(device)

    assert result.returncode == 1
    assert expected_error in error_log(device)
    assert not sqlite_calls(device)
    assert_no_browser_call(device)


def test_missing_config_and_database_are_reported(device):
    missing_conf = device.conf.with_name("missing.conf")
    device.env["KOBO_EREADER_CONF"] = str(missing_conf)
    result = run_launcher(device)
    assert result.returncode == 1
    assert "Kobo eReader.conf not found" in error_log(device)

    (device.addon / "last-error.log").unlink()
    device.env["KOBO_EREADER_CONF"] = str(device.conf)
    device.env["KOBO_READER_DB"] = str(device.db.with_name("missing.sqlite"))
    result = run_launcher(device)
    assert result.returncode == 1
    assert "KoboReader.sqlite not found" in error_log(device)
    assert_no_browser_call(device)


def test_sqlite_failure_is_reported(device):
    device.env["SQLITE_EXIT_CODE"] = "1"

    result = run_launcher(device)

    assert result.returncode == 1
    assert "Could not read KoboReader.sqlite" in error_log(device)
    assert_no_browser_call(device)


def test_missing_qndb_is_reported(device):
    (device.bin_dir / "qndb").unlink()
    if shutil.which("qndb", path=device.env["PATH"]) or any(
        Path(path).exists() for path in ("/usr/bin/qndb", "/usr/local/bin/qndb")
    ):
        pytest.skip("qndb is installed outside the test stub directory")

    result = run_launcher(device)

    assert result.returncode == 1
    assert "qndb not found" in error_log(device)
    assert not device.qndb_log.exists()


def test_qndb_failure_is_reported(device):
    device.env["QNDB_EXIT_CODE"] = "1"

    result = run_launcher(device)

    assert result.returncode == 1
    assert "qndb bwmOpenBrowser failed" in error_log(device)


def test_nickelmenu_config_has_minimal_visible_entries():
    config = NICKELMENU_CONFIG.read_text(encoding="utf-8")
    script = "/mnt/onboard/.adds/calibre-web-chapters/add_chapter.sh"
    visible = [
        line.strip()
        for line in config.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    assert f"menu_item :reader :Add chapter mark :cmd_spawn :quiet:{script}" in visible
    assert "menu_item :browser :Quit :nickel_misc :home" in visible
    assert not any(":main :" in line for line in visible)
    assert "http://" not in config


def test_diagnostic_menu_entries_are_commented_out():
    config = NICKELMENU_CONFIG.read_text(encoding="utf-8")
    script = "/mnt/onboard/.adds/calibre-web-chapters"

    assert (
        f"# menu_item :main :Add chapter mark :cmd_spawn :quiet:{script}/add_chapter.sh"
        in config
    )
    assert (
        f"# menu_item :reader :Add chapter (debug reader) :cmd_spawn :quiet:"
        f"{script}/add_chapter.sh --debug" in config
    )
    assert (
        f"# menu_item :main :Reload page tracker :cmd_spawn :quiet:"
        f"{script}/track_page.sh --restart" in config
    )


def test_launcher_uses_tracker_chapter_when_state_is_fresh(device):
    (device.addon / "page-state.txt").write_text(
        "\n".join(
            [
                "view=ReadingView",
                "page=14",
                f"uuid={DEFAULT_UUID}",
                "title=Dracula",
                "last_page=14",
                "max_page=14",
                "resets=1",
                f"updated={int(time.time())}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    device.env["KOBO_SQLITE_CHAPTER_ROWS"] = "\n".join(
        [
            "chapter1.xhtml", "0", "10", "100",
            "chapter2.xhtml", "10", "10", "100",
            "chapter3.xhtml", "20", "10", "100",
        ]
    )
    device.env["FAKE_LOCATION"] = "OEBPS/chapter1.xhtml#kobo.1.1"

    result = run_launcher(device)

    assert result.returncode == 0, result.stderr
    calls = sqlite_calls(device)
    assert len(calls) == 2
    assert f"ContentID LIKE '{DEFAULT_UUID}%'" in calls[0][1]
    assert "ContentType=9" in calls[1][1]
    expected_url = (
        f"{DEFAULT_BASE}/chapters/{DEFAULT_UUID}"
        f"?name={encode('Dracula')}"
        f"&location={encode('chapter2.xhtml')}"
    )
    assert_browser_url(device, expected_url)


def test_launcher_ignores_stale_tracker_state(device):
    (device.addon / "page-state.txt").write_text(
        "\n".join(
            [
                "view=ReadingView",
                "page=99",
                f"uuid={DEFAULT_UUID}",
                "title=Dracula",
                "last_page=99",
                "max_page=99",
                "resets=9",
                "updated=0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = run_launcher(device)

    assert result.returncode == 0, result.stderr
    assert len(sqlite_calls(device)) == 1
    expected_url = (
        f"{DEFAULT_BASE}/chapters/{DEFAULT_UUID}"
        f"?name={encode('Dracula')}"
        f"&location={encode('chapter1.xhtml#kobo.1.1')}"
    )
    assert_browser_url(device, expected_url)


def test_tracker_records_page_events_and_chapter_resets(device):
    write_stub(
        device.bin_dir / "qndb",
        "\n".join(
            [
                'if [ "$1" = "-m" ]; then',
                '    printf "HomePageView\\n"',
                "    exit 0",
                "fi",
                'counter=${KOBO_TRACKER_EVENT_COUNTER}',
                'n=$(cat "$counter" 2>/dev/null || printf 0)',
                'n=$((n + 1))',
                'printf "%s" "$n" > "$counter"',
                'case "$n" in',
                "    1) printf 'rvPageChanged 12\\n' ;;",
                "    2) printf 'rvPageChanged 13\\n' ;;",
                "    3) printf 'rvPageChanged 1\\n' ;;",
                "    4) printf 'ndbViewChanged HomePageView\\n' ;;",
                "esac",
                "exit 0",
            ]
        )
        + "\n",
    )

    with pytest.raises(subprocess.TimeoutExpired):
        run_tracker(device)

    state = {}
    for line in (device.addon / "page-state.txt").read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        state[key] = value
    assert state["view"] == "HomePageView"
    assert state["page"] == "1"
    assert state["last_page"] == "1"
    assert state["max_page"] == "13"
    assert state["resets"] == "1"
    assert state["updated"].isdigit()


def test_tracker_captures_current_book_when_reader_opens(device):
    write_stub(
        device.bin_dir / "qndb",
        "\n".join(
            [
                'if [ "$1" = "-m" ]; then',
                '    printf "ReadingView\\n"',
                "    exit 0",
                "fi",
                "printf 'rvPageChanged 3\\n'",
            ]
        )
        + "\n",
    )

    with pytest.raises(subprocess.TimeoutExpired):
        run_tracker(device)

    state = {}
    for line in (device.addon / "page-state.txt").read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        state[key] = value
    assert state["view"] == "ReadingView"
    assert state["uuid"] == DEFAULT_UUID
    assert state["title"] == "Dracula"
    assert state["page"] == "3"
    assert any("ContentType=6" in call[1] for call in sqlite_calls(device))


def test_launcher_stops_when_server_does_not_serve_book(device):
    device.env["KOBO_WGET_RESPONSE"] = '{"served":false,"error":"missing"}'

    result = run_launcher(device)

    assert result.returncode == 1
    assert "not in this calibre-web library" in error_log(device)
    assert_no_browser_call(device)
    urls = wget_urls(device)
    assert len(urls) == 1
    assert urls[0].endswith("&check=1")


def test_launcher_reports_when_server_is_unreachable(device):
    device.env["WGET_EXIT_CODE"] = "1"

    result = run_launcher(device)

    assert result.returncode == 1
    assert "Could not contact calibre-web" in error_log(device)
    assert_no_browser_call(device)


def kepub_sqlite_rows():
    return "\n".join(
        [
            f"{DEFAULT_UUID}!OEBPS!chapter1.xhtml-0", "899",
            "application/x-kobo-epub+zip", DEFAULT_UUID, "Dracula",
            "0", "1", "", "",
            f"{DEFAULT_UUID}!OEBPS!chapter1.xhtml", "9",
            "application/xhtml+xml", DEFAULT_UUID, "Dracula",
            "0", "1", "", "chapter1.xhtml",
        ]
    )


def test_launcher_adds_local_toc_row_after_successful_save(device):
    device.db.write_bytes(b"")
    with sqlite3.connect(device.db) as connection:
        connection.execute(
            """
            CREATE TABLE content (
                ContentID TEXT PRIMARY KEY,
                ContentType TEXT,
                MimeType TEXT,
                BookID TEXT,
                BookTitle TEXT,
                Title TEXT,
                Attribution TEXT,
                adobe_location TEXT,
                IsEncrypted BOOL,
                FirstTimeReading BOOL,
                ParagraphBookmarked INTEGER,
                BookmarkWordOffset INTEGER,
                VolumeIndex INTEGER,
                ___NumPages INTEGER,
                ReadStatus INTEGER,
                ___UserID TEXT NOT NULL,
                ___FileOffset INTEGER,
                ___FileSize INTEGER,
                ___PercentRead INTEGER,
                Depth INTEGER,
                ChapterIDBookmarked TEXT,
                NumShortcovers INTEGER
            )
            """
        )
        connection.execute(
            "INSERT INTO content (ContentID, ContentType, ___UserID, NumShortcovers) "
            "VALUES (?, '6', '', 2)", (DEFAULT_UUID,))
        connection.execute(
            "INSERT INTO content (ContentID, ContentType, ___UserID, VolumeIndex) "
            "VALUES (?, '899', '', 0)",
            (f"{DEFAULT_UUID}!OEBPS!chapter1.xhtml-0",))
        connection.execute(
            "INSERT INTO content (ContentID, ContentType, ___UserID, VolumeIndex) "
            "VALUES (?, '9', '', 0)",
            (f"{DEFAULT_UUID}!OEBPS!chapter1.xhtml",))
    device.env["KOBO_SQLITE_CHAPTER_ROWS"] = kepub_sqlite_rows()
    device.env["KOBO_RESULT_ATTEMPTS"] = "2"
    device.env["KOBO_RESULT_INTERVAL"] = "0"
    device.env["KOBO_RESULT_RESPONSE"] = (
        "saved=1\nname=Page 12\nlocation=OEBPS/chapter1.xhtml")

    result = run_launcher(device)

    assert result.returncode == 0, result.stderr
    urls = wget_urls(device)
    assert any(url.endswith("&check=1") for url in urls)
    assert any("/result?client=" in url for url in urls)

    toasts = [call[3] for call in qndb_calls(device)
              if call[:3] == ["-m", "mwcToast", "4000"]]
    assert toasts[-1] == "Chapter mark added to reader"

    writes = [call for call in sqlite_calls(device)
              if any("INSERT INTO content" in line for line in call)]
    assert len(writes) == 1
    sql = "\n".join(writes[0][1:])
    assert f"'{DEFAULT_UUID}!OEBPS!chapter1.xhtml-1'" in sql
    assert "899" in sql
    assert "'Page 12'" in sql
    assert "NULLIF('" + DEFAULT_UUID + "!OEBPS!chapter1.xhtml','')" in sql
    assert "adobe_location, IsEncrypted" in sql
    assert "NULL, 'false', 'true'" in sql
    assert "NumShortcovers = COALESCE(NumShortcovers, 0) + 1" in sql

    with sqlite3.connect(device.db) as connection:
        connection.executescript(sql)
        row = connection.execute(
            "SELECT ContentType, MimeType, BookID, Title, adobe_location, "
            "ChapterIDBookmarked FROM content WHERE ContentID = ?",
            (f"{DEFAULT_UUID}!OEBPS!chapter1.xhtml-1",),
        ).fetchone()
        shortcovers = connection.execute(
            "SELECT NumShortcovers FROM content WHERE ContentID = ?",
            (DEFAULT_UUID,),
        ).fetchone()[0]
    assert row == (
        "899", "application/x-kobo-epub+zip", DEFAULT_UUID, "Page 12", None,
        f"{DEFAULT_UUID}!OEBPS!chapter1.xhtml",
    )
    assert shortcovers == 3


def test_launcher_does_not_write_database_when_save_incomplete(device):
    device.env["KOBO_RESULT_ATTEMPTS"] = "1"
    device.env["KOBO_RESULT_INTERVAL"] = "0"
    device.env["KOBO_RESULT_RESPONSE"] = "saved=0"

    result = run_launcher(device)

    assert result.returncode == 0, result.stderr
    assert not any("INSERT INTO content" in line
                   for call in sqlite_calls(device) for line in call)
    assert not any(call[3] == "Chapter mark added to reader"
                   for call in qndb_calls(device))


def test_launcher_writes_local_row_with_python_sqlite_fallback(device):
    (device.bin_dir / "sqlite3").unlink()
    for name in (
        "sh", "date", "mkdir", "sed", "head", "tail", "tr", "od", "rm", "awk",
    ):
        source = shutil.which(name, path="/usr/bin:/bin")
        assert source is not None, f"missing host command: {name}"
        (device.bin_dir / name).symlink_to(source)
    device.env["PATH"] = str(device.bin_dir)
    device.env["KOBO_SQLITE_CHAPTER_ROWS"] = kepub_sqlite_rows()
    device.env["KOBO_RESULT_ATTEMPTS"] = "1"
    device.env["KOBO_RESULT_INTERVAL"] = "0"
    device.env["KOBO_RESULT_RESPONSE"] = (
        "saved=1\nname=Python mark\nlocation=OEBPS/chapter1.xhtml")

    result = run_launcher(device)

    assert result.returncode == 0, error_log(device) or result.stderr
    python_log = device.python_log.read_text(encoding="utf-8")
    assert "executescript" in python_log
    assert "INSERT INTO content" in python_log
    assert "'Python mark'" in python_log


@pytest.mark.parametrize(
    "script",
    [LAUNCHER, TRACKER, INIT_SCRIPT, BUILD_PACKAGE],
)
def test_device_shell_scripts_have_valid_syntax(script):
    result = subprocess.run(
        ["sh", "-n", str(script)], capture_output=True, text=True, check=False
    )

    assert result.returncode == 0, result.stderr


def test_build_package_contains_runtime_files_and_init_script(tmp_path):
    output = tmp_path / "KoboRoot.tgz"
    env = os.environ.copy()
    env["KOBO_PACKAGE_OUTPUT"] = str(output)

    result = subprocess.run(
        ["sh", str(BUILD_PACKAGE)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    with tarfile.open(output, "r:gz") as archive:
        members = {member.name: member for member in archive.getmembers()}

    addon = "./mnt/onboard/.adds/calibre-web-chapters"
    expected_modes = {
        "./etc/init.d/calibre-web-chapters": 0o755,
        f"{addon}/add_chapter.sh": 0o755,
        f"{addon}/track_page.sh": 0o755,
        f"{addon}/sqlite3-armv7": 0o755,
        "./mnt/onboard/.adds/nm/calibre-web-chapters": 0o644,
    }
    assert expected_modes.keys() <= members.keys()
    for name, mode in expected_modes.items():
        assert members[name].mode & 0o777 == mode
    assert not any("page-state" in name or "debug.txt" in name for name in members)
