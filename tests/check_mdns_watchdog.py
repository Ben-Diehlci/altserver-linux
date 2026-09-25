#!/usr/bin/env python3
"""Check that the mDNS advertisement re-registers itself when it disappears.

WHY THIS EXISTS. On 2026-09-22 avahi-daemon restarted on the deployment host. Every registration
on the box was dropped, and AltServer's advertisement never came back. The server was
undiscoverable for three days: AltStore could not find it, nothing refreshed, and every other
health signal stayed green the entire time. It was found only because a human happened to open
the status page.

Nothing in the process could have caught it. avahi-compat's worker thread runs exactly one poll()
and then blocks for a command that only DNSServiceProcessResult() sends, which this project never
calls -- so the client never reaches AVAHI_CLIENT_FAILURE and the registration callback can never
fire. Process liveness is not a signal either: the helper stayed alive for three days holding a
handle to a service that no longer existed.

So the advertisement is now re-checked from outside, by browsing for it. This guard exercises that
loop against a fake avahi, because the real thing cannot be reproduced in CI: it needs a running
avahi-daemon, a D-Bus system bus, and a way to restart the daemon mid-test.

The cases encode three rules that are easy to get wrong and impossible to notice when wrong:

  * MATCH ON PORT AND serverID, NEVER THE NAME. flags is 0, so on a name collision avahi RENAMES
    the service to "AltServer #2" instead of withdrawing it. A name-based check would see its own
    record as missing and re-register in a loop forever. That is the `renamed` case.
  * A FAILED BROWSE IS UNKNOWN, NOT ABSENT. Re-registering because avahi-browse timed out would
    turn a blip into a self-inflicted outage. That is the `browse_error` case.
  * TWO CONSECUTIVE MISSES BEFORE ACTING, so one false negative costs nothing. That is the `flap`
    case.

The helper source is extracted from dnssd_loader.cpp and executed verbatim, so this also catches a
syntax error in it. That matters: today a broken helper is only discoverable on a live host, where
the parent reports it as the generic "the python3 helper exited immediately".
"""

import os
import re
import subprocess
import socket
import sys
import tempfile

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
LOADER = os.path.join(ROOT, "libraries", "dnssd_loader", "dnssd_loader.cpp")

PORT = 48647
SERVER_ID = "serverID=1234567"
TXT_HEX = ("%02X" % len(SERVER_ID)) + SERVER_ID.encode().hex()


def helper_source():
    src = open(LOADER, encoding="utf-8").read()
    m = re.search(r'R"PY\((.*?)\)PY"', src, re.S)
    if not m:
        sys.exit("FAIL: no R\"PY( ... )PY\" helper block in %s" % LOADER)
    return m.group(1)


def row(name="AltServer", port=PORT, txt=SERVER_ID, fields=10):
    line = ["=", "eth0", "IPv4", name, "_altserver._tcp", "local",
            "ubuntu-docker.local", "192.168.5.16", str(port), '"%s"' % txt]
    return ";".join(line[:fields])


# name -> (browse results per call, sleeps before stopping, expected registers, expected deallocs)
# A browse result is (returncode, stdout).
HIT = (0, row() + "\n")
EMPTY = (0, "")
ERROR = (1, "")

CASES = {
    # Healthy: the record is there every time. Must not touch the registration at all.
    "healthy": ([HIT, HIT, HIT, HIT], 4, 1, 0),

    # The real incident: the record is gone and stays gone. Must re-register exactly once,
    # deallocating first so there is never a duplicate.
    "gone": ([EMPTY, EMPTY], 2, 2, 1),

    # avahi-browse fails or times out. UNKNOWN is not ABSENT: must do nothing.
    "browse_error": ([ERROR, ERROR, ERROR, ERROR], 4, 1, 0),

    # One isolated false negative between healthy reads. Two consecutive misses are required,
    # so this must not act.
    "flap": ([EMPTY, HIT, EMPTY, HIT], 4, 1, 0),

    # avahi renamed the service on a collision. Same port, same serverID, different NAME. This is
    # our record and it is healthy; a name-based check would re-register forever.
    "renamed": ([(0, row(name="AltServer #2") + "\n")] * 4, 4, 1, 0),

    # Someone else's _altserver._tcp on a different port. Not ours: our record is absent.
    "wrong_port": ([(0, row(port=31337) + "\n")] * 2, 2, 2, 1),

    # A truncated line (fewer than 10 fields) must not crash or count as a match.
    "short_row": ([(0, row(fields=6) + "\n")] * 2, 2, 2, 1),

    # ANOTHER host's AltServer, which happens to have landed on the same ephemeral port. The port
    # alone is not proof: ports are handed out by the kernel per process, so a second AltServer
    # anywhere on the LAN can collide. Only the serverID says the record is OURS. Without that
    # check we would read a stranger's advertisement as proof our own is healthy, and never
    # re-register.
    "foreign_server": ([(0, row(txt="serverID=7654321") + "\n")] * 2, 2, 2, 1),
}

# Substrings the helper MUST print for a case. A watchdog that cannot browse is blind, and a
# blind watchdog that says nothing is the same silent failure one level up -- so it has to
# announce it. Likewise the streak warning: a permanently broken avahi-browse must not look
# identical to a healthy server.
SAYS = {
    "browse_error": "WARNING",
    "healthy": "mDNS watchdog: ON",
}

DRIVER = r'''
import json, os, sys, types

REC = os.environ["REC"]
BROWSE = json.loads(os.environ["BROWSE"])
MAX_SLEEPS = int(os.environ["MAX_SLEEPS"])
calls = {"register": 0, "deallocate": 0, "browse": 0, "sleeps": 0}

import ctypes as _real_ctypes

def _register(*a, **k):
    calls["register"] += 1
    return 0

def _deallocate(*a, **k):
    calls["deallocate"] += 1

class _DLL(object):
    def __init__(self):
        self.DNSServiceRegister = _register
        self.DNSServiceRefDeallocate = _deallocate

_dll = _DLL()
_fake_ctypes = types.ModuleType("ctypes")
for _n in dir(_real_ctypes):
    setattr(_fake_ctypes, _n, getattr(_real_ctypes, _n))
_fake_ctypes.CDLL = lambda *a, **k: _dll
sys.modules["ctypes"] = _fake_ctypes

class _Completed(object):
    def __init__(self, rc, out):
        self.returncode = rc
        self.stdout = out.encode()

def _run(cmd, **kw):
    i = calls["browse"]
    calls["browse"] += 1
    rc, out = BROWSE[i] if i < len(BROWSE) else BROWSE[-1]
    return _Completed(rc, out)

_fake_subprocess = types.ModuleType("subprocess")
_fake_subprocess.run = _run
_fake_subprocess.PIPE = -1
_fake_subprocess.DEVNULL = -3
sys.modules["subprocess"] = _fake_subprocess

import shutil as _real_shutil
_fake_shutil = types.ModuleType("shutil")
for _n in dir(_real_shutil):
    setattr(_fake_shutil, _n, getattr(_real_shutil, _n))
_fake_shutil.which = lambda n: "/usr/bin/" + n
sys.modules["shutil"] = _fake_shutil

import time as _real_time
def _sleep(n):
    calls["sleeps"] += 1
    if calls["sleeps"] > MAX_SLEEPS:
        raise SystemExit(0)
_fake_time = types.ModuleType("time")
for _n in dir(_real_time):
    setattr(_fake_time, _n, getattr(_real_time, _n))
_fake_time.sleep = _sleep
sys.modules["time"] = _fake_time

import io, contextlib
_out = io.StringIO()
source = open(os.environ["HELPER"], encoding="utf-8").read()
try:
    with contextlib.redirect_stdout(_out):
        exec(compile(source, "<helper>", "exec"), {"__name__": "__main__"})
except SystemExit:
    pass
except Exception as exc:
    calls["error"] = "%s: %s" % (type(exc).__name__, exc)

calls["stdout"] = _out.getvalue()
open(REC, "w").write(json.dumps(calls))
'''


def main():
    body = helper_source()
    try:
        compile(body, "<helper>", "exec")
    except SyntaxError as exc:
        print("FAIL: the embedded python helper does not compile: %s" % exc, file=sys.stderr)
        return 1

    if "sys.exit(1)" not in body:
        print("FAIL: the helper no longer exits non-zero when registration fails. The parent's\n"
              "      ~1s waitpid check in dnssd_loader.cpp is how a failed advertisement is\n"
              "      reported at all; without this it reports success unconditionally.",
              file=sys.stderr)
        return 1

    tmp = tempfile.mkdtemp(prefix="mdns-watchdog-")
    helper_path = os.path.join(tmp, "helper.py")
    open(helper_path, "w", encoding="utf-8").write(body)
    driver_path = os.path.join(tmp, "driver.py")
    open(driver_path, "w", encoding="utf-8").write(DRIVER)

    import json
    argv = [str(0), str(0), "", "_altserver._tcp", "", "",
            str(socket.htons(PORT)), TXT_HEX]

    failures = []
    for case, (browse, max_sleeps, want_reg, want_dealloc) in sorted(CASES.items()):
        rec = os.path.join(tmp, "%s.json" % case)
        env = dict(os.environ)
        env.update({"REC": rec, "HELPER": helper_path, "BROWSE": json.dumps(browse),
                    "MAX_SLEEPS": str(max_sleeps),
                    "ALTSERVER_MDNS_RECHECK_SECONDS": "1"})
        proc = subprocess.run([sys.executable, driver_path] + argv,
                              env=env, capture_output=True, text=True, timeout=60)
        if not os.path.exists(rec):
            failures.append("%s: driver produced no result\n    stdout: %s\n    stderr: %s"
                            % (case, proc.stdout[-400:], proc.stderr[-400:]))
            continue
        got = json.loads(open(rec).read())
        if "error" in got:
            failures.append("%s: helper raised %s" % (case, got["error"]))
            continue
        want_says = SAYS.get(case)
        if want_says and want_says not in got.get("stdout", ""):
            failures.append(
                "%s: expected the helper to say %r, but it said:\n      %s"
                % (case, want_says, got.get("stdout", "").strip().replace("\n", "\n      ")))
            continue
        if got["register"] != want_reg or got["deallocate"] != want_dealloc:
            failures.append(
                "%s: expected %d register / %d deallocate, got %d / %d"
                % (case, want_reg, want_dealloc, got["register"], got["deallocate"]))
        else:
            print("  ok  %-13s %d register, %d deallocate, %d browse"
                  % (case, got["register"], got["deallocate"], got["browse"]))

    if failures:
        print("\nmDNS watchdog guard FAILED:\n", file=sys.stderr)
        for f in failures:
            print("  " + f, file=sys.stderr)
        print("\nThe watchdog is what stops a dropped advertisement from going unnoticed for days.\n"
              "See docs/REVIVAL.md for the incident this encodes.", file=sys.stderr)
        return 1

    print("OK: %d watchdog cases pass." % len(CASES))
    return 0


if __name__ == "__main__":
    sys.exit(main())
