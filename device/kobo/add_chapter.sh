#!/bin/sh
# Calibre-Web Kobo chapter-mark launcher.
#
# Reads the current book from the Kobo database and the auth token from the
# configured calibre-web api_endpoint, then opens the chapter form in a modal
# Nickel browser through NickelDBus (qndb).
#
# Install to: /mnt/onboard/.adds/calibre-web-chapters/add_chapter.sh

# The overrides exist only for host-side tests; on the device the standard
# Kobo paths are used.
ADDON_DIR="${KOBO_ADDON_DIR:-/mnt/onboard/.adds/calibre-web-chapters}"
CONF="${KOBO_EREADER_CONF:-/mnt/onboard/.kobo/Kobo/Kobo eReader.conf}"
DB="${KOBO_READER_DB:-/mnt/onboard/.kobo/KoboReader.sqlite}"
SYSTEM_SQLITE_PATHS=${KOBO_SQLITE_SYSTEM_PATHS-/usr/bin/sqlite3 /usr/local/bin/sqlite3}
BUNDLED_SQLITE="$ADDON_DIR/sqlite3-armv7"
LOG="$ADDON_DIR/last-error.log"
DEBUG_LOG="$ADDON_DIR/debug.txt"
STATE_FILE="$ADDON_DIR/page-state.txt"
SQLITE_BIN=
SQLITE_PYTHON=
PYTHON_SQLITE_CODE='import sqlite3, sys
connection = sqlite3.connect(sys.argv[1])
for row in connection.execute(sys.argv[2]):
    values = [u"" if value is None else value for value in row]
    text = u"\n".join(values) + u"\n"
    if sys.version_info[0] < 3:
        text = text.encode("utf-8")
    sys.stdout.write(text)
'

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

command_exists() {
    case "$1" in
        /*) [ -x "$1" ] ;;
        *) command -v "$1" >/dev/null 2>&1 ;;
    esac
}

find_readers() {
    HAVE_READER=

    # Test override first, then the statically linked ARM binary shipped with
    # the launcher. Stock Kobo firmware does not provide a sqlite3 command.
    if [ -n "${KOBO_SQLITE:-}" ] && command_exists "$KOBO_SQLITE"; then
        HAVE_READER=1
        return
    fi
    if command_exists "$BUNDLED_SQLITE"; then
        HAVE_READER=1
        return
    fi
    for candidate in sqlite3 $SYSTEM_SQLITE_PATHS python2 python python3; do
        if command_exists "$candidate"; then
            HAVE_READER=1
            return
        fi
    done
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

state_value() {
    [ -f "$STATE_FILE" ] || return 0
    sed -n "s/^$1=//p" "$STATE_FILE" 2>/dev/null | tail -n 1
}

# The tracker records KEPUB page-number resets. Each reset means Nickel moved
# into the next chapter file, so count forward from the saved bookmark.
resolve_tracked_chapter() {
    bookmark=$(printf '%s' "$2" | sed 's/#.*//')
    chapter_query="SELECT Title || char(10) || COALESCE(CAST(\"___FileOffset\" AS REAL),0) || char(10) || COALESCE(CAST(\"___FileSize\" AS REAL),0) || char(10) || COALESCE(WordCount,0) FROM content WHERE ContentID LIKE '$1%' AND ContentType=9 ORDER BY CAST(\"___FileOffset\" AS REAL), Title;"
    TRACK_BOOKMARK=$bookmark
    TRACK_RESETS=$3
    export TRACK_BOOKMARK TRACK_RESETS
    execute_sql "$chapter_query" 2>/dev/null | awk '
function norm(s) {
    sub(/^OEBPS\//, "", s)
    return s
}
{
    line[NR] = $0
}
END {
    count = 0
    for (i = 1; i <= NR; i += 4) {
        if (line[i] != "") {
            count++
            title[count] = line[i]
        }
    }
    if (count == 0) exit

    target = norm(ENVIRON["TRACK_BOOKMARK"])
    base = 0
    for (i = 1; i <= count; i++) {
        candidate = norm(title[i])
        if (candidate == target || (candidate != "" && index(target, candidate) > 0)) {
            base = i
            break
        }
    }
    if (base == 0) exit

    idx = base + (ENVIRON["TRACK_RESETS"] + 0)
    if (idx < 1) idx = 1
    if (idx > count) idx = count
    print title[idx]
}'
}

query_current_book() {
    READER=
    # A valid reader that fails on the query should not be silently replaced
    # by another implementation. Exit 126/127 means "cannot execute", which is
    # how shells report an ARM binary running on an incompatible device.
    if [ -n "${KOBO_SQLITE:-}" ] && command_exists "$KOBO_SQLITE"; then
        if "$KOBO_SQLITE" "$DB" "$QUERY"; then
            READER=$KOBO_SQLITE
            SQLITE_BIN=$KOBO_SQLITE
            return 0
        else
            status=$?
            [ "$status" -eq 126 ] || [ "$status" -eq 127 ] || return "$status"
        fi
    fi
    if command_exists "$BUNDLED_SQLITE"; then
        if "$BUNDLED_SQLITE" "$DB" "$QUERY"; then
            READER=$BUNDLED_SQLITE
            SQLITE_BIN=$BUNDLED_SQLITE
            return 0
        else
            status=$?
            [ "$status" -eq 126 ] || [ "$status" -eq 127 ] || return "$status"
        fi
    fi
    for candidate in sqlite3 $SYSTEM_SQLITE_PATHS; do
        command_exists "$candidate" || continue
        if "$candidate" "$DB" "$QUERY"; then
            READER=$candidate
            SQLITE_BIN=$candidate
            return 0
        else
            status=$?
            [ "$status" -eq 126 ] || [ "$status" -eq 127 ] || return "$status"
        fi
    done
    for candidate in python2 python python3; do
        command_exists "$candidate" || continue
        if "$candidate" -c "$PYTHON_SQLITE_CODE" "$DB" "$QUERY"; then
            READER="$candidate (python)"
            SQLITE_PYTHON=$candidate
            return 0
        fi
    done
    return 1
}

# Run a diagnostic query using the reader that successfully read the book.
execute_sql() {
    if [ -n "$SQLITE_PYTHON" ]; then
        "$SQLITE_PYTHON" -c "$PYTHON_SQLITE_CODE" "$DB" "$1"
    else
        "$SQLITE_BIN" "$DB" "$1"
    fi
}

MODE=${1:-}

find_qndb
find_readers

[ -f "$CONF" ] || fail "Kobo eReader.conf not found at $CONF"
[ -f "$DB" ] || fail "KoboReader.sqlite not found at $DB"
[ -n "$HAVE_READER" ] || fail "No SQLite reader found; copy sqlite3-armv7 into $ADDON_DIR"

endpoint=$(sed -n 's/^api_endpoint=//p' "$CONF" 2>/dev/null | head -n 1 | tr -d '\r')
[ -n "$endpoint" ] || fail "No api_endpoint in Kobo eReader.conf; sync calibre-web first."

# Keep the http(s) origin plus /kobo/<token>; drop any /v1/... suffix.
base=$(printf '%s\n' "$endpoint" | sed -n 's|\(.*/kobo/[^/?]*\).*|\1|p')
[ -n "$base" ] || fail "api_endpoint is not a calibre-web Kobo URL: $endpoint"

# Kobo stores the book title in Title on the ContentType=6 row; BookTitle is
# populated on chapter rows. Prefer the book being read, then the most recent.
QUERY="SELECT ContentID || char(10) || COALESCE(NULLIF(Title,''),NULLIF(BookTitle,''),'') || char(10) || COALESCE(ChapterIDBookmarked,'') FROM content WHERE ContentType=6 ORDER BY CASE WHEN ReadStatus=1 THEN 0 ELSE 1 END, COALESCE(DateLastRead,'') DESC LIMIT 1;"

QUERY_OUT="$ADDON_DIR/current-book.txt"
: > "$QUERY_OUT" 2>/dev/null || fail "Could not write $QUERY_OUT"
query_current_book > "$QUERY_OUT" 2>/dev/null || fail "Could not read KoboReader.sqlite"
info=$(sed -n '1,$p' "$QUERY_OUT")
rm -f "$QUERY_OUT" 2>/dev/null || true

uuid=$(printf '%s\n' "$info" | sed -n '1p' | tr -d '\r')
title=$(printf '%s\n' "$info" | sed -n '2p' | tr -d '\r')
location=$(printf '%s\n' "$info" | sed -n '3p' | tr -d '\r')
[ -n "$uuid" ] || fail "No book found in KoboReader.sqlite."

# Prefer a live page-tracker result over Kobo's stale bookmark. Only use a
# reading-session state that is recent, which avoids inheriting a position
# from the last time the device was used.
tracker_view=$(state_value view)
tracker_page=$(state_value page)
tracker_resets=$(state_value resets)
tracker_updated=$(state_value updated)
case "$tracker_page" in ''|*[!0-9]*) tracker_page= ;; esac
case "$tracker_resets" in ''|*[!0-9]*) tracker_resets=0 ;; esac
case "$tracker_updated" in ''|*[!0-9]*) tracker_updated= ;; esac
if [ "$tracker_view" = "ReadingView" ] && [ -n "$tracker_page" ] \
        && [ "$tracker_page" -gt 0 ] && [ -n "$tracker_updated" ]; then
    now=$(date '+%s' 2>/dev/null || printf 0)
    case "$now" in
        ''|*[!0-9]*) now=0 ;;
    esac
    age=$((now - tracker_updated))
    if [ "$age" -ge 0 ] && [ "$age" -le 300 ]; then
        case "$uuid" in
            ????????-????-????-????-????????????)
                tracked=$(resolve_tracked_chapter "$uuid" "$location" "$tracker_resets")
                [ -n "$tracked" ] && location=$tracked
                ;;
        esac
    fi
fi

url="$base/chapters/$uuid?name=$(urlencode "$title")&location=$(urlencode "$location")"

# Debug first, so it also reports books whose ContentID is not a UUID.
if [ "$MODE" = "--debug" ]; then
    {
        printf 'arch:     %s\n' "$(uname -m 2>/dev/null || printf unknown)"
        printf 'reader:   %s\n' "$READER"
        printf 'endpoint: %s\n' "$endpoint"
        printf 'base:     %s\n' "$base"
        printf 'uuid:     %s\n' "$uuid"
        printf 'title:    %s\n' "$title"
        printf 'location: %s\n' "$location"
        printf 'url:      %s\n' "$url"
        printf 'tracker:  view=%s page=%s resets=%s updated=%s\n' \
            "$tracker_view" "$tracker_page" "$tracker_resets" "$tracker_updated"
    } > "$DEBUG_LOG" 2>/dev/null || fail "Could not write $DEBUG_LOG"

    DIAG_QUERY=$(cat <<SQL
SELECT ContentID, ContentType, Title, BookTitle, ChapterIDBookmarked,
       "___NumPages" AS NumPages, WordCount,
       "___FileOffset" AS FileOffset, "___FileSize" AS FileSize,
       "___PercentRead" AS PercentRead, CurrentChapterProgress AS ChapterProgress
FROM content
WHERE ContentID LIKE '$uuid%'
ORDER BY ContentType, "___FileOffset";
SQL
)
    {
        if [ -n "$QNDB" ]; then
            printf 'firmware: %s\n' "$("$QNDB" -m ndbFirmwareVersion 2>/dev/null | head -n 1)"
        fi
        printf '\n=== content columns ===\n'
        execute_sql "PRAGMA table_info(content);" 2>/dev/null |
            grep -Ei 'NumPages|WordCount|FileOffset|FileSize|PercentRead|ChapterProgress|ChapterIDBookmarked|ContentID|ContentType' || true
        printf '\n=== book/chapter rows ===\n'
        execute_sql "$DIAG_QUERY" 2>/dev/null || true
    } >> "$DEBUG_LOG" 2>/dev/null || true

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
