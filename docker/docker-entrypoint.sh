#!/bin/sh
# Keeps /data/AltStore.ipa current, then hands off to AltServer.
#
# Deliberately NON-FATAL: a failed refresh must not stop the server from starting. An existing IPA
# is fine, and even without one the daemon still serves refreshes for apps already installed --
# only a first-time install needs the file. Exiting here would turn a transient network blip into
# an outage.
#
# Set ALTSTORE_SKIP_FETCH=1 to leave the file alone entirely.
set -e

if [ "${ALTSTORE_SKIP_FETCH:-}" = "1" ]; then
    echo "entrypoint: ALTSTORE_SKIP_FETCH=1, not touching ${ALTSTORE_IPA_PATH:-/data/AltStore.ipa}"
else
    if ! /usr/local/bin/fetch-altstore --dest "${ALTSTORE_IPA_PATH:-/data/AltStore.ipa}"; then
        echo "entrypoint: WARNING -- could not refresh the AltStore IPA; continuing anyway." >&2
        echo "entrypoint: a first-time install needs it; refreshes of installed apps do not." >&2
    fi
fi

exec /usr/local/bin/AltServer "$@"
