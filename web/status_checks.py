"""Health checks for an AltServer-Linux deployment.

Stdlib only, deliberately: python3 is already a hard runtime requirement of this project (the
-static binary cannot dlopen Bonjour, so it shells out to python3 to do it), which means adding
these checks costs no new dependency.

Every check here corresponds to a failure this project can produce SILENTLY. That is the whole
point -- AltServer cannot report its own health:

  * DNSServiceRegister returned success unconditionally until we fixed it, and even now avahi can
    report success while publishing nothing, so the only trustworthy test is an external browse.
  * A background refresh that finds no server is suppressed by AltStore itself
    (BackgroundRefreshAppsOperation sets ignoresServerNotFoundError = true), so the phone stays
    quiet too.
  * `journalctl -p err` is empty no matter what breaks, because essentially everything is written
    to stdout at info level.

So the first symptom of a broken deployment is an app that will not open, a week later. These
checks exist to turn that into something visible.
"""

import json
import os
import shutil
import socket
import subprocess
import urllib.error
import urllib.request

# The exact contract src/AnisetteDataManager.cpp enforces. Casing is inconsistent upstream and
# matched case-sensitively: X-MMe-Client-Info has a capital MM, X-Mme-Device-Id a lowercase m.
ANISETTE_REQUIRED_KEYS = [
    "X-Apple-I-MD-M",
    "X-Apple-I-MD",
    "X-Apple-I-MD-LU",
    "X-Apple-I-MD-RINFO",
    "X-Mme-Device-Id",
    "X-Apple-I-SRL-NO",
    "X-MMe-Client-Info",
    "X-Apple-I-Client-Time",
    "X-Apple-Locale",
    "X-Apple-I-TimeZone",
]

OK, WARN, FAIL, UNKNOWN = "ok", "warn", "fail", "unknown"


def _result(name, state, summary, detail=None, fix=None):
    return {"name": name, "state": state, "summary": summary, "detail": detail or "", "fix": fix or ""}


def _run(cmd, timeout=10):
    """Run a command, returning (rc, stdout+stderr). Never raises."""
    if shutil.which(cmd[0]) is None:
        return None, "%s is not installed" % cmd[0]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return None, "%s timed out after %ss" % (cmd[0], timeout)
    except Exception as exc:  # pragma: no cover - defensive
        return None, "%s could not be run: %s" % (cmd[0], exc)


def check_anisette(url=None):
    """Fetch anisette data and validate it against the client's actual contract."""
    url = url or os.environ.get("ALTSERVER_ANISETTE_SERVER", "")

    if not url:
        return _result("Anisette server", FAIL, "ALTSERVER_ANISETTE_SERVER is not set",
                       "There is no default; the server that used to be hardcoded is dead.",
                       "Set it to a full URL including the scheme, e.g. http://127.0.0.1:6969")

    if not url.startswith(("http://", "https://")):
        return _result("Anisette server", FAIL, "URL has no http:// or https:// scheme",
                       "Configured as %r." % url,
                       "AltServer's HTTP client constructor rejects a scheme-less URL before "
                       "sending anything. Use e.g. http://127.0.0.1:6969")

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Xcode"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            status = resp.status
            body = resp.read(65536).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return _result("Anisette server", FAIL, "HTTP %s from %s" % (exc.code, url),
                       "Server is reachable but unhealthy.",
                       "Check the anisette container's logs.")
    except Exception as exc:
        return _result("Anisette server", FAIL, "Cannot reach %s" % url, str(exc),
                       "Is the anisette container running? Check `docker ps`.")

    if status != 200:
        return _result("Anisette server", FAIL, "HTTP %s" % status, body[:200],
                       "AltServer requires exactly 200.")

    try:
        data = json.loads(body)
    except ValueError:
        return _result("Anisette server", FAIL, "Response is not JSON", body[:200],
                       "Wrong endpoint? Some servers serve the v1 payload only at /.")

    if not isinstance(data, dict):
        return _result("Anisette server", FAIL, "Response is not a JSON object", body[:200])

    missing = [k for k in ANISETTE_REQUIRED_KEYS if k not in data]
    if missing:
        return _result("Anisette server", FAIL, "Missing %d required field(s)" % len(missing),
                       ", ".join(missing),
                       "This server does not speak the legacy v1 flat-JSON contract.")

    # A numeric value here is the one failure the client swallows: X-Apple-I-MD-RINFO is parsed
    # with std::atoi, which returns 0 for a non-string without erroring, and the consequence
    # surfaces much later as an opaque Apple -36607.
    not_strings = [k for k in ANISETTE_REQUIRED_KEYS if not isinstance(data[k], str)]
    if not_strings:
        return _result("Anisette server", FAIL, "Field(s) not sent as JSON strings",
                       ", ".join(not_strings),
                       "AltServer requires every value to be a string. X-Apple-I-MD-RINFO as a "
                       "number is the common variant, and it fails silently.")

    client_info = data.get("X-MMe-Client-Info", "")
    detail = "Device-Id %s" % data.get("X-Mme-Device-Id", "?")
    if "com.apple.dt.Xcode" in client_info:
        # Verified against live Apple infrastructure: with this substring present the first GSA
        # request returns 503; rewritten to com.apple.akd it returns 200.
        detail += " | client-info contains com.apple.dt.Xcode, so the built-in sanitizer is " \
                  "load-bearing (leave ALTSERVER_NO_CLIENTINFO_SANITIZE unset)"

    return _result("Anisette server", OK, "All 10 fields present, all strings, HTTP 200", detail)


def check_clock():
    """The anisette server's timestamp is forwarded to Apple verbatim, so skew matters."""
    rc, out = _run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"])
    if rc is None:
        return _result("Host clock", UNKNOWN, "Could not determine NTP status", out)
    if out.strip() == "yes":
        return _result("Host clock", OK, "NTP synchronised")
    return _result("Host clock", WARN, "Clock is NOT NTP-synchronised",
                   "Apple sees the anisette server's timestamp verbatim.",
                   "Skew surfaces as an opaque -36607 with nothing naming time as the cause.")


def check_device():
    """Is a device visible, and is the pairing record valid?"""
    rc, out = _run(["idevice_id", "-l"])
    if rc is None:
        return _result("iPhone pairing", UNKNOWN, "idevicepair/idevice_id not available", out,
                       "Install libimobiledevice-utils.")

    udids = [line.strip() for line in out.splitlines() if line.strip()]
    if not udids:
        return _result("iPhone pairing", FAIL, "No device detected",
                       "usbmuxd sees nothing.",
                       "For the first pairing the phone must be plugged in by USB -- wireless "
                       "pairing is not possible in this build. On a VM, check USB passthrough.")

    rc, out = _run(["idevicepair", "validate"])
    if rc == 0:
        return _result("iPhone pairing", OK, "Pairing valid", "UDID %s" % udids[0])
    if "passcode" in out.lower():
        return _result("iPhone pairing", WARN, "Device is locked", out.strip(),
                       "Unlock the phone and re-check; validation needs it unlocked.")
    return _result("iPhone pairing", FAIL, "Pairing did not validate", out.strip(),
                   "Re-pair over USB and tap Trust. Back up BOTH files in /var/lib/lockdown "
                   "together -- half a pairing is indistinguishable from none.")


def check_advertisement(service="_altserver._tcp"):
    """The only trustworthy advertisement test: browse for it, do not trust the server."""
    rc, out = _run(["avahi-browse", "-rpt", service], timeout=15)
    if rc is None:
        return _result("mDNS advertisement", UNKNOWN, "avahi-browse not available", out,
                       "Install avahi-utils. This is the ONLY reliable check: AltServer cannot "
                       "detect its own advertisement failing, and avahi can report success "
                       "while publishing nothing.")
    if any(line.startswith("=") for line in out.splitlines()):
        hosts = [l.split(";")[6] for l in out.splitlines()
                 if l.startswith("=") and len(l.split(";")) > 6]
        return _result("mDNS advertisement", OK, "%s is published" % service,
                       "Seen on: %s" % ", ".join(sorted(set(hosts))) if hosts else "")
    return _result("mDNS advertisement", FAIL, "%s is NOT published" % service,
                   "Nothing is advertising it, so AltStore cannot discover this server.",
                   "Check python3 and libavahi-compat-libdnssd-DEV (not -libdnssd1: the code "
                   "dlopens the unversioned libdns_sd.so) and that avahi-daemon is running.")


def check_altserver_running(port_hint=None):
    """Is the process up? Note it binds an EPHEMERAL port, so no fixed port can be checked."""
    rc, out = _run(["pgrep", "-af", "AltServer"])
    if rc is None:
        return _result("AltServer process", UNKNOWN, "Could not check", out)
    lines = [l for l in out.splitlines() if "AltServer" in l and "pgrep" not in l]
    if lines:
        return _result("AltServer process", OK, "Running", lines[0][:160])
    return _result("AltServer process", FAIL, "Not running",
                   "Nothing to discover, and no refreshes will happen.",
                   "Note AltServer has no liveness signal: Listen() can fail early and the "
                   "process stays alive with no listener, so 'running' is necessary, not "
                   "sufficient -- trust the mDNS check above over this one.")


def run_all(anisette_url=None):
    checks = [
        check_anisette(anisette_url),
        check_clock(),
        check_device(),
        check_advertisement(),
        check_altserver_running(),
    ]
    states = [c["state"] for c in checks]
    if FAIL in states:
        overall = FAIL
    elif WARN in states or UNKNOWN in states:
        overall = WARN
    else:
        overall = OK
    return {"overall": overall, "checks": checks, "host": socket.gethostname()}


if __name__ == "__main__":
    print(json.dumps(run_all(), indent=2))
