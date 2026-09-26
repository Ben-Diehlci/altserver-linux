#!/usr/bin/env python3
"""Check that the README still describes the code that exists.

WHY THIS EXISTS. An audit of README.md against the tree found 38 confirmed defects: environment
variables and API endpoints that were never documented, a `docker build` command that could not
work because there is no Dockerfile at the repo root, Setup steps that run files from the repo
without ever saying to clone it, and -- worst -- a security claim that `docker logs` was
unfiltered and full of bearer tokens, which the entrypoint had redacted for months. That one
contradicted another paragraph ninety lines further down.

None of it was caught by anything, because documentation is the one part of this repo that
nothing executed. Every other class of silent failure here now has a guard; this is that guard.

It deliberately checks only what is MECHANICALLY decidable: does the thing named exist, is the
number stated the number configured. Prose accuracy is not testable and is not attempted. The
point is to make the cheap half impossible to get wrong, not to pretend the expensive half is
covered.
"""

import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
README = os.path.join(ROOT, "README.md")

problems = []
checked = 0


def bad(msg):
    problems.append(msg)


def ok(msg):
    global checked
    checked += 1
    print("  ok  " + msg)


src = open(README, encoding="utf-8").read()
lines = src.split("\n")


# --- every documented environment variable is actually read -----------------------------------
documented = set(re.findall(r"^\| `(ALTSERVER_[A-Z_]+|ALTSTORE_[A-Z_]+|USBMUXD_[A-Z_]+)`",
                            src, re.M))
used = set(subprocess.run(
    ["git", "-C", ROOT, "grep", "-hoE",
     "ALTSERVER_[A-Z_]+|ALTSTORE_[A-Z_]+|USBMUXD_[A-Z_]+",
     "--", "web/", "docker/", "src/", "deploy/", "libraries/", ".github/", "buildenv/"],
    capture_output=True, text=True).stdout.split())
for v in sorted(documented):
    if v in used:
        ok("%s is documented and read" % v)
    else:
        bad("%s is in the Environment table but nothing reads it" % v)

# The reverse is the direction that actually bit: four variables were read and undocumented.
# ALTSERVER_TARGETARCH / BUILD_ARCH are Dockerfile build args, not runtime knobs.
BUILD_ONLY = {"ALTSERVER_TARGETARCH", "ALTSERVER_BUILD_ARCH", "USBMUXD_SOCKET_ADRESS"}
runtime_used = set(subprocess.run(
    ["git", "-C", ROOT, "grep", "-hoE", "ALTSERVER_[A-Z_]+|ALTSTORE_[A-Z_]+",
     "--", "web/", "docker/", "libraries/"],
    capture_output=True, text=True).stdout.split()) - BUILD_ONLY
for v in sorted(runtime_used):
    if v in src:
        ok("%s is read and documented" % v)
    else:
        bad("%s is read by the runtime but appears nowhere in the README. Four variables were "
            "in this state; a reader cannot configure what is not written down." % v)


# --- every API endpoint is documented, and every documented one exists ------------------------
server = open(os.path.join(ROOT, "web", "server.py"), encoding="utf-8").read()
implemented = set(re.findall(r'path == "(/api/[a-z/]+)"', server))
for e in sorted(implemented):
    if e.rsplit("/", 1)[0] == "/api/install":
        continue                      # internal to the install flow, not a documented surface
    if e in src:
        ok("%s is documented" % e)
    else:
        bad("%s exists but is not in the README" % e)
for e in sorted(set(re.findall(r"`(/api/[a-z]+)`", src))):
    if e not in implemented:
        bad("%s is documented but no handler implements it" % e)


# --- every guard named exists, and every guard is named ---------------------------------------
named = set(re.findall(r"`(check_[a-z_]+)`", src))
actual = {f[:-3] for f in os.listdir(os.path.join(ROOT, "tests"))
          if f.startswith("check_") and f.endswith(".py")}
for g in sorted(named - actual):
    bad("the README names tests/%s.py, which does not exist" % g)
for g in sorted(actual - named):
    bad("tests/%s.py exists but the README never mentions it" % g)
if named and not (named - actual) and not (actual - named):
    ok("all %d guards are named and all named guards exist" % len(actual))


# --- stated healthcheck windows match the compose file ----------------------------------------
try:
    import yaml
    stack = yaml.safe_load(open(os.path.join(ROOT, "deploy", "altserver-stack.yml")))

    def secs(v, dflt=0):
        if v is None:
            return dflt
        s = str(v).strip()
        mult = {"s": 1, "m": 60, "h": 3600}.get(s[-1:], 1)
        try:
            return int(float(s.rstrip("smh"))) * mult
        except ValueError:
            return dflt

    for name, svc in sorted(stack["services"].items()):
        hc = svc.get("healthcheck") or {}
        if not hc:
            continue
        mins = secs(hc.get("interval")) * int(hc.get("retries", 1)) // 60
        row = [l for l in lines if l.startswith("  | `%s`" % name)]
        if not row:
            bad("the badge table has no row for %s" % name)
        elif ("~%d min" % mins) not in row[0]:
            bad("the badge table says %r for %s, but its healthcheck gives ~%d min"
                % (row[0].split("|")[-2].strip(), name, mins))
        else:
            ok("%s: the badge table's ~%d min matches the compose file" % (name, mins))
except ImportError:
    print("  (pyyaml absent: skipping the healthcheck-window comparison)")


# --- links resolve, and every bash block parses -----------------------------------------------
def slug(h):
    s = h.strip().lower()
    s = re.sub(r"[^\w\s-]", "", s)
    return re.sub(r"\s", "-", s)


slugs = {slug(m.group(2)) for l in lines for m in [re.match(r"^(#{1,6})\s+(.*)$", l)] if m}
for l in lines:
    for m in re.finditer(r"\]\(#([^)]+)\)", l):
        if m.group(1) not in slugs:
            bad("broken anchor: #%s" % m.group(1))
for l in lines:
    for m in re.finditer(r"\]\(([^)#:]+\.[a-zA-Z0-9]+)\)", l):
        if not os.path.exists(os.path.join(ROOT, os.path.normpath(m.group(1)))):
            bad("broken file link: %s" % m.group(1))

# A bash fence INSIDE a blockquote is a copy-paste trap: every line carries a "> " prefix, and
# bash reads the leading > as a redirection, so pasting it writes files instead of running the
# command. Same class as the angle-bracket placeholders below. Caught by this guard on a block
# this very session added.
for n, l in enumerate(lines, 1):
    if re.match(r"^\s*>\s*```bash", l):
        bad("README.md:%d: a bash block is inside a blockquote; copying it carries the '> ' "
            "prefixes, which bash reads as redirections" % n)

blocks = re.findall(r"```bash\n(.*?)```", src, re.S)
for i, b in enumerate(blocks, 1):
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as fh:
        fh.write(b)
        path = fh.name
    if subprocess.run(["bash", "-n", path], capture_output=True).returncode:
        bad("bash block %d does not parse: %s" % (i, b.strip()[:70]))
    os.unlink(path)
    # `<stack>` in a shell block is a redirection, not a placeholder: pasting it creates files.
    if re.search(r"<[a-z][a-z-]*>", b):
        bad("bash block %d has an angle-bracket placeholder, which bash reads as a redirection: %s"
            % (i, b.strip()[:70]))
ok("%d bash block(s) parse, no anchors or file links broken" % len(blocks))


if problems:
    print("\n%d README claim(s) no longer match the code:\n" % len(problems), file=sys.stderr)
    for p in problems:
        print("  - " + p, file=sys.stderr)
    print("\nThe README is the only entry point a stranger has, and the only thing the owner "
          "re-reads months later.", file=sys.stderr)
    sys.exit(1)

print("OK: %d README claim(s) still match the code." % checked)
