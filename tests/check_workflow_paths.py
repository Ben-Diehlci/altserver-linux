#!/usr/bin/env python3
"""Check that everything COPY-ed into the image also triggers an image build.

WHY THIS EXISTS. build_image.yml has a `paths:` filter, and it once omitted `web/**` -- which is
COPY-ed into the runtime image. Five consecutive commits of web UI fixes were therefore never
built into a published image. Nothing indicated anything was wrong: CI stayed green, the stack
deployed successfully, and `:latest` does not re-pull on its own, so the deployment kept serving
known-broken code. The result was fixed bugs that still reproduced, which sent us back to debug
code that was already correct.

A guard for this CANNOT live in build_image.yml itself -- that workflow is skipped by exactly the
bug being detected. It runs from build.yml, which has no paths filter.
"""

import os
import re
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


def covered(src, pats):
    src = src.rstrip("/")
    for p in pats:
        base = p.rstrip("/").removesuffix("/**").removesuffix("/*")
        if src == base or src.startswith(base + "/"):
            return p
    return None


def main():
    pats = filter_patterns()
    failures = []

    for lineno, src in copied_context_paths():
        if src == ".":
            # The build stage copies the whole context. Listing '**' here would rebuild the image
            # for a README typo, so the filter deliberately names the compile inputs instead --
            # which this check verifies directly.
            missing = [need for need in ("src", "shims", "makefiles", "Makefile", "upstream_repo")
                       if not covered(need, pats)]
            if missing:
                failures.append("Dockerfile:%d `COPY . /src` compiles the repo, but the filter "
                                "does not list: %s" % (lineno, ", ".join(missing)))
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
