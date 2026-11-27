# Kobo / NickelMenu launcher for calibre-web chapter marks

These files turn the server endpoint into a one-tap action on the device.

The launcher does **not** call any hypothesis-specific server code. It only:

1. Reads `api_endpoint=` from `.kobo/Kobo/Kobo eReader.conf` to get the calibre-web host and Kobo auth token.
2. Reads the current book from `.kobo/KoboReader.sqlite` (`ContentID` = Calibre UUID, `Title`, `ChapterIDBookmarked`).
3. If the page tracker has a fresh reading position, uses that instead of Kobo's stale bookmark.
4. Opens a **modal** Nickel browser at:

   ```
   /kobo/<token>/chapters/<uuid>?name=<title>&location=<current chapter>
   ```

The form is prefilled, so you normally just type the chapter name (e.g. `Page 12`) and press **Save chapter**.

## Prerequisites

| Component | Why |
|---|---|
| NickelMenu | Adds the menu entry. Install `KoboRoot.tgz` into `.kobo/` and reboot. |
| NickelDBus | Provides `qndb` with `bwmOpenBrowser` and `rvPageChanged`. Install `KoboRoot.tgz` into `.kobo/` and reboot. |
| Bundled `sqlite3-armv7` | Statically linked SQLite CLI used to read the current book. Stock Kobo firmware has no `sqlite3` command; no extra install is needed. |
| calibre-web Kobo sync | The `api_endpoint=` line must already point at calibre-web. Generate it from **Profile → Kobo Sync Token → Create/View**. |
| Book must be synced from calibre-web | Synced books have `ContentID` equal to the Calibre UUID. USB-sideloaded books have a path there and cannot be used with this endpoint. |

## Install the complete package

From the repo root:

```sh
device/kobo/build-kobo-root.sh
```

This writes `device/kobo/KoboRoot.tgz`. Copy that file to the Kobo as:

```text
KOBOeReader/.kobo/KoboRoot.tgz
```

Safely eject and reboot. The package installs:

```text
etc/init.d/calibre-web-chapters                         <- starts the tracker at boot
mnt/onboard/.adds/nm/calibre-web-chapters               <- NickelMenu config
mnt/onboard/.adds/calibre-web-chapters/add_chapter.sh   <- launcher
mnt/onboard/.adds/calibre-web-chapters/track_page.sh    <- page tracker
mnt/onboard/.adds/calibre-web-chapters/sqlite3-armv7    <- bundled SQLite CLI
```

A `KoboRoot.tgz` overwrites the files it contains. Runtime files such as `debug.txt`, `last-error.log`, `tracker.log`, and `page-state.txt` are **not** included, so they survive redeploys. The NickelMenu filename is unique, so other NickelMenu config files are not overwritten.

## Visible menu

The default config keeps the menu minimal:

- Reader overflow menu → **Add chapter mark**
- Browser menu → **Quit** (needed on firmware 4.23+ to leave the browser)

Diagnostic entries are present but commented out in `.adds/nm/calibre-web-chapters`. Uncomment and reboot to use them:

- Add chapter mark from the main menu (uses the most recently read book).
- Add chapter debug / reader debug (write `debug.txt` without opening the browser).
- Start chapter tracker (normally unnecessary; the init script starts it at boot).
- Reload page tracker (useful after copying a new `track_page.sh`).

## Manual install / quick iteration

If you are iterating on scripts, you can copy individual files over USB instead of rebuilding the package each time:

```text
KOBOeReader/
├── .adds/
│   ├── nm/
│   │   └── calibre-web-chapters        <- device/kobo/nickelmenu-config
│   └── calibre-web-chapters/
│       ├── add_chapter.sh              <- device/kobo/add_chapter.sh
│       ├── track_page.sh               <- device/kobo/track_page.sh
│       └── sqlite3-armv7               <- device/kobo/sqlite3-armv7
```

Keep the scripts executable:

```sh
chmod +x /mnt/onboard/.adds/calibre-web-chapters/add_chapter.sh \
         /mnt/onboard/.adds/calibre-web-chapters/track_page.sh \
         /mnt/onboard/.adds/calibre-web-chapters/sqlite3-armv7
```

Then safely eject and reboot so NickelMenu and the init script pick up any config/init changes. To pick up only a new `track_page.sh`, uncomment **Reload page tracker** once, or use the package install.

## Use

1. Open the book you want to mark in Nickel. The tracker starts automatically at boot.
2. Read to the place you want to mark. The tracker records page changes while the book is open.
3. Tap the reader menu → **Add chapter mark**.
4. The modal browser opens with the chapter list and a free-text location prefilled from the tracker (or from Kobo's bookmark when no tracker state is available).
5. Enter the chapter name and, if the listed page is not right, adjust the free-text location.
6. Press **Save chapter**, then close the modal.
7. Run **Sync** on the device. The rewritten EPUB/KEPUB is downloaded via the normal `ChangedEntitlement` flow.

## Verify device-side detection

No SSH is needed. Mount the Kobo over USB and inspect:

```text
.adds/calibre-web-chapters/page-state.txt
```

While reading, it should contain something like:

```text
view=ReadingView
page=14
last_page=14
max_page=14
resets=1
updated=1764223800
```

A page change updates `page`. A KEPUB chapter change normally resets the page number, which increments `resets`. The launcher combines that with `KoboReader.sqlite` to choose the chapter file.

For more detail, uncomment one of the debug menu entries, reboot, and run it. **Add chapter (debug)** writes `/mnt/onboard/.adds/calibre-web-chapters/debug.txt` containing the device architecture, selected SQLite reader, parsed endpoint, UUID, title, final URL, and tracker state. Copy that file off the device when reporting an issue.

You can also run the launcher manually over SSH/telnet:

```sh
sh /mnt/onboard/.adds/calibre-web-chapters/add_chapter.sh --debug
cat /mnt/onboard/.adds/calibre-web-chapters/debug.txt
```

Errors are appended to `last-error.log` in the same folder and also shown as a toast. Tracker errors go to `tracker.log`.

## Host-side tests

The launcher and tracker logic can be exercised on a development machine with stub `sqlite3`, `python` and `qndb` executables on PATH. These environment variables redirect device paths and helpers for those tests:

| Variable | Device default |
|---|---|
| `KOBO_ADDON_DIR` | `/mnt/onboard/.adds/calibre-web-chapters` |
| `KOBO_EREADER_CONF` | `/mnt/onboard/.kobo/Kobo/Kobo eReader.conf` |
| `KOBO_READER_DB` | `/mnt/onboard/.kobo/KoboReader.sqlite` |
| `KOBO_SQLITE` | bundled `sqlite3-armv7`, then any system `sqlite3` |
| `KOBO_SQLITE_SYSTEM_PATHS` | `/usr/bin/sqlite3 /usr/local/bin/sqlite3` |
| `KOBO_TRACKER_SLEEP` | `2` seconds between signal subscriptions |
| `KOBO_PACKAGE_OUTPUT` | `device/kobo/KoboRoot.tgz` |

These overrides are test-only; NickelMenu invokes the scripts without them, so the standard Kobo paths are used on the device. See `tests/unit/test_kobo_device_launcher.py` for config parsing, database reader selection, tracker state, URL building, package layout and error-path coverage.

## Rebuilding the bundled SQLite CLI

`sqlite3-armv7` is a stripped, statically linked ARMv7 hard-float build of the public-domain SQLite shell. To rebuild it from source with Podman:

```sh
device/kobo/build-sqlite3.sh
```

The script downloads the pinned SQLite amalgamation, patches out static-glibc `passwd` lookups, and writes `device/kobo/sqlite3-armv7`. The single NickelMenu entry stays dynamic: it reads whichever book is currently open from `KoboReader.sqlite`.

## Notes / caveats

- Nickel does not write the reading position to `KoboReader.sqlite` on page turns. The page tracker fills that gap using NickelDBus `rvPageChanged`.
- On firmware 4.46, `___NumPages` is `-1`; the tracker therefore uses page-number resets for KEPUB chapter detection. Backwards jumps or very short chapters can leave the estimate wrong. The form's dropdown and free-text location are there to correct it.
- If no fresh tracker state exists, the launcher falls back to `ChapterIDBookmarked`, which may be stale until the book is closed.
- `ChapterIDBookmarked` is Kobo-generated and often looks like `chapter1.xhtml#kobo.1.1`. The server strips `#kobo.*` / `#point(...)` fragments, so the chapter target is the clean path.
- The title used as `name` prefill is the device's book title; it is only a starting value, not a TOC label.
- The modal browser needs Wi-Fi. Opening a URL normally makes Nickel connect automatically.
- If the bundled ARM binary cannot execute on a device, the launcher falls back to any system `sqlite3` or Python `sqlite3` module available.
- If NickelDBus is not installed, the menu item shows a toast and writes to `last-error.log`; nothing is written to the book.
