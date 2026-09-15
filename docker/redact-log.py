#!/usr/bin/env python3
"""Filter AltServer's stdout so credentials never reach `docker logs`.

WHY. A successful sign-in prints Apple's full account record and every bearer token -- including
com.apple.gs.icloud.auth with a 31536000-second lifetime -- and every refresh prints the anisette
machine identity: MachineID, the one-time password, the local user ID. All of it goes to stdout,
and Docker's json-file driver writes it to disk on the host, where it accumulates for the life of
the deployment. web/installer.py has always redacted what reaches the BROWSER; nothing redacted
what reaches the log, which is the copy that persists.

ONE IMPLEMENTATION, NOT TWO. The redaction comes from installer.py rather than being reimplemented
here. Two copies of a security filter drift, and the one nobody is looking at is the one that
rots. tests/check_redaction.py covers that function, so it covers this too.

FAIL OPEN, ALWAYS. This sits in the daemon's output path. A filter that crashes and takes the logs
with it is worse than no filter -- you would lose the only diagnostic this project has, and the
"silent failure" trap the rest of the codebase exists to avoid. Every failure mode here degrades
to passing the line through unchanged:

  * installer.py missing or unimportable -> passthrough for every line
  * a line that makes _redact raise       -> that line passes through
  * anything unexpected at the top level  -> passthrough for the rest of the stream

The cost of failing open is a credential in a log you already had. The cost of failing closed is a
server you cannot debug.
"""

import os
import sys

sys.path.insert(0, "/opt/altserver-web")

# Optional second destination, for the web UI's live log view. Passed as --tee <path>; the file
# lives on the volume altserver and altserver-web share, so the UI needs neither the Docker socket
# nor a host bind mount to read it. Everything written here has already been through _redact.
TEE_PATH = None
if "--tee" in sys.argv:
    i = sys.argv.index("--tee")
    if i + 1 < len(sys.argv):
        TEE_PATH = sys.argv[i + 1]

# Bound it. This file is written for the life of the deployment and nothing rotates it; without a
# cap it would do exactly what the unbounded docker logs did before.
TEE_MAX = 1_000_000
TEE_KEEP = 500_000

try:
    from installer import _redact
except Exception as exc:  # noqa: BLE001 -- any import failure must degrade, not abort
    sys.stderr.write("redact-log: installer.py unavailable (%s); logging unfiltered\n" % exc)
    _redact = None


def _open_tee():
    if not TEE_PATH:
        return None
    try:
        return open(TEE_PATH, "a", encoding="utf-8", errors="replace")
    except Exception as exc:
        sys.stderr.write("redact-log: cannot write %s (%s); stdout only\n" % (TEE_PATH, exc))
        return None


def _trim(tee):
    """Keep the tail when the file grows past the cap. Returns a fresh handle, or the old one."""
    try:
        if tee.tell() < TEE_MAX:
            return tee
        tee.close()
        with open(TEE_PATH, "r", encoding="utf-8", errors="replace") as f:
            f.seek(os.path.getsize(TEE_PATH) - TEE_KEEP)
            f.readline()          # drop the partial line the seek landed in
            tail = f.read()
        with open(TEE_PATH, "w", encoding="utf-8") as f:
            f.write(tail)
        return open(TEE_PATH, "a", encoding="utf-8", errors="replace")
    except Exception:
        try:
            return open(TEE_PATH, "a", encoding="utf-8", errors="replace")
        except Exception:
            return None           # tee is best-effort; stdout must keep working


def main():
    out = sys.stdout
    tee = _open_tee()
    n = 0
    for line in sys.stdin:
        line = line.rstrip("\n")
        if _redact is None:
            shown = line
        else:
            try:
                shown = _redact(line)
            except Exception:
                shown = line  # never drop a line because the filter tripped over it
            if shown is None:
                continue  # _redact drops pure noise, e.g. the SRP "Byte:-42" spew
        out.write(shown + "\n")
        out.flush()  # unbuffered: `docker logs -f` must stay live

        if tee is not None:
            try:
                tee.write(shown + "\n")
                tee.flush()
                n += 1
                if n % 200 == 0:
                    tee = _trim(tee)
            except Exception:
                tee = None  # never let the tee break the primary output path


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write("redact-log: filter died (%s); remaining output is unfiltered\n" % exc)
        for line in sys.stdin:
            sys.stdout.write(line)
            sys.stdout.flush()
