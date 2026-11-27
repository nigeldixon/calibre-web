#!/bin/sh
# Calibre-Web Kobo chapter-mark page tracker.
#
# Nickel only writes ChapterIDBookmarked when a book is closed. NickelDBus
# exposes rvPageChanged, which this script records so the launcher can work
# out the current chapter while the book is still open.
#
# Install to: /mnt/onboard/.adds/calibre-web-chapters/track_page.sh
# It is started at boot by /etc/init.d/calibre-web-chapters.

ADDON_DIR="${KOBO_ADDON_DIR:-/mnt/onboard/.adds/calibre-web-chapters}"
STATE="$ADDON_DIR/page-state.txt"
PIDFILE="$ADDON_DIR/track_page.pid"
INIT_PIDFILE=/var/run/calibre-web-chapters.pid
LOG="$ADDON_DIR/tracker.log"
SLEEP_SECONDS="${KOBO_TRACKER_SLEEP:-2}"

mkdir -p "$ADDON_DIR" 2>/dev/null || exit 1

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

state_value() {
    [ -f "$STATE" ] || return 0
    sed -n "s/^$1=//p" "$STATE" 2>/dev/null | tail -n 1
}

write_state() {
    tmp="$STATE.tmp"
    {
        printf 'view=%s\n' "$view"
        printf 'page=%s\n' "$page"
        printf 'last_page=%s\n' "$last_page"
        printf 'max_page=%s\n' "$max_page"
        printf 'resets=%s\n' "$resets"
        printf 'updated=%s\n' "$(date '+%s' 2>/dev/null || printf 0)"
    } > "$tmp" 2>/dev/null || return 1
    mv "$tmp" "$STATE" 2>/dev/null || return 1
}

handle_view() {
    new_view=$1
    [ -n "$new_view" ] || return 0
    if [ "$new_view" != "$view" ]; then
        # A fresh ReadingView means a newly opened book. Start its chapter
        # count from the bookmark that was current when the book was opened.
        if [ "$new_view" = "ReadingView" ]; then
            page=
            last_page=
            max_page=
            resets=0
        fi
        view=$new_view
        write_state || log "could not write $STATE"
    fi
}

handle_page() {
    new_page=$1
    case "$new_page" in
        ''|*[!0-9]*) return 0 ;;
    esac
    [ "$new_page" -gt 0 ] || return 0

    # KEPUB page numbering restarts at 1 for each chapter. A large drop is a
    # chapter change; an ordinary backwards page turn is not.
    if [ -n "$last_page" ] && [ "$last_page" -ge 3 ] && [ "$new_page" -le 2 ]; then
        resets=$((resets + 1))
    fi

    page=$new_page
    last_page=$new_page
    if [ -z "$max_page" ] || [ "$new_page" -gt "$max_page" ]; then
        max_page=$new_page
    fi
    write_state || log "could not write $STATE"
}

MODE=${1:-}

# The menu reload entry makes script iterations quick: copy the new file over
# USB, then tap Reload page tracker instead of rebooting.
if [ "$MODE" = "--restart" ]; then
    if [ -f "$PIDFILE" ]; then
        old_pid=$(sed -n '1p' "$PIDFILE" 2>/dev/null)
        if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
            kill "$old_pid" 2>/dev/null || true
            sleep 1
        fi
    fi
    rm -f "$PIDFILE" "$INIT_PIDFILE" 2>/dev/null || true
fi

# A second copy (for example from the manual menu entry) must not create two
# qndb subscriptions with the same state file.
if [ -f "$PIDFILE" ]; then
    old_pid=$(sed -n '1p' "$PIDFILE" 2>/dev/null)
    if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
        exit 0
    fi
fi
printf '%s\n' "$$" > "$PIDFILE" 2>/dev/null || true
trap 'rm -f "$PIDFILE" 2>/dev/null || true' EXIT INT TERM

view=$(state_value view)
page=$(state_value page)
last_page=$(state_value last_page)
max_page=$(state_value max_page)
resets=$(state_value resets)
[ -n "$resets" ] || resets=0

find_qndb
if [ -z "$QNDB" ]; then
    log "qndb not found; page tracker stopped"
    exit 1
fi

log "page tracker started"

# NickelDBus only emits the reader signals after ndbCurrentView has been
# called. Call it before every subscription so a NickelDBus restart cannot
# leave the tracker permanently deaf.
while :; do
    current_view=$("$QNDB" -m ndbCurrentView 2>>"$LOG" | head -n 1)
    handle_view "$current_view"

    # Do not pipe into the read loop: POSIX shells run pipeline elements in
    # subshells, which would discard the reset counter after each event.
    events="$ADDON_DIR/tracker-events.tmp"
    if "$QNDB" -s rvPageChanged -s ndbViewChanged > "$events" 2>>"$LOG"; then
        while IFS= read -r event; do
            case "$event" in
                'rvPageChanged '*)
                    handle_page "${event#rvPageChanged }"
                    ;;
                'ndbViewChanged '*)
                    handle_view "${event#ndbViewChanged }"
                    ;;
            esac
        done < "$events"
    fi
    rm -f "$events" 2>/dev/null || true

    sleep "$SLEEP_SECONDS"
done
