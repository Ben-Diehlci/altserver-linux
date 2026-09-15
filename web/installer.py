"""Supervises an AltServer install so a browser can drive it.

WHY A SUPERVISOR. AltServer reads the two-factor code from stdin
(`std::cin >> _verificationCode`), and a detached container has no terminal. A parent process owns
its child's stdin, so a web form can deliver the code to a read that has no tty. That is the whole
trick, and it is why the UI does not need any change to the C++.

It also handles the other stdin consumer: `ShowAlert` ends in `getchar()` ("Press any key to
continue..."), which would block forever against a pipe that stays open. We answer it.

REDACTION IS NOT OPTIONAL HERE. A successful sign-in prints Apple's full account record to stdout
-- real name, phone number, `adsid`, and a dozen bearer tokens including `com.apple.gs.icloud.auth`
with a 31536000-second (one year) lifetime. Those reach `docker logs` and journald already, which
is bad enough; rendering them into a browser page that someone screenshots would be worse. Every
line is filtered before it leaves this module, and `tests/check_redaction.py` holds that claim to
real captured output rather than to intent -- an earlier version of this filter asserted exactly
the same thing while printing the anisette MachineID, one-time password and local user ID in full.
"""

import os
import re
import subprocess
import threading
import time

IDLE, RUNNING, AWAITING_2FA, SUCCEEDED, FAILED = "idle", "running", "awaiting_2fa", "succeeded", "failed"

# AltServer asks for the code with this, then blocks on std::cin.
PROMPT_2FA = "Enter two factor code"
# ShowAlert prints this and then calls getchar(), which blocks against a pipe.
PROMPT_ANYKEY = "Press any key to continue"

# Lines that carry credentials or account data. Dropped or replaced, never shown.
_SENSITIVE = re.compile(
    r"(GsIdmsToken|adsid|DsPrsId|phoneNumber|<data>|com\.apple\.gs\.[a-z.]+\s*</key>|"
    r"\"token\"|<key>token</key>|sessionKey|Got token for)",
    re.IGNORECASE,
)
# The account record arrives as one enormous line beginning with "Data: <dict>".
_ACCOUNT_DUMP = re.compile(r"^\s*Data:\s*<dict>")
# The SRP debug spew: hundreds of "Byte:-42" lines.
_BYTE_NOISE = re.compile(r"^\s*(Byte:-?\d+|HMAC_OUT:|NP:)\s*$")

# The whole anisette payload on one line. It carries every X-Apple-I-* header at once, so there is
# nothing in it worth keeping.
_ANISETTE_JSON = re.compile(r"^\s*Got anisetteData json:", re.IGNORECASE)

# Anisette machine identifiers, printed one per line by a successful sign-in. Not credentials in
# the password sense, but together they are the stable identity this server presents to Apple, and
# Apple rate-limits and locks accounts per identity. Until 2026-09-15 these rendered in full into
# the browser while the docstring above claimed every line was filtered.
#
# The VALUE is masked and the LABEL kept, so the log still shows how far the install got.
_LABELLED_SECRET = re.compile(
    r"(?i)\b("
    r"MachineID|One-Time Password|OTP|Local User ID|Device UDID|"
    r"X-Apple-I-MD(?:-M|-LU|-RINFO)?|X-Mme-Device-Id|X-Apple-I-SRL-NO"
    r")(\s*[:=]\s*)(\S[^\s]*(?:[^\S\n][^\s]*)*?)(?=\s\b(?:MachineID|OTP|Local User ID)\s*[:=]|$)"
)

# Backstop for identifiers not enumerated above: any long unbroken base64/hex-looking run. Kept
# deliberately long (40) so ordinary words, paths and UUIDs are untouched.
_LONG_BLOB = re.compile(r"(?<![\w/.-])([A-Za-z0-9+/]{40,}={0,2})(?![\w/.-])")


def _mask_labelled(line):
    return _LABELLED_SECRET.sub(lambda m: m.group(1) + m.group(2) + "[withheld]", line)


def _redact(line):
    """Return a display-safe line, or None to drop it entirely.

    Order matters: whole-line rules first, then value masking, then the catch-all blob rule, so a
    line that is wholly unsafe is never merely partially masked.
    """
    if _BYTE_NOISE.match(line):
        return None
    if _ACCOUNT_DUMP.match(line):
        return "[Apple account record received -- withheld, it contains long-lived tokens]"
    if _ANISETTE_JSON.match(line):
        return "Got anisetteData json: [withheld -- carries every X-Apple-I-* identifier]"
    if _SENSITIVE.search(line):
        return "[line withheld: contains credentials or account data]"

    line = _mask_labelled(line)
    line = _LONG_BLOB.sub("[withheld]", line)

    # Anything long that survived every check above.
    if len(line) > 400:
        return line[:200] + " ... [truncated]"
    return line


class Installer:
    """Runs one install at a time and exposes its state to the web layer."""

    def __init__(self, binary="/usr/local/bin/AltServer", ipa="/data/AltStore.ipa"):
        self.binary = binary if os.path.exists(binary) else "AltServer"
        self.ipa = ipa
        self._lock = threading.Lock()
        self._reset()

    def _reset(self):
        self.state = IDLE
        self.lines = []
        self.error = ""
        self.started_at = None
        self._proc = None

    def _emit(self, text):
        shown = _redact(text.rstrip("\n"))
        if shown is None:
            return
        with self._lock:
            self.lines.append(shown)
            # Bound the buffer; an install is chatty and nobody reads 5000 lines.
            if len(self.lines) > 400:
                del self.lines[:-400]

    def start(self, udid, apple_id, password, anisette_url=None):
        with self._lock:
            if self.state in (RUNNING, AWAITING_2FA):
                return False, "An install is already running."
        if not os.path.exists(self.ipa):
            return False, "No IPA at %s. The container fetches it on start; check its logs." % self.ipa
        if not (udid and apple_id and password):
            return False, "UDID, Apple ID and password are all required."

        self._reset()
        self.state = RUNNING
        self.started_at = time.time()

        env = dict(os.environ)
        if anisette_url:
            env["ALTSERVER_ANISETTE_SERVER"] = anisette_url
        # Credentials go through the environment, never argv -- argv is world-readable in `ps`.
        env["ALTSERVER_UDID"] = udid
        env["ALTSERVER_APPLE_ID"] = apple_id
        env["ALTSERVER_APPLE_PASSWORD"] = password

        try:
            self._proc = subprocess.Popen(
                [self.binary, self.ipa],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                env=env, text=True, bufsize=1,
            )
        except Exception as exc:
            self.state = FAILED
            self.error = "Could not start AltServer: %s" % exc
            return False, self.error

        threading.Thread(target=self._pump, daemon=True).start()
        return True, ""

    def _pump(self):
        proc = self._proc
        try:
            for raw in proc.stdout:
                line = raw.rstrip("\n")
                self._emit(line)

                if PROMPT_2FA in line:
                    with self._lock:
                        self.state = AWAITING_2FA

                elif PROMPT_ANYKEY in line:
                    # getchar() is waiting. Answer it so the process can finish rather than hang.
                    try:
                        proc.stdin.write("\n")
                        proc.stdin.flush()
                    except Exception:
                        pass
        except Exception as exc:
            self._emit("[supervisor: error reading output: %s]" % exc)

        rc = proc.wait()
        text = "\n".join(self.lines)
        with self._lock:
            # AltServer exits 0 even on failure (it catches, logs, prints "Finished!" and falls off
            # the end of main), so the exit code is not trustworthy -- read the output instead.
            if "Installation Succeeded" in text or "Installed app" in text:
                self.state = SUCCEEDED
            elif re.search(r"Could not install|Error:|error code", text, re.IGNORECASE):
                self.state = FAILED
                self.error = "AltServer reported a failure -- see the log below."
            else:
                self.state = SUCCEEDED if rc == 0 else FAILED
                if self.state == FAILED:
                    self.error = "AltServer exited with status %s." % rc

    def submit_code(self, code):
        with self._lock:
            if self.state != AWAITING_2FA:
                return False, "Not waiting for a code right now."
        code = (code or "").strip()
        if not re.fullmatch(r"\d{6}", code):
            return False, "The code should be six digits."
        try:
            self._proc.stdin.write(code + "\n")
            self._proc.stdin.flush()
        except Exception as exc:
            return False, "Could not send the code: %s" % exc
        with self._lock:
            self.state = RUNNING
        self._emit("[supervisor: two-factor code submitted]")
        return True, ""

    def cancel(self):
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            return True
        return False

    def snapshot(self):
        with self._lock:
            return {
                "state": self.state,
                "error": self.error,
                "lines": list(self.lines),
                "elapsed": int(time.time() - self.started_at) if self.started_at else 0,
            }


INSTALLER = Installer()
