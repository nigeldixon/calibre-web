# Kobo / NickelMenu launcher for calibre-web chapter marks

These files turn the server endpoint into a one-tap action on the device.

The launcher does **not** call any hypothesis-specific server code. It only:

1. Reads `api_endpoint=` from `.kobo/Kobo/Kobo eReader.conf` to get the calibre-web host and Kobo auth token.
2. Reads the current book from `.kobo/KoboReader.sqlite` (`ContentID` = Calibre UUID, `BookTitle`, `ChapterIDBookmarked`).
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
| calibre-web Kobo sync | The `api_endpoint=` line must already point at calibre-web. Generate it from **Profile → Kobo Sync Token → Create/View**. |
| Book must be synced from calibre-web | Synced books have `ContentID` equal to the Calibre UUID. USB-sideloaded books have a path there and cannot be used with this endpoint. |

## Install

Copy the files with the device mounted over USB:

```
KOBOeReader/
├── .adds/
│   ├── nm/
│   │   └── calibre-web-chapters        <- device/kobo/nickelmenu-config
│   └── calibre-web-chapters/
│       └── add_chapter.sh              <- device/kobo/add_chapter.sh
```

Then safely eject and reboot the device. You should see **Add chapter mark** in the reader overflow menu and in the main menu.

Make sure the script stays executable:

```sh
chmod +x /mnt/onboard/.adds/calibre-web-chapters/add_chapter.sh
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

**Add chapter (debug)** writes `/mnt/onboard/.adds/calibre-web-chapters/debug.txt` containing the parsed endpoint, UUID, title, location, and final URL. Copy that file off the device when reporting an issue.

You can also run it manually over SSH/telnet:

```sh
sh /mnt/onboard/.adds/calibre-web-chapters/add_chapter.sh --debug
cat /mnt/onboard/.adds/calibre-web-chapters/debug.txt
```

Errors are appended to `last-error.log` in the same folder and also shown as a toast.

## Host-side tests

The launcher logic can be exercised on a development machine with stub `sqlite3` and `qndb` executables on `PATH`. Three environment variables redirect the device paths for those tests:

| Variable | Device default |
|---|---|
| `KOBO_ADDON_DIR` | `/mnt/onboard/.adds/calibre-web-chapters` |
| `KOBO_EREADER_CONF` | `/mnt/onboard/.kobo/Kobo/Kobo eReader.conf` |
| `KOBO_READER_DB` | `/mnt/onboard/.kobo/KoboReader.sqlite` |

These overrides are test-only; NickelMenu invokes the script without them, so the standard Kobo paths are used on the device. See `tests/unit/test_kobo_device_launcher.py` for the config-parsing, database, URL-building and error-path coverage.

## Fixed-book shortcut (optional)

If you want a menu item pinned to one book (useful for repeat testing), add a line like this to any file in `.adds/nm/`:

```
menu_item :main :Chapter form (test book) :nickel_browser :modal:http://192.168.1.10:8083/kobo/TOKEN/chapters/UUID?name=Page+12&location=chapter1.xhtml
```

Replace the host/port, `TOKEN`, and `UUID`. A `Quit` entry is recommended on firmware 4.23+ if you ever open the full browser:

```
menu_item :browser :Quit :nickel_misc :home
```

## Notes / caveats

- `ChapterIDBookmarked` is Kobo-generated and often looks like `chapter1.xhtml#kobo.1.1`. The server strips `#kobo.*` / `#point(...)` fragments, so the chapter target is the clean path.
- The title used as `name` prefill is the device's `BookTitle`; it is only a starting value, not a TOC label.
- The modal browser needs Wi-Fi. Opening a URL normally makes Nickel connect automatically.
- If NickelDBus is not installed, the menu item shows a toast and writes to `last-error.log`; nothing is written to the book.
