#!/bin/bash
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

# Route stdout and stderr through the redaction filter so the anisette identity and Apple's
# account record never reach `docker logs`, which persists to disk on the host. See
# docker/redact-log.py.
#
# Process substitution rather than a pipeline, deliberately: `cmd | filter` would make AltServer a
# child of a subshell, so it would no longer be tini's direct child and would not receive SIGTERM
# on `docker stop`, nor would its exit status be the container's. Redirecting first and THEN
# exec'ing keeps AltServer as the exec'd process with its own pid, signals and status -- the
# filter just happens to own the other end of its stdout.
#
# Fail open: if python3 or the filter is missing, log unfiltered rather than not at all. A server
# you cannot debug is worse than a credential in a log you already had.
if [ -x /usr/local/bin/redact-log ] && command -v python3 >/dev/null 2>&1; then
    # --tee also appends to the shared volume so the web UI can show a live view without
    # the Docker socket. Everything written there has already been redacted.
    exec 1> >(exec /usr/local/bin/redact-log --tee /data/altserver.log)
    exec 2>&1
else
    echo "entrypoint: WARNING -- log redaction unavailable; credentials will appear in docker logs" >&2
fi

exec /usr/local/bin/AltServer "$@"
