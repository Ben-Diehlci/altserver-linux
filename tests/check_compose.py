#!/usr/bin/env python3
"""Validate the compose stacks the way Docker does, not the way PyYAML does.

WHY THIS EXISTS. A duplicate `depends_on:` key was added to one service and shipped, because the
check used to validate it was `yaml.safe_load`, which SILENTLY KEEPS THE LAST duplicate rather than
erroring. The validation printed a correct-looking merged result from a file Docker Compose refuses
to load:

    failed to parse deploy/altserver-stack.yml:
    line 231: mapping key "depends_on" already defined at line 137

That is the whole trap: the file parsed fine locally and broke only at deploy time, in Portainer,
after a pull -- the slowest possible place to find it.

Also checks two things a plain parse cannot: that every named volume a service mounts is actually
declared, and that every depends_on target is a real service. Both fail at deploy time otherwise.
"""

import os
import sys

try:
    import yaml
except ImportError:
    print("FAIL: PyYAML is not installed (pip install pyyaml)")
    sys.exit(1)

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DEPLOY = os.path.join(ROOT, "deploy")


class StrictLoader(yaml.SafeLoader):
    """SafeLoader that refuses duplicate mapping keys instead of quietly merging them."""


def _no_duplicate_keys(loader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.YAMLError(
                'duplicate mapping key "%s" at line %d (already defined at line %d)'
                % (key, key_node.start_mark.line + 1, mapping[key])
            )
        mapping[key] = key_node.start_mark.line + 1
    return {
        loader.construct_object(k, deep=deep): loader.construct_object(v, deep=deep)
        for k, v in node.value
    }


StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_duplicate_keys)


def check(path):
    rel = os.path.relpath(path, ROOT)
    try:
        with open(path) as fh:
            doc = yaml.load(fh, Loader=StrictLoader)
    except yaml.YAMLError as exc:
        return ["%s: %s" % (rel, exc)]

    if not isinstance(doc, dict) or "services" not in doc:
        return []

    problems = []
    services = doc.get("services") or {}
    declared = set((doc.get("volumes") or {}).keys())

    for name, svc in services.items():
        if not isinstance(svc, dict):
            continue

        for dep in svc.get("depends_on") or []:
            if dep not in services:
                problems.append("%s: service %r depends_on %r, which is not a service in this file"
                                % (rel, name, dep))

        for mount in svc.get("volumes") or []:
            if not isinstance(mount, str) or ":" not in mount:
                continue
            src = mount.split(":", 1)[0]
            # Anything with a path separator is a bind mount; only bare names are named volumes.
            if "/" in src or src.startswith((".", "$")):
                continue
            if src not in declared:
                problems.append(
                    "%s: service %r mounts named volume %r, which is not declared under the "
                    "top-level `volumes:` key" % (rel, name, src))

    print("ok    %-34s %d services, %d volumes" % (rel, len(services), len(declared)))
    return problems


def main():
    if not os.path.isdir(DEPLOY):
        print("no deploy/ directory")
        return 0

    problems = []
    found = False
    for entry in sorted(os.listdir(DEPLOY)):
        if entry.endswith((".yml", ".yaml")):
            found = True
            problems += check(os.path.join(DEPLOY, entry))

    if not found:
        print("no compose files in deploy/")
        return 0

    if problems:
        print("\n" + "\n".join("FAIL: " + p for p in problems))
        print("\nDocker Compose refuses files like this at DEPLOY time. Note that yaml.safe_load "
              "does NOT -- it keeps the last duplicate key, so a plain parse looks correct.")
        return 1
    print("\nAll compose files load strictly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
