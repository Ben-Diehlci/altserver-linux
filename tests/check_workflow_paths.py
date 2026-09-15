#!/usr/bin/env python3
"""Check that everything built INTO the image also triggers an image build.

Two ways in, and both are checked here:
  * COPY-ed from the build context (the web UI, the entrypoint);
  * COMPILED by the build stage's `make`, which reaches far outside the Dockerfile's COPY lines.

WHY THIS EXISTS. build_image.yml has a `paths:` filter, and it once omitted `web/**` -- which is
COPY-ed into the runtime image. Five consecutive commits of web UI fixes were therefore never
built into a published image. Nothing indicated anything was wrong: CI stayed green, the stack
deployed successfully, and `:latest` does not re-pull on its own, so the deployment kept serving
known-broken code. The result was fixed bugs that still reproduced, which sent us back to debug
code that was already correct.

A guard for this CANNOT live in build_image.yml itself -- that workflow is skipped by exactly the
bug being detected. It runs from build.yml, which has no paths filter.

The compile-input half exists because the same omission recurred one level down: `libraries/**`
was missing, so bumping a vendored submodule (libimobiledevice, libplist, libusbmuxd, ...) changed
the SHIPPED BINARY without rebuilding the image. The compile inputs are therefore DERIVED from the
makefiles rather than listed here -- a hardcoded list is the very thing that goes stale.
"""

import os
import posixpath
import re
import subprocess
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DOCKERFILE = os.path.join(ROOT, "Dockerfile")
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "build_image.yml")


def copied_context_paths():
    """Source paths the Dockerfile copies from the BUILD CONTEXT (not from an earlier stage)."""
    out = []
    for n, raw in enumerate(open(DOCKERFILE), 1):
        line = raw.strip()
        if not re.match(r"^(COPY|ADD)\s", line):
            continue
        if "--from=" in line:
            continue  # comes from another stage, not from the repo
        parts = [p for p in line.split()[1:] if not p.startswith("--")]
        if len(parts) < 2:
            continue
        for src in parts[:-1]:
            out.append((n, src))
    return out


# `NAME := $(OTHER)/segment` -- the only assignment form that needs resolving here.
VAR_ASSIGN = re.compile(r"^\s*([A-Za-z_]\w*)\s*:?=\s*\$\(([A-Za-z_]\w*)\)/([\w.-]+)\s*$")
# `$(NAME)/a/b/c` anywhere on a line.
VAR_USE = re.compile(r"\$\(([A-Za-z_]\w*)\)((?:/[^\s/:()'\"]+)+)")
# A path segment make expands later (wildcards, pattern rules, nested variables) names no fixed
# directory, so it cannot be resolved to a repo path.
UNRESOLVED = re.compile(r"[*%$?\[\]]")


def makefile_paths():
    """Every makefile the build reads, repo-relative. These are build inputs in their own right."""
    found = ["Makefile"]
    for dirpath, _, names in os.walk(os.path.join(ROOT, "makefiles")):
        for n in sorted(names):
            if n.endswith(".mak"):
                found.append(os.path.relpath(os.path.join(dirpath, n), ROOT).replace(os.sep, "/"))
    return found


def compile_inputs():
    """Top-level repo paths the build COMPILES, as {path: "where it is referenced"}.

    Derived, not listed. `make` pulls in whole source trees the Dockerfile never names -- the
    libraries/ submodules are compiled straight into the static binary -- so any hand-written list
    here would fall out of date exactly the way the paths: filter itself did.
    """
    # Blank out comments rather than dropping the lines, so reported line numbers stay real. A
    # commented-out reference must not count: Makefile:50 still names $(LIB_DIR) in dead code.
    files = {rel: ["" if l.lstrip().startswith("#") else l
                   for l in open(os.path.join(ROOT, rel)).read().splitlines()]
             for rel in makefile_paths()}

    # MAIN_DIR is the repo root, defined once in makefiles/main.mak, which every other makefile
    # includes. Vars hanging off it -- LIB_DIR, UPSTREAM_DIR, SHIM_DIR -- mean the same thing
    # everywhere, so they resolve once, globally.
    shared = {"MAIN_DIR": ""}
    for _ in range(4):
        for lines in files.values():
            for raw in lines:
                m = VAR_ASSIGN.match(raw)
                if m and m.group(2) in shared and m.group(1) not in shared:
                    shared[m.group(1)] = (shared[m.group(2)] + "/" + m.group(3)).lstrip("/")

    found = {}
    for rel in files:
        found.setdefault(rel.split("/")[0], rel)

    for rel, lines in files.items():
        # ROOT_DIR is `$(dir $(abspath $(lastword $(MAKEFILE_LIST))))` -- the directory of the
        # makefile being read, NOT the repo root. Every sub-makefile redefines it to its own
        # directory, so it has to be resolved per file or their paths land at the repo root.
        dirs = dict(shared, ROOT_DIR=os.path.dirname(rel))
        for _ in range(3):
            for raw in lines:
                m = VAR_ASSIGN.match(raw)
                if m and m.group(2) in dirs and m.group(1) not in dirs:
                    dirs[m.group(1)] = (dirs[m.group(2)] + "/" + m.group(3)).lstrip("/")

        for n, raw in enumerate(lines, 1):
            for var, tail in VAR_USE.findall(raw):
                if var not in dirs:
                    continue
                seg = tail.strip("/").split("/")[0]
                if UNRESOLVED.search(seg):
                    continue
                path = posixpath.normpath((dirs[var] + "/" + seg).lstrip("/"))
                if path.startswith("..") or path == ".":
                    continue  # outside the build context; nothing the filter could name
                found.setdefault(path.split("/")[0], "%s:%d" % (rel, n))
    return found


def filter_patterns():
    """The `paths:` list from the push trigger."""
    lines = open(WORKFLOW).read().splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.strip() == "paths:")
    except StopIteration:
        print("FAIL: build_image.yml has no `paths:` block. If the filter was removed "
              "deliberately, delete this check too.")
        sys.exit(1)

    indent = len(lines[start]) - len(lines[start].lstrip())
    pats = []
    for l in lines[start + 1:]:
        if not l.strip() or l.strip().startswith("#"):
            continue
        if len(l) - len(l.lstrip()) <= indent:
            break
        if l.strip().startswith("- "):
            pats.append(l.strip()[2:].strip().strip("'\""))
    return pats


def gitlink_paths():
    """Repo-relative paths of submodules, i.e. entries git stores with mode 160000."""
    try:
        out = subprocess.run(["git", "-C", ROOT, "ls-files", "-s"],
                             capture_output=True, text=True, check=True).stdout
    except Exception:
        return set()
    found = set()
    for line in out.splitlines():
        parts = line.split(maxsplit=3)
        if len(parts) == 4 and parts[0] == "160000":
            found.add(parts[3].strip())
    return found


def covered(src, pats, exact_only=False):
    """Which filter pattern matches `src`, or None.

    `exact_only` is for paths git reports AS THEMSELVES rather than via a child -- a submodule
    bump changes one gitlink entry, so the push reports the bare path (`upstream_repo`), never
    `upstream_repo/<anything>`. GitHub's `upstream_repo/**` requires that `upstream_repo/` prefix
    and so would NOT match, while a bare `upstream_repo` does. Treating the two as equivalent
    would pass a filter that never fires.
    """
    src = src.rstrip("/")
    for p in pats:
        pat = p.rstrip("/")
        if exact_only:
            if src == pat:
                return p
            continue
        base = pat.removesuffix("/**").removesuffix("/*")
        if src == base or src.startswith(base + "/"):
            return p
    return None


def main():
    pats = filter_patterns()
    failures = []

    for lineno, src in copied_context_paths():
        if src == ".":
            # The build stage copies the whole context and runs `make`, so what actually ends up in
            # the binary is decided by the makefiles, not by this COPY. Listing '**' in the filter
            # would rebuild the image for a README typo, so it names the compile inputs instead --
            # and those are derived below rather than assumed.
            gitlinks = gitlink_paths()
            for path, where in sorted(compile_inputs().items()):
                # A compile input that IS a submodule is reported by git as the bare path, so a
                # `path/**` glob would never match it. One that merely CONTAINS submodules is
                # fine: bumping libraries/libimobiledevice reports that full path, which
                # `libraries/**` does match.
                is_gitlink = path.rstrip("/") in gitlinks
                hit = covered(path, pats, exact_only=is_gitlink)
                if hit:
                    print("ok    %-28s compiled in, covered by %r%s"
                          % (path, hit, " (submodule: exact match required)" if is_gitlink else ""))
                elif is_gitlink:
                    loose = covered(path, pats)
                    failures.append(
                        "%r is a SUBMODULE compiled into the binary (referenced by %s). A bump "
                        "changes only its gitlink, so the push reports the bare path %r -- "
                        "never %r.\n      build_image.yml must list it as a bare entry; %s would "
                        "not match and the image would silently keep the old binary."
                        % (path, where, path, path + "/...",
                           ("the glob %r" % loose) if loose else "a glob"))
                else:
                    failures.append(
                        "Dockerfile:%d `COPY . /src` + `make` COMPILES %r into the binary "
                        "(referenced by %s), but build_image.yml's paths: filter does not match "
                        "it.\n      Changing it -- bumping a submodule, say -- would change the "
                        "SHIPPED BINARY without rebuilding the image, and the deployment would "
                        "keep running the old one with CI green." % (lineno, path, where))
            continue

        hit = covered(src, pats)
        if hit:
            print("ok    %-28s covered by %r" % (src, hit))
        else:
            failures.append(
                "Dockerfile:%d copies %r into the image, but build_image.yml's paths: filter "
                "does not match it.\n      Changing it would NOT rebuild the image, and the "
                "deployment would keep serving the old copy with CI green." % (lineno, src))

    # The workflow must also rebuild when its own definition changes.
    if not covered(".github/workflows/build_image.yml", pats):
        failures.append("build_image.yml does not list itself, so changes to the build are "
                        "not themselves built.")
    else:
        print("ok    %-28s covered" % "build_image.yml (self)")

    if failures:
        print("\n" + "\n\n".join("FAIL: " + f for f in failures))
        return 1
    print("\nEvery build input triggers a rebuild.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
