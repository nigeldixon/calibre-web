#!/bin/sh
# Calibre-Web Kobo chapter-mark launcher.
#
# Reads the current book from the Kobo database and the auth token from the
# configured calibre-web api_endpoint, then opens the chapter form in a modal
# Nickel browser through NickelDBus (qndb).
#
# Install to: /mnt/onboard/.adds/calibre-web-chapters/add_chapter.sh

ADDON_DIR="/mnt/onboard/.adds/calibre-web-chapters"
CONF="/mnt/onboard/.kobo/Kobo/Kobo eReader.conf"
DB="/mnt/onboard/.kobo/KoboReader.sqlite"
LOG="$ADDON_DIR/last-error.log"
DEBUG_LOG="$ADDON_DIR/debug.txt"

mkdir -p "$ADDON_DIR" 2>/dev/null || true

log() {
    printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG" 2>/dev/null || true
}

find_qndb() {
    if command -v qndb >/dev/null 2>&1; then
        QNDB=qndb
    elif [ -x /usr/local/bin/qndb ]; then
        QNDB=/usr/local/bin/qndb
    elif [ -x /usr/bin/qndb ]; then
        QNDB=/usr/bin/qndb
    else
        QNDB=
    fi
}

find_sqlite() {
    if command -v sqlite3 >/dev/null 2>&1; then
        SQLITE=sqlite3
    elif [ -x /usr/bin/sqlite3 ]; then
        SQLITE=/usr/bin/sqlite3
    elif [ -x /usr/local/bin/sqlite3 ]; then
        SQLITE=/usr/local/bin/sqlite3
    else
        SQLITE=
    fi
}

toast() {
    [ -n "$QNDB" ] || return 0
    "$QNDB" -m mwcToast 4000 "$1" >/dev/null 2>&1 || true
}

fail() {
    log "$1"
    toast "$1"
    exit 1
}

# Percent-encode every byte. Over-encoding is valid and avoids locale issues.
urlencode() {
    printf '%s' "$1" | od -An -v -tx1 | tr -d ' \n' | sed 's/../%&/g' | tr 'abcdef' 'ABCDEF'
}

find_qndb
find_sqlite

[ -f "$CONF" ] || fail "Kobo eReader.conf not found at $CONF"
[ -f "$DB" ] || fail "KoboReader.sqlite not found at $DB"
[ -n "$SQLITE" ] || fail "sqlite3 not found on device"

endpoint=$(sed -n 's/^api_endpoint=//p' "$CONF" 2>/dev/null | head -n 1 | tr -d '\r')
[ -n "$endpoint" ] || fail "No api_endpoint in Kobo eReader.conf; sync calibre-web first."

# Keep the http(s) origin plus /kobo/<token>; drop any /v1/... suffix.
base=$(printf '%s\n' "$endpoint" | sed -n 's|\(.*/kobo/[^/?]*\).*|\1|p')
[ -n "$base" ] || fail "api_endpoint is not a calibre-web Kobo URL: $endpoint"

# Prefer the book currently being read; otherwise the most recently opened one.
info=$("$SQLITE" "$DB" \
    "SELECT ContentID || char(10) || COALESCE(BookTitle,'') || char(10) || COALESCE(ChapterIDBookmarked,'') \
     FROM content WHERE ContentType=6 \
     ORDER BY CASE WHEN ReadStatus=1 THEN 0 ELSE 1 END, COALESCE(DateLastRead,'') DESC \
     LIMIT 1;" 2>/dev/null) || fail "Could not read KoboReader.sqlite"

uuid=$(printf '%s\n' "$info" | sed -n '1p')
title=$(printf '%s\n' "$info" | sed -n '2p')
location=$(printf '%s\n' "$info" | sed -n '3p')
[ -n "$uuid" ] || fail "No book found in KoboReader.sqlite."

url="$base/chapters/$uuid?name=$(urlencode "$title")&location=$(urlencode "$location")"

# Debug first, so it also reports books whose ContentID is not a UUID.
if [ "$1" = "--debug" ]; then
    {
        printf 'endpoint: %s\n' "$endpoint"
        printf 'base:     %s\n' "$base"
        printf 'uuid:     %s\n' "$uuid"
        printf 'title:    %s\n' "$title"
        printf 'location: %s\n' "$location"
        printf 'url:      %s\n' "$url"
    } > "$DEBUG_LOG" 2>/dev/null || fail "Could not write $DEBUG_LOG"
    toast "Chapter debug written to debug.txt"
    exit 0
fi

case "$uuid" in
    ????????-????-????-????-????????????) ;;
    *) fail "Current book '$title' was not synced from calibre-web (ContentID=$uuid)." ;;
esac

[ -n "$QNDB" ] || fail "qndb not found; install NickelDBus."

"$QNDB" -m bwmOpenBrowser true "$url" >/dev/null 2>&1 || fail "qndb bwmOpenBrowser failed (is NickelDBus running?)."
toast "Chapter form opened"
exit 0
