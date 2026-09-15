#!/usr/bin/env python3
"""Hold web/installer.py's redaction claim to real output instead of to intent.

WHY THIS EXISTS. installer.py's docstring has always said "Every line is filtered before it leaves
this module." It was not true. The filter was a blocklist built around Apple's *token* names
(GsIdmsToken, adsid, DsPrsId), and it missed the anisette machine identifiers that a successful
sign-in prints one per line:

    MachineID : <80 chars of base64>
    One-Time Password: <base64>
    Local User ID: <64 hex chars>

All three rendered in full into a web page served over plain HTTP on the LAN, which is exactly the
screenshot risk the redaction exists to prevent. Nobody noticed because the docstring asserted the
opposite and nothing checked.

So this test does not check that the filter "looks right". It feeds it lines captured verbatim
from a real install and asserts that no secret VALUE survives anywhere in the output, whatever
rule was supposed to catch it. A new identifier that nothing matches fails here rather than
appearing in a browser.

It also asserts the opposite direction: ordinary progress lines must come through UNCHANGED. A
filter that redacts everything is safe and useless -- the page exists to show a human how far the
install got, and over-redaction would push them back to `docker logs`, which is not filtered at
all.
"""

import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "web"))

try:
    from installer import _redact
except Exception as exc:  # pragma: no cover
    print("FAIL: could not import web/installer.py: %s" % exc)
    sys.exit(1)

# Values that must never appear in output. SYNTHETIC -- every one contains the literal
# string EXAMPLE. They only need the character class and length the redaction rules key
# on. An earlier version of this file used values copied verbatim from a real install
# log while claiming to be scrambled, which published a live anisette machine identity
# to a public repo. Never paste a real captured value in here, even a partial one.
MACHINE_ID = "EXAMPLEmachineIDnotARealAnisetteIdentity0000000000000000000000000000000000000000"
OTP = "EXAMPLEoneTimePasswordNotReal0000000000=="
LOCAL_USER_ID = "EXAMPLE1ocaluser1dEXAMPLE1ocaluser1dEXAMPLE1ocaluser1dEXAMPLE123"
DEVICE_ID = "EXAMPLE1-0000-4000-8000-EXAMPLE00000"
MD_LU = "EXAMPLE1ocaluser1dEXAMPLE1ocaluser1dEXAMPLE1ocaluser1dEXAMPLE123"

SECRETS = [MACHINE_ID, OTP, LOCAL_USER_ID, DEVICE_ID, MD_LU]

# Captured from a real successful install, with the values above substituted in.
MUST_BE_REDACTED = [
    "MachineID : " + MACHINE_ID,
    "One-Time Password: " + OTP,
    "Local User ID: " + LOCAL_USER_ID,
    "Device UDID: " + DEVICE_ID,
    "OTP: %s MachineID: %s" % (OTP, MACHINE_ID),
    'Got anisetteData json: {"X-Apple-I-MD":"%s","X-Apple-I-MD-LU":"%s","X-Apple-I-MD-M":"%s"}'
    % (OTP, MD_LU, MACHINE_ID),
    "Data: <dict><key>adsid</key><string>0001234-5678</string></dict>",
    "Got token for com.apple.gs.idms.pet",
    '  "token" : "eyJhbGciOiJIUzI1NiJ9.%s"' % MACHINE_ID,
]

# Must survive untouched: this is what makes the page worth looking at.
MUST_BE_UNCHANGED = [
    "Fetching anisette data from: http://127.0.0.1:6969",
    "Received response status code: 200",
    "Building anisetteData obj...",
    "Device Description: <MacBookPro13,2> <macOS;13.1;22C65>",
    "Date: 2026-09-15T01:39:37Z",
    "Unzipping .ipa...",
    "Enter two factor code:",
    "Installation Succeeded",
    "Finished handling request!",
    "Removed profile: com.rileytestut.AltStore (PROFILE0-1111-4111-8111-PROFILE11111)",
    "ERROR: Could not install app: -22421",
]


def main():
    problems = []

    for line in MUST_BE_REDACTED:
        out = _redact(line)
        if out is None:
            continue  # dropped entirely is strictly safer than masked
        for secret in SECRETS:
            if secret in out:
                problems.append(
                    "a secret value survived redaction.\n"
                    "      input:  %s\n"
                    "      output: %s\n"
                    "      leaked: %s" % (line[:90], out[:90], secret[:40] + "..."))
                break
        else:
            if out == line:
                problems.append("line passed through completely unfiltered:\n      %s" % line[:90])

    for line in MUST_BE_UNCHANGED:
        out = _redact(line)
        if out is None:
            problems.append("a useful progress line was DROPPED, making the page less usable than "
                            "docker logs:\n      %s" % line[:90])
        elif out != line:
            problems.append("a useful progress line was altered:\n      in:  %s\n      out: %s"
                            % (line[:80], out[:80]))

    # Belt and braces: no long base64/hex run should ever reach the page, whatever its label.
    for line in MUST_BE_REDACTED:
        out = _redact(line) or ""
        for blob in re.findall(r"[A-Za-z0-9+/]{40,}={0,2}", out):
            problems.append("an unlabelled %d-char blob reached the output:\n      %s"
                            % (len(blob), blob[:60]))

    if problems:
        print("\n".join("FAIL: " + p for p in problems))
        print("\n%d problem(s). web/installer.py's docstring promises every line is filtered; "
              "that promise is what this test enforces." % len(problems))
        return 1

    # The same filter now sits in the container's log path (docker/redact-log.py), so check the
    # SHIPPED script, not just the function it imports. It rewrites the hardcoded
    # /opt/altserver-web path for this run; everything else executes exactly as it does in the
    # image.
    filt = os.path.join(ROOT, "docker", "redact-log.py")
    if not os.path.exists(filt):
        print("FAIL: docker/redact-log.py is missing; container logs would be unfiltered")
        return 1

    src = open(filt).read().replace("/opt/altserver-web", os.path.join(ROOT, "web"))
    feed = "\n".join(MUST_BE_REDACTED + MUST_BE_UNCHANGED) + "\n"
    run = subprocess.run([sys.executable, "-c", src], input=feed,
                         capture_output=True, text=True)
    if run.returncode != 0:
        print("FAIL: docker/redact-log.py exited %d\n%s" % (run.returncode, run.stderr.rstrip()))
        return 1

    out = run.stdout
    for secret in SECRETS:
        if secret in out:
            print("FAIL: a secret survived docker/redact-log.py, so it would reach `docker logs`.\n"
                  "      leaked: %s..." % secret[:40])
            return 1
    for line in MUST_BE_UNCHANGED:
        if line not in out:
            print("FAIL: docker/redact-log.py dropped or altered a progress line:\n      %s" % line)
            return 1
    # It must reuse installer.py rather than carry its own copy of the rules, or the two drift.
    if "from installer import _redact" not in open(filt).read():
        print("FAIL: docker/redact-log.py no longer imports _redact from installer.py.\n"
              "      Two copies of a redaction filter drift, and the unwatched one rots.")
        return 1
    print("docker/redact-log.py filters the container log path using the same implementation.")

    print("%d sensitive lines redacted, %d progress lines preserved verbatim."
          % (len(MUST_BE_REDACTED), len(MUST_BE_UNCHANGED)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
