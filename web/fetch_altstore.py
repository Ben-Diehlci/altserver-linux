#!/usr/bin/env python3
"""Fetch the current AltStore Classic IPA, so nobody has to place it by hand.

Resolves the download URL from Apple-independent metadata rather than hardcoding it: AltStore
publishes a source catalogue, and the IPA URL is versioned inside it
(.../apps/altstore/2_2_2.ipa today). Hardcoding a URL means silently installing an ever-older
AltStore, and guessing one is how you end up with a file that is not the app you wanted.

Idempotent. It records the version it fetched alongside the file and re-downloads only when the
catalogue moves ahead, so running it on every container start costs one small JSON request.

Stdlib only -- python3 is already a hard requirement of this image for the Bonjour shim.

    python3 fetch_altstore.py [--dest /data/AltStore.ipa] [--force] [--beta]
"""

import argparse
import json
import os
import sys
import tempfile
import urllib.request
import zipfile

SOURCE_URL = "https://cdn.altstore.io/file/altstore/apps.json"

# AltStore Classic. NOT com.rileytestut.AltStore.Beta, and emphatically not AltStore PAL, which is
# the EU marketplace build and is installed a completely different way.
BUNDLE_ID = "com.rileytestut.AltStore"
BETA_BUNDLE_ID = "com.rileytestut.AltStore.Beta"


def resolve(bundle_id):
    """Return (version, url) for the requested app from AltStore's own catalogue."""
    req = urllib.request.Request(SOURCE_URL, headers={"User-Agent": "AltServer-Linux"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        catalogue = json.load(resp)

    for app in catalogue.get("apps", []):
        if app.get("bundleIdentifier") != bundle_id:
            continue
        versions = app.get("versions") or []
        if versions:
            return versions[0].get("version"), versions[0].get("downloadURL")
        return app.get("version"), app.get("downloadURL")

    raise SystemExit("could not find %s in %s" % (bundle_id, SOURCE_URL))


def verify(path):
    """A downloaded file is only useful if it is actually an IPA. Returns the bundle name."""
    try:
        names = zipfile.ZipFile(path).namelist()
    except zipfile.BadZipFile as exc:
        raise SystemExit("downloaded file is not a zip archive: %s" % exc)

    bundles = sorted({
        n.split("/")[1] for n in names
        if n.startswith("Payload/") and n.count("/") > 1 and n.split("/")[1].endswith(".app")
    })
    if not bundles:
        raise SystemExit("archive contains no Payload/*.app -- not an IPA")
    return bundles[0]


def main():
    ap = argparse.ArgumentParser(description="Fetch the current AltStore IPA")
    ap.add_argument("--dest", default=os.environ.get("ALTSTORE_IPA_PATH", "/data/AltStore.ipa"))
    ap.add_argument("--force", action="store_true", help="re-download even if up to date")
    ap.add_argument("--beta", action="store_true", help="fetch the beta channel instead")
    args = ap.parse_args()

    bundle_id = BETA_BUNDLE_ID if args.beta else BUNDLE_ID
    stamp_path = args.dest + ".version"

    try:
        version, url = resolve(bundle_id)
    except Exception as exc:
        # Do not fail the container start over this: an existing IPA is better than no server.
        if os.path.exists(args.dest):
            print("could not check for updates (%s); keeping the existing %s" % (exc, args.dest))
            return 0
        raise SystemExit("could not resolve the AltStore download URL: %s" % exc)

    have = None
    if os.path.exists(stamp_path):
        try:
            have = open(stamp_path).read().strip()
        except OSError:
            pass

    if not args.force and have == version and os.path.exists(args.dest):
        print("AltStore %s already present at %s" % (version, args.dest))
        return 0

    print("fetching AltStore %s from %s" % (version, url), flush=True)
    os.makedirs(os.path.dirname(args.dest) or ".", exist_ok=True)

    # Download to a temporary file in the SAME directory, then rename. A half-written IPA left at
    # the real path would be found, fail to unpack, and report "The app could not be found" --
    # which looks like a signing problem rather than a truncated download.
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(args.dest) or ".", suffix=".part")
    os.close(fd)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AltServer-Linux"})
        with urllib.request.urlopen(req, timeout=300) as resp, open(tmp, "wb") as out:
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                out.write(chunk)

        bundle = verify(tmp)
        os.replace(tmp, args.dest)
        with open(stamp_path, "w") as f:
            f.write(version or "")
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

    print("AltStore %s -> %s (%s, %d bytes)"
          % (version, args.dest, bundle, os.path.getsize(args.dest)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
