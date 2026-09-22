#!/usr/bin/env python3
"""Check that every tracked text file is pure ASCII.

WHY THIS EXISTS. The docs had accumulated 264 typographic characters that are not on a US
keyboard -- 218 em dashes, 28 arrows, 14 ellipses, an en dash and a "greater than or equal".
None of them can be typed without a compose key or a copy-paste, so every one was a marker of
machine-generated prose rather than something a maintainer wrote. They are now spelled in ASCII
and this guard keeps them from coming back one commit at a time.

It is not only a style rule. Two of the characters were actively load-bearing:

  * A line that STARTS with an em dash is a wrapped sentence, but a line that starts with "- "
    is a Markdown list item. Three continuation lines had a leading em dash, so a naive
    search-and-replace silently split those paragraphs into bullets -- valid Markdown that
    renders wrongly, which no syntax check would ever flag.

  * GitHub's heading slugger STRIPS an em dash but KEEPS a hyphen, so retitling
    "### Path A <em dash> amd64" to "### Path A - amd64" moves the anchor from
    #path-a--amd64 to #path-a---amd64. Nine headings were affected. Any in-repo link to one
    would have broken; they are checked because next time there may be some.

Vendored code is NOT scanned and must not be: `git ls-files` reports submodules as gitlinks, so
upstream's own text never reaches this check. Patches to vendored sources belong in the rewriter
scripts anyway.
"""

import os
import subprocess
import sys
import unicodedata

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

# Written as escapes ON PURPOSE. Spelling these as literal characters would make this file fail
# its own check, which is a property worth keeping.
SUGGESTIONS = {
    "\u2014": "-",     # em dash
    "\u2013": "-",     # en dash
    "\u2026": "...",   # horizontal ellipsis
    "\u2192": "->",    # rightwards arrow
    "\u2190": "<-",    # leftwards arrow
    "\u2265": ">=",    # greater-than or equal to
    "\u2264": "<=",
    "\u2018": "'",     # curly quotes, in case a paste ever brings them in
    "\u2019": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u00a0": " ",     # non-breaking space: invisible, and breaks code blocks when pasted
    "\u00d7": "x",
    "\u2022": "*",     # bullet
    "\u00b7": "*",
}

# Files that genuinely need a non-ASCII byte. Empty, and should stay that way; a real case would
# be something like a UTF-8 encoding test fixture. Add the path here with a comment saying why.
ALLOW = set()


def tracked_files():
    out = subprocess.run(
        ["git", "-C", ROOT, "ls-files"], capture_output=True, text=True, check=True
    ).stdout.splitlines()
    return [f for f in out if f]


def main():
    problems = []
    skipped_binary = []
    scanned = 0

    for rel in tracked_files():
        if rel in ALLOW:
            continue
        path = os.path.join(ROOT, rel)
        if os.path.isdir(path) or not os.path.exists(path):
            continue  # submodule gitlink
        raw = open(path, "rb").read()
        if b"\x00" in raw:
            skipped_binary.append(rel)
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            problems.append("%s: not valid UTF-8 (%s)" % (rel, exc))
            continue
        scanned += 1
        for lineno, line in enumerate(text.split("\n"), 1):
            for col, ch in enumerate(line, 1):
                if ord(ch) < 128:
                    continue
                try:
                    name = unicodedata.name(ch)
                except ValueError:
                    name = "unnamed"
                fix = SUGGESTIONS.get(ch)
                hint = ("write %r instead" % fix) if fix else "replace with an ASCII equivalent"
                problems.append(
                    "%s:%d:%d: U+%04X %s -- %s\n      %s"
                    % (rel, lineno, col, ord(ch), name, hint, line.strip()[:100])
                )

    if skipped_binary:
        print("Skipped %d binary file(s): %s" % (len(skipped_binary), ", ".join(skipped_binary)))

    if problems:
        print("Non-ASCII characters in tracked text files:\n", file=sys.stderr)
        for p in problems:
            print("  " + p, file=sys.stderr)
        print(
            "\n%d problem(s). These characters are not on a keyboard; use the ASCII spelling.\n"
            "Careful with two of them:\n"
            "  * Do not leave a dash at the START of a wrapped line -- '- ' begins a Markdown\n"
            "    list item and will split the paragraph into bullets.\n"
            "  * Changing a HEADING changes its anchor slug (an em dash is stripped, a hyphen is\n"
            "    kept), so re-check every '](#...)' link that points at it." % len(problems),
            file=sys.stderr,
        )
        return 1

    print("OK: %d tracked text file(s) are pure ASCII." % scanned)
    return 0


if __name__ == "__main__":
    sys.exit(main())
