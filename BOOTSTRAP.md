# Bootstrap: installing AltStore on the iPhone from the Linux server

First-time install, from nothing to AltStore running on the phone. Ongoing wireless refresh is
**Phase 7**, deliberately last — get the install working over a cable first, then make it wireless.
Background and rationale live in [REVIVAL.md](REVIVAL.md).

Target: Dell OptiPlex 5060 → Proxmox → Ubuntu 24.04 VM (`192.168.9.16`, `ens18`) → one iPhone on
iOS 26.x, same LAN.

> **Use a secondary Apple ID if you have one.** Issue #88 documents Apple IDs being *locked* after
> anisette trouble. An app-specific password will **not** work — sideloading needs the real
> password plus a 2FA code. Free accounts cap at 3 sideloaded apps, 10 app IDs/week, 7-day certs.

---

## Phase 0 — Get a binary that contains all our fixes

The artifact from the last green CI run predates every code fix. Push first, then take the new one.

```bash
git push origin bd/revival
```

Then on GitHub: **Actions → Build AltServer →** newest run → **Artifacts → `AltServer-amd64`**.
That downloads to your Mac's browser. GitHub artifact URLs need an authenticated API call even on
a public repo, so fetching it directly on the VM with `curl` will not work — copy it across:

Safari auto-expands downloads ("Open safe files after downloading"), so you will most likely end
up with the bare binary `AltServer-x86_64` rather than `AltServer-amd64.zip`. Either is fine —
copy whichever you actually got:

```bash
# on the Mac -- if Safari already expanded it
scp ~/Downloads/AltServer-x86_64 youruser@192.168.9.16:~/

# or, if you got the zip
scp ~/Downloads/AltServer-amd64.zip youruser@192.168.9.16:~/
```

```bash
# in the VM's SSH session
sudo apt install -y unzip                 # only if you copied the zip
unzip ~/AltServer-amd64.zip               # only if you copied the zip
chmod +x ~/AltServer-x86_64               # ALWAYS -- artifact upload strips the executable bit,
                                          # and scp does not reliably preserve it either.
                                          # Without it, systemd fails with status=203/EXEC
~/AltServer-x86_64 --help
```

### Confirm you have the right binary, not the pre-fix one

The artifact from a run that predates the fixes looks identical. Check the strings:

```bash
grep -c "No anisette server is configured" ~/AltServer-x86_64   # expect >= 1
grep -c "armconverter.com/anisette" ~/AltServer-x86_64          # expect 0 -- the dead default
```

Expect the first to be non-zero and the second to be **zero**. `file` will also report
`statically linked, with debug_info, not stripped` at roughly 52 MB — the size is the `-g` debug
info from the Makefile, not a problem.

The binary is named for the gcc triple (`x86_64`), the artifact for the matrix label (`amd64`).

### What runs in Portainer and what does not

This host is managed through Portainer, but the split for bootstrap is deliberate:

| Component | Where | Why |
|---|---|---|
| **Anisette server** | **Portainer stack** | A long-lived service with persistent state. Belongs in the stack. |
| **AltServer, for the bootstrap install** | **Plain binary over SSH** | The 2FA code is read from **stdin**. It needs a real interactive terminal for this one run. |
| **AltServer, as a daemon afterwards** | Portainer stack (Phase 7 / F1) | Once signed in, it no longer needs stdin. |

So Phase 4 is a one-time command typed into an SSH session, not a container. Containerising it is
future item F1 in [REVIVAL.md](REVIVAL.md), and it depends on solving 2FA entry out-of-band.

---

## Phase 1 — Host prerequisites

Already done on this host: `avahi-daemon`, `avahi-utils`, `libavahi-compat-libdnssd-dev`.
Multicast is confirmed working. Remaining:

```bash
sudo apt install -y usbmuxd libimobiledevice-utils jq   # jq is used by the Phase 2 checks
```

Do **not** `systemctl enable usbmuxd` on Ubuntu — it is udev-activated and has no `[Install]`
section, so `enable` prints a confusing "unit files have no installation config" message. It
starts on its own when an iOS device is plugged in. Confirm it is working in Phase 3, by whether
`idevice_id -l` sees the phone, not by whether the unit is enabled.

Re-confirm the mDNS chain, since a silent failure here makes the server invisible later:

```bash
python3 -c "from ctypes import CDLL; CDLL('libdns_sd.so'); print('libdns_sd.so OK')"
systemctl is-active avahi-daemon
```

---

## Phase 2 — Anisette server (Portainer stack)

Use [`deploy/anisette-stack.yml`](deploy/anisette-stack.yml) — **dadoum/anisette-v3-server**,
digest-pinned, loopback-only. It is the only maintained server still serving the legacy v1
flat-JSON endpoint our client needs, verified by reading the source commit the published image
was built from.

### ⚠️ The trap: the official README tells you to mount the wrong directory

Every copy of the docs, and every forum post repeating them, says to mount:

```
/home/Alcoholic/.config/anisette-v3/lib/      ← WRONG
```

`lib/` holds **only** the two Apple `.so` files downloaded at first run. The machine identity —
`device.json` and the ADI provisioning blob — lives one level **up**. Verified in source:

```
59:  configurationPath = expandTilde("~/.config/anisette-v3");
95:  libraryPath = configurationPath.buildPath("lib");      // just the .so cache
134: v1Device = new Device(configurationPath.buildPath("device.json"));
136: v1Adi.provisioningPath = configurationPath;            // identity lives HERE
```

Mount `lib/` and the identity stays on the container's writable layer, so **every Portainer
"Update the stack" destroys it**: the server mints a new machine, Apple demands 2FA, and
unattended refresh dies silently. That is issue #86, straight out of the official docs.

### Prep (before deploying)

The container runs as **uid 1000**, and a bind mount inherits the *host* directory's ownership
(`root:root`), so without this it cannot write and dies with `FileException … Permission denied`:

```bash
sudo mkdir -p /opt/stacks/anisette/config
sudo chown 1000:1000 /opt/stacks/anisette/config
sudo chmod 700 /opt/stacks/anisette/config
timedatectl        # must say: System clock synchronized: yes
```

### Deploy

Portainer → **Stacks → Add stack → Web editor**, paste `deploy/anisette-stack.yml`, Deploy.

First start downloads the Apple Music APK and provisions against Apple — expect a few minutes,
ending in `Machine creation done!` then `Provisioning done!`.

### Two settings that are load-bearing, and why

- **`TZ: UTC` — do not copy `TZ=America/New_York` from your Plex/Immich/AdGuard stacks.** The
  server stamps `X-Apple-I-Client-Time` from *local* wall-clock time and then appends a literal
  `Z`. Under any other TZ it sends Apple a timestamp wrong by your UTC offset while claiming to be
  UTC — well-formed, contract-passing, and silently wrong.
- **The healthcheck hits `/v3/client_info`, never `/`.** The v1 route at `/` performs *real*
  provisioning against Apple when the machine is not yet provisioned, so polling `/` would hammer
  Apple's endpoint exactly when your identity volume has gone missing. If the container reports
  unhealthy immediately, the image may lack `curl` — just delete the healthcheck block.

### Verify (in order — each catches something the next assumes)

```bash
docker exec anisette id                       # expect uid=1000(Alcoholic); the mount depends on it

curl -sS -o /dev/null -w 'HTTP %{http_code}\n' -H 'User-Agent: Xcode' http://127.0.0.1:6969/

curl -sS -H 'User-Agent: Xcode' http://127.0.0.1:6969/ | python3 -c '
import json,sys
d=json.load(sys.stdin)
req=["X-Apple-I-MD-M","X-Apple-I-MD","X-Apple-I-MD-LU","X-Apple-I-MD-RINFO","X-Mme-Device-Id",
     "X-Apple-I-SRL-NO","X-MMe-Client-Info","X-Apple-I-Client-Time","X-Apple-Locale","X-Apple-I-TimeZone"]
bad=[k for k in req if k not in d]
notstr=[k for k in req if k in d and not isinstance(d[k],str)]
print("MISSING:",bad or "none")
print("NOT A STRING:",notstr or "none")
print("Client-Time:",d.get("X-Apple-I-Client-Time"))
print("Client-Info:",d.get("X-MMe-Client-Info"))
print("VERDICT:","PASS" if not bad and not notstr else "FAIL")'

date -u +%Y-%m-%dT%H:%M:%SZ      # must match Client-Time above to within seconds

docker exec anisette ls -la /home/Alcoholic/.config/anisette-v3   # device.json + adi.pb + lib/
sudo ls -la /opt/stacks/anisette/config                           # same files on the HOST side
docker diff anisette | grep -iE 'anisette-v3|adi|device'          # expect NOTHING identity-related

sudo ss -lntp | grep 6969        # must be 127.0.0.1:6969, never 0.0.0.0
```

**Then the test that actually matters — survive a REDEPLOY, not a restart.** `docker restart`
keeps the same container and proves nothing; Portainer redeploys destroy and recreate it, which is
what breaks people. In Portainer: **Stacks → anisette → Editor → Update the stack**, then confirm
the container ID changed *and* `X-Mme-Device-Id` / `X-Apple-I-MD-LU` did **not**.
(`X-Apple-I-MD` is a one-time password and *should* differ.)

### Back up the identity immediately

`adi.pb` is rewritten on every request, so copy it while idle:

```bash
docker stop anisette
sudo tar czf ~/anisette-identity-$(date +%F).tgz -C /opt/stacks/anisette config
docker start anisette
```

Restoring that tarball is the **only** disaster-recovery path. Without it, a lost identity means
re-provisioning and a fresh 2FA prompt.

### Honest confidence

| Claim | Confidence |
|---|---|
| Serves all ten keys as strings, correct casing | **Verified in source** |
| Identity lives in the parent dir, not `lib/` | **Verified in source** |
| Published image matches that source | Medium — `:latest` is ~17 months behind; upstream's publish workflow has been failing |
| This produces a successful Apple sign-in | **Low — unproven.** No one has reported a completed AltServer-Linux sign-in in 2026; every success report predates both the GSA block and iOS 26 |

Rejected alternatives: `dadoum/anisette-server` (crashes at startup, missing libplist),
`omnisette-server` (its v1 handler removes three of the ten keys),
`nyamisty/alt_anisette_server` (dead since 2022, implicated in the #88 lockouts), and any public
shared server (shared identity is the lockout mechanism).

---

## Phase 3 — Pair the iPhone (requires a USB cable, once)

Unavoidable: `HAVE_WIRELESS_PAIRING` is undefined in this build, and wireless pairing is
Apple-TV-only. Plug the iPhone into the Ubuntu VM (pass the USB device through in Proxmox if the
VM does not see it), unlock it, and tap **Trust**.

```bash
idevice_id -l                    # prints the UDID -- save it
idevicepair validate             # expect: SUCCESS
```

Back up **both** files together — they are not independent, and half a pairing is
indistinguishable from none:

```bash
sudo tar czf ~/lockdown-backup.tgz /var/lib/lockdown/
```

---

## Phase 4 — The install

Get `AltStore.ipa` from <https://altstore.io> onto the VM. Then, **in an interactive terminal** —
2FA is read from stdin, which does not exist under systemd:

Do **not** paste angle-bracket placeholders into the shell — bash reads `<` as input redirection
and fails with `No such file or directory`. Prompt for the values instead, which also keeps the
Apple ID password out of `~/.bash_history`:

```bash
ls -la ~/AltStore.ipa                       # confirm the IPA is actually there first

export ALTSERVER_ANISETTE_SERVER=http://127.0.0.1:6969

read -rp  "UDID: "     UDID
read -rp  "Apple ID: " APPLEID
read -rsp "Password: " APPLEPW; echo

~/AltServer-x86_64 -u "$UDID" -a "$APPLEID" -p "$APPLEPW" ~/AltStore.ipa
```

> The password is still visible in `ps` for the duration of the run, because the binary accepts it
> only as a command-line argument. Harmless on a single-user homelab VM, but it is a real interface
> flaw — see TODO 9 in [REVIVAL.md](REVIVAL.md).

Flag order no longer matters (the `-a` fallthrough is fixed), but keep `-u -a -p` anyway. You will
be prompted for a **2FA code** — type it at the prompt.

### Reading the output

| What you see | What it means |
|---|---|
| `No anisette server is configured` | `ALTSERVER_ANISETTE_SERVER` not exported — note `sudo` drops it without `-E` |
| `ALTSERVER_ANISETTE_SERVER is not a usable URL` | Missing `http://` scheme |
| `Could not reach the anisette server at …` | Container not running, or wrong port |
| `… returned HTTP 502/404. Response body: …` | Anisette server up but unhealthy — body is quoted for you |
| `… did not return a JSON object` | Wrong endpoint; you are getting HTML |
| `… no "X-Apple-I-MD-M" field` | Protocol mismatch — re-check Phase 2 |
| `-36607` / "Unable to sign you in" | Anisette identity or clock. Check NTP on the anisette host. Then try `ALTSERVER_NO_CLIENTINFO_SANITIZE=1` |
| `AltServer could not find the device` | Pairing or usbmuxd, **not** mDNS at this stage |
| `Finished!` | **Not proof of success** — it prints even on failure. Read the lines above it |

---

## Phase 5 — Verify it actually worked

1. AltStore appears on the home screen.
2. **Settings → General → VPN & Device Management** → trust the developer certificate.
3. **Open AltStore.** This is the real test.

**If it installs but crashes instantly at launch, that is issue #131** — the fix in `0e8090b` did
not work, and that is exactly the unverified assumption. Report the symptom; do not conclude the
deployment failed.

---

## Phase 6 — Don't lose it

```bash
sudo tar czf ~/lockdown-backup.tgz /var/lib/lockdown/
docker run --rm -v <anisette-volume>:/data -v ~:/backup alpine tar czf /backup/anisette-state.tgz /data
```

Certificates expire in **7 days**. Phase 7 is what stops that mattering.

---

## Phase 7 — Wireless refresh (only after Phase 5 passes)

Do not start this until the cabled install works — it changes the device transport, and debugging
both at once is miserable.

1. **Stop `usbmuxd`, start `netmuxd`** (≥ 0.3). They collide: stock usbmuxd never emits
   ConnectionType `Network`, and netmuxd binds `/var/run/usbmuxd` by default. The only success
   report in issue #77 is netmuxd with no flags and usbmuxd not running.
   *(If pointing at TCP instead, the variable is `USBMUXD_SOCKET_ADDRESS` — the widely-copied
   instruction in #49 misspells it `USBMUXD_SOCKET_ADRESS`, one D, and is silently ignored.)*
2. Run AltServer in **daemon mode** — no IPA argument — with `ALTSERVER_ANISETTE_SERVER` set. It
   prints `Using anisette server: …` and `Advertising this server over mDNS as _altserver._tcp`.
3. **Confirm publication from another machine**, not from the server:
   ```bash
   avahi-browse -rt _altserver._tcp
   ```
   Do this from a different host. `DNSServiceRegister result: 0` is **not** proof of publication —
   avahi can report success while publishing nothing.
4. In AltStore on the phone, trigger a **manual** refresh. Manual surfaces errors; background
   refresh suppresses them.

### Before trusting it unattended

- **Watchdog first.** Nothing inside AltServer reports its own health: no liveness signal, no
  re-registration if avahi restarts, and `journalctl -p err` is empty no matter what breaks. A
  background refresh that finds no server notifies nobody on either end. Have something external
  run `avahi-browse` and track the last successful refresh.
- **Beware a misleading error.** Real device faults are *displayed* as "AltServer could not be
  found", because AltStore remaps them for any server that is not `isPreferred`, and this port
  hardcodes serverID `"1234567"` where Mac/Windows use a UUID. It will send you to debug mDNS when
  mDNS is fine.
- **`-d` makes things worse.** `libusbmuxd_set_debug_level(debugLogLevel - 2)` underflows, and one
  `-d` silences the two messages that actually diagnose a netmuxd mismatch.
- Systemd needs an **absolute `WorkingDirectory`** (`./AltServerData` is relative, and systemd
  defaults CWD to `/`), `Environment=ALTSERVER_ANISETTE_SERVER=…`, and journald rate limiting off.
  A container needs `network_mode: host` and `init: true`.
