#!/usr/bin/env python3
"""Check that editing a source rewriter actually rebuilds what it generates.

WHY THIS EXISTS. This project does not patch vendored code in the submodules. It rewrites the
sources textually at compile time, with makefiles/rewrite_*.py -- so a rewriter IS the patch, and
editing one is the documented way to change vendored behaviour.

Three of the four rules that run a rewriter listed only the vendored source as a prerequisite, not
the rewriter itself. Make therefore compared the vendored source against the already-patched
output, found the output newer, and did nothing. Measured before the fix:

    touch makefiles/rewrite_altserver_source.py
    make            ->  regenerates 0 files
    make clean      ->  patched tree still present, 35 files
    make            ->  regenerates 0 files

So editing a rewriter had NO effect on the build, and the build reported complete success with the
previous patch compiled in. `make clean` did not help because it never removed the patched trees.
The only thing that picked up a change was deleting build/ outright, which is why CI never saw it:
CI always starts from an empty tree. Locally it can silently waste hours -- you edit a rewriter,
rebuild, redeploy, and observe the old behaviour with nothing anywhere indicating why.

This also checks .DELETE_ON_ERROR, which is the same class one step along: the rewriters exit
non-zero on a pattern mismatch BY DESIGN, and `>` has already created the output file by then.
Without .DELETE_ON_ERROR make leaves that truncated file in place and treats it as finished, so
the next build compiles a half-written source.
"""

import os
import re
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

MAKEFILES = [
    os.path.join(ROOT, "Makefile"),
    os.path.join(ROOT, "makefiles", "AltSign-build", "AltSign.mak"),
    os.path.join(ROOT, "makefiles", "libimobiledevice-build", "libimobiledevice.mak"),
    os.path.join(ROOT, "makefiles", "dnssd_loader-build", "dnssd_loader.mak"),
]

RULE_RE = re.compile(r"^([^\t#][^:=]*?):(?!=)(.*)$")
REWRITER_RE = re.compile(r"(rewrite_[a-z_]+\.py)")

problems = []
checked = 0


def rules_with_recipes(path):
    """Yield (lineno, targets, prerequisites, recipe_lines) for each rule in a makefile."""
    lines = open(path, encoding="utf-8").read().split("\n")
    i = 0
    while i < len(lines):
        m = RULE_RE.match(lines[i])
        if not m:
            i += 1
            continue
        targets, prereqs = m.group(1), m.group(2)
        recipe, j = [], i + 1
        while j < len(lines) and (lines[j].startswith("\t") or not lines[j].strip()):
            if lines[j].startswith("\t"):
                recipe.append(lines[j])
            j += 1
        if recipe:
            yield i + 1, targets, prereqs, recipe
        i = j if j > i else i + 1


for mk in MAKEFILES:
    if not os.path.exists(mk):
        problems.append("%s does not exist" % mk)
        continue
    rel = os.path.relpath(mk, ROOT)
    text = open(mk, encoding="utf-8").read()
    runs_a_rewriter = False

    for lineno, targets, prereqs, recipe in rules_with_recipes(mk):
        scripts = set()
        for line in recipe:
            if line.lstrip().startswith("#"):
                continue
            scripts.update(REWRITER_RE.findall(line))
        if not scripts:
            continue
        runs_a_rewriter = True
        for script in scripts:
            checked += 1
            if script in prereqs:
                print("  ok  %s:%d  %s is a prerequisite of %s"
                      % (rel, lineno, script, targets.strip()))
            else:
                problems.append(
                    "%s:%d: the rule for %s RUNS %s but does not DEPEND on it.\n"
                    "      Editing %s will regenerate nothing and the build will succeed with\n"
                    "      the previous patch compiled in. Add it to the prerequisites:\n"
                    "          %s: <sources> $(ROOT_DIR)/.../%s"
                    % (rel, lineno, targets.strip(), script, script, targets.strip(), script))

    # Either protection is acceptable. An atomic write is in fact STRONGER than
    # .DELETE_ON_ERROR: the target is only ever created by the final mv, so it survives an
    # interruption (SIGKILL, a full disk) that a deleted-on-error target would not.
    if runs_a_rewriter:
        checked += 1
        atomic = re.search(r"rewrite_[a-z_]+\.py.*>\s*\$@\.[^\s]*tmp.*&&.*mv", text)
        if ".DELETE_ON_ERROR" in text:
            print("  ok  %s has .DELETE_ON_ERROR" % rel)
        elif atomic:
            print("  ok  %s writes its rewriter output atomically" % rel)
        else:
            problems.append(
                "%s runs a rewriter with neither .DELETE_ON_ERROR nor an atomic write. The "
                "rewriters exit non-zero on a pattern mismatch BY DESIGN, and `>` has already "
                "created the output by then, so make would leave a truncated file and treat it "
                "as finished." % rel)


# Every generated tree must be removed by a clean target, or `make clean && make` rebuilds from
# stale rewritten sources and is not a clean build at all.
TREES = {
    "Makefile": ["$(main_patched_root)"],
    os.path.join("makefiles", "AltSign-build", "AltSign.mak"): ["$(ALTSIGN_NEWROOT)",
                                                                "$(LDID_NEWROOT)"],
}
for rel, trees in TREES.items():
    path = os.path.join(ROOT, rel)
    text = open(path, encoding="utf-8").read()
    for tree in trees:
        checked += 1
        if re.search(r"rm\s+-rf\s+" + re.escape(tree), text):
            print("  ok  %s  clean removes %s" % (rel, tree))
        else:
            problems.append(
                "%s: `clean` does not `rm -rf %s`, so `make clean && make` rebuilds every object "
                "from the stale rewritten sources." % (rel, tree))

if problems:
    print("\n%d problem(s):\n" % len(problems), file=sys.stderr)
    for p in problems:
        print("  " + p + "\n", file=sys.stderr)
    print("A rewriter IS the patch in this project. If editing one does not rebuild, the "
          "documented way to change vendored code silently does nothing.", file=sys.stderr)
    sys.exit(1)

print("OK: %d rewriter dependency/clean invariant(s) hold." % checked)
