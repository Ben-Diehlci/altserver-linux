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

import calendar
import concurrent.futures
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
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

# Machine-readable verdicts, so something other than a human looking at the page can tell this is
# broken.
#
# WHY. On 2026-09-22 the mDNS advertisement was dropped and the server went undiscoverable for at
# least three days. This file reported the failure correctly the entire time -- and nothing heard
# it, because `python3 status_checks.py` exited 0 whatever it found and /api/status answered 200
# whatever it found. Following the obvious monitoring advice would have shown green throughout an
# outage in which nothing refreshed.
#
# Exit codes are the Nagios plugin convention (0 OK, 1 WARNING, 2 CRITICAL), which Nagios, Icinga
# and anything that shells out to a check script already understand.
EXIT_CODES = {OK: 0, WARN: 1, UNKNOWN: 1, FAIL: 2}

# HTTP has only two useful answers for a health endpoint, so WARN deliberately stays 200: degraded
# is not down, and UNKNOWN collapses into WARN whenever a tool is merely absent. The consequence
# is worth stating plainly rather than discovering later: A MONITOR WATCHING ONLY THE STATUS CODE
# CANNOT SEE A WARN. That includes the case where the mDNS check cannot run at all, which looks
# identical to health over HTTP alone. Anything that needs to distinguish must read `overall` from
# the body, or use the exit code.
HTTP_CODES = {OK: 200, WARN: 200, UNKNOWN: 200, FAIL: 503}


def exit_code(overall):
    """Process exit status for an `overall` verdict. Unknown verdicts are a WARNING, not an OK."""
    return EXIT_CODES.get(overall, 1)


def http_status(overall):
    """HTTP status for an `overall` verdict. Only FAIL is not-200; see HTTP_CODES above."""
    return HTTP_CODES.get(overall, 200)


def _in_container():
    return os.path.exists("/.dockerenv")


def _result(name, state, summary, detail=None, fix=None):
    return {"name": name, "state": state, "summary": summary, "detail": detail or "", "fix": fix or ""}


def _run(cmd, timeout=10, env=None):
    """Run a command, returning (rc, stdout+stderr). Never raises."""
    if shutil.which(cmd[0]) is None:
        return None, "%s is not installed" % cmd[0]
    try:
        merged = dict(os.environ, **env) if env else None
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=merged)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return None, "%s timed out after %ss" % (cmd[0], timeout)
    except Exception as exc:  # pragma: no cover - defensive
        return None, "%s could not be run: %s" % (cmd[0], exc)


def check_anisette(url=None, polling=False):
    """Fetch anisette data and validate it against the client's actual contract.

    polling=True makes this SAFE TO RUN ON A TIMER, and that is not a performance concern.

    This function fetches the BARE ROOT of the anisette server, which is the v1 data route. On a
    server whose machine identity is missing, that route performs REAL PROVISIONING AGAINST
    APPLE -- deploy/anisette-stack.yml:62-73 refuses to point even a healthcheck at it for
    exactly this reason: "a polling healthcheck on / would hammer Apple's endpoint at exactly
    the moment your identity volume has gone missing, turning a restore-the-backup incident into
    rate-limit / account-lock territory."

    That was harmless while this ran only when a human opened the status page. It stopped being
    harmless the moment the altserver-web healthcheck started running the checks every five
    minutes. So on the timer we ask the STATIC route instead, which makes no Apple contact, and
    say plainly that the field contract was not verified. The page, which is a human asking once,
    still does the real fetch.
    """
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

    if polling:
        # /v3/client_info is static and contacts Apple for nothing.
        probe = url.rstrip("/") + "/v3/client_info"
        try:
            with urllib.request.urlopen(
                    urllib.request.Request(probe, headers={"User-Agent": "Xcode"}),
                    timeout=10) as resp:
                if resp.status == 200:
                    return _result(
                        "Anisette server", OK, "Reachable (field contract not checked on a timer)",
                        "Probed %s, which makes no Apple contact. The full ten-field check runs "
                        "when the status page is opened." % probe)
                return _result("Anisette server", FAIL, "HTTP %s from %s" % (resp.status, probe),
                               "Reachable but unhealthy.", "Check the anisette container's logs.")
        except Exception as exc:
            return _result("Anisette server", FAIL, "Cannot reach %s" % probe, str(exc),
                           "Is the anisette container running? Check `docker ps`.")

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

    ok = _result("Anisette server", OK, "All 10 fields present, all strings, HTTP 200", detail)
    ok["anisette_time"] = data.get("X-Apple-I-Client-Time")
    return ok


def check_clock(anisette_time=None):
    """Drift against the ANISETTE server's clock is what actually matters.

    Linux forwards the anisette server's X-Apple-I-Client-Time to Apple verbatim (macOS stamps
    Date() locally instead), so NTP on the AltServer host proves nothing on its own. Comparing the
    two directly measures the thing that breaks sign-in, and unlike timedatectl it works inside a
    container.
    """
    if anisette_time:
        try:
            parsed = time.strptime(anisette_time[:19], "%Y-%m-%dT%H:%M:%S")
            skew = abs(calendar.timegm(parsed) - time.time())
            if skew <= 30:
                return _result("Clock agreement", OK,
                               "Anisette clock within %ds of ours" % int(skew),
                               "Anisette said %s" % anisette_time)
            return _result("Clock agreement", FAIL,
                           "Anisette clock is %ds away from ours" % int(skew),
                           "Anisette said %s" % anisette_time,
                           "Apple sees the anisette server's timestamp verbatim. Skew surfaces as "
                           "an opaque -36607 with nothing naming time as the cause. Fix NTP on "
                           "whichever host runs anisette -- not just this one.")
        except Exception as exc:
            # Do NOT fall through to the timedatectl fallback below. This used to be a bare
            # `except: pass`, which meant an unparseable anisette timestamp silently skipped the
            # only comparison that matters and then reported whatever LOCAL NTP said -- a green
            # "NTP synchronised" for a check that never ran, measuring the very thing this
            # docstring says proves nothing.
            #
            # The trigger is realistic, not hypothetical. AltServer's own C parses this field with
            # strptime(), which is a PREFIX match, while this is a full match on a fixed 19-char
            # slice. A non-zero-padded month ("2026-9-25T...") is accepted by AltServer and raises
            # here, so refresh would keep working normally while the clock check became a
            # permanent no-op -- and clock skew is the failure that surfaces as an opaque Apple
            # -36607 with nothing naming time as the cause.
            return _result("Clock agreement", WARN,
                           "Could not read the anisette server's clock",
                           "Anisette said %r (%s: %s)"
                           % (anisette_time, type(exc).__name__, exc),
                           "The drift comparison did not run, so clock skew would go unreported. "
                           "Local NTP is not a substitute: Linux forwards the ANISETTE server's "
                           "timestamp to Apple verbatim, so this host's clock proves nothing. "
                           "Expected format is YYYY-MM-DDTHH:MM:SS.")

    rc, out = _run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"])
    if rc is None:
        return _result("Clock agreement", UNKNOWN, "No anisette timestamp to compare against",
                       "timedatectl is unavailable here, which is normal in a container.",
                       "This resolves itself once the anisette check above succeeds.")
    if out.strip() == "yes":
        # WARN, not OK. This measured LOCAL NTP, which the docstring above explains is not the
        # thing that breaks sign-in. Reporting OK here would be a verification that cannot fail,
        # which this project has already been bitten by twice.
        return _result("Clock agreement", WARN, "Local NTP synchronised, anisette drift NOT checked",
                       "There was no anisette timestamp to compare against, so only this host's "
                       "clock was verified.",
                       "Apple sees the anisette server's timestamp, not this one. Fix the "
                       "anisette check above to get a real answer.")
    return _result("Clock agreement", WARN, "Clock is NOT NTP-synchronised")


# netmuxd's socket. Set by the stack; the default matches deploy/altserver-stack.yml.
NETMUXD_SOCKET = os.environ.get("ALTSERVER_NETMUXD_SOCKET", "/run/muxd/usbmuxd")

# libusbmuxd's env var. Note the spelling -- USBMUXD_SOCKET_ADRESS, with one D, is a widely-copied
# typo that is silently ignored. Verified at upstream_repo/libusbmuxd/src/libusbmuxd.c:158.
_WIRELESS_ENV = {"USBMUXD_SOCKET_ADDRESS": "UNIX:" + NETMUXD_SOCKET}
# Empty string makes libusbmuxd fall back to its compiled-in default, /var/run/usbmuxd.
_USB_ENV = {"USBMUXD_SOCKET_ADDRESS": ""}


def _devices_via(env, flag):
    """(udids, note) over one transport. Distinguishes 'no devices' from 'no mux listening'.

    `flag` is NOT optional and must match the transport. idevice_id's -l and -n are not
    verbosity switches, they select which transports are enumerated:

        -l  include_usb = 1        (tools/idevice_id.c: case 'l')
        -n  include_network = 1    (case 'n')
        neither, with no other args: both

    netmuxd only ever presents the phone as ConnectionType: Network, so `idevice_id -l` against
    netmuxd's socket returns an empty list NO MATTER WHAT -- the device is there, it is simply not
    a USB device. This check used -l for the wireless probe and so could never report OK for a
    wireless-only setup, which is the exact configuration it exists to verify. It read "No device
    on either transport" while refresh was demonstrably working.
    """
    rc, out = _run(["idevice_id", flag], env=env)
    if rc is None:
        return None, out
    # This exact string means libusbmuxd could not reach the socket AT ALL -- a dead or absent
    # mux. An empty device list is a silent success with no output, which is a completely
    # different condition and must not be conflated with it.
    if "Unable to retrieve device list" in out:
        return None, "no mux is listening on that socket"
    return [l.strip() for l in out.splitlines() if l.strip()], ""


# lockdownd result codes, from libimobiledevice/include/libimobiledevice/lockdown.h. Only the
# ones this file reasons about; the point is that -8 is a TRANSPORT failure and says nothing
# whatever about the pairing record, which is what the check used to claim it meant.
LOCKDOWN_INVALID_CONF = -2
LOCKDOWN_PAIRING_FAILED = -4
LOCKDOWN_SSL_ERROR = -5
LOCKDOWN_RECEIVE_TIMEOUT = -7
LOCKDOWN_MUX_ERROR = -8
LOCKDOWN_USER_DENIED_PAIRING = -18
LOCKDOWN_PAIRING_DIALOG_PENDING = -19


def _lockdown_code(out):
    """The numeric lockdownd code in idevicepair's output, or None."""
    m = re.search(r"error code\s+(-?\d+)", out or "")
    return int(m.group(1)) if m else None


def check_device():
    """Is the phone reachable, and -- the part that decides unattended refresh -- over WHICH path?

    Wireless is not a nicety here. Stock usbmuxd enumerates USB only, and on Ubuntu its unit is
    udev-activated: it exits when the last cable is unplugged. So a server with no cable has no mux
    at all unless netmuxd is running, and every refresh fails with what looks like a device fault.
    """
    # -n for netmuxd (network transport), -l for the host's usbmuxd (USB transport).
    wireless, wnote = _devices_via(_WIRELESS_ENV, "-n")
    usb, unote = _devices_via(_USB_ENV, "-l")

    if wireless:
        # -n is required here for the same reason as above: idevicepair.c:372 selects
        # IDEVICE_LOOKUP_USBMUX unless it is passed, so without it this validates a USB device
        # that does not exist on a cable-free server and reports a stale pairing record.
        rc, out = _run(["idevicepair", "-n", "validate"], env=_WIRELESS_ENV)
        if rc == 0:
            return _result("iPhone reachability", OK, "Reachable over Wi-Fi, pairing valid",
                           "UDID %s via netmuxd%s" % (wireless[0], ", also on USB" if usb else ""))
        if "passcode" in out.lower():
            return _result("iPhone reachability", WARN, "Found over Wi-Fi, but the device is locked",
                           out.strip(), "Unlock the phone and re-check.")

        # WHICH failure, not just THAT it failed. lockdownd's codes distinguish "could not reach
        # the device" from "the device rejected this pairing", and they mean opposite things.
        #
        # Found by turning a phone's Wi-Fi off to test: netmuxd goes on listing the device from
        # its stored record for a while, so idevice_id -n still returns it, idevicepair then
        # cannot reach it, and this branch used to announce a stale pairing record and send the
        # owner to fetch a cable, unplug, re-pair and tap Trust -- for a phone that was simply
        # switched off. Confidently wrong advice that costs real work is worse than "unknown".
        code = _lockdown_code(out)
        if code in (LOCKDOWN_MUX_ERROR, LOCKDOWN_RECEIVE_TIMEOUT):
            return _result(
                "iPhone reachability", FAIL, "Listed over Wi-Fi, but the phone did not answer",
                out.strip(),
                "This is a TRANSPORT failure (lockdownd error %d), not a pairing problem -- do "
                "NOT re-pair on account of it. netmuxd keeps listing a known device for a while "
                "after it goes away, so this is what an asleep phone, one with Wi-Fi off, or one "
                "off this network looks like. Check the phone first." % code)
        if code == LOCKDOWN_PAIRING_DIALOG_PENDING:
            return _result("iPhone reachability", WARN, "Waiting for Trust on the phone",
                           out.strip(),
                           "The phone is showing (or has dismissed) the Trust prompt. Unlock it "
                           "and tap Trust; no re-pairing needed.")
        if code == LOCKDOWN_USER_DENIED_PAIRING:
            return _result("iPhone reachability", FAIL, "The phone refused the pairing",
                           out.strip(),
                           "Someone tapped Do Not Trust. Re-pair over USB and tap Trust.")
        if code in (LOCKDOWN_INVALID_CONF, LOCKDOWN_PAIRING_FAILED, LOCKDOWN_SSL_ERROR):
            return _result("iPhone reachability", FAIL,
                           "Found over Wi-Fi, but pairing did not validate", out.strip(),
                           "The pairing record in /var/lib/lockdown is stale or half-written "
                           "(lockdownd error %d). Re-pair over USB and tap Trust; back up BOTH "
                           "files together." % code)
        # Unrecognised: report it without inventing a cause.
        return _result("iPhone reachability", FAIL, "Found over Wi-Fi, but validation failed",
                       out.strip(),
                       "Unrecognised lockdownd result. Do not assume a pairing problem: the "
                       "codes that actually mean that are -2, -4 and -5. See "
                       "libraries/libimobiledevice/include/libimobiledevice/lockdown.h.")

    if usb:
        return _result(
            "iPhone reachability", WARN, "Reachable over USB ONLY -- wireless refresh will not work",
            "UDID %s. netmuxd: %s" % (usb[0], wnote or "running, but reports no device"),
            "Unattended refresh needs the phone reachable with no cable. Check the netmuxd "
            "container is up, that the phone is on this LAN, and that it advertises itself "
            "(see the next check).")

    # Tools absent entirely is not the same as "no device" -- saying FAIL there would be a
    # confident false negative on a host that simply lacks libimobiledevice.
    if "not installed" in (wnote or "") and "not installed" in (unote or ""):
        return _result("iPhone reachability", UNKNOWN, "idevice_id is not available here", wnote,
                       "Install libimobiledevice-utils. The container image ships it.")

    return _result(
        "iPhone reachability", FAIL, "No device on either transport",
        "netmuxd: %s | usbmuxd: %s" % (wnote or "no device", unote or "no device"),
        "If both say no mux is listening, nothing is serving device access at all. The host's "
        "usbmuxd is udev-activated and exits with the cable removed, which is normal -- that is "
        "what the netmuxd container is for.")


def check_phone_advertisement():
    """Is the phone itself discoverable? netmuxd finds it by mDNS, so this is its precondition."""
    rc, out = _run(["avahi-browse", "-rpt", "_apple-mobdev2._tcp"], timeout=15)
    if rc is None:
        return _result("iPhone is advertising", UNKNOWN, "avahi-browse not available", out)

    rows = [l.split(";") for l in out.splitlines() if l.startswith("=")]
    rows = [r for r in rows if len(r) > 8]
    if not rows:
        return _result(
            "iPhone is advertising", FAIL, "The phone is not advertising _apple-mobdev2._tcp",
            "netmuxd discovers the device this way, so it cannot find it.",
            "The phone must be awake, on this Wi-Fi, and have been paired over USB at least once. "
            "This advert is how a device offers itself for wireless access.")

    seen = sorted({"%s:%s" % (r[7], r[8]) for r in rows})
    return _result("iPhone is advertising", OK, "Discoverable over mDNS", ", ".join(seen))


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


def check_altserver_running():
    """Is the daemon up? Only meaningful if we can actually see its process.

    Each container has its own PID namespace, so from a sidecar this sees nothing no matter how
    healthy the daemon is. Rather than report a confident false negative, say so -- and point at
    the mDNS check, which is the trustworthy signal either way.
    """
    rc, out = _run(["pgrep", "-af", "AltServer"])
    if rc is None:
        return _result("AltServer process", UNKNOWN, "Could not check", out)

    lines = [l for l in out.splitlines() if "AltServer" in l and "pgrep" not in l
             and "server.py" not in l]
    if lines:
        return _result("AltServer process", OK, "Running", lines[0][:160])

    if _in_container() and not os.path.exists("/proc/1/root/usr/local/bin/AltServer"):
        return _result(
            "AltServer process", UNKNOWN,
            "Cannot see other containers' processes from here",
            "Containers have separate PID namespaces, so this check is blind unless the service "
            "runs with pid: host.",
            "Judge by the mDNS check above -- if _altserver._tcp is published, something is "
            "advertising it and the daemon is alive.")

    return _result("AltServer process", FAIL, "Not running",
                   "Nothing to discover, and no refreshes will happen.",
                   "AltServer has no liveness signal: Listen() can fail early and the process "
                   "stays alive with no listener, so 'running' is necessary but not sufficient. "
                   "Trust the mDNS check above over this one.")


# The checks that depend on the PHONE being present, as opposed to the server working.
#
# This distinction matters for the container healthcheck. check_device returns FAIL when the
# phone is on neither transport, which is correct on the dashboard and wrong as a verdict on the
# server: take the phone out of the house and the container would go unhealthy every time. A
# badge that is red whenever its owner leaves gets ignored, which is how alert fatigue starts --
# and an ignored badge is no better than the no-badge state that let the 09-22 outage run for
# days.
DEVICE_DEPENDENT = ("iPhone reachability", "iPhone is advertising")

# Where the run-by-run history lives. The web container and altserver share this volume.
HISTORY_PATH = os.environ.get("ALTSERVER_HISTORY", "/data/status-history.jsonl")
# ~17 days at the healthcheck's 5-minute cadence, about 750KB. Long enough to answer "when did
# this start" across a 7-day certificate cycle, which is the question the 2026-09-22 outage could
# not answer: the advertisement had been dead for somewhere between three and nine days and
# nothing anywhere recorded which.
HISTORY_MAX = int(os.environ.get("ALTSERVER_HISTORY_MAX", "5000"))


def record(result, path=None):
    """Append one run to the history. Returns None on success, or a reason string.

    Deliberately returns the reason rather than raising: recording must never break the checks
    or the healthcheck that calls it. But it must not vanish either -- a history that silently
    stopped recording would look exactly like a server that has been fine all along, which is
    the failure this whole file now exists to prevent. The caller reports what comes back.
    """
    path = path or HISTORY_PATH
    line = json.dumps({
        "t": int(time.time()),
        "overall": result["overall"],
        "checks": {c["name"]: c["state"] for c in result["checks"]},
    }, sort_keys=True)
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception as exc:
        return "%s: %s" % (type(exc).__name__, exc)

    # Trim by LINE COUNT, so the timeline is a predictable span of TIME rather than of bytes.
    #
    # This used to skip the count unless the file exceeded HISTORY_MAX * 400 bytes, as a cheap
    # way to avoid reading it every run. That guess does not bind: with the short lines a
    # four-check run produces, the file held roughly double the intended window before the
    # threshold was ever reached. A bound that depends on how long the check names happen to be
    # is not a bound. The read costs nothing at a five-minute cadence on a file this size.
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
        if len(lines) > HISTORY_MAX:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.writelines(lines[-HISTORY_MAX:])
            os.replace(tmp, path)   # atomic: a reader never sees a half-written file
    except Exception as exc:
        return "trim failed: %s: %s" % (type(exc).__name__, exc)
    return None


def read_history(path=None, limit=None):
    """Recent runs, oldest first. Never raises; an unreadable history is an empty one."""
    path = path or HISTORY_PATH
    out = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    out.append(json.loads(raw))
                except ValueError:
                    continue      # a torn line must not discard the rest of the timeline
    except OSError:
        return []
    return out[-limit:] if limit else out


def verdict(checks, server_only=False):
    """Roll per-check states into one overall verdict.

    server_only EXCLUDES the device checks from the verdict -- it does not stop them running.
    That distinction was got wrong first time round and is worth stating: the healthcheck needs
    to ignore "the phone is out of the house" when deciding whether the SERVER is healthy, but
    the history wants those checks recorded anyway, or "when did the phone drop off the network"
    becomes unanswerable for exactly the same reason the mDNS outage was.
    """
    considered = [c for c in checks
                  if not (server_only and c["name"] in DEVICE_DEPENDENT)]
    states = [c["state"] for c in considered]
    if FAIL in states:
        return FAIL
    if WARN in states or UNKNOWN in states:
        return WARN
    return OK


def _result_history_broken(reason):
    return _result("Status history", WARN, "Could not record this run", reason,
                   "The checks themselves ran fine; only the timeline is affected. Check that "
                   "%s is writable and that the volume is not full." % HISTORY_PATH)


def run_all(anisette_url=None, server_only=False, polling=False):
    """Run every check, in parallel apart from the one real dependency.

    server_only narrows the OVERALL VERDICT to what the server is responsible for. Every check
    still runs and still appears in the result.

    These were serial, which made the page as slow as the SUM of its checks. Two of them shell out
    to avahi-browse with a 15s timeout, so when an AppArmor rule started denying avahi's D-Bus
    signals the dashboard took over half a minute to render anything -- the checks were reporting
    a problem correctly and the page was unusable while they did it.

    They are subprocess and HTTP calls, so threads are the right tool: the page is now as slow as
    its SLOWEST check, not their total. check_clock is the one genuine dependency, needing the
    timestamp check_anisette collected, so anisette runs first and the rest run together.

    A check that raises must not take the dashboard with it -- that is what the whole page exists
    to avoid -- so each result is collected defensively.
    """
    anisette = check_anisette(anisette_url, polling=polling)

    def _clock():
        return check_clock(anisette.get("anisette_time"))

    rest = [_clock, check_device, check_phone_advertisement,
            check_advertisement, check_altserver_running]

    results = [None] * len(rest)
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(rest)) as pool:
        futures = {pool.submit(fn): i for i, fn in enumerate(rest)}
        for fut in concurrent.futures.as_completed(futures):
            i = futures[fut]
            try:
                results[i] = fut.result()
            except Exception as exc:  # a broken check must not blank the page
                results[i] = _result("Check #%d" % (i + 1), UNKNOWN,
                                     "This check raised an exception", str(exc))

    checks = [anisette] + results
    return {"overall": verdict(checks, server_only), "checks": checks,
            "host": socket.gethostname()}


if __name__ == "__main__":
    # Exits non-zero when something is wrong, so this is usable from cron, a Docker healthcheck or
    # a monitoring agent without parsing the JSON. See EXIT_CODES.
    #
    # -q prints ONE line naming what is wrong instead of the full JSON. Docker keeps only the last
    # 5 healthcheck outputs and truncates each to 4KB, so the full document would be clipped
    # mid-object and tell an operator running `docker inspect` nothing useful.
    _quiet = "-q" in sys.argv[1:] or "--quiet" in sys.argv[1:]
    # --server-only: judge the SERVER, not whether the phone happens to be home. See
    # DEVICE_DEPENDENT above for why a healthcheck wants this and the dashboard does not.
    _server_only = "--server-only" in sys.argv[1:]
    # --record appends this run to the history, so the page can answer "when did this start"
    # rather than only "is it broken now". The healthcheck is the writer: it already runs every
    # five minutes, which is a better cadence than anything a page load would produce.
    _record = "--record" in sys.argv[1:]
    # Anything on a timer is polling. See check_anisette: the bare root provisions against Apple
    # when the identity is missing, so it must not be fetched every five minutes.
    _polling = _record or _server_only or "--polling" in sys.argv[1:]
    _run = run_all(server_only=_server_only, polling=_polling)

    _rec_err = record(_run) if _record else None
    if _rec_err:
        # Loud, and it DEGRADES the verdict. A history that quietly stopped recording looks
        # identical to a server that has been fine all along -- which is the exact shape of
        # every bug this file has been fixed for. Never worse than the checks themselves said,
        # so a recording problem cannot mask a real failure.
        sys.stderr.write("status_checks: could not record history (%s)\n" % _rec_err)
        _run["checks"].append(_result_history_broken(_rec_err))
        _run["overall"] = verdict(_run["checks"], _server_only)

    if _quiet:
        _bad = [c["name"] for c in _run["checks"] if c["state"] in (FAIL, WARN, UNKNOWN)]
        print("%s%s" % (_run["overall"], (": " + ", ".join(_bad)) if _bad else ""))
    else:
        print(json.dumps(_run, indent=2))
    sys.exit(exit_code(_run["overall"]))
