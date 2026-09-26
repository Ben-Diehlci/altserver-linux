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
def serve_with(overall):
    """Run the REAL Handler against a canned verdict and return (status, parsed body)."""
    real = status_checks.run_all
    status_checks.run_all = lambda *a, **k: {
        "overall": overall, "host": "test",
        "checks": [{"name": "x", "state": overall, "summary": "s", "detail": "", "fix": ""}]}
    httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        url = "http://127.0.0.1:%d/api/status" % httpd.server_address[1]
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


if failures:
    print("\n%d check(s) failed:" % len(failures), file=sys.stderr)
    for f in failures:
        print("  - " + f, file=sys.stderr)
    print("\nThese are the channels a monitor uses. When they lie, an outage looks like health.",
          file=sys.stderr)
    sys.exit(1)

print("OK: failures are visible over HTTP and as an exit code.")
