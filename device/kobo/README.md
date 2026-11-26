# Kobo / NickelMenu launcher for calibre-web chapter marks

These files turn the server endpoint into a one-tap action on the device.

The launcher does **not** call any hypothesis-specific server code. It only:

1. Reads `api_endpoint=` from `.kobo/Kobo/Kobo eReader.conf` to get the calibre-web host and Kobo auth token.
2. Reads the current book from `.kobo/KoboReader.sqlite` (`ContentID` = Calibre UUID, `Title`, `ChapterIDBookmarked`).
3. Opens a **modal** Nickel browser at:

   ```
   /kobo/<token>/chapters/<uuid>?name=<title>&location=<current chapter>
   ```

The form is prefilled, so you normally just type the chapter name (e.g. `Page 12`) and press **Save chapter**.

## Prerequisites

| Component | Why |
|---|---|
| NickelMenu | Adds the menu entry. Install `KoboRoot.tgz` into `.kobo/` and reboot. |
| NickelDBus | Provides `qndb` with `bwmOpenBrowser`, used to open a URL built at runtime. Install `KoboRoot.tgz` into `.kobo/` and reboot. |
| Bundled `sqlite3-armv7` | Statically linked SQLite CLI used to read the current book. Stock Kobo firmware has no `sqlite3` command; no extra install is needed. |
| calibre-web Kobo sync | The `api_endpoint=` line must already point at calibre-web. Generate it from **Profile → Kobo Sync Token → Create/View**. |
| Book must be synced from calibre-web | Synced books have `ContentID` equal to the Calibre UUID. USB-sideloaded books have a path there and cannot be used with this endpoint. |

## Install

Copy the files with the device mounted over USB:

```
KOBOeReader/
├── .adds/
│   ├── nm/
│   │   └── config                      <- device/kobo/nickelmenu-config
│   └── calibre-web-chapters/
│       ├── add_chapter.sh              <- device/kobo/add_chapter.sh
│       └── sqlite3-armv7               <- device/kobo/sqlite3-armv7
```

Then safely eject and reboot the device. You should see **Add chapter mark** in the reader overflow menu and in the main menu.

Make sure both files stay executable:

```sh
chmod +x /mnt/onboard/.adds/calibre-web-chapters/add_chapter.sh \
         /mnt/onboard/.adds/calibre-web-chapters/sqlite3-armv7
```

## Use

1. Open the book you want to mark in Nickel.
2. Tap the reader menu → **Add chapter mark**.
3. The modal browser opens with the chapter list and a free-text location prefilled from the device's current chapter bookmark.
4. Enter the chapter name and, for comics, adjust/free-text the location if the listed page is not right.
5. Press **Save chapter**, then close the modal.
6. Run **Sync** on the device. The rewritten EPUB/KEPUB is downloaded via the normal `ChangedEntitlement` flow.

## Verify the device-side detection first

If the URL or prefill looks wrong, run the debug entry:

**Add chapter (debug)** writes `/mnt/onboard/.adds/calibre-web-chapters/debug.txt` containing the device architecture, selected SQLite reader, parsed endpoint, UUID, title, location, and final URL. Copy that file off the device when reporting an issue.

You can also run it manually over SSH/telnet:

```sh
sh /mnt/onboard/.adds/calibre-web-chapters/add_chapter.sh --debug
cat /mnt/onboard/.adds/calibre-web-chapters/debug.txt
```

Errors are appended to `last-error.log` in the same folder and also shown as a toast.

## Host-side tests

The launcher logic can be exercised on a development machine with stub `sqlite3`, `python` and `qndb` executables on `PATH`. These environment variables redirect the device paths and helpers for those tests:

| Variable | Device default |
|---|---|
| `KOBO_ADDON_DIR` | `/mnt/onboard/.adds/calibre-web-chapters` |
| `KOBO_EREADER_CONF` | `/mnt/onboard/.kobo/Kobo/Kobo eReader.conf` |
| `KOBO_READER_DB` | `/mnt/onboard/.kobo/KoboReader.sqlite` |
| `KOBO_SQLITE` | bundled `sqlite3-armv7`, then any system `sqlite3` |
| `KOBO_SQLITE_SYSTEM_PATHS` | `/usr/bin/sqlite3 /usr/local/bin/sqlite3` |

These overrides are test-only; NickelMenu invokes the script without them, so the standard Kobo paths are used on the device. See `tests/unit/test_kobo_device_launcher.py` for the config-parsing, database, URL-building and error-path coverage.

## Rebuilding the bundled SQLite CLI

`sqlite3-armv7` is a stripped, statically linked ARMv7 hard-float build of the public-domain SQLite shell. To rebuild it from source with Podman:

```sh
device/kobo/build-sqlite3.sh
```

The script downloads the pinned SQLite amalgamation, patches out static-glibc `passwd` lookups, and writes `device/kobo/sqlite3-armv7`. The single NickelMenu entry stays dynamic: it reads whichever book is currently open from `KoboReader.sqlite`.

## Notes / caveats

- `ChapterIDBookmarked` is Kobo-generated and often looks like `chapter1.xhtml#kobo.1.1`. The server strips `#kobo.*` / `#point(...)` fragments, so the chapter target is the clean path.
- The title used as `name` prefill is the device's `BookTitle`; it is only a starting value, not a TOC label.
- The modal browser needs Wi-Fi. Opening a URL normally makes Nickel connect automatically.
- If the bundled ARM binary cannot execute on a device, the launcher falls back to any system `sqlite3` or Python `sqlite3` module available.
- If NickelDBus is not installed, the menu item shows a toast and writes to `last-error.log`; nothing is written to the book.
