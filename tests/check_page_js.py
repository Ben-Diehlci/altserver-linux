#!/usr/bin/env python3
"""Parse the JavaScript in every generated page.

WHY THIS EXISTS. An escaped newline once collapsed into a LITERAL newline inside a JavaScript
string literal in the install page. That is a syntax error, and a syntax error anywhere in a
<script> block kills the entire block -- so one bad character produced four symptoms that looked
unrelated: the form would not submit, no log ever appeared, the state stayed on its placeholder,
and the UDID never auto-filled.

Every layer that could have caught it was green. The backend was provably fine (curl against the
API returned correct validation errors), no exception reached the container log, and installer.py
had been tested against a mock. The gap was that nothing between Python and the browser ever
PARSED the thing the browser actually runs.

This closes that gap. It does not check behaviour -- only that the JS the server emits is
syntactically valid, which is precisely the failure that got through.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web"))

SCRIPT_RE = re.compile(r"<script>(.*?)</script>", re.DOTALL)


def main():
    node = shutil.which("node")
    if node is None:
        # In CI this is a hard failure: silently skipping is how the original bug shipped.
        if os.environ.get("GITHUB_ACTIONS") == "true":
            print("FAIL: node is not available on this runner, so the JS was never parsed.")
            return 1
        print("SKIP: node is not installed locally. CI will run this for real.")
        return 0

    import server  # noqa: E402  -- path is set up above

    pages = [
        ("status page  (PAGE)", server.PAGE),
        ("pairing page (PAIRING_PAGE)", server.PAIRING_PAGE),
        ("install page (INSTALL_PAGE)", server.INSTALL_PAGE),
    ]

    failures = 0
    for name, html in pages:
        blocks = SCRIPT_RE.findall(html)
        if not blocks:
            print("FAIL  %s: no <script> block found at all -- did the page stop emitting one?"
                  % name)
            failures += 1
            continue

        for i, js in enumerate(blocks):
            with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
                fh.write(js)
                path = fh.name
            try:
                p = subprocess.run([node, "--check", path], capture_output=True, text=True)
            finally:
                os.unlink(path)

            label = name if len(blocks) == 1 else "%s block %d" % (name, i + 1)
            if p.returncode == 0:
                print("ok    %s: %d lines of JS parse" % (label, js.count("\n") + 1))
            else:
                # node reports the temp filename, resolved -- on macOS /var/... becomes
                # /private/var/... -- so strip both spellings. The line number is what matters.
                msg = (p.stderr or p.stdout)
                for spelling in (os.path.realpath(path), path):
                    msg = msg.replace(spelling, label)
                print("FAIL  %s does not parse:\n%s" % (label, msg))
                failures += 1

    if failures:
        print("\n%d page(s) emit broken JavaScript. The browser would run NONE of the script "
              "block containing the error." % failures)
        return 1
    print("\nAll pages parse.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
