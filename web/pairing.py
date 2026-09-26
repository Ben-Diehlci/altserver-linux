"""Pairing diagnosis for the setup wizard.

Pairing is where someone without background knowledge gets stuck, and its failure modes are
unusually opaque:

  * Wireless pairing is impossible in this build (HAVE_WIRELESS_PAIRING is undefined, and
    upstream libimobiledevice restricts it to Apple TV), so a USB cable is mandatory exactly
    once -- which is surprising for a project whose whole point is wireless refresh.
  * `idevicepair validate` fails with "a passcode is set" unless the device is UNLOCKED at that
    moment, which reads like a permissions error rather than "press the button".
  * On a VM the device may never appear at all, and the cause is hypervisor USB passthrough
    rather than anything on the Linux side.
  * A genuine device fault is later DISPLAYED by AltStore as "AltServer could not be found",
    because it remaps deviceNotFound/lostConnection to serverNotFound for any wireless server
    that is not isPreferred -- and AltServer-Linux hardcodes serverID "1234567" where
    Mac/Windows use a UUID, so isPreferred is permanently false here. That sends people to debug
    mDNS when mDNS is fine.

So this module's job is not to run commands, it is to tell the three "nothing is showing up"
cases apart and say which one you are in.
"""

import os
import re
import shutil
import subprocess

# One definition of the lockdownd result codes, imported rather than copied: two tables drift,
# and this one decides whether the page tells someone to go and re-pair. status_checks does not
# import this module, so there is no cycle.
from status_checks import classify_pair_failure

STEP_OK, STEP_TODO, STEP_BLOCKED = "ok", "todo", "blocked"


# netmuxd's socket, matching deploy/altserver-stack.yml. Same variable status_checks.py uses.
NETMUXD_SOCKET = os.environ.get("ALTSERVER_NETMUXD_SOCKET", "/run/muxd/usbmuxd")
_WIRELESS_ENV = {"USBMUXD_SOCKET_ADDRESS": "UNIX:" + NETMUXD_SOCKET}


def _run(cmd, timeout=15, env=None):
    if shutil.which(cmd[0]) is None:
        return None, "%s is not installed" % cmd[0]
    try:
        run_env = dict(os.environ, **env) if env else None
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=run_env)
        return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()
    except subprocess.TimeoutExpired:
        return None, "%s timed out" % cmd[0]
    except Exception as exc:  # pragma: no cover
        return None, "%s could not be run: %s" % (cmd[0], exc)


def _in_container():
    return os.path.exists("/.dockerenv")


def _find_pairing_backup():
    """Find a lockdown backup that contains BOTH required plists. Returns (path, summary)."""
    import glob
    import tarfile

    candidates = []
    for pattern in ("~/lockdown-backup*.tgz", "~/lockdown-backup*.tar.gz",
                    "/root/lockdown-backup*.tgz", "~/*lockdown*.tgz"):
        candidates.extend(glob.glob(os.path.expanduser(pattern)))

    for path in sorted(set(candidates), key=os.path.getmtime, reverse=True):
        try:
            with tarfile.open(path) as tf:
                names = tf.getnames()
        except Exception:
            continue
        has_system = any(n.endswith("SystemConfiguration.plist") for n in names)
        has_device = any(re.search(r"/[0-9A-Fa-f-]{8,}\.plist$", n) for n in names)
        if has_system and has_device:
            return path, "contains both plists"
    return None, ""


def diagnose():
    """Return an ordered wizard state: which step you are on, and what to do about it."""
    steps = []
    udids = []

    # --- 1. tooling -------------------------------------------------------------------------
    missing = [t for t in ("idevice_id", "idevicepair") if shutil.which(t) is None]
    if missing:
        steps.append({
            "title": "Install the device tools",
            "state": STEP_BLOCKED,
            "detail": "Missing: %s" % ", ".join(missing),
            "action": "sudo apt install -y usbmuxd libimobiledevice-utils",
            "note": "Do NOT `systemctl enable usbmuxd` on Ubuntu -- it is udev-activated and has "
                    "no [Install] section, so enabling it just prints a confusing message.",
        })
        return {"steps": steps, "udids": [], "paired": False, "next": steps[-1]["title"]}
    steps.append({"title": "Device tools installed", "state": STEP_OK,
                  "detail": "idevice_id and idevicepair are available", "action": "", "note": ""})

    # --- 2. the host's usbmuxd socket (informational only) ----------------------------------
    # This used to BLOCK here, and it was the wrong shape twice over.
    #
    # Ubuntu's usbmuxd is udev-activated: it starts when a cable is inserted and exits when the
    # last device is removed. An unattended wireless server therefore has no socket as its NORMAL
    # steady state -- so the wizard answered the designed configuration by refusing to continue
    # and telling the owner to start a service that was behaving correctly. A phone that was
    # paired and reachable over netmuxd never got checked, so a finished setup reported BLOCKED.
    #
    # And the file proves little either way: a bind mount creates it regardless, and REVIVAL.md
    # records measuring the socket present with nothing bound to it. Whether a mux ANSWERS is
    # established by the next step, which actually talks to one.
    sock = "/var/run/usbmuxd"
    steps.append({
        "title": "Host usbmuxd socket present" if os.path.exists(sock)
                 else "No host usbmuxd socket (normal without a cable)",
        "state": STEP_OK,
        "detail": sock if os.path.exists(sock) else "%s does not exist" % sock,
        "action": "",
        "note": "" if os.path.exists(sock) else
                "Not a fault. Ubuntu's usbmuxd is udev-activated and exits when the last cable is "
                "removed, so a cable-free server normally has no socket. netmuxd is what serves "
                "the phone over Wi-Fi. This matters only when you are pairing for the first time, "
                "which needs the cable anyway.",
    })

    def _udids(args, env=None):
        """Returns (udids, note). note is non-empty when the MUX could not be reached.

        Discarding the return code here conflated two opposite conditions: idevice_id prints
        "ERROR: Unable to retrieve device list!" and exits non-zero when it cannot reach the
        socket at all, versus no output and exit 0 for a mux that answered and has no devices.
        Treating the first as the second made a dead netmuxd look like an absent phone, and the
        wizard prescribed hypervisor USB passthrough and a different cable for a container that
        needed restarting.
        """
        rc, o = _run(["idevice_id"] + args, env=env)
        found = [l.strip() for l in (o or "").splitlines()
                 if re.match(r"^[0-9A-Fa-f-]{8,}$", l.strip())]
        if found:
            return found, ""
        if rc is None:
            return [], (o or "idevice_id could not be run")
        if rc != 0 or "unable to retrieve device list" in (o or "").lower():
            return [], "no mux answered: %s" % (o or "").strip()[:120]
        return [], ""

    usb_udids, usb_note = _udids(["-l"])
    net_list, net_note = _udids(["-n"], env=_WIRELESS_ENV)
    net_udids = [u for u in net_list if u not in usb_udids]
    udids = usb_udids + net_udids
    wireless_only = bool(net_udids) and not usb_udids

    if not udids and net_note:
        # The WIRELESS mux did not answer. That is a container problem, not a phone problem, and
        # it must not be answered with a cable and a hypervisor setting.
        steps.append({
            "title": "netmuxd is not answering",
            "state": STEP_TODO,
            "detail": "%s%s" % (net_note, (" | usbmuxd: " + usb_note) if usb_note else ""),
            "action": "docker restart netmuxd",
            "note": "This says NOTHING about whether your phone is present -- nothing was able to "
                    "ask. netmuxd serves the device over Wi-Fi; if it is down or its socket is "
                    "not mounted into this container, no device can be seen however healthy the "
                    "phone is. Check `docker ps` for netmuxd and that the muxd-socket volume is "
                    "mounted. Do NOT go looking for a cable on account of this.",
        })
        return {"steps": steps, "udids": [], "paired": False, "next": steps[-1]["title"]}

    if not udids:
        steps.append({
            "title": "Plug the iPhone in with a USB cable",
            "state": STEP_TODO,
            "detail": "No device detected yet.",
            "action": "",
            "note": "A cable is required for this step and cannot be avoided: wireless pairing is "
                    "not supported by this build. Once paired, refreshing works over Wi-Fi and the "
                    "cable is never needed again.\n\n"
                    "If it is plugged in and still not showing: on a Proxmox/VMware guest the USB "
                    "device must be passed through to the VM in the hypervisor. Also try a "
                    "different cable -- charge-only cables carry no data.",
        })
        return {"steps": steps, "udids": [], "paired": False, "next": steps[-1]["title"]}

    steps.append({
        "title": "iPhone detected over Wi-Fi" if wireless_only else "iPhone detected",
        "state": STEP_OK,
        "detail": "UDID %s%s" % (udids[0], " (via netmuxd, no cable attached)" if wireless_only else ""),
        "action": "",
        "note": ("Already paired and reachable wirelessly -- no cable is needed. The USB step "
                 "above is only for a phone that has never been paired with this server."
                 if wireless_only else ""),
    })

    # --- 4. pairing ---------------------------------------------------------------------------
    # -n for a network device: idevicepair.c:372 selects IDEVICE_LOOKUP_USBMUX unless it is
    # passed, so validating a wireless device without it checks a USB device that is not there.
    rc, out = (_run(["idevicepair", "-n", "validate"], env=_WIRELESS_ENV) if wireless_only
               else _run(["idevicepair", "validate"]))
    low = (out or "").lower()

    if rc == 0:
        steps.append({"title": "Pairing valid", "state": STEP_OK,
                      "detail": out, "action": "", "note": ""})
        # Look for an existing backup rather than nagging about one already taken. Only counts
        # it if the archive actually contains BOTH files -- an archive with one of them is worse
        # than none, because it restores a mismatched HostID/SystemBUID that iOS rejects.
        backup, why = _find_pairing_backup()
        steps.append({
            "title": "Back up the pairing record",
            "state": STEP_OK if backup else STEP_TODO,
            "detail": ("Found %s (%s)" % (backup, why)) if backup
                      else "Losing it means fetching the cable again.",
            "action": "" if backup else "sudo tar czf ~/lockdown-backup.tgz /var/lib/lockdown/",
            "note": "Back up BOTH <UDID>.plist and SystemConfiguration.plist together. They are "
                    "not independent -- restoring only one produces a mismatched HostID/SystemBUID "
                    "that iOS rejects, reported as the same generic error as every other "
                    "lockdownd fault. Half a pairing is indistinguishable from none.",
        })
        return {"steps": steps, "udids": udids, "paired": True,
                "next": "Nothing -- setup complete" if backup else "Back up the pairing record"}

    kind = classify_pair_failure(out)

    def _step(title, detail, action, note, nxt):
        steps.append({"title": title, "state": STEP_TODO, "detail": detail,
                      "action": action, "note": note})
        return {"steps": steps, "udids": udids, "paired": False, "next": nxt}

    if kind == "passcode":
        return _step("Unlock the iPhone", out, "",
                     "The screen must be unlocked at the moment this runs. Unlock it and "
                     "re-check -- this is not a permissions problem, despite how it reads.",
                     "Unlock the iPhone")

    if kind in ("transport", "gone"):
        # "No device found." and a lockdownd transport error both mean the device did not answer.
        # The catch-all used to call this "visible but not paired" and send the owner for a
        # cable -- to fix a phone that was asleep.
        return _step("The phone did not answer",
                     out or "Listed, but unreachable.", "",
                     "This is a TRANSPORT failure, not a pairing one. The device is still listed "
                     "because netmuxd remembers it for a while after it goes away, so this is "
                     "what an asleep phone, one with Wi-Fi off, or one on another network looks "
                     "like. Wake it and put it back on this LAN -- do NOT re-pair to fix this.",
                     "Wake the phone and put it on this network")

    if kind == "trust":
        return _step("Tap Trust on the iPhone", out, "",
                     "The Trust prompt is waiting (or was dismissed) on the device. Unlock it "
                     "and tap Trust. No cable and no re-pairing needed.",
                     "Tap Trust on the iPhone")

    if kind == "denied":
        return _step("The phone refused the pairing", out, "idevicepair pair",
                     "Someone tapped Do Not Trust. Run the pair command with the phone unlocked "
                     "and tap TRUST this time.", "Trust this computer")

    if kind == "connection":
        return _step("Pairing is not possible over this connection", out, "",
                     "The device will not pair over the transport in use. First-time pairing "
                     "needs the cable; refreshing afterwards does not.",
                     "Pair over USB")

    if kind in ("unpaired", "failed"):
        # The genuine case, and the only one that earns a cable.
        return _step("Trust this computer on the iPhone",
                     out or "The device is visible but not paired.", "idevicepair pair",
                     "Unlock the phone, run the pair command, then tap TRUST on the prompt that "
                     "appears on the device and enter its passcode." + ("" if wireless_only else
                     " If no prompt appears, unplug and replug the cable with the phone "
                     "unlocked."),
                     "Trust this computer")

    return _step("Pairing could not be validated",
                 out or "idevicepair gave no output.", "",
                 "Unrecognised idevicepair output, quoted above verbatim. This does not "
                 "establish that the pairing is bad, so do not re-pair on account of it alone.",
                 "Read the message above")

    steps.append({
        "title": "Trust this computer on the iPhone",
        "state": STEP_TODO,
        "detail": out or "The device is visible but not paired.",
        "action": "idevicepair pair",
        "note": "Unlock the phone, run the pair command, then tap TRUST on the prompt that "
                "appears on the device and enter its passcode. If no prompt appears, unplug and "
                "replug the cable with the phone unlocked.",
    })
    return {"steps": steps, "udids": udids, "paired": False, "next": "Trust this computer"}


if __name__ == "__main__":
    import json
    print(json.dumps(diagnose(), indent=2))
