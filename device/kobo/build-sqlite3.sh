#!/bin/sh
# Rebuild the statically linked ARMv7 sqlite3 CLI shipped for the Kobo launcher.
#
# Stock Kobo firmware has no sqlite3 command. The resulting binary is copied
# to /mnt/onboard/.adds/calibre-web-chapters/sqlite3-armv7 alongside
# add_chapter.sh. Requires Podman (or change the PODMAN variable to docker).
set -eu

PODMAN=${PODMAN:-podman}
SQLITE_VERSION=${SQLITE_VERSION:-3500400}
SQLITE_YEAR=${SQLITE_YEAR:-2025}
SQLITE_SHA256=${SQLITE_SHA256:-a3db587a1b92ee5ddac2f66b3edb41b26f9c867275782d46c3a088977d6a5b18}
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
OUT="$SCRIPT_DIR/sqlite3-armv7"
BUILD_DIR=$(mktemp -d "${TMPDIR:-/tmp}/cw-kobo-sqlite.XXXXXX")
chmod 0777 "$BUILD_DIR"
trap 'rm -rf "$BUILD_DIR"' EXIT INT TERM

"$PODMAN" run --rm -v "$BUILD_DIR:/out:z" -w /tmp docker.io/library/ubuntu:noble sh -c "
set -eu
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq >/dev/null
apt-get install -y -qq gcc-arm-linux-gnueabihf curl ca-certificates >/dev/null
curl -fsSL https://www.sqlite.org/${SQLITE_YEAR}/sqlite-autoconf-${SQLITE_VERSION}.tar.gz -o sqlite.tar.gz
printf '%s  sqlite.tar.gz\n' ${SQLITE_SHA256} | sha256sum -c -
tar xzf sqlite.tar.gz
cd sqlite-autoconf-${SQLITE_VERSION}
# Static glibc cannot resolve passwd entries on the device; HOME is enough.
sed -i 's/if( (pwent=getpwuid(uid)) != NULL) {/if( 0 \&\& (pwent=getpwuid(uid)) != NULL) {/' shell.c
arm-linux-gnueabihf-gcc -static -Os -s \\
    -DSQLITE_THREADSAFE=0 -DSQLITE_OMIT_LOAD_EXTENSION \\
    shell.c sqlite3.c -o /out/sqlite3-armv7 -ldl -lpthread -lm
"

install -m 0755 "$BUILD_DIR/sqlite3-armv7" "$OUT"
printf 'Built %s\n' "$OUT"
sha256sum "$OUT" || true
