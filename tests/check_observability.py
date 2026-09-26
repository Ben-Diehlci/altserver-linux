#!/usr/bin/env python3
"""Check that a broken deployment can actually be SEEN to be broken.

WHY THIS EXISTS. The 2026-09-22 outage was not detected because every channel that could have
reported it was, in one way or another, mute. Fixing the two loudest (HTTP 503, Nagios exit codes)
left them with no listener, and a sweep for the same shape found three more places where a failure
presents as a healthy, idle server. This guard pins all of it.

Each assertion below corresponds to a way the system previously lied:

  * NO HEALTHCHECKS. Docker computes no health state unless a healthcheck exists, so Portainer had
    no health column at all and was structurally incapable of showing a problem. Every failure
    this project has suffered leaves the process alive, so `restart: unless-stopped` never fires.
  * NOTHING RAN THE CHECKS. status_checks.py executed only inside a browser's HTTP GET, so the
    verdict existed solely in the instant somebody opened the page. The altserver-web healthcheck
    is what finally runs it on a schedule.
  * fetch_altstore RETURNED 0 WHEN IT FAILED, making the entrypoint's own "could not refresh"
    warning unreachable on the common path, so the IPA could stay pinned at an ever-older version.
  * THE ENTRYPOINT ONLY STAT-ED THE LOG FILTER. AltServer ignores SIGPIPE, so a filter that failed
    to start took every log line with it while the container stayed Up and answering.
  * THE TEE DISABLED ITSELF SILENTLY on the first write error and never retried, so
    /data/altserver.log froze while stdout kept flowing and neither copy said so.
  * /api/logs PRESENTED A FROZEN FILE AS LIVE, with no mtime and no staleness test.
"""

import ast
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
STACK = os.path.join(ROOT, "deploy", "altserver-stack.yml")
ENTRY = os.path.join(ROOT, "docker", "docker-entrypoint.sh")

failures = []


def check(cond, msg):
    print(("  ok  " if cond else "  FAIL ") + msg)
    if not cond:
        failures.append(msg)


# ---- every service reports health ----------------------------------------------------------
try:
    import yaml
except ImportError:
    print("SKIP: pyyaml not installed", file=sys.stderr)
    sys.exit(0)

stack = yaml.safe_load(open(STACK, encoding="utf-8"))
services = stack["services"]
for name, svc in sorted(services.items()):
    hc = svc.get("healthcheck")
    check(bool(hc) and bool(hc.get("test")),
          "%s declares a healthcheck (without one Docker computes no health state at all)" % name)

# The altserver probe must browse for the advertisement, which is the only honest test: AltServer
# cannot detect its own registration failing.
alt = services["altserver"].get("healthcheck", {})
check("_altserver._tcp" in " ".join(alt.get("test", [])),
      "the altserver healthcheck browses for its own mDNS advertisement")


def seconds(v, default=0):
    if v is None:
        return default
    s = str(v).strip()
    mult = {"s": 1, "m": 60, "h": 3600}.get(s[-1:], 1)
    try:
        return int(float(s.rstrip("smh"))) * mult
    except ValueError:
        return default


# The mDNS probe must not fire INSIDE the window the watchdog uses to repair itself, or it will
# report unhealthy for an outage already being fixed. The watchdog needs two consecutive misses
# at ALTSERVER_MDNS_RECHECK_SECONDS.
recheck = seconds(services["altserver"].get("environment", {})
                  .get("ALTSERVER_MDNS_RECHECK_SECONDS"), 60)
window = seconds(alt.get("interval"), 30) * int(alt.get("retries", 1))
check(window > recheck * 2,
      "the altserver health window (%ds) outlasts the watchdog's recovery (2 x %ds)"
      % (window, recheck))

# The web healthcheck is what actually runs the checks on a schedule.
web = services["altserver-web"].get("healthcheck", {})
check(any("status_checks.py" in x for x in web.get("test", [])),
      "the altserver-web healthcheck runs status_checks.py (nothing else ever runs it)")
check("-q" in web.get("test", []) or "--quiet" in web.get("test", []),
      "it uses quiet mode (Docker truncates healthcheck output to 4KB)")
check("--server-only" in web.get("test", []),
      "it judges the SERVER, not whether the phone is home -- otherwise the container goes "
      "unhealthy every time its owner leaves the house, and the badge gets ignored")

# And --server-only must actually DROP the device checks, not just be accepted as a flag.
sys.path.insert(0, os.path.join(ROOT, "web"))
import status_checks as _sc  # noqa: E402
_names = set()
_real_device = _sc.check_device
_sc.check_device = lambda: (_names.add("device-ran") or
                            {"name": "iPhone reachability", "state": _sc.FAIL,
                             "summary": "", "detail": "", "fix": ""})
try:
    _out = _sc.run_all(anisette_url="http://127.0.0.1:1", server_only=True)
finally:
    _sc.check_device = _real_device
_reported = [c["name"] for c in _out["checks"]]
check("device-ran" not in _names,
      "--server-only does not even RUN the device checks")
check(not any(n in _sc.DEVICE_DEPENDENT for n in _reported),
      "and none of %s appears in the result (%s)" % (list(_sc.DEVICE_DEPENDENT), _reported))

# Polling anisette's "/" performs REAL provisioning against Apple. A healthcheck must never.
ani = " ".join(services["anisette"].get("healthcheck", {}).get("test", []))
check("/v3/client_info" in ani,
      "the anisette healthcheck hits /v3/client_info, NOT / (which provisions against Apple)")


# ---- fetch_altstore must not report success when it failed ---------------------------------
d = tempfile.mkdtemp()
dest = os.path.join(d, "AltStore.ipa")
open(dest, "w").write("existing ipa")
env = dict(os.environ)
env["https_proxy"] = "http://127.0.0.1:1"      # force the catalogue lookup to fail
env["http_proxy"] = "http://127.0.0.1:1"
proc = subprocess.run([sys.executable, os.path.join(ROOT, "web", "fetch_altstore.py"),
                       "--dest", dest], env=env, capture_output=True, text=True, timeout=120)
check(proc.returncode != 0,
      "fetch_altstore exits non-zero when the catalogue is unreachable (got %d); the "
      "entrypoint's warning is unreachable otherwise" % proc.returncode)
check(os.path.exists(dest),
      "it still leaves the existing IPA in place (fail open, but audibly)")


# ---- the entrypoint must EXERCISE the log filter, not stat it ------------------------------
entry = open(ENTRY, encoding="utf-8").read()
guard_line = [l for l in entry.split("\n") if "redact-log" in l and "if [" in l]
check(bool(guard_line) and "|" in "\n".join(entry.split("\n")[:45]),
      "the entrypoint pipes a probe through redact-log before relying on it")
check("redaction filter probe" in entry,
      "the probe is an actual execution, not a file-existence test")


# ---- redact-log must report and retry a failed tee -----------------------------------------
# BEHAVIOURAL, not a grep. An earlier version of this guard checked that the source contained the
# string "tee_failures" and passed happily when the reporting was deleted, because the retry block
# still mentioned the name. A guard that cannot catch the regression is the very thing this file
# is about.
import importlib.util  # noqa: E402
import io  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "redactlog_under_test", os.path.join(ROOT, "docker", "redact-log.py"))
_rl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_rl)        # __name__ != "__main__", so main() does not run


class _FlakyTee:
    """A tee handle that fails every write after the Nth, like a volume that filled up."""
    instances = []

    def __init__(self, fail_after):
        self.fail_after = fail_after
        self.writes = 0
        self.closed = False
        _FlakyTee.instances.append(self)

    def write(self, s):
        self.writes += 1
        if self.writes > self.fail_after:
            raise OSError(28, "No space left on device")

    def flush(self):
        pass

    def close(self):
        self.closed = True

    def tell(self):
        return 0


_opens = {"n": 0}


def _fake_open_tee():
    _opens["n"] += 1
    # First handle dies almost immediately; any reopen works, so a retry must visibly recover.
    return _FlakyTee(fail_after=3 if _opens["n"] == 1 else 10 ** 9)


_real_open_tee, _real_stdin, _real_stderr = _rl._open_tee, sys.stdin, sys.stderr
_captured = io.StringIO()
try:
    _rl._open_tee = _fake_open_tee
    _rl.TEE_PATH = "/tmp/does-not-matter"
    sys.stdin = io.StringIO("".join("line %d\n" % i for i in range(600)))
    sys.stderr = _captured
    _rl.main()
finally:
    _rl._open_tee, sys.stdin, sys.stderr = _real_open_tee, _real_stdin, _real_stderr

_err = _captured.getvalue()
check("failed" in _err,
      "a failing tee is REPORTED to stderr (stderr still reaches docker logs)")
check(_opens["n"] > 1,
      "the tee is REOPENED after a failure rather than disabled for the life of the container "
      "(opened %d time(s))" % _opens["n"])
check("recovered" in _err,
      "the recovery is reported too, so the gap in the file is explainable")
check(_FlakyTee.instances[0].closed,
      "the dead handle is closed rather than leaked")

redact = open(os.path.join(ROOT, "docker", "redact-log.py"), encoding="utf-8").read()
check("filter started" in redact,
      "it writes a startup line, so a healthy filter always leaves a fresh mtime")


# ---- /api/logs must expose staleness -------------------------------------------------------
server = open(os.path.join(ROOT, "web", "server.py"), encoding="utf-8").read()
for field in ("mtime", "age_seconds", "stale"):
    check('"%s"' % field in server,
          "/api/logs reports %s, so a frozen log is not presented as live" % field)


if failures:
    print("\n%d check(s) failed:" % len(failures), file=sys.stderr)
    for f in failures:
        print("  - " + f, file=sys.stderr)
    print("\nEach of these is a channel that once reported health during a real outage.",
          file=sys.stderr)
    sys.exit(1)

print("OK: the deployment can be seen to be broken.")
