#!/bin/sh
# Build a KoboRoot.tgz that installs the complete calibre-web chapter-mark
# add-on: launcher, page tracker, NickelMenu config, SQLite CLI and init script.
set -eu

repo=$(CDPATH= cd -- "$(dirname "$0")/../.." && pwd)
device="$repo/device/kobo"
output="${KOBO_PACKAGE_OUTPUT:-$device/KoboRoot.tgz}"
stage=$(mktemp -d "${TMPDIR:-/tmp}/calibre-web-kobo.XXXXXX")
trap 'rm -rf "$stage"' EXIT INT TERM

install -d "$stage/etc/init.d"
install -d "$stage/mnt/onboard/.adds/nm"
install -d "$stage/mnt/onboard/.adds/calibre-web-chapters"

install -m 0755 "$device/add_chapter.sh" \
    "$stage/mnt/onboard/.adds/calibre-web-chapters/add_chapter.sh"
install -m 0755 "$device/track_page.sh" \
    "$stage/mnt/onboard/.adds/calibre-web-chapters/track_page.sh"
install -m 0755 "$device/sqlite3-armv7" \
    "$stage/mnt/onboard/.adds/calibre-web-chapters/sqlite3-armv7"
install -m 0644 "$device/nickelmenu-config" \
    "$stage/mnt/onboard/.adds/nm/calibre-web-chapters"
install -m 0755 "$device/init.d-calibre-web-chapters" \
    "$stage/etc/init.d/calibre-web-chapters"

# Kobo extracts KoboRoot.tgz into /. A deterministic archive makes rebuilds
# easy to compare.
tar --sort=name --owner=0 --group=0 --numeric-owner \
    --mtime='UTC 1970-01-01' -czf "$output" -C "$stage" .

printf 'Wrote %s\n' "$output" >&2
