import io
import os
import re
import time
from pathlib import Path

import pytest
import requests
from playwright.sync_api import Page, expect


BASE_URL = os.environ.get("CALIBRE_WEB_URL", "http://127.0.0.1:8083")
BOOK_ID = int(os.environ.get("CALIBRE_WEB_TEST_BOOK_ID", "2"))
BOOK_DIR = Path(
    os.environ.get(
        "CALIBRE_WEB_TEST_BOOK_DIR",
        "library/Bram Stoker/Dracula (2)",
    )
)
COVER_PATH = BOOK_DIR / "cover.jpg"
HISTORY_DIR = BOOK_DIR / ".cover_history"
USERNAME = os.environ.get("CALIBRE_WEB_USERNAME", "admin")
PASSWORD = os.environ.get("CALIBRE_WEB_PASSWORD", "admin123")


def _server_available():
    try:
        r = requests.get(BASE_URL + "/login", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


@pytest.fixture(scope="session", autouse=True)
def require_server():
    if not _server_available():
        pytest.skip(f"Calibre-Web server not available at {BASE_URL}")


@pytest.fixture(scope="session")
def original_cover_bytes():
    return COVER_PATH.read_bytes()


@pytest.fixture(scope="session", autouse=True)
def fixture_library_db():
    """Temporarily install populated test DB so a running app can see the sample book.

    Restores the base empty library/metadata.db afterwards to avoid replacing it in git.
    """
    root_db = Path("library/metadata.db")
    fixture_db = Path("tests/fixtures/library/metadata.db")
    if not root_db.exists() or not fixture_db.exists():
        yield
        return
    base_bytes = root_db.read_bytes()
    root_db.write_bytes(fixture_db.read_bytes())
    try:
        yield
    finally:
        root_db.write_bytes(base_bytes)


@pytest.fixture(autouse=True)
def clean_cover_state(original_cover_bytes):
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    for p in HISTORY_DIR.iterdir():
        if p.is_file():
            try:
                p.unlink()
            except OSError:
                pass
    COVER_PATH.write_bytes(original_cover_bytes)
    yield
    if HISTORY_DIR.exists():
        for p in HISTORY_DIR.iterdir():
            if p.is_file():
                try:
                    p.unlink()
                except OSError:
                    pass
    COVER_PATH.write_bytes(original_cover_bytes)


def login(page: Page):
    page.goto(BASE_URL + "/login")
    page.locator("#username").fill(USERNAME)
    page.locator("#password").fill(PASSWORD)
    page.get_by_role("button", name=re.compile(r"login", re.I)).first.click()
    # wait until redirected away from login
    page.wait_for_url(lambda url: "/login" not in url, timeout=15000)


def open_cover_editor(page: Page):
    page.goto(BASE_URL + f"/admin/book/{BOOK_ID}")
    expect(page.locator("#detailcover")).to_be_visible()
    edit_btn = page.locator("#edit_cover_btn")
    expect(edit_btn).to_be_visible()
    edit_btn.click()
    modal = page.locator("#coverEditorModal")
    expect(modal).to_be_visible()
    # cropper initializes on shown; wait for cropper container/image readiness
    expect(page.locator("#cover-editor-image")).to_be_visible()
    page.wait_for_timeout(300)
    expect(modal.locator(".cropper-container")).to_be_visible(timeout=10000)
    return modal


def test_open_cover_editor_modal(page: Page):
    login(page)
    modal = open_cover_editor(page)
    expect(modal.locator("#cover-zoom-in")).to_be_visible()
    expect(modal.locator("#cover-zoom-out")).to_be_visible()
    expect(modal.locator("#cover-brightness")).to_be_visible()
    expect(modal.locator("#cover-contrast")).to_be_visible()
    expect(modal.locator("#cover-saturation")).to_be_visible()
    expect(modal.locator("#cover-save-btn")).to_be_visible()
    expect(modal.locator("#cover-history-list")).to_be_visible()


def test_zoom_and_adjustment_controls_update_ui(page: Page):
    login(page)
    modal = open_cover_editor(page)

    # zoom in should change zoom indicator/value
    zoom_val = modal.locator("#cover-zoom-val")
    before = zoom_val.inner_text()
    modal.locator("#cover-zoom-in").click()
    page.wait_for_timeout(200)
    after = zoom_val.inner_text()
    assert after != before

    # adjustments update displayed percentages and apply preview filter
    modal.locator("#cover-brightness").evaluate("(el) => { el.value = '120'; el.dispatchEvent(new Event('input', {bubbles:true})); el.dispatchEvent(new Event('change', {bubbles:true})); }")
    modal.locator("#cover-contrast").evaluate("(el) => { el.value = '80'; el.dispatchEvent(new Event('input', {bubbles:true})); el.dispatchEvent(new Event('change', {bubbles:true})); }")
    modal.locator("#cover-saturation").evaluate("(el) => { el.value = '140'; el.dispatchEvent(new Event('input', {bubbles:true})); el.dispatchEvent(new Event('change', {bubbles:true})); }")
    expect(modal.locator("#cover-brightness-val")).to_have_text("120%")
    expect(modal.locator("#cover-contrast-val")).to_have_text("80%")
    expect(modal.locator("#cover-saturation-val")).to_have_text("140%")

    modal.locator("#cover-reset-adjustments").click()
    expect(modal.locator("#cover-brightness-val")).to_have_text("100%")
    expect(modal.locator("#cover-contrast-val")).to_have_text("100%")
    expect(modal.locator("#cover-saturation-val")).to_have_text("100%")


def test_save_creates_history_and_updates_cover(page: Page, original_cover_bytes):
    login(page)
    modal = open_cover_editor(page)

    before_src = page.locator("#detailcover").get_attribute("src")
    modal.locator("#cover-save-btn").click()

    status = modal.locator("#cover-editor-status")
    expect(status).to_contain_text(re.compile(r"saved", re.I), timeout=15000)
    expect(modal.locator("#cover-history-list .cover-history-item")).to_have_count(1, timeout=10000)

    after_src = page.locator("#detailcover").get_attribute("src")
    assert after_src and before_src != after_src
    assert "c=" in after_src

    # saved cover file still valid and history snapshot exists
    assert COVER_PATH.read_bytes()  # non-empty
    assert any(HISTORY_DIR.iterdir())


def test_restore_and_delete_history_version(page: Page, original_cover_bytes):
    login(page)
    modal = open_cover_editor(page)

    # create first version
    modal.locator("#cover-save-btn").click()
    expect(modal.locator("#cover-editor-status")).to_contain_text(re.compile(r"saved", re.I), timeout=15000)
    expect(modal.locator("#cover-history-list .cover-history-item")).to_have_count(1, timeout=10000)
    first_name = modal.locator("#cover-history-list .cover-history-item").first.get_attribute("data-version")
    assert first_name

    # create second state/version by saving again
    modal.locator("#cover-brightness").evaluate("(el) => { el.value = '130'; el.dispatchEvent(new Event('input', {bubbles:true})); }")
    modal.locator("#cover-save-btn").click()
    expect(modal.locator("#cover-editor-status")).to_contain_text(re.compile(r"saved", re.I), timeout=15000)
    expect(modal.locator("#cover-history-list .cover-history-item")).to_have_count(2, timeout=10000)

    # restore the first version
    first_item = modal.locator(f"#cover-history-list .cover-history-item[data-version='{first_name}']")
    expect(first_item).to_have_count(1)
    first_item.locator(".cover-history-restore").click()
    expect(modal.locator("#cover-editor-status")).to_contain_text(re.compile(r"restor", re.I), timeout=15000)
    # restore snapshots current cover, so history count should grow or remain >=2
    expect(modal.locator("#cover-history-list .cover-history-item")).not_to_have_count(0, timeout=10000)

    # delete one history entry
    items = modal.locator("#cover-history-list .cover-history-item")
    count_before_delete = items.count()
    assert count_before_delete >= 1
    items.first.locator(".cover-history-delete").click()
    page.wait_for_timeout(500)
    expect(modal.locator("#cover-history-list .cover-history-item")).to_have_count(count_before_delete - 1, timeout=10000)
