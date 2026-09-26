#!/usr/bin/env python3
"""Check that a failing server reports the failure to MACHINES, not just to a human reading a page.

WHY THIS EXISTS. On 2026-09-22 the mDNS advertisement was dropped and the server went completely
undiscoverable. Nothing refreshed for at least three days -- possibly nine; nothing recorded the
advertisement working, so the start of the outage cannot be dated. It was found by accident, when
the owner happened to need to update an app.

status_checks.py had detected it correctly the whole time. Nobody heard, because both machine
readable channels lied:

  * `/api/status` answered HTTP 200 no matter what `overall` said, so an uptime monitor saw a
    healthy endpoint throughout a total outage.
  * `python3 status_checks.py` exited 0 no matter what it found, so a cron job could never alert.

Following the obvious monitoring advice would have shown green for the entire incident. That is
worse than having no monitoring, because it answers the question "is it up?" with a confident yes.

This guard drives the REAL request handler and runs the REAL script. Testing the mapping functions
alone would not do: a correct mapping that no caller uses is the same bug wearing a better hat,
and that is precisely the shape of the original -- status_checks.py computed `overall` perfectly
and then threw it away at the exit.
"""

import json
import os
import subprocess
import sys
import threading
import urllib.error
import urllib.request

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
WEB = os.path.join(ROOT, "web")
sys.path.insert(0, WEB)

import status_checks  # noqa: E402
import server  # noqa: E402

failures = []


def check(cond, msg):
    if cond:
        print("  ok  %s" % msg)
    else:
        failures.append(msg)
        print("  FAIL %s" % msg)


# ---- the vocabulary is covered ------------------------------------------------------------
STATES = [status_checks.OK, status_checks.WARN, status_checks.FAIL, status_checks.UNKNOWN]
check(all(s in status_checks.EXIT_CODES for s in STATES),
      "every state has an exit code")
check(all(s in status_checks.HTTP_CODES for s in STATES),
      "every state has an HTTP code")

# Nagios plugin convention, which is what a monitoring agent expects from a check script.
check(status_checks.exit_code(status_checks.OK) == 0, "OK exits 0")
check(status_checks.exit_code(status_checks.WARN) == 1, "WARN exits 1")
check(status_checks.exit_code(status_checks.FAIL) == 2, "FAIL exits 2 (CRITICAL)")
check(status_checks.exit_code("something-new") == 1,
      "an unrecognised verdict is a WARNING, not an OK")

check(status_checks.http_status(status_checks.FAIL) == 503, "FAIL is HTTP 503")
check(status_checks.http_status(status_checks.OK) == 200, "OK is HTTP 200")
check(status_checks.http_status(status_checks.WARN) == 200, "WARN stays HTTP 200")


# ---- the real handler actually uses them ---------------------------------------------------
def serve_with(overall, checks=None, query=""):
    """Run the REAL Handler against a canned verdict and return (status, parsed body)."""
    real = status_checks.run_all
    body = checks or [{"name": "x", "state": overall, "summary": "s", "detail": "", "fix": ""}]
    status_checks.run_all = lambda *a, **k: {
        "overall": overall, "host": "test", "checks": body}
    httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        url = "http://127.0.0.1:%d/api/status%s" % (httpd.server_address[1], query)
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as exc:      # 503 arrives here, with its body intact
            return exc.code, json.loads(exc.read())
    finally:
        httpd.shutdown()
        httpd.server_close()
        status_checks.run_all = real


code, body = serve_with(status_checks.FAIL)
check(code == 503, "/api/status returns 503 when overall is fail (was an unconditional 200)")
check(body.get("overall") == "fail",
      "the 503 still carries the full JSON body, so the page can render the reason")

code, _ = serve_with(status_checks.OK)
check(code == 200, "/api/status returns 200 when healthy")
code, _ = serve_with(status_checks.WARN)
check(code == 200, "/api/status returns 200 when degraded")

# The endpoint the README tells you to monitor must NOT page you for leaving the house. A
# monitor that cries wolf gets muted, which lands back at no monitoring at all -- the state
# that let the 09-22 outage run for days.
PHONE_AWAY = [
    {"name": "mDNS advertisement", "state": status_checks.OK, "summary": "", "detail": "", "fix": ""},
    {"name": "iPhone reachability", "state": status_checks.FAIL, "summary": "", "detail": "", "fix": ""},
]
code, body = serve_with(status_checks.FAIL, PHONE_AWAY)
check(code == 200,
      "a phone off the LAN does NOT make /api/status return 503 (got %d)" % code)
check(body.get("overall") == "fail",
      "but the body still says fail, so the page shows the truth")
check(body.get("server_overall") == "ok",
      "and server_overall says the SERVER is fine (got %r)" % body.get("server_overall"))

SERVER_DOWN = [
    {"name": "mDNS advertisement", "state": status_checks.FAIL, "summary": "", "detail": "", "fix": ""},
    {"name": "iPhone reachability", "state": status_checks.OK, "summary": "", "detail": "", "fix": ""},
]
code, body = serve_with(status_checks.FAIL, SERVER_DOWN)
check(code == 503,
      "a real server failure DOES still return 503 (got %d) -- the outage case must not be "
      "masked by this" % code)

code, _ = serve_with(status_checks.FAIL, PHONE_AWAY, query="?full=1")
check(code == 503,
      "?full=1 judges on everything, for someone who wants that (got %d)" % code)


# ---- the page must keep working against a non-200 ------------------------------------------
# The page's own fetch() must NOT gate on r.ok, or changing the status code above would blank the
# dashboard during exactly the outage it is meant to describe. These two facts are coupled: if
# someone adds an r.ok guard later, this fails and points at the status code.
import re  # noqa: E402
page = open(os.path.join(WEB, "server.py"), encoding="utf-8").read()
js = "\n".join(re.findall(r"<script>(.*?)</script>", page, re.DOTALL))
check(bool(js.strip()), "found the page's <script> block to inspect")
check("fetch(" in js, "the page does fetch its status (otherwise this check is vacuous)")
# Match `.ok` only on names a fetch Response is bound to. `d.ok` elsewhere on the install page is
# a JSON body FIELD, not a Response check, and must not trip this. A guard is a heuristic here:
# renaming the variable would slip past it, which is why the behavioural 503 tests above are the
# real check and this is the early warning.
resp_ok = re.search(r"\b(?:r|res|resp|response)\.ok\b", js)
check(resp_ok is None,
      "the page's fetch() does not gate on response.ok, so a 503 still renders%s"
      % ("" if resp_ok is None else " -- found %r" % resp_ok.group(0)))


# ---- the script really exits non-zero, end to end ------------------------------------------
# Not mocked. Point the anisette check at a closed port so run_all genuinely reaches FAIL, then
# run the shipped script the way a cron job would.
env = dict(os.environ)
env["ALTSERVER_ANISETTE_SERVER"] = "http://127.0.0.1:1"   # nothing listens on port 1
proc = subprocess.run([sys.executable, os.path.join(WEB, "status_checks.py")],
                      env=env, capture_output=True, text=True, timeout=180)
try:
    verdict = json.loads(proc.stdout)["overall"]
except Exception:
    verdict = None
check(verdict == "fail",
      "a dead anisette server really does produce overall=fail (got %r)" % verdict)
check(proc.returncode == 2,
      "status_checks.py exits 2 when it fails (was 0 always; got %d)" % proc.returncode)
check(proc.stdout.strip().startswith("{"),
      "it still prints the JSON, so the exit code did not replace the output")


# ---- a check must not report success for work it did not do --------------------------------
# check_clock used to wrap its real comparison in a bare `except Exception: pass` and then fall
# through to `timedatectl`. An unparseable anisette timestamp therefore skipped the only
# comparison that matters and reported whatever LOCAL NTP said -- and its own docstring explains
# that local NTP proves nothing, because Linux forwards the ANISETTE server's timestamp to Apple
# verbatim.
#
# The trigger is not hypothetical: AltServer's C parses this field with strptime(), a PREFIX
# match, while this check uses a full match on a fixed 19-character slice. A non-zero-padded
# month is accepted by AltServer and raises here, so refresh keeps working while the check
# becomes a permanent no-op. Clock skew is the failure that surfaces as an opaque Apple -36607
# with nothing naming time as the cause.
import time as _time  # noqa: E402
now = _time.strftime("%Y-%m-%dT%H:%M:%S", _time.gmtime())

good = status_checks.check_clock(now)
check(good["state"] == status_checks.OK,
      "a current anisette timestamp still reports OK (got %r)" % good["state"])

for bad, why in [("2026-9-25T12:34:56Z", "non-zero-padded month, which AltServer's own C accepts"),
                 ("2026-09-25 12:34:56Z", "space instead of T"),
                 ("1758800000", "epoch integer"),
                 ("not a timestamp at all", "garbage")]:
    r = status_checks.check_clock(bad)
    check(r["state"] != status_checks.OK,
          "an unparseable timestamp (%s) never reports OK (got %r)" % (why, r["state"]))
    check(r["state"] == status_checks.WARN,
          "an unparseable timestamp (%s) reports WARN (got %r)" % (why, r["state"]))
    check(bad in r.get("detail", ""),
          "the failing value is shown, so it can be diagnosed (%s)" % why)

# And a real skew must still FAIL rather than be softened by any of the above.
skewed = status_checks.check_clock("2020-01-01T00:00:00")
check(skewed["state"] == status_checks.FAIL,
      "a genuinely skewed clock still reports FAIL (got %r)" % skewed["state"])


# ---- an inconclusive probe must never be reported as a definite negative --------------------
# avahi-browse can be missing, time out, or RUN AND REFUSE (D-Bus denied, daemon down). Only the
# first was handled; a non-zero exit fell through to the "found nothing" branch and was announced
# as "_altserver._tcp is NOT published". That is a confident outage report from a probe that never
# answered -- and it drives the HTTP 503 and the container healthcheck.
_real_run_sc = status_checks._run

def _browse_returns(rc, out):
    status_checks._run = lambda cmd, **kw: (rc, out)
    try:
        return status_checks.check_advertisement(), status_checks.check_phone_advertisement()
    finally:
        status_checks._run = _real_run_sc

for rc, out, label in [(1, "Failed to create client: Daemon not running", "avahi refused"),
                       (None, "avahi-browse timed out after 15s", "browse timed out")]:
    adv, phone = _browse_returns(rc, out)
    check(adv["state"] == status_checks.UNKNOWN,
          "%s reports UNKNOWN for the advertisement, not a false outage (got %r)"
          % (label, adv["state"]))
    check(phone["state"] == status_checks.UNKNOWN,
          "%s reports UNKNOWN for the phone, not 'the phone is not advertising'" % label)
    # Match the INSTRUCTION, not the word: the correct fix text says "avahi-browse is
    # installed", which a bare substring search flags as install-advice.
    check("install avahi-utils" not in adv["fix"].lower(),
          "%s does not tell you to install a tool that is already installed" % label)

adv, _ = _browse_returns(None, "avahi-browse is not installed")
check("install" in adv["fix"].lower(),
      "a genuinely missing avahi-browse DOES still say to install it")

# An advertisement from ANOTHER host used to read as OK, so a second AltServer on the LAN -- or a
# stale record from a machine that has gone -- made this green while this host published nothing.
import socket as _socket  # noqa: E402
_ROW = "=;eth0;IPv4;AltServer;_altserver._tcp;local;%s;192.168.5.16;48647;\"serverID=1\""
_me = _socket.gethostname().split(".")[0]
adv, _ = _browse_returns(0, _ROW % (_me + ".local"))
check(adv["state"] == status_checks.OK, "our own advertisement still reads OK")
adv, _ = _browse_returns(0, _ROW % "someone-elses-box.local")
check(adv["state"] != status_checks.OK,
      "a STRANGER's _altserver._tcp does not read as OK (got %r)" % adv["state"])
check("not by this host" in adv["summary"].lower(),
      "and it says whose it is (%r)" % adv["summary"])


# ---- an HTTP error means the server ANSWERED -------------------------------------------------
# Reporting it as "cannot reach ... is the container running? check docker ps" sends the owner to
# a command that shows it UP, and invites a redeploy -- which anisette-stack.yml:47-58 documents
# as destroying device.json and adi.pb, minting a new machine and demanding 2FA.
import urllib.error as _ue  # noqa: E402
_real_urlopen_sc = status_checks.urllib.request.urlopen


def _anisette_with(exc_or_resp, polling):
    def _f(*a, **k):
        if isinstance(exc_or_resp, Exception):
            raise exc_or_resp
        return exc_or_resp
    status_checks.urllib.request.urlopen = _f
    try:
        return status_checks.check_anisette("http://127.0.0.1:6969", polling=polling)
    finally:
        status_checks.urllib.request.urlopen = _real_urlopen_sc


for polling in (True, False):
    r = _anisette_with(_ue.HTTPError("u", 502, "Bad Gateway", {}, None), polling)
    where = "polled" if polling else "full"
    check("cannot reach" not in r["summary"].lower() and "nothing answered" not in r["summary"].lower(),
          "%s: an HTTP 502 is not reported as unreachable (said %r)" % (where, r["summary"]))
    check("502" in r["summary"],
          "%s: the status code is shown (said %r)" % (where, r["summary"]))

r = _anisette_with(OSError("Connection refused"), True)
check("nothing answered" in r["summary"].lower() or "cannot reach" in r["summary"].lower(),
      "a genuine transport failure IS still reported as unreachable (said %r)" % r["summary"])


# ---- a check must not assert a CAUSE it has not established -----------------------------------
# Found by the owner turning their phone's Wi-Fi off to test the new healthcheck. netmuxd keeps
# listing a known device for a while after it disappears, so idevice_id still returned it,
# idevicepair could not reach it, and the check announced "the pairing record is stale or
# half-written. Re-pair over USB and tap Trust" -- sending someone to fetch a cable and redo a
# pairing that was perfectly fine, for a phone that was merely switched off.
#
# lockdownd distinguishes these and the codes mean opposite things: -8 is MUX_ERROR, a transport
# failure that says nothing about pairing, while -2/-4/-5 are the ones that actually do. Wrong
# advice that costs real work is worse than saying "unknown".
_real_run, _real_devs = status_checks._run, status_checks._devices_via


def diagnose(pair_output):
    """check_device() with a device listed over Wi-Fi and idevicepair returning this."""
    status_checks._devices_via = lambda env, flag: ((["UDID0001"], "") if flag == "-n"
                                                    else ([], "no device"))
    status_checks._run = lambda cmd, **kw: (1, pair_output)
    try:
        return status_checks.check_device()
    finally:
        status_checks._run, status_checks._devices_via = _real_run, _real_devs


ERR = "ERROR: Could not connect to lockdownd, error code %d"

r = diagnose(ERR % -8)
check("pairing" not in r["summary"].lower(),
      "a transport failure (-8) is NOT reported as a pairing problem (said %r)" % r["summary"])
check("re-pair" not in r["fix"].lower().replace("do not re-pair", ""),
      "and it does not tell the owner to re-pair over USB")
check("transport" in r["fix"].lower(),
      "it names the real cause: a transport failure, i.e. the phone did not answer")

r = diagnose(ERR % -19)
check(r["state"] == status_checks.WARN and "trust" in r["summary"].lower(),
      "a pending Trust prompt (-19) is reported as such, not as a stale record (%r)" % r["summary"])

for code in (-2, -4, -5):
    r = diagnose(ERR % code)
    check("stale" in r["fix"].lower(),
          "a real pairing failure (%d) DOES still advise re-pairing" % code)

r = diagnose(ERR % -42)
check("stale" not in r["fix"].lower(),
      "an unrecognised code invents no cause (said %r)" % r["fix"][:60])

r = diagnose("ERROR: Please enter the passcode on the device and retry.")
check(r["state"] == status_checks.WARN and "locked" in r["summary"].lower(),
      "a locked device is still reported as locked")

# The PAIRING PAGE had the identical bug and is where someone actually goes to act on the advice.
import pairing as _pairing  # noqa: E402

check(_pairing.LOCKDOWN_MUX_ERROR == status_checks.LOCKDOWN_MUX_ERROR,
      "pairing.py shares one definition of the codes rather than a second copy that can drift")


def pair_diagnose(pair_output):
    # _udids is nested inside diagnose(), so _run is the only seam. Dispatch on the command:
    # a device listed over the network, nothing on USB, and the validate result under test.
    _r = _pairing._run

    def _fake(cmd, timeout=15, env=None):
        if cmd[:1] == ["idevice_id"]:
            # Must look like a real UDID: _udids filters on ^[0-9A-Fa-f-]{8,}$.
            return (0, "00008130-001975AE3660001C\n") if "-n" in cmd else (0, "")
        if cmd[:1] == ["idevicepair"]:
            return (1, pair_output)
        return (0, "")

    # EVERY host dependency in diagnose() must be pinned, or this test reports whatever the
    # machine happens to look like. It first shipped patching only which() and _run(), and
    # passed on macOS purely because /var/run/usbmuxd EXISTS there -- on the Linux CI runner it
    # does not, diagnose() returned early at that step, and the guard failed. A test that
    # depends on undeclared host state is not a test; it is a coin flip that happened to land.
    _w, _e = _pairing.shutil.which, _pairing.os.path.exists
    _pairing.shutil.which = lambda n, *a, **k: "/usr/bin/" + n
    _pairing.os.path.exists = lambda path: True if path == "/var/run/usbmuxd" else _e(path)
    _pairing._run = _fake
    try:
        return _pairing.diagnose()
    finally:
        _pairing._run = _r
        _pairing.shutil.which = _w
        _pairing.os.path.exists = _e


def reached_pairing(pr):
    """Did diagnose() actually get as far as the pairing step, or bail at an earlier gate?

    Without this the assertions below silently grade whatever early-return they happened to
    reach. That is exactly how the first version passed on macOS and failed in CI: it bailed at
    the usbmuxd-socket step and the test read the wrong branch's text as a feature failure.
    """
    return any("detected" in s.get("title", "").lower() for s in pr.get("steps", []))


try:
    pr = pair_diagnose(ERR % -8)
    check(reached_pairing(pr),
          "the pairing test reaches the pairing step (it bailed at %r, so the assertions below "
          "would be grading the wrong branch)" % pr.get("next"))
    _note = " ".join(s.get("note", "") for s in pr["steps"]).lower()
    _next = pr.get("next", "").lower()
    check("replug" not in _note and "cable" not in _next,
          "the pairing page does not send you for a cable when the phone is merely unreachable")
    check("transport" in _note,
          "it names the transport failure instead (next: %r)" % pr.get("next"))
    pr = pair_diagnose(ERR % -19)
    check("trust" in pr.get("next", "").lower(),
          "a pending Trust prompt still says to tap Trust")
except Exception as _exc:      # pairing.diagnose touches more of the host than check_device
    check(False, "pairing.py diagnosis could not be exercised: %s" % _exc)


if failures:
    print("\n%d check(s) failed:" % len(failures), file=sys.stderr)
    for f in failures:
        print("  - " + f, file=sys.stderr)
    print("\nThese are the channels a monitor uses. When they lie, an outage looks like health.",
          file=sys.stderr)
    sys.exit(1)

print("OK: failures are visible over HTTP and as an exit code.")
