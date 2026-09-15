#!/usr/bin/env python3
"""Hold web/installer.py's redaction claim to real output instead of to intent.

WHY THIS EXISTS. installer.py's docstring has always said "Every line is filtered before it leaves
this module." It was not true. The filter was a blocklist built around Apple's *token* names
(GsIdmsToken, adsid, DsPrsId), and it missed the anisette machine identifiers that a successful
sign-in prints one per line:

    MachineID : EXAMPLEmachine...
    One-Time Password: EXAMPLEotp...
    Local User ID: EXAMPLElocaluser...

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
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "web"))

try:
    from installer import _redact
except Exception as exc:  # pragma: no cover
    print("FAIL: could not import web/installer.py: %s" % exc)
    sys.exit(1)

# Values that must never appear in output. Shapes are real; digits are scrambled.
MACHINE_ID = "EXAMPLEmachineIDnotARealAnisetteIdentity0000000000000000000000000000000000000000"
OTP = "EXAMPLEoneTimePasswordNotReal000000000=="
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

    print("%d sensitive lines redacted, %d progress lines preserved verbatim."
          % (len(MUST_BE_REDACTED), len(MUST_BE_UNCHANGED)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
