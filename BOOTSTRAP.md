# Bootstrap: installing AltStore on an iPhone from a Linux server

First-time setup, from nothing to AltStore running on the phone and refreshing itself over Wi-Fi.
Background and rationale live in [REVIVAL.md](REVIVAL.md).

**What you need:** a Linux host running Docker (this is written for Ubuntu 24.04, but nothing here
is Ubuntu-specific beyond the `apt` lines), an iPhone on the same LAN, and a **USB cable, once**.
After the pairing step the cable is never needed again.

> **Use a secondary Apple ID if you have one.** Issue #88 documents Apple IDs being *locked* after
> anisette trouble. An app-specific password will **not** work — sideloading needs the real
> password plus a 2FA code. Free accounts cap at 3 sideloaded apps, 10 app IDs/week, 7-day certs.

> **Never run a second signing agent against the same Apple ID** — a Mac or Windows AltServer,
> Sideloadly, or Xcode. Each one revokes the other's certificate, and your apps stop opening.

---

## Phase 1 — Host prerequisites

The container image bundles everything AltServer itself needs. Three things must exist on the
**host**, because the stack reaches out to them:

```bash
sudo apt install -y avahi-daemon avahi-utils usbmuxd libimobiledevice-utils
```

| Host package | Why the stack needs it |
|---|---|
| `avahi-daemon` **running** | The containers bind-mount its socket and the system D-Bus socket; it does the actual mDNS publishing |
| `usbmuxd` | Owns the USB cable for Phase 2's one-time pairing. The stack bind-mounts `/var/run/usbmuxd` |
| `libimobiledevice-utils` | `idevice_id` / `idevicepair`, used to confirm the pairing worked |

Do **not** `systemctl enable usbmuxd` on Ubuntu — it is udev-activated and has no `[Install]`
section, so `enable` prints a confusing "unit files have no installation config" message. It starts
on its own when an iOS device is plugged in, and exits when the last one is unplugged. That is
normal and is exactly why the stack ships netmuxd for the wireless path.

```bash
systemctl is-active avahi-daemon          # expect: active
```

---

## Phase 2 — Pair the iPhone (USB cable, once)

Do this before deploying anything. Wireless pairing is not possible in this build —
`HAVE_WIRELESS_PAIRING` is undefined, and Apple restricts it to Apple TV.

Plug the iPhone into the host, unlock it, and tap **Trust**. If the host is a VM, pass the USB
device through to it first.

```bash
idevice_id -l                    # prints the UDID -- save it
idevicepair validate             # expect: SUCCESS
```

The pairing record lands in `/var/lib/lockdown/` on the host, which the stack mounts. Back up
**both** files together — they are not independent, and half a pairing is indistinguishable from
none:

```bash
sudo tar czf ~/lockdown-backup.tgz /var/lib/lockdown/
```

> That tarball contains a record named after your phone's UDID. `.gitignore` already refuses
> `lockdown-backup*.tgz`, but keep it out of anything you publish.

---

## Phase 3 — Deploy the stack

Everything else runs as one stack: AltServer, an anisette server, netmuxd for the wireless
transport, and a web UI that drives the install.

**Portainer → Stacks → Add stack → Repository**, pointing at this repo with compose path
`deploy/altserver-stack.yml`. Or with plain compose:

```bash
docker compose -f deploy/altserver-stack.yml up -d
```

**No host preparation beyond Phase 1.** It uses named volumes, the image is public, and the
AltStore IPA is fetched automatically on start, resolved from AltStore's own catalogue so it is
always current.

Then open **`http://<your-host>:8099`**.

### Two settings that are load-bearing

- **`security_opt: apparmor=unconfined`** on the `altserver` and `altserver-web` services. Docker's
  default AppArmor profile contains no `dbus` rules at all, and AppArmor denies a mediated class it
  does not mention — so the very first call a Bonjour client makes is refused, mDNS silently fails,
  and the server is invisible to your phone. [`deploy/apparmor/`](deploy/apparmor/) ships a
  narrower profile you can install on the host instead; see its README comments.
- **`network_mode: host`** on netmuxd and the web UI. netmuxd has to see mDNS on UDP 5353.

### The anisette trap, if you deploy anisette separately

Every copy of the upstream docs says to mount `/home/Alcoholic/.config/anisette-v3/lib/`. That is
**wrong**. `lib/` holds only the two Apple `.so` files downloaded at first run; the machine identity
— `device.json` and the ADI provisioning blob — lives one level **up**. Mount the parent, or you
re-provision on every redeploy and collect 2FA prompts forever.

[`deploy/altserver-stack.yml`](deploy/altserver-stack.yml) already gets this right with a named
volume. The standalone [`deploy/anisette-stack.yml`](deploy/anisette-stack.yml) exists for running
anisette on its own.

### Verify before going further

```bash
curl -fsS http://127.0.0.1:6969 | jq 'keys'     # ten X-Apple-* keys, all strings
```

The status page at `/` checks this and more. **Survive a redeploy, not just a restart** — `docker
restart` keeps the container's filesystem, so it proves nothing about persistence. Recreate the
container and confirm the identity is still there.

---

## Phase 4 — Install AltStore

Open **`http://<your-host>:8099/install`**, enter your Apple ID and password, and submit. The 2FA
code is prompted for **in the browser**.

That page exists because AltServer reads the 2FA code from `std::cin`, and a detached container has
no terminal. The web UI supervises the process and delivers the code to a read with no tty.

> **The install page takes an Apple ID password over plain HTTP.** On a trusted LAN that is a
> considered trade-off. Anywhere else, change the `altserver-web` command to
> `["--host", "127.0.0.1", "--port", "8099"]` and reach it over an SSH tunnel:
> `ssh -L 8099:127.0.0.1:8099 you@your-host`. Never put it behind a reverse proxy or a tunnel —
> this is LAN-only by design.

Credentials go to AltServer through the **environment**, never a command line, and the install log
shown in the browser is filtered before it is rendered — enforced by
[`tests/check_redaction.py`](tests/check_redaction.py). `docker logs` is **not** filtered and does
contain the full account record and bearer tokens.

### Reading the output

| What you see | What it means |
|---|---|
| `No anisette server is configured` | `ALTSERVER_ANISETTE_SERVER` unset |
| `ALTSERVER_ANISETTE_SERVER is not a usable URL` | Missing `http://` scheme |
| `Could not reach the anisette server at …` | Container not running, or wrong port |
| `… returned HTTP 502/404. Response body: …` | Anisette up but unhealthy — the body is quoted for you |
| `… did not return a JSON object` | Wrong endpoint; you are getting HTML |
| `… no "X-Apple-I-MD-M" field` | Protocol mismatch — re-check Phase 3 |
| `-36607` / "Unable to sign you in" | Anisette identity or clock. Check NTP **on the anisette host** — its timestamp is forwarded to Apple verbatim |
| `AltServer could not find the device` | Pairing or usbmuxd, **not** mDNS at this stage |
| `Finished!` | **Not proof of success** — it prints even on failure. Read the lines above it |

---

## Phase 5 — Verify it actually worked

1. AltStore appears on the home screen.
2. **Settings → General → VPN & Device Management** → trust the developer certificate.
3. **Open AltStore.** This is the real test — an app that installs but will not launch is the
   failure mode issue #131 described, and that is fixed in this fork.

---

## Phase 6 — Wireless refresh

**Already running.** netmuxd is part of the stack; there is nothing to install and nothing to
switch over. Confirmed working on iOS 26: profiles refresh over Wi-Fi with no cable attached.

This deliberately does **not** follow the advice in issue #77, and you should not apply that advice
here. netmuxd runs on its **own socket path** in a shared volume:

```yaml
command: ["--socket-path", "/run/muxd/usbmuxd", "--disable-usb", ...]
```

so the host's usbmuxd is left completely alone to handle the cable, and nothing contends for
anything. **Do not stop usbmuxd** — older guidance says to, because netmuxd binds `/var/run/usbmuxd`
by default, but this stack overrides that. AltServer is pointed at netmuxd with
`USBMUXD_SOCKET_ADDRESS` (note the spelling — the widely-copied `USBMUXD_SOCKET_ADRESS`, one D, is
silently ignored).

Requirements on the phone side, both normally already true after Phase 2:

| Needs to be true | How to check from the server |
|---|---|
| Phone advertises itself | `avahi-browse -rt _apple-mobdev2._tcp` shows it |
| `lockdownd` accepts network connections | port **62078** open on the phone's IP |

Port 62078 is the one that matters. The port in the mDNS TXT record is a different service and
refuses connections — which looks alarming and is not.

AltStore refreshes itself: it sets an hourly background-fetch interval and runs a
`BackgroundRefreshAppsOperation`. iOS grants that at its own discretion, so if an app ever expires
unexpectedly, that is why — not the server.

---

## Phase 7 — Don't lose it

```bash
sudo tar czf ~/lockdown-backup.tgz /var/lib/lockdown/
docker run --rm -v <stack>_anisette-config:/data -v ~:/backup \
  alpine tar czf /backup/anisette-state.tgz /data
```

`adi.pb` is rewritten on every request, so stop the container first if you want a clean copy.
Restoring that tarball is the **only** disaster-recovery path for the anisette identity; without it
a loss means re-provisioning and a fresh 2FA prompt.

Both tarballs contain identifying material and are already in `.gitignore`.

---

## Before trusting it unattended

- **Watch it from outside.** Nothing inside AltServer reports its own health: no liveness signal,
  no re-registration if avahi restarts, and `journalctl -p err` stays empty no matter what breaks.
  A background refresh that finds no server notifies nobody on either end. The status page at `/`
  exists for this; have something poll it.
- **Two checks that cannot tell you anything.** `docker exec altserver idevice_id -l` and the
  wireless row on the status page both use **Debian's** libimobiledevice from the image's apt
  packages, not the vendored copy AltServer links. They were green throughout a bug that broke
  every wireless refresh. The only evidence that refresh works is AltServer's own log.
- **A misleading error.** Real device faults are *displayed* as "AltServer could not be found",
  because AltStore remaps them for any server that is not `isPreferred`, and this port hardcodes
  serverID `"1234567"` where Mac and Windows use a UUID. It will send you to debug mDNS when mDNS
  is fine.
- **`-d` makes things worse.** `libusbmuxd_set_debug_level(debugLogLevel - 2)` underflows, and a
  single `-d` silences the two messages that actually diagnose a netmuxd mismatch.
- **Changing the Apple ID password kills unattended refresh.** A background refresh has no way to
  present a login view, so it fails permanently until someone opens AltStore **on the phone** and
  re-enters the credentials. The phone's keychain and the stack's `ALTSERVER_APPLE_PASSWORD` are
  separate copies — updating Portainer alone is not enough.
- **Do not casually re-run the one-shot install.** The revoke-confirmation prompt is compiled out
  on Linux, so a revoke proceeds unattended and invalidates the certificate your installed apps
  depend on.

---

## Running the binary directly, without the stack

Rarely needed, but the binary is a static executable and works standalone. Download it from the
Actions artifacts of a build run, then:

```bash
chmod +x AltServer-*           # artifact upload strips the executable bit
export ALTSERVER_ANISETTE_SERVER=http://127.0.0.1:6969
./AltServer-*                  # no IPA argument = daemon mode
```

With an IPA argument it performs a one-time install, which needs a real terminal because the 2FA
code is read from stdin. Prefer the environment variables `ALTSERVER_APPLE_ID` /
`ALTSERVER_APPLE_PASSWORD` over `-a` / `-p`: a password passed as an argument is visible in `ps` to
every user on the host.

Running it outside the container means providing the runtime dependencies yourself — in particular
`libavahi-compat-libdnssd-dev`, the **`-dev`** package, because the code dlopens the unversioned
`libdns_sd.so` that only the dev package ships. Without it, advertisement fails silently. See
[README.md](README.md) for the full list.
