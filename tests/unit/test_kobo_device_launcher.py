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
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = REPO_ROOT / "device" / "kobo" / "add_chapter.sh"
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
    sqlite_log = tmp_path / "sqlite.log"
    python_log = tmp_path / "python.log"

    write_stub(
        bin_dir / "sqlite3",
        "\n".join(
            [
                'printf "%s\\n" "$1" >> "$KOBO_SQLITE_LOG"',
                'printf "%s\\n" "$2" >> "$KOBO_SQLITE_LOG"',
                'printf "%s\\n%s\\n%s\\n" "$FAKE_UUID" "$FAKE_TITLE" "$FAKE_LOCATION"',
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
                'printf "%s\\n%s\\n%s\\n" "$FAKE_UUID" "$FAKE_TITLE" "$FAKE_LOCATION"',
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

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "KOBO_ADDON_DIR": str(addon),
            "KOBO_EREADER_CONF": str(conf),
            "KOBO_READER_DB": str(db),
            "KOBO_QNDB_LOG": str(qndb_log),
            "KOBO_SQLITE_LOG": str(sqlite_log),
            "KOBO_PYTHON_LOG": str(python_log),
            "KOBO_TEST_DB": str(db),
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


def sqlite_calls(device):
    if not device.sqlite_log.exists():
        return []
    lines = device.sqlite_log.read_text(encoding="utf-8").splitlines()
    return [lines[index : index + 2] for index in range(0, len(lines), 2)]


def assert_no_browser_call(device):
    assert not any(
        call[:3] == ["-m", "bwmOpenBrowser", "true"] for call in qndb_calls(device)
    )


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
    assert calls[0][3] == expected_url
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

    assert result.returncode == 0, result.stderr
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

    assert result.returncode == 0, result.stderr
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


def test_nickelmenu_config_uses_dynamic_launcher():
    config = NICKELMENU_CONFIG.read_text(encoding="utf-8")
    script = "/mnt/onboard/.adds/calibre-web-chapters/add_chapter.sh"

    assert f":reader :Add chapter mark :cmd_spawn :quiet:{script}" in config
    assert f":main :Add chapter mark :cmd_spawn :quiet:{script}" in config
    assert f":main :Add chapter (debug) :cmd_spawn :quiet:{script} --debug" in config
    assert ":browser :Quit :nickel_misc :home" in config
    assert "http://" not in config
