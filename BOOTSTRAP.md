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
Copy it to the VM, then:

```bash
unzip AltServer-amd64.zip
chmod +x AltServer-x86_64        # artifact upload strips the executable bit; without this: status=203/EXEC
./AltServer-x86_64 --help        # sanity check
```

The binary is named for the gcc triple (`x86_64`), the artifact for the matrix label (`amd64`).

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

## Phase 2 — Anisette server

**This is the one step most likely to cost you an evening.** Run it on the same box, bound to
loopback, over plain HTTP — that avoids TLS trust entirely, which matters because our client uses
the default http_client config with certificate verification ON.

Run whichever anisette server you choose in Docker with a **named volume for its provisioning
state**, then verify it against our client's actual contract before going further.

### The contract our client requires (from `src/AnisetteDataManager.cpp`)

A plain `GET` returning HTTP **200** and a JSON **object** with these ten keys, **every value a
JSON string** (not a number):

```
X-Apple-I-MD-M   X-Apple-I-MD   X-Apple-I-MD-LU   X-Apple-I-MD-RINFO   X-Mme-Device-Id
X-Apple-I-SRL-NO   X-MMe-Client-Info   X-Apple-I-Client-Time   X-Apple-Locale   X-Apple-I-TimeZone
```

Note the inconsistent capitalisation — `X-MMe-Client-Info` (capital MM) versus `X-Mme-Device-Id`
(lowercase m). Matching is case-sensitive.

```bash
curl -s -H 'User-Agent: Xcode' http://127.0.0.1:6969 | jq 'map_values(type)'
```

Every one of the ten must read `"string"`. A server emitting `X-Apple-I-MD-RINFO` as a *number* is
a known real-world variant — and it is the one field parsed with `std::atoi`, which returns 0
silently rather than erroring, producing an opaque `-36607` later.

### Verify its identity survives a restart

Skip this and you may hit a 2FA prompt on every refresh, which unattended operation can never
answer:

```bash
curl -s http://127.0.0.1:6969 > /tmp/a1.json
docker restart <anisette-container>
sleep 5
curl -s http://127.0.0.1:6969 > /tmp/a2.json

for k in X-Apple-I-MD-M X-Apple-I-MD-LU X-Mme-Device-Id X-Apple-I-SRL-NO; do
  a=$(jq -r ".\"$k\"" /tmp/a1.json); b=$(jq -r ".\"$k\"" /tmp/a2.json)
  [ "$a" = "$b" ] && echo "$k stable" || echo "$k CHANGED -- state is not persisting"
done
```

Those four must be **identical**. `X-Apple-I-MD` is a one-time password and *should* differ.
If anything changed, find where the ADI blob actually lives and mount that path properly.

**Also: NTP must be right on whichever host runs the anisette server**, not just the AltServer
host — Linux forwards the anisette server's timestamp verbatim to Apple. Clock skew surfaces as a
generic `-36607` with nothing pointing at a clock.

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

```bash
export ALTSERVER_ANISETTE_SERVER=http://127.0.0.1:6969

./AltServer-x86_64 \
  -u <UDID-from-phase-3> \
  -a <appleid@example.com> \
  -p '<password>' \
  AltStore.ipa
```

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

**If it installs but crashes instantly at launch, that is issue #131** — the fix in `65a5727` did
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
