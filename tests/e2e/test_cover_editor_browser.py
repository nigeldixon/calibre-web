import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
import requests
from playwright.sync_api import Page, expect


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_LIBRARY = ROOT / "tests" / "fixtures" / "library"
BOOK_ID = int(os.environ.get("CALIBRE_WEB_TEST_BOOK_ID", "2"))
BOOK_REL_PATH = os.environ.get("CALIBRE_WEB_TEST_BOOK_REL", "Bram Stoker/Dracula (2)")
USERNAME = os.environ.get("CALIBRE_WEB_USERNAME", "admin")
PASSWORD = os.environ.get("CALIBRE_WEB_PASSWORD", "admin123")


def _find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_http(url, timeout=30):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            r = requests.get(url, timeout=2)
            if r.status_code == 200:
                return True
        except Exception as e:
            last = e
        time.sleep(0.5)
    if last:
        raise RuntimeError(f"Server not ready: {last}")
    raise RuntimeError("Server not ready")


@pytest.fixture(scope="session")
def live_server(tmp_path_factory):
    """Start a Calibre-Web instance against a temp copy of the fixture library."""
    work = tmp_path_factory.mktemp("cw-e2e")
    library_dir = work / "library"
    shutil.copytree(FIXTURE_LIBRARY, library_dir)

    settings_src = ROOT / "app.db"
    if not settings_src.exists():
        pytest.skip("app.db not found; cannot start Calibre-Web for browser E2E")
    settings_db = work / "app.db"
    shutil.copy2(settings_src, settings_db)

    port = _find_free_port()
    conn = sqlite3.connect(str(settings_db))
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE settings SET config_calibre_dir=?, config_port=? WHERE id=1",
            (str(library_dir), port),
        )
        conn.commit()
    finally:
        conn.close()

    env = os.environ.copy()
    cmd = [sys.executable, str(ROOT / "cps.py"), "-p", str(settings_db), "-i", "127.0.0.1"]
    log_path = work / "cps.log"
    log_f = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=log_f, stderr=subprocess.STDOUT, env=env)
    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_http(base_url + "/login", timeout=40)
    except Exception:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        log_f.close()
        try:
            tail = "\n".join(log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-80:])
        except Exception:
            tail = ""
        pytest.skip(f"Could not start Calibre-Web for browser E2E. Log tail:\n{tail}")

    try:
        yield {
            "base_url": base_url,
            "book_dir": library_dir / BOOK_REL_PATH,
            "cover_path": library_dir / BOOK_REL_PATH / "cover.jpg",
            "history_dir": library_dir / BOOK_REL_PATH / ".cover_history",
            "proc": proc,
            "log_path": log_path,
        }
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        log_f.close()


@pytest.fixture(scope="session")
def original_cover_bytes(live_server):
    return live_server["cover_path"].read_bytes()


@pytest.fixture(autouse=True)
def clean_cover_state(live_server, original_cover_bytes):
    cover_path = live_server["cover_path"]
    history_dir = live_server["history_dir"]
    history_dir.mkdir(parents=True, exist_ok=True)
    for p in history_dir.iterdir():
        if p.is_file():
            try:
                p.unlink()
            except OSError:
                pass
    cover_path.write_bytes(original_cover_bytes)
    yield
    if history_dir.exists():
        for p in history_dir.iterdir():
            if p.is_file():
                try:
                    p.unlink()
                except OSError:
                    pass
    cover_path.write_bytes(original_cover_bytes)


def login(page: Page, base_url: str):
    page.goto(base_url + "/login")
    page.locator("#username").fill(USERNAME)
    page.locator("#password").fill(PASSWORD)
    page.get_by_role("button", name=re.compile(r"login", re.I)).first.click()
    page.wait_for_url(lambda url: "/login" not in url, timeout=15000)


def open_cover_editor(page: Page, base_url: str):
    page.goto(base_url + f"/admin/book/{BOOK_ID}")
    expect(page.locator("#detailcover")).to_be_visible(timeout=10000)
    edit_btn = page.locator("#edit_cover_btn")
    expect(edit_btn).to_be_visible()
    edit_btn.click()
    modal = page.locator("#coverEditorModal")
    expect(modal).to_be_visible()
    expect(page.locator("#cover-editor-image")).to_be_visible()
    page.wait_for_timeout(300)
    expect(modal.locator(".cropper-container")).to_be_visible(timeout=10000)
    return modal


def test_open_cover_editor_modal(page: Page, live_server):
    login(page, live_server["base_url"])
    modal = open_cover_editor(page, live_server["base_url"])
    expect(modal.locator("#cover-zoom-in")).to_be_visible()
    expect(modal.locator("#cover-zoom-out")).to_be_visible()
    expect(modal.locator("#cover-brightness")).to_be_visible()
    expect(modal.locator("#cover-contrast")).to_be_visible()
    expect(modal.locator("#cover-saturation")).to_be_visible()
    expect(modal.locator("#cover-save-btn")).to_be_visible()
    expect(modal.locator("#cover-history-list")).to_be_visible()


def test_zoom_and_adjustment_controls_update_ui(page: Page, live_server):
    login(page, live_server["base_url"])
    modal = open_cover_editor(page, live_server["base_url"])

    zoom_val = modal.locator("#cover-zoom-val")
    before = zoom_val.inner_text()
    modal.locator("#cover-zoom-in").click()
    page.wait_for_timeout(250)
    after = zoom_val.inner_text()
    assert after != before

    modal.locator("#cover-brightness").evaluate(
        "(el) => { el.value = '120'; el.dispatchEvent(new Event('input', {bubbles:true})); el.dispatchEvent(new Event('change', {bubbles:true})); }"
    )
    modal.locator("#cover-contrast").evaluate(
        "(el) => { el.value = '80'; el.dispatchEvent(new Event('input', {bubbles:true})); el.dispatchEvent(new Event('change', {bubbles:true})); }"
    )
    modal.locator("#cover-saturation").evaluate(
        "(el) => { el.value = '140'; el.dispatchEvent(new Event('input', {bubbles:true})); el.dispatchEvent(new Event('change', {bubbles:true})); }"
    )
    expect(modal.locator("#cover-brightness-val")).to_have_text("120%")
    expect(modal.locator("#cover-contrast-val")).to_have_text("80%")
    expect(modal.locator("#cover-saturation-val")).to_have_text("140%")

    modal.locator("#cover-reset-adjustments").click()
    expect(modal.locator("#cover-brightness-val")).to_have_text("100%")
    expect(modal.locator("#cover-contrast-val")).to_have_text("100%")
    expect(modal.locator("#cover-saturation-val")).to_have_text("100%")


def test_save_creates_history_and_updates_cover(page: Page, live_server):
    login(page, live_server["base_url"])
    modal = open_cover_editor(page, live_server["base_url"])

    before_src = page.locator("#detailcover").get_attribute("src")
    modal.locator("#cover-save-btn").click()

    status = modal.locator("#cover-editor-status")
    expect(status).to_contain_text(re.compile(r"saved", re.I), timeout=15000)
    expect(modal.locator("#cover-history-list .cover-history-item")).to_have_count(1, timeout=10000)

    after_src = page.locator("#detailcover").get_attribute("src")
    assert after_src and before_src != after_src
    assert "c=" in after_src
    assert live_server["cover_path"].read_bytes()
    assert any(live_server["history_dir"].iterdir())


def test_restore_and_delete_history_version(page: Page, live_server):
    login(page, live_server["base_url"])
    modal = open_cover_editor(page, live_server["base_url"])

    modal.locator("#cover-save-btn").click()
    expect(modal.locator("#cover-editor-status")).to_contain_text(re.compile(r"saved", re.I), timeout=15000)
    expect(modal.locator("#cover-history-list .cover-history-item")).to_have_count(1, timeout=10000)
    first_name = modal.locator("#cover-history-list .cover-history-item").first.get_attribute("data-version")
    assert first_name

    modal.locator("#cover-brightness").evaluate(
        "(el) => { el.value = '130'; el.dispatchEvent(new Event('input', {bubbles:true})); el.dispatchEvent(new Event('change', {bubbles:true})); }"
    )
    modal.locator("#cover-save-btn").click()
    expect(modal.locator("#cover-editor-status")).to_contain_text(re.compile(r"saved", re.I), timeout=15000)
    expect(modal.locator("#cover-history-list .cover-history-item")).to_have_count(2, timeout=10000)

    first_item = modal.locator(f"#cover-history-list .cover-history-item[data-version='{first_name}']")
    expect(first_item).to_have_count(1)
    first_item.locator(".cover-history-restore").click()
    expect(modal.locator("#cover-editor-status")).to_contain_text(re.compile(r"restor", re.I), timeout=15000)
    expect(modal.locator("#cover-history-list .cover-history-item")).not_to_have_count(0, timeout=10000)

    items = modal.locator("#cover-history-list .cover-history-item")
    count_before_delete = items.count()
    assert count_before_delete >= 1
    items.first.locator(".cover-history-delete").click()
    page.wait_for_timeout(500)
    expect(modal.locator("#cover-history-list .cover-history-item")).to_have_count(count_before_delete - 1, timeout=10000)
