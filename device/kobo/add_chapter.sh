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
PYTHON_SQLITE_EXEC_CODE='import sqlite3, sys
connection = sqlite3.connect(sys.argv[1])
connection.executescript(sys.argv[2])
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

# Small HTTP GET helper for the launcher's two server calls.
http_get() {
    url=$1
    if command_exists wget; then
        wget -q -O - "$url" 2>>"$LOG"
    elif command_exists /usr/bin/wget; then
        /usr/bin/wget -q -O - "$url" 2>>"$LOG"
    elif command_exists /bin/wget; then
        /bin/wget -q -O - "$url" 2>>"$LOG"
    elif command_exists busybox; then
        busybox wget -q -O - "$url" 2>>"$LOG"
    else
        return 127
    fi
}

# Ask the server whether this book is served from this library. This is the
# device-side allow-list: it avoids opening a browser page that would only
# report "Book not found". Returns 0 served, 1 not served, 2 unreachable.
book_is_served() {
    check_url="$1"
    case "$check_url" in
        *\?*) check_url="$check_url&check=1" ;;
        *) check_url="$check_url?check=1" ;;
    esac

    response=$(http_get "$check_url")
    status=$?
    if [ "$status" -eq 127 ]; then
        # No usable HTTP client: fall back to opening the form directly.
        return 0
    fi
    [ "$status" -eq 0 ] || return 2
    case "$response" in
        *'"served":true'*|*'"served": true'*) return 0 ;;
        *) return 1 ;;
    esac
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

execute_sql_write() {
    if [ -n "$SQLITE_PYTHON" ]; then
        "$SQLITE_PYTHON" -c "$PYTHON_SQLITE_EXEC_CODE" "$DB" "$1"
    else
        "$SQLITE_BIN" "$DB" "$1"
    fi
}

sql_quote() {
    printf '%s' "$1" | sed "s/'/''/g"
}

new_client_id() {
    now=$(date '+%s' 2>/dev/null || printf 0)
    case "$now" in ''|*[!0-9]*) now=0 ;; esac
    random=$(od -An -N4 -tx4 /dev/urandom 2>/dev/null | tr -d ' \n')
    case "$random" in ''|*[!0-9A-Fa-f]*) random=$$ ;; esac
    printf 'cw%s-%s-%s' "$now" "$$" "$random"
}

# Poll the one-shot result marker written by a successful form POST. The
# launcher stays alive in the background while the modal browser is open.
poll_chapter_result() {
    result_url=$1
    attempts=${KOBO_RESULT_ATTEMPTS-150}
    interval=${KOBO_RESULT_INTERVAL-2}
    case "$attempts" in ''|*[!0-9]*) attempts=150 ;; esac
    case "$interval" in ''|*[!0-9]*) interval=2 ;; esac

    attempt=0
    while [ "$attempt" -lt "$attempts" ]; do
        response=$(http_get "$result_url")
        status=$?
        if [ "$status" -eq 0 ]; then
            saved=$(printf '%s\n' "$response" | sed -n 's/^saved=//p' | tail -n 1)
            if [ "$saved" = "1" ]; then
                CHAPTER_SAVE_NAME=$(
                    printf '%s\n' "$response" | sed -n 's/^name=//p' | tail -n 1)
                CHAPTER_SAVE_LOCATION=$(
                    printf '%s\n' "$response" | sed -n 's/^location=//p' | tail -n 1)
                return 0
            fi
        fi
        attempt=$((attempt + 1))
        if [ "$attempt" -lt "$attempts" ]; then
            sleep "$interval" >/dev/null 2>&1 || true
        fi
    done
    return 1
}

# Add a TOC row to the copied device database. The downloaded book file is
# deliberately untouched: the server rewrite remains the durable source of
# truth, and the next sync converges with this additive local row.
insert_local_chapter() {
    book_uuid=$1
    book_title=$2
    chapter_name=$3
    chapter_location=$4

    uuid_sql=$(sql_quote "$book_uuid")
    title_sql=$(sql_quote "$book_title")
    name_sql=$(sql_quote "$chapter_name")
    location_sql=$(sql_quote "$chapter_location")

    chapter_query="SELECT COALESCE(ContentID,'') || char(10) || COALESCE(CAST(ContentType AS TEXT),'') || char(10) || COALESCE(MimeType,'') || char(10) || COALESCE(BookID,'') || char(10) || COALESCE(BookTitle,'') || char(10) || COALESCE(CAST(VolumeIndex AS TEXT),'0') || char(10) || COALESCE(CAST(Depth AS TEXT),'0') || char(10) || COALESCE(ChapterIDBookmarked,'') || char(10) || COALESCE(adobe_location,'') FROM content WHERE ContentID LIKE '$uuid_sql%' ORDER BY CAST(VolumeIndex AS INTEGER), ContentID;"
    rows=$(execute_sql "$chapter_query" 2>>"$LOG") || return 1
    [ -n "$rows" ] || return 1

    LOCAL_TARGET=$chapter_location
    export LOCAL_TARGET
    match=$(printf '%s\n' "$rows" | awk '
function norm(s) {
    sub(/^file:\/\//, "", s)
    sub(/^OEBPS\//, "", s)
    sub(/^.*!/, "", s)
    sub(/^.*\)/, "", s)
    sub(/#.*$/, "", s)
    sub(/-[0-9]+$/, "", s)
    return s
}
function baseid(s) {
    sub(/-[0-9]+$/, "", s)
    return s
}
function newid(id, idx, out) {
    if (match(id, /#\([0-9]+\)/)) {
        out = substr(id, 1, RSTART - 1) "#(" idx ")" substr(id, RSTART + RLENGTH)
    } else if (match(id, /-[0-9]+$/)) {
        out = substr(id, 1, RSTART - 1) "-" idx
    } else {
        out = id "-" idx
    }
    return out
}
BEGIN { target = norm(ENVIRON["LOCAL_TARGET"]); bestscore = -1 }
{
    line[NR] = $0
}
END {
    count = int(NR / 9)
    if (count < 1) exit 1

    preferred = 9
    for (i = 1; i <= count; i++) {
        if (line[(i - 1) * 9 + 2] + 0 == 899) {
            preferred = 899
            break
        }
    }

    maxvol = 0
    for (i = 1; i <= count; i++) {
        if (line[(i - 1) * 9 + 2] + 0 == preferred) {
            vol = line[(i - 1) * 9 + 6] + 0
            if (vol > maxvol) maxvol = vol
        }
    }
    idx = maxvol + 1

    bookmark = ""
    for (i = 1; i <= count; i++) {
        id = line[(i - 1) * 9 + 1]
        if (line[(i - 1) * 9 + 2] + 0 == 9 && norm(id) == target) {
            bookmark = id
            break
        }
    }

    for (i = 1; i <= count; i++) {
        offset = (i - 1) * 9
        id = line[offset + 1]
        type = line[offset + 2] + 0
        nid = norm(id)
        score = 0
        if (nid == target) score = 100
        else if (target != "" && index(nid, target) > 0) score = 50
        else if (nid != "" && index(target, nid) > 0) score = 40
        if (type == 899) score += 20
        if (score > bestscore && score > 0) {
            bestscore = score
            bestid = id
            bestmime = line[offset + 3]
            bestdepth = line[offset + 7] + 0
            bestbm = line[offset + 8]
        }
    }

    if (bestid == "") {
        for (i = 1; i <= count; i++) {
            offset = (i - 1) * 9
            if (line[offset + 2] + 0 == preferred) {
                bestid = line[offset + 1]
                bestmime = line[offset + 3]
                bestdepth = line[offset + 7] + 0
                bestbm = line[offset + 8]
                break
            }
        }
    }
    if (bestid == "") exit 1

    if (bestbm != "") bookmark = bestbm
    else if (bookmark == "") bookmark = baseid(bestid)

    kepub = (index(bestid, "!") > 0) ? 1 : 0
    if (bestmime == "")
        bestmime = kepub ? "application/x-kobo-epub+zip" : "application/epub+zip"
    if (bestdepth < 1) bestdepth = 1

    if (norm(bestid) == target || (target != "" && index(norm(bestid), target) > 0)) {
        new = newid(bestid, idx)
    } else if (kepub) {
        bang1 = index(bestid, "!")
        rest = substr(bestid, bang1 + 1)
        bang2 = index(rest, "!")
        prefix = substr(bestid, 1, bang1 + bang2)
        new = prefix ENVIRON["LOCAL_TARGET"]
    } else if (match(bestid, /#\([0-9]+\)/)) {
        prefix = substr(bestid, 1, RSTART - 1)
        new = prefix "#(" idx ")" ENVIRON["LOCAL_TARGET"]
    } else {
        new = baseid(bestid) "-" idx
    }

    print new
    print bestmime
    print idx
    print bestdepth
    print bookmark
    print kepub
}') || return 1

    new_id=$(printf '%s\n' "$match" | sed -n '1p')
    mime=$(printf '%s\n' "$match" | sed -n '2p')
    index_value=$(printf '%s\n' "$match" | sed -n '3p')
    depth=$(printf '%s\n' "$match" | sed -n '4p')
    bookmark=$(printf '%s\n' "$match" | sed -n '5p')
    kepub=$(printf '%s\n' "$match" | sed -n '6p')
    [ -n "$new_id" ] || return 1
    case "$index_value" in ''|*[!0-9]*) index_value=1 ;; esac
    case "$depth" in ''|*[!0-9]*) depth=1 ;; esac

    new_id_sql=$(sql_quote "$new_id")
    mime_sql=$(sql_quote "$mime")
    bookmark_sql=$(sql_quote "$bookmark")
    if [ "$kepub" = "1" ]; then
        adobe_sql=NULL
    else
        adobe_sql="'$location_sql'"
    fi

    insert_sql="BEGIN;
INSERT INTO content (ContentID, ContentType, MimeType, BookID, BookTitle, Title, Attribution, adobe_location, IsEncrypted, FirstTimeReading, ParagraphBookmarked, BookmarkWordOffset, VolumeIndex, ___NumPages, ReadStatus, ___UserID, ___FileOffset, ___FileSize, ___PercentRead, Depth, ChapterIDBookmarked)
VALUES ('$new_id_sql', 899, '$mime_sql', '$uuid_sql', '$title_sql', '$name_sql', NULL, $adobe_sql, 'false', 'true', 0, 0, $index_value, -1, 0, '', 0, 0, 0, $depth, NULLIF('$bookmark_sql',''));
UPDATE content SET NumShortcovers = COALESCE(NumShortcovers, 0) + 1 WHERE ContentID = '$uuid_sql' AND ContentType = 6;
COMMIT;"
    execute_sql_write "$insert_sql" 2>>"$LOG"
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

# A fresh tracker state records the book UUID when the reader opened. That is
# more reliable than ReadStatus/DateLastRead, which can point at an old book.
tracker_view=$(state_value view)
tracker_page=$(state_value page)
tracker_uuid=$(state_value uuid)
tracker_resets=$(state_value resets)
tracker_updated=$(state_value updated)
case "$tracker_page" in ''|*[!0-9]*) tracker_page= ;; esac
case "$tracker_resets" in ''|*[!0-9]*) tracker_resets=0 ;; esac
case "$tracker_updated" in ''|*[!0-9]*) tracker_updated= ;; esac

TRACKER_FRESH=
if [ "$tracker_view" = "ReadingView" ] && [ -n "$tracker_updated" ]; then
    case "$tracker_uuid" in
        ????????-????-????-????-????????????)
            now=$(date '+%s' 2>/dev/null || printf 0)
            case "$now" in ''|*[!0-9]*) now=0 ;; esac
            age=$((now - tracker_updated))
            if [ "$age" -ge 0 ] && [ "$age" -le 300 ]; then
                TRACKER_FRESH=1
            fi
            ;;
    esac
fi

# Kobo stores the book title in Title on the ContentType=6 row; BookTitle is
# populated on chapter rows. Query the tracked UUID when it is fresh.
if [ -n "$TRACKER_FRESH" ]; then
    QUERY="SELECT ContentID || char(10) || COALESCE(NULLIF(Title,''),NULLIF(BookTitle,''),'') || char(10) || COALESCE(ChapterIDBookmarked,'') FROM content WHERE ContentType=6 AND ContentID LIKE '$tracker_uuid%' ORDER BY COALESCE(DateLastRead,'') DESC LIMIT 1;"
else
    QUERY="SELECT ContentID || char(10) || COALESCE(NULLIF(Title,''),NULLIF(BookTitle,''),'') || char(10) || COALESCE(ChapterIDBookmarked,'') FROM content WHERE ContentType=6 ORDER BY CASE WHEN ReadStatus=1 THEN 0 ELSE 1 END, COALESCE(DateLastRead,'') DESC LIMIT 1;"
fi

QUERY_OUT="$ADDON_DIR/current-book.txt"
: > "$QUERY_OUT" 2>/dev/null || fail "Could not write $QUERY_OUT"
query_current_book > "$QUERY_OUT" 2>/dev/null || fail "Could not read KoboReader.sqlite"
info=$(sed -n '1,$p' "$QUERY_OUT")
rm -f "$QUERY_OUT" 2>/dev/null || true

uuid=$(printf '%s\n' "$info" | sed -n '1p' | tr -d '\r')
title=$(printf '%s\n' "$info" | sed -n '2p' | tr -d '\r')
location=$(printf '%s\n' "$info" | sed -n '3p' | tr -d '\r')
[ -n "$uuid" ] || fail "No book found in KoboReader.sqlite."
if [ -n "$TRACKER_FRESH" ]; then
    case "$uuid" in
        "$tracker_uuid"|"$tracker_uuid"*) ;;
        *) fail "Tracked book $tracker_uuid was not found in KoboReader.sqlite." ;;
    esac
fi

# Prefer the tracker's chapter count over Kobo's stale bookmark.
if [ -n "$TRACKER_FRESH" ]; then
    tracked=$(resolve_tracked_chapter "$uuid" "$location" "$tracker_resets")
    [ -n "$tracked" ] && location=$tracked
fi

client=$(new_client_id)
url="$base/chapters/$uuid?name=$(urlencode "$title")&location=$(urlencode "$location")&client=$client"
result_url="$base/chapters/$uuid/result?client=$client"

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
        printf 'client:   %s\n' "$client"
        printf 'url:      %s\n' "$url"
        printf 'tracker:  view=%s page=%s uuid=%s resets=%s updated=%s\n' \
            "$tracker_view" "$tracker_page" "$tracker_uuid" "$tracker_resets" "$tracker_updated"
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

# Ask calibre-web whether this UUID is served by this library before opening
# the browser. A missing HTTP client falls back to opening the form directly.
check_status=0
book_is_served "$url" || check_status=$?
case "$check_status" in
    0) ;;
    1) fail "Current book '$title' is not in this calibre-web library; sync it from calibre-web first." ;;
    2) fail "Could not contact calibre-web; check Wi-Fi and the sync token." ;;
esac

[ -n "$QNDB" ] || fail "qndb not found; install NickelDBus."

"$QNDB" -m bwmOpenBrowser true "$url" >/dev/null 2>&1 || fail "qndb bwmOpenBrowser failed (is NickelDBus running?)."
toast "Chapter form opened"

# Stay alive long enough for the form to be submitted. A successful save is
# reported through a one-shot marker and added to the device database here,
# so Nickel sees the new chapter without waiting for a sync.
if poll_chapter_result "$result_url"; then
    if insert_local_chapter "$uuid" "$title" \
            "$CHAPTER_SAVE_NAME" "$CHAPTER_SAVE_LOCATION"; then
        toast "Chapter mark added to reader"
    else
        log "Chapter was saved, but the local KoboReader.sqlite update failed"
        toast "Saved, but reader chapter list could not be updated"
    fi
fi
exit 0
