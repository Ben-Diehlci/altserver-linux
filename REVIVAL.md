# AltServer-Linux revival log

Working log for the `bd/revival` branch of `Ben-Diehlci/altserver-linux`, a fork of
`NyaMisty/AltServer-Linux`. Upstream's last real code commit predates 2025 — everything since
is `[proj] keepalive-workflow auto commit` bot noise.

**Keep this file current in the same commit as the change it describes.** The point is that
nothing here has to be re-derived later.

---

## The goal

**Run AltServer unattended on a Linux home server, so sideloaded apps keep refreshing without a
Mac or PC having to be powered on.**

Everything below is ranked against that, and it implies requirements a desktop AltServer does
not have:

- **Headless.** No GUI, no console operator. Failures must be visible in logs and on the phone,
  because nobody is watching a screen. This is why the anisette work routes errors through
  `ServerError` (they reach AltStore on the device) and why startup warns about missing config.
- **Unattended and long-lived.** Runs under systemd or Docker across reboots. Config comes from
  the unit file or `-e` flags, so "it starts but silently cannot sign in" is the worst failure
  mode — hence the startup check.
- **Wi-Fi, not USB.** The phone is not plugged into the server. Wireless refresh is *essential
  here*, not optional, which promotes the netmuxd/usbmuxd discovery issues from "someone's
  edge case" to a core requirement.
- **7-day refresh cycle.** Free Apple developer certificates expire weekly, so the whole point
  is that the box signs in and refreshes on its own. Anything that breaks sign-in breaks the
  entire premise.

---

## Reference sources

| Source | What it is | Why it matters |
|---|---|---|
| `~/Local Work/AltStore` | **`rileytestut/AltStore`, actively maintained.** Clone is at `56854e66`, v2.3.3, 2026-07-14. | The live upstream. Contains `AltSign`, the macOS `AltServer`, `AltJIT`, `AltDaemon`. **Check here first** for how something is *currently* done before inventing an answer. |
| `upstream_repo/` (submodule) | `rileytestut/AltServer-Windows`, pinned at `071b1dd`, **2022-04-25** | What this fork actually compiles. Four years stale — see TODO. |
| `libraries/*` (submodules) | libimobiledevice family | All pinned 2020–2022. |
| `NyaMisty/AltServer-Linux` issues | 53 open, most long stale | Triaged 2026-09-13; ~28 of 53 are Apple-side, obsolete, or another project's bug. |

Cross-referencing AltStore has already paid off twice: it confirmed `NSISO8601DateFormatter`
with default options is the canonical `X-Apple-I-Client-Time` form
(`Dependencies/AltSign/AltSign/Apple API/ALTAppleAPI.m:47`), and that
`AltServer/Anisette Data/AnisetteDataManager.swift:165` still emits a client-info string
containing `com.apple.dt.Xcode/3594.4.19` as of v2.3.3 — consistent with the Apple GSA block
being a recent (~2026-09) change rather than a long-standing one.

---

## How to build

macOS cannot build this. A Colima VM is set up; the build tree lives **inside** the VM at
`~/altserver-linux` (ext4, case-sensitive) — *not* the macOS copy, which is case-insensitive
APFS and can mask CI-only include-casing errors.

```bash
colima start --vm-type=vz --vz-rosetta --cpu 8 --memory 12 --disk 60

colima ssh -- bash -lc 'cd ~/altserver-linux && docker run --rm \
  -v "$HOME/altserver-linux":/workdir -w /workdir \
  ghcr.io/nyamisty/altserver_builder_alpine_aarch64 \
  bash -c "mkdir -p build; cd build; make -f ../Makefile -j8"'
```

`aarch64` is native on Apple Silicon. `amd64` and `386` work via Rosetta/QEMU. **`arm/v7` is
not registered by default** and needs an extra binfmt handler. Changes do not sync between the
macOS and VM copies automatically — copy deliberately in both directions.

To exercise runtime behaviour without an iPhone, link a harness against the built objects,
excluding `AltServerMain.cpp.o` (it owns `main`) and stubbing `make_uuid()`,
`temporary_directory()` and `readFile()`.

---

## Done

| Commit | What |
|---|---|
| `df570bd` | **CI unbroken.** Dead `gautamkrishnar/keepalive-workflow@master` removed; five actions off retired node12/16; `::set-output` → `$GITHUB_OUTPUT`; per-job `permissions`; `sync_upstream` boolean bug. |
| `6cd382a` | **node24 bump**, part 1: checkout v4→v7, setup-qemu v3→v4, login-action v3→v4, gh-release v2→v3. |
| `8494e36` | **node24 bump**, part 2: third-party uploader → `actions/upload-artifact@v7`, download-artifact v4→v8, matrix restructured to carry an `arch` label. |
| `a266861` + `15e5be6` | **corecrypto: all 3 layers fixed — CLOSES #111.** Full `make; make install` exits 0. The buildenv image is rebuildable from source again. |
| `f2540f3` | **Bootstrap blockers cleared:** PR #135 client-info sanitization (with an `ALTSERVER_NO_CLIENTINFO_SANITIZE` escape hatch), and the getopt `-a` fallthrough / uninitialised argv pointers / unreachable `-h`. |
| `65a5727` | **#131 fixed** via the ldid rewriter: capture the SHA-256 CodeDirectory hash before truncating to 20 bytes. Build-verified; **not** verified on an iOS 26 device. |
| `654907a` | **mDNS advertisement failure made loud.** Both failure paths verified; success path NOT verified locally — no working avahi in the build container. Must be confirmed on the real host. |
| `b885501` | **anisette error handling** rewritten; `mktime`→`timegm`; `ResetProvisioning` Windows-path bug. Closes #104. |
| `301eb8b` | **Daemon warns at startup** when no anisette server is configured, instead of looking healthy and failing at first use. Warns rather than exits: only `AnisetteDataRequest` needs anisette; AltJIT and profile requests do not. |
| `4f5c086` | **Anisette as a Portainer stack** — and the volume path the upstream README gets wrong (`lib/` holds only the `.so` cache; the identity is one level up). |
| `e2a2c36` | **Non-200 from Apple's auth endpoint reported** instead of surfacing as "invalid response". The status was logged then discarded, so a 429 became a plist parse failure. |
| `f88d87f` | `--help` registered as a long option — it was documented and handled but missing from `long_options`, so it hit the error path. |
| `22ea3fc` | Pairing wizard detects an existing backup, and only counts one containing **both** plists. |
| `d0495b3` | README: removed instructions that were actively wrong (missing `cd build`, the stale `-mno-default` ARM warning, corecrypto steps for a distribution Apple no longer ships). |
| `9507c25` | **GrandSlam 429 fixed.** `gsaClient()` returns a fresh client per call, so each request opens its own connection. Proven by probe, then confirmed by a real sign-in. |
| `3b3f672` + `4be5f9f` | **CI iteration cost cut.** `fail-fast: false`, and amd64-only on branch pushes with the full matrix on tags / schedule / `all_arches`. |
| `dcac3de` | **Credentials from the environment** (`ALTSERVER_UDID` / `_APPLE_ID` / `_APPLE_PASSWORD`), so a detached container works and the password is not in `ps`. |
| `7b8a3eb` | **Containerised.** Multi-stage `Dockerfile` bundling every runtime prerequisite and verifying `libdns_sd.so` loads at build time; `deploy/altserver-stack.yml`; `build_image.yml` publishing to the fork's OWN namespace. |
| `4f97ab4` | **Named volumes** — no host `mkdir`, and no `chown`, since a fresh volume inherits the image path's ownership. |
| `41d7a76` | **AltStore IPA fetched automatically** on start, resolved from AltStore's own catalogue so it is always current. Idempotent, atomic, verifies a `Payload/*.app`, non-fatal on failure. |
| `732f5a5` `63b27a9` `9b57afd` | **Setup web UI**, three slices: status dashboard, pairing wizard, and install with in-browser 2FA entry. |
| `f3cbbf3` | Web UI runs as a stack service on `:8099`; image gains the tools its checks shell out to. |
| `308c9b9` | **`ThreadingHTTPServer`** — the single-threaded server was serialising every page behind slow checks. Measured: a 10s request no longer blocks others. |
| `a3937e6` | Install page auto-populates the UDID from `/api/pairing`. |
| `64d7441` | Two status checks that reported nonsense: the process check was a PID-namespace false negative, and the clock check used `timedatectl`, which cannot work in a container. |
| `af2be1e` | **mDNS from a container needs AppArmor rules.** Tested profile + installer; see below. |
| `1bd0183` | README rewritten for the current state, leading with an honest status table. |
| `2a2e99e` | **JS syntax error fixed** — a literal newline in a string literal was killing every page's script block. All three pages now `node --check`ed. |
| `b98a01e` | **`paths:` filter fixed** — `web/**` changes were not rebuilding the image, so five commits of fixes never shipped. |
| `bb4713d` | REVIVAL.md: both findings written up. |
| *(this)* | **Two CI guards added** (`tests/`), each verified to fail by reintroducing the original bug. |
| *(this)* | **netmuxd added to the image and the stack** — wireless device transport, the last missing piece for unattended refresh. |

### CONFIRMED 2026-09-14: the Apple GSA client-info block is real

Upstream PR #135 was an unverified third-party claim. It is now reproduced against real Apple
infrastructure, in both directions, on this deployment:

| `ALTSERVER_NO_CLIENTINFO_SANITIZE` | `X-MMe-Client-Info` sent | First GSA response |
|---|---|---|
| unset (sanitizer ON) | `…(com.apple.akd/3594.4.19)` | **200** |
| `=1` (sanitizer OFF) | `…(com.apple.dt.Xcode/3594.4.19)` | **503** |

So Apple's `gsa.apple.com/grandslam/GsService2` does reject any request carrying the substring
`com.apple.dt.Xcode`, and the `com.apple.akd` rewrite does get past it. **Keep the sanitizer on.**
Worth reporting back on PR #135 — the author's curl repro is independently confirmed.

This also cleanly separates two issues that looked like one. The remaining failure is a **429 on
the SECOND GSA request**, which is unaffected by the client-info value, reproduced identically 28
minutes apart, and present on the very first attempt ever made from this machine — so it is not
cumulative volume throttling.

**CAUSE IDENTIFIED (high confidence): HTTP connection reuse.** An earlier hypothesis recorded
here — that the one-time password was being replayed — was WRONG and is retracted. Upstream passes
a single anisette object to init, complete *and* apptokens
(`AltStore/Dependencies/AltSign/.../ALTAppleAPI+Authentication.swift`, same object at lines 69, 98
and 165), so one OTP per transaction is the designed and historically working behaviour.

The real mechanism, verified in this tree: `AppleAPI` is a process-wide singleton holding ONE
`_gsaClient`, built in its constructor (`AppleAPI.cpp:112-117`). `gsaClient()` (`:1013`) returns a
copy sharing the same cpprestsdk impl and therefore the same asio connection pool, and the second
GSA request is issued from a `.then()` continuation the instant the first completes — textbook
keep-alive reuse. No `Connection` header is ever set; the request carries exactly four
(`AppleAPI+Authentication.cpp:963-968`). Since ~2026-09 Apple's GrandSlam edge refuses the second
request on a reused connection.

The structural argument is what makes this convincing: between request 1 and request 2, every
header and all ten anisette values are **byte-identical**. Only the plist body (`o=init` vs
`o=complete`) and the connection position differ. Anything identical in both cannot by itself
explain a different outcome, which eliminates the `X-Apple-I-SRL-NO` of `"0"`, the timestamp, the
User-Agent and the client-info string as sole causes.

Corroborated across the ecosystem: rileytestut/AltSign PR #52 is literally *"Use a separate
connection for each GrandSlam request"* (shipped in AltServer 1.7.6); nab138/iloader 2.3.3's
entire release note is *"Disabled reqwest pooling to alleviate http 429 from grandslam"* — on a
client that **already carried** the akd fix, which is why the akd rewrite *reveals* the 429 rather
than causing it; iloader #709 places the 429 at the proof/complete request across five Apple IDs
on five machines, so it is not account-scoped.

**PROVEN 2026-09-14, with zero Apple ID attempts.** `docs/gsa-connection-probe.sh` sends four
`o=init` requests (no password, nonexistent `.invalid` address) against the real endpoint:

| Run | Request | Status | `num_connects` |
|---|---|---|---|
| A — one curl, `--next` | 1 | `200` | 1 |
| A — one curl, `--next` | 2 | **`429`** | **0** ← socket reused |
| B — `Connection: close` | 1 | `200` | 1 |
| B — `Connection: close` | 2 | **`200`** | 1 ← fresh socket |

Byte-identical requests; the only variable is whether the socket was reused. This is **not volume
throttling** — waiting between attempts was never going to help, and the per-connection rule
explains every observation: positional, stable, non-cumulative, present on the first attempt ever.

**Fixed** in `makefiles/AltSign-build/rewrite_altsign_source.py`: `gsaClient()` returns a fresh
client per call. The fix matches the proven mechanism; still to be confirmed by an actual sign-in.

Two corrections recorded so they are not re-derived. The probe's first run returned `HTTP 000`
with "self-signed certificate in certificate chain", which looks like interception but is not:
`gsa.apple.com` is served from **`Apple Server Authentication CA`**, Apple's own private CA, which
no public trust store contains. DNS is clean (both resolvers answer inside Apple's `17.0.0.0/8`;
differing addresses are Akamai geo-routing). Consequently `set_validate_certificates(false)` on the
GSA client in `AppleAPI.cpp` is **required**, not careless — pinning Apple's CA would be an
improvement, but it is not the security hole it resembles.

Known remaining divergence, NOT the cause: our sanitizer replaces only the bundle-id substring, so
the wire value is `com.apple.akd/3594.4.19` — akd has never carried an Xcode build number.
Upstream PR #1790 replaces the whole token with `com.apple.akd/1.0`. Worth tightening separately;
it is identical in both requests so it cannot explain 200-then-429.

### CONFIRMED 2026-09-14: mDNS from a container is blocked by AppArmor, not by anything it looks like

The daemon ran, anisette was healthy, pairing was valid — and `_altserver._tcp` was never
published. `DNSServiceRegister` returned **-65553 (`kDNSServiceErr_Refused`)**.

**Cause: Docker's `docker-default` AppArmor profile contains no `dbus` rules, and AppArmor denies
a mediated class a profile does not mention.** So the very first call any D-Bus client makes is
refused, and avahi-compat never connects:

```
apparmor="DENIED" operation="dbus_method_call" bus="system"
path="/org/freedesktop/DBus" member="Hello" label="docker-default"
```

**Everything else looks correct while this is happening**, which is what makes it expensive:
the sockets are `srw-rw-rw-`, avahi's D-Bus policy allows the default context, the container is
uid 0 with no userns remapping, and the host publishes fine with `avahi-publish`. None of it is
ever consulted — the call never leaves the container.

Four hypotheses were wrong before the right one: TLS interception (the cert is genuinely Apple's,
from their private CA), a missing `/etc/machine-id` (mounting it changed nothing), socket
permissions, and D-Bus policy. **The kernel audit log named it exactly.** For anything
inexplicably denied inside a container, `sudo dmesg | grep -i 'apparmor.*DENIED'` is the first
move, not the last.

Fix, both shipped:

| | |
|---|---|
| `apparmor=unconfined` | The stack default — **only** because a container requesting a profile the host has not loaded FAILS TO START, which would break deploying straight from the repo |
| `deploy/apparmor/altserver-mdns` | `docker-default` verbatim plus the narrowest D-Bus rules Bonjour needs (bus handshake + `org.freedesktop.Avahi`, nothing else). Install with `deploy/apparmor/install.sh` |

Tested rather than argued: under `docker-default` the call fails `Access denied` with one AppArmor
denial in `dmesg`; under `altserver-mdns` it reaches the daemon with **zero** denials, and
`/proc/self/attr/current` reads `altserver-mdns (enforce)`.

**This step cannot be automated into the deployment.** AppArmor profiles load into the host kernel
as root, and `security_opt` only *selects* an already-loaded profile. That is a property of
AppArmor, not a packaging gap — the only alternative would be a privileged init container mounting
`/sys/kernel/security`, which is a far larger hole than the one being closed.

### CONFIRMED 2026-09-14: a stale image made fixed bugs keep reproducing

`build_image.yml`'s `paths:` filter listed only the C++ build inputs, so **`web/**` changes never
triggered an image rebuild**. Five consecutive commits of web UI fixes sat in the repo without
ever reaching a published image, while CI stayed green and deploys succeeded.

The symptom was the worst available: known-fixed bugs still reproducing in production, with
nothing anywhere indicating the artifact was stale. It sent us back to debug code that was already
correct.

**Two rules, both learned the expensive way:**

1. A `paths:` filter must list everything that goes *into* the artifact, not just what compiles.
   `web/` is `COPY`-ed into the image; so is `docker-entrypoint.sh`. `upstream_repo` decides what
   gets compiled at all.
2. **"Does the deployed thing actually contain the fix?" comes before "why didn't the fix work?"**
   One command settles it:
   ```bash
   docker inspect altserver-web --format '{{.Image}}'
   docker exec altserver-web grep -c "<something from the fix>" /opt/altserver-web/server.py
   ```
   Note `:latest` does not re-pull on its own — a Portainer stack update needs **Re-pull image**
   ticked, so a deploy can silently reuse a months-old layer.

**`tests/check_workflow_paths.py` now enforces rule 1**: it cross-checks every `COPY` in the
Dockerfile against the `paths:` filter and fails if something entering the image would not
rebuild it. It runs from `build.yml`, not `build_image.yml` — a guard living inside the filtered
workflow would be skipped by precisely the bug it detects.

### CONFIRMED 2026-09-14: one bad JS escape broke every page

The install page did nothing: the form would not submit, no log appeared, the state stayed on its
placeholder, and the UDID never filled in. The backend was provably fine the whole time — `curl`
against `/api/install/start` returned correct validation errors, and no exception ever reached the
container log.

An escaped newline had collapsed into a **literal newline inside a JavaScript string literal**
when the page was generated, which is a syntax error and kills the entire `<script>` block. Every
symptom followed from that one line, which is why it presented as several unrelated faults.

Now built with `String.fromCharCode(10)`, which nothing between Python and the browser can mangle.
`installer.py` had been well tested against a mock; the thing the browser actually runs had never
been parsed by anything. **`tests/check_page_js.py` now extracts every `<script>` block from all
three pages and runs `node --check` on it** — verified to fail by reintroducing this exact bug.

### CONFIRMED 2026-09-14: wireless refresh needs netmuxd, and iOS 26 still allows it

AltStore installed successfully, so the remaining goal is refresh happening with no cable. Measured
directly on the server, phone unplugged:

```
idevice_id -l          -> ERROR: Unable to retrieve device list!
systemctl is-active usbmuxd  -> inactive        (unit is `static`, udev-activated)
ss -lpx | grep usbmuxd -> nothing, though /var/run/usbmuxd exists as a stale socket file
```

**That error is not "no devices"** -- an empty list prints nothing and exits 0. It is a socket
connection failure: nothing was listening. Ubuntu's usbmuxd starts on cable-insert and exits when
the last device is removed, so an unattended server has no mux at all. Even with one running, stock
usbmuxd enumerates USB only and has no network-device support.

Everything else in the wireless path was already healthy:

| Layer | Measured |
|---|---|
| Subnet | phone `192.168.8.45`, server `192.168.9.16/22` -- same subnet, ICMP fine |
| Phone advertising | `_apple-mobdev2._tcp` -> `iPhone.local`, TXT `authTag` + `identifier` |
| **lockdownd over Wi-Fi** | **port 62078 OPEN** |
| AltServer discoverable | `_altserver._tcp` on `192.168.9.16:37271` |

**62078 is the port that matters.** The port in the mDNS TXT record (32498 here) is a different
service and returns RST; that looked like a dead phone and was not. The IPv6 link-local address
embedded in the service name times out -- NDP to a phone's link-local across a bridged VM
interface is unreliable -- and is irrelevant, since IPv4 works.

So **plain wireless lockdown still exists on iOS 26**. The iOS 17+ RemoteXPC shift that killed
AltJIT did not take this path with it. (`_remoted._tcp` is absent over Wi-Fi, but that is expected:
it is advertised over the USB RSD interface.)

**The fix, now in the stack.** netmuxd v0.4.3 (July 2026 -- the README's old "netmuxd >= 0.3" note
was stale) ships prebuilt Linux binaries, so the image downloads one rather than carrying a Rust
toolchain. Flags verified against `src/config.rs`, not memory: `--socket-path` (default
`/var/run/usbmuxd`), `--plist-storage`, `--disable-usb`, `--disable-mdns`, `--disable-heartbeat`.
It uses the pure-Rust `mdns-sd` crate, not avahi -- so it needs host networking for multicast but
none of the D-Bus/avahi sockets AltServer requires, and it coexists with the host's avahi on 5353.

**Design decision worth keeping: netmuxd gets a PRIVATE socket path in a named volume, not
`/var/run/usbmuxd`.** Taking the host socket would mean masking the host's usbmuxd, and a
bind-mounted socket *file* cannot be replaced from inside a container anyway. On a private path the
host's usbmuxd keeps handling the cable for first-time pairing and nothing contends. AltServer is
pointed at netmuxd via `USBMUXD_SOCKET_ADDRESS` -- the `UNIX:` prefix is supported, verified at
`upstream_repo/libusbmuxd/src/libusbmuxd.c:160`, which calls `socket_connect_unix()` on the
remainder. Watch the spelling: `USBMUXD_SOCKET_ADRESS` with one D is silently ignored.

The status page now checks BOTH transports separately and reports which one found the device.
Reporting them together would hide the one failure that matters: USB fine, wireless dead.

### Other things learned the hard way

- **`gsa.apple.com` is served from `Apple Server Authentication CA`**, Apple's own private CA,
  which no public trust store contains. `curl` rejects it as self-signed. So
  `set_validate_certificates(false)` in `AppleAPI.cpp` is **required**, not careless — pinning
  Apple's CA would be better, but validation cannot succeed as things stand.
- **A verification that cannot fail is worse than none.** The anisette persistence check reported
  four fields "stable" when the server was returning nothing, because `jq -r` on an empty file
  prints an empty string and exits 0. Same family as `DNSServiceRegister` returning success
  unconditionally, and the CI `chmod +x` that was a no-op.
- **`HTTPServer` is single-threaded.** Three pages polling at 30s/5s/1.5s serialised behind checks
  that take up to 15s, which presented as the UI freezing when switching pages.
- **Containers have separate PID namespaces**, so a sidecar's `pgrep` cannot see the daemon —
  a confident false negative, fixed with `pid: host` plus a check that knows when it is blind.
- **`timedatectl` cannot work in a container.** Drift against the *anisette server's* timestamp is
  both measurable there and the thing that actually breaks sign-in.

### Verified facts worth not re-deriving

- **The CI failure was an unresolvable action, not the node12 versions.** `uses:` resolution
  happens in "Set up job", before any script runs. `gautamkrishnar/keepalive-workflow` has no
  `action.yml` at any ref. Because `build`/`release`/`update_submodule` gate on `check` via
  `needs:`, the whole workflow skipped — which is what #121 is really reporting.
- **`@actions/artifact` has exactly one backend boundary**: toolkit 2.0.0, the v3→v4 service
  migration. The download path has no format-version gating. But the old uploader served blobs
  with Content-Type `zip`, which `download-artifact@v8` does *not* recognise as a zip — so the
  uploader and downloader had to move together or raw `.zip` files would land in releases.
- **`upload-artifact` rejects `/ \ : < > | * ? "` in artifact names**, which is why the matrix
  grew an `arch` label instead of reusing `matrix.builder`.
- **Artifact upload does not preserve the executable bit** — so `chmod +x` in the build step is
  a no-op. Likely the root of #126, unfixable in CI alone: raw GitHub Release assets never carry
  the bit. Needs docs or tarball packaging.
- **The four `ghcr.io/nyamisty/altserver_builder_alpine_*` images are alive and public.** The
  build depends entirely on them; nobody can currently rebuild them (see #111).
- **Apple versioned the corecrypto archive**: it now extracts to `corecrypto-2024/`. Docker's
  `WORKDIR` silently *creates* a missing directory, which is why the error surfaced one line
  later as a confusing "no CMakeLists.txt".
- **The build rewrites Windows source at compile time.** `makefiles/rewrite_altsign_source.py`
  contains `content.replace(b'winsock2.h', b'WinSock2.h')` — that is how lowercase Windows
  includes resolve against capitalised shim filenames on case-sensitive Linux.
- **`-mno-default` is already guarded to i386/i686.** The README's "remove it for ARM" note is
  stale; ARM builds work unmodified.
- **`SPOOF_MAC` is never defined**, so the `#else` provisioning block in `AnisetteDataManager`
  is dead code.
- **Only `AnisetteDataRequest` needs an anisette server.** The daemon's other five request types
  — PrepareApp, Install/RemoveProvisioningProfiles, RemoveApp, EnableUnsignedCodeExecution
  (AltJIT) — work without one. This is why the startup check warns instead of exiting.
- **Errors reach the phone, not just the log.** `ClientConnection::ErrorResponse` dynamic_casts
  to `ServerError` and forwards `userInfo`, so a `ServerError` with `NSLocalizedFailure` set is
  displayed in AltStore.

---

## Deployment research findings (2026-09-14)

**Scope: one iPhone, iOS 26.x. No iPad, no second device, no other systems.** So multi-device
concerns, `activeProfiles` juggling and device-slot exhaustion (#113, #86) are all out of scope.

### Host prerequisites — concrete checklist

- **`python3` on the service's PATH.** A *runtime* dependency, not a build one:
  `dnssd_loader.cpp:68` `execlp`s it, because the AltServer binary is `-static` and cannot dlopen
  Bonjour itself. Needs only stdlib `ctypes`.
- **`libavahi-compat-libdnssd-dev`**, not `...-libdnssd1`. Confirmed on the target host.
- **`avahi-daemon` running, with dbus under it.** avahi-compat is a thin client proxying to the
  daemon; it owns UDP/5353, not us.
- **`/etc/avahi/avahi-daemon.conf`**: `[publish] disable-publishing=no`,
  `disable-user-service-publishing=no`, and `allow-interfaces=ens18` so avahi does not also
  publish `docker0`/`virbr0` — `DNSServiceRegister` is called with `interfaceIndex 0`
  (`ConnectionManager.cpp:122`). Consider `use-ipv6=no`: `ConnectionManager.cpp:140,152` binds
  AF_INET only, while upstream macOS uses a dual-stack listener.
- **`avahi-utils`** for `avahi-browse` — the only way to distinguish *published* from
  *DNSServiceRegister returned 0*.
- **`usbmuxd` + `libimobiledevice-utils`** for the one-time cabled pairing and for triaging
  netmuxd without involving AltServer.
- **`netmuxd` >= 0.3 owning `/var/run/usbmuxd`, with `usbmuxd` STOPPED.** Stock usbmuxd never
  emits ConnectionType "Network", and netmuxd binds that socket by default, so the two collide.
  The only success report in #77 is: netmuxd, no flags, usbmuxd not running. If pointing at TCP
  instead, the variable is `USBMUXD_SOCKET_ADDRESS` — the widely-copied instruction in #49
  misspells it `USBMUXD_SOCKET_ADRESS` (one D) and is silently ignored.
- **`/var/lib/lockdown` on real persistent storage, never tmpfs.** Back up `<UDID>.plist` **and**
  `SystemConfiguration.plist` as a unit — they are not independent, and half a pairing is
  indistinguishable from none. In Docker this lives in whichever container runs the muxer.
- **Anisette on the same box, loopback, plain HTTP**, ADI state on a named volume. Plain
  `http://127.0.0.1:6969` also sidesteps TLS trust entirely: `FetchAnisetteData` uses the default
  http_client config, so certificate verification is ON (unlike AltSign's gsa client).
- **Accurate NTP on whichever host runs the ANISETTE server**, not the AltServer host. Linux
  forwards the anisette server's `X-Apple-I-Client-Time` verbatim; macOS stamps `Date()` locally.
- **Absolute `WorkingDirectory`** (systemd) or workdir (docker) — `./AltServerData` is relative
  and systemd defaults CWD to `/`.
- **Disable journald rate limiting** for the unit (`LogRateLimitIntervalSec=0`,
  `LogRateLimitBurst=0`). `WirelessConnection.cpp:95,122` print two unbuffered lines per <=4096-byte
  chunk, so a large transfer trips the 10000-per-30s default — and the suppressed messages are the
  ones at the END of an install, exactly the errors you want.
- **Firewall: the whole ephemeral TCP range on the LAN interface, plus UDP/5353 both ways.**
  `ConnectionManager.cpp:151` sets `sin_port = 0`, so the port differs every start and no static
  rule is writable.
- **Docker only:** `network_mode: host`, a bind mount of the dbus system bus socket (or
  avahi-daemon inside the image), and **`init: true`** — the binary installs no SIGTERM handler,
  so as PID 1 the kernel drops `docker stop`'s SIGTERM and every restart costs the full grace
  period then SIGKILL.
- **`chmod +x` the downloaded release binary** — artifact upload does not preserve the bit.
  systemd at least fails legibly here: `status=203/EXEC`.

### Silent failure modes — the real enemy for unattended operation

Ranked. These are the ways it stops refreshing with nobody finding out.

1. **Both ends go quiet at once.** Covered above: the phone suppresses server-not-found on
   background refresh, and the server logs nothing because no connection was attempted. Zero
   evidence anywhere. This is why the Shortcuts intent path
   (`RefreshAllAppsIntent.swift:187`, `ignoresServerNotFoundError = false`) is a requirement.
2. **`DNSServiceRegister result: 0` is not proof of publication.** A user in `closed_issue_0051`
   got result 0 with avahi-daemon *stopped* — consistent with avahi-compat deferring via
   `AVAHI_CLIENT_NO_FAIL`. **Our committed fix (`654907a`) does not close this**: it catches a
   child that exits, and the `sys.exit` addition catches a non-zero result, but it cannot catch
   avahi *lying* about success. Only an out-of-band `avahi-browse` from another host can.
3. **avahi restarts and nothing re-registers.** `StartAdvertising` is called exactly once
   (`ConnectionManager.cpp:174`). No retry, no health check, and nothing calls
   `DNSServiceProcessResult`, so the registration callback never fires either way. An
   unattended-upgrades run touching avahi overnight is a multi-day silent outage.
4. **A stale python3 child can advertise a dead port.** `PR_SET_PDEATHSIG` fires when the
   *forking thread* exits, not the process, and the fork happens on the listening thread. The
   child can survive holding a registration for an ephemeral port that no longer exists; since
   `flags = 0` (no `NoAutoRename`), avahi renames rather than replaces, and the phone takes
   `discoveredServers.first` — a coin flip between live and dead.
5. **A dropped client pins a worker at 100% CPU and floods the disk.** `ReceiveData` ignores
   `recv()`'s return (`WirelessConnection.cpp:116`); on peer close it returns 0 forever while
   select still reports readable, so the loop spins printing two lines per pass. Enough of these
   exhaust cpprest's ~40-thread pool and the daemon stops answering while still reporting
   `active (running)`. Never filed — it presents as "the server stopped refreshing".
6. **A response that failed to send is logged as success.** `SendData` never checks `send()`'s
   return while SIGPIPE is ignored, and its break condition is true on the first iteration
   regardless. Journal says "Finished handling request!"; the device saw a timeout.
7. **Anisette identity silently regenerates.** Every identity field comes from the HTTP response;
   Linux holds no local ADI state. A container recreated onto the wrong volume path means Apple
   sees a new machine and demands 2FA — which background refresh can never surface. #86 reports
   exactly this.
8. **Anisette clock drift is invisible here.** We parse and re-emit the *server's* timestamp, so
   NTP on the AltServer box proves nothing; skew surfaces as an opaque -36607.
9. **`X-Apple-I-MD-RINFO` uses `std::atoi`**, which returns 0 for non-numeric input with no error
   — the one field of the ten not guarded by `requireString`. Pre-existing; unchanged by our work.
10. **Real device faults are displayed as "AltServer could not be found".** AltStore remaps
    deviceNotFound/lostConnection to serverNotFound for any wireless server that is not
    `isPreferred`, and AltServer-Linux hardcodes serverID `"1234567"` while Mac/Windows use a
    UUID — so unless the Linux box itself installed AltStore, `isPreferred` is permanently false
    and every netmuxd/pairing fault wears the wrong error message. **This will send you to debug
    mDNS when mDNS is fine.**
11. **Adding `-d` makes the decisive lines disappear.** `AltServerMain.cpp:179` calls
    `libusbmuxd_set_debug_level(debugLogLevel - 2)`, so one `-d` sets level -1 and
    `LIBUSBMUXD_ERROR` stops printing — losing exactly the two messages that diagnose a netmuxd
    mismatch.
12. **`journalctl -p err` is empty no matter what breaks.** `OutputDebugStringA` is `std::cout`,
    so nearly everything is stdout at info. Severity filtering is useless on this unit, and
    unbuffered cout from ~40 threads interleaves mid-token, so even grep can miss it.
13. **The CLI bootstrap exits 0 even when it failed.** `AltServerMain.cpp:224-238` catches, logs,
    prints "Finished!" and falls off the end of main. A oneshot unit cannot tell success from
    failure.
14. **No liveness signal at all, so `Restart=` can never fire.** `main()` ends in
    `while (1) { sleep(100); }` and never joins the listening thread. `Listen()` can return early
    on socket or bind failure and the process stays `active (running)` forever with no listener
    and no advertisement.

**Conclusion: build an external watchdog before trusting any of this.** Something that
independently runs `avahi-browse` to confirm the service is published, checks the anisette
endpoint, and tracks when a refresh last actually succeeded. Nothing inside AltServer can be
trusted to report its own health.

## TODO

### Blockers for the actual goal — a working headless refresh server

These are what stand between us and "the Linux box refreshes apps by itself". Ranked by what
breaks the premise soonest, not by how interesting the code is.

- **A. An anisette server that actually works in 2026.** Without one, sign-in fails and nothing
  refreshes — the entire goal is dead. The fork no longer ships a default (it was dead anyway),
  so one has to be chosen and run, most likely alongside AltServer on the same box. The old
  `nyamisty/alt_anisette_server` image is from April 2022 and unverified against Apple's current
  flow. **This is the number one practical blocker and is a deployment decision, not a code
  change.**
- **B. PR #135 — the Apple GSA block.** Even with a healthy anisette server, Apple 503s any
  request whose `X-MMe-Client-Info` contains `com.apple.dt.Xcode`, which anisette servers
  commonly return. Trivial code fix, listed under "Next up" below. Blocks sign-in, so it blocks
  the 7-day refresh.
- **C. Wi-Fi device discovery.** The phone will not be plugged into the server, so wireless
  refresh is mandatory here. Needs `netmuxd` (> v0.1.1) in place of or alongside `usbmuxd`.
  Covers issues #87, #81, #77, #76, #75, #122, #49, #13 — previously triaged as mostly
  user-error, but for *this* deployment they describe the critical path. Needs a verified,
  written-down working configuration.
- **D. iOS-version signing — BOOTSTRAP ONLY, NOT THE REFRESH PATH.** Corrected 2026-09-14; an
  earlier entry here wrongly called this the largest task on the critical path.
  **A 7-day refresh does not re-sign anything and does not transfer an IPA.**
  `AltStore/Operations/RefreshAppOperation.swift:68` sends exactly one request,
  `InstallProvisioningProfilesRequest`, which lands at `ClientConnection.cpp:238` →
  `DeviceManager::InstallProvisioningProfiles` → misagent. `Signer` has exactly **one** call site
  in the entire server, `AltServerApp.cpp:1453-1454`, inside `InstallApp`, reachable only from
  the CLI install path. So the 2022-stale signer and #131 block the *first* install on iOS 26.x,
  not the recurring refresh that is the actual goal.
  **And #131 is two lines, not a submodule bump.** `upstream_repo/ldid/ldid.cpp:2215` runs
  `hash.resize(20)` *before* `:2217-2220` captures `alternateCDSHA256 = hash`, so the SHA-256
  hash-agility attribute carries a hash truncated to 20 bytes and CoreTrust rejects it. Moving the
  capture above the resize is the whole fix — verified in source here, matching jaakkopalvaila's
  diagnosis in `open_issue_0131.md`. The competing diagnosis in that thread does not hold against
  this tree: DER entitlements *are* emitted, CodeDirectory version *is* 0x00020400, and a SHA-256
  alternate CD *is* present.
  **ANSWERED 2026-09-14: AltStore is NOT installed. Installing it is the whole point.** So the
  bootstrap path IS required, and #131 is therefore back on the critical path — it is the *first*
  thing that will bite. The mechanism analysis above still stands (it is not a *refresh* blocker),
  but you cannot reach refresh without passing through the install it breaks.
  **FIXED in `makefiles/AltSign-build/rewrite_ldid_source.py`** rather than in the submodule,
  using the project's existing build-time rewriting mechanism, with a guard that fails the build
  loudly if upstream ldid.cpp ever stops matching. **UNVERIFIED ON HARDWARE** — the diagnosis is
  confirmed in source and matches the issue reporter, but nobody has yet confirmed it makes an app
  launch on a real iOS 26 device.

Items A and C are deployment/config work rather than patches, and both need a real device to
confirm. B is a small patch. D is a substantial one. None are blocked by anything already done.

### Bootstrap progress

- **Phase 0 — binary: DONE.** `AltServer-x86_64` from a green CI run on `8997649`, verified by
  string check (contains the new anisette text; the dead armconverter default is absent).
- **Phase 1 — host prereqs: DONE.** avahi-daemon, avahi-utils, libavahi-compat-libdnssd-dev,
  usbmuxd, libimobiledevice-utils. `CDLL('libdns_sd.so')` loads.
- **Phase 3 — USB pairing: DONE.** Proxmox passthrough worked; `idevicepair validate` returns
  SUCCESS and `/var/lib/lockdown/` is backed up with BOTH the per-device plist and
  `SystemConfiguration.plist`. Note: validation fails with "a passcode is set" unless the device
  is unlocked at the time — expected, and it will matter again if re-pairing.
- **Phase 2 — anisette: DEPLOYED AND VERIFIED** (redeploy-persistence test still pending).
  `dadoum/anisette-v3-server`, digest-pinned, Portainer stack, `127.0.0.1:6969`.
  Contract PASS — all ten keys present, all JSON strings, HTTP 200. Clock matches `date -u`.
  **The volume mapping is proven correct**: `docker diff` shows nothing identity-related on the
  writable layer, and `adi.pb` + `device.json` + `lib/` are visible host-side at
  `/opt/stacks/anisette/config`. The `lib/`-only mount the upstream README recommends was avoided.
  Container runs as uid 1000 = host `youruser`.
  Quirk: `adi.pb` is mode `---x-w-rwt` (written by Apple's closed-source libCoreADI), so backups
  need `sudo`.
  **Confirmed: this server returns `com.apple.dt.Xcode/3594.4.19` in `X-MMe-Client-Info`**, so the
  PR #135 sanitizer is load-bearing for sign-in. Leave `ALTSERVER_NO_CLIENTINFO_SANITIZE` unset.
- **Phase 4 — install: sign-in WORKS.** Full Apple authentication completes — SRP, 2FA, team
  lookup, device registration, certificate issuance, all `200`. As far as can be told this is the
  first completed AltServer-Linux sign-in reported in 2026.
  **Still failing at the archive step:** `com.rileytestut.Archive (2)` / "The app could not be
  found" after "Importing app... Downloaded app!". Undiagnosed — the IPA had vanished (named-volume
  redeploy) before it could be inspected. Now fetched automatically and verified present at the
  right size, so it may not recur. If it does, `/tmp` space inside the container is the next
  suspect: unpacking a 33 MB IPA needs roughly double that.
- **Phase 5 — does AltStore open on iOS 26?** The real test of the #131 fix, and the last genuine
  unknown in the whole exercise.

### Security note

A successful sign-in prints Apple's entire account record to stdout — real name, phone number,
`adsid`, and a dozen bearer tokens including `com.apple.gs.icloud.auth` with a **one-year**
lifetime. That reaches `docker logs` and journald. The web UI filters all of it before display,
but the CLI does not. Treat any captured install log as credential material.

### Confirmed deployment facts

Target device runs **iOS 26.x** → #131 / the `upstream_repo` bump is required.
Anisette: **none yet**, needs standing up, most likely as another container on the same box.

Host (from the operator's `HOMELAB_CONTEXT.md`):

| | |
|---|---|
| Hardware | Dell OptiPlex 5060, Intel **x86_64** |
| Stack | Proxmox → Ubuntu VM → Docker, managed via Portainer |
| Binary | **`AltServer-x86_64`** — already produced by our CI, confirmed by artifact name |
| mDNS | `apt install libavahi-compat-libdnssd1` on the VM; `network_mode: host` if containerised |
| Registry | Already publishes to `ghcr.io/ben-diehlci/` |
| Network | Zero open router ports; Cloudflare Tunnels + NPM for external access |
| Philosophy | Prefer simplicity; avoid unjustified complexity |

Consequences for this project:

- The x86_64 leg is the one that matters. It builds under Rosetta locally and is already green
  in CI. `aarch64` remains the fast local loop for compile-checking.
- Sideloading is **entirely LAN-local**. Cloudflare Tunnels, NPM and the zero-open-ports posture
  are irrelevant to it — no reverse proxy should be put in front of AltServer, and nothing about
  this needs to be externally reachable.
- Because they already own `ghcr.io/ben-diehlci/`, re-namespacing `build_docker.yml` (item 6)
  stops being hypothetical, and a purpose-built AltServer container for the Portainer stack
  becomes the natural deliverable — see item 8.

**Network topology — RESOLVED.** The Ubuntu VM is **bridged onto the same network as the phone**;
the operator reaches server services by IP from the phone while at home. So there is no NAT or
VLAN boundary between them and the mDNS prerequisite is satisfied.

Residual risk, small but worth one command to rule out: reaching a host by IP proves L3
routability, whereas mDNS needs **multicast on the same broadcast domain**. A Wi-Fi AP with
client/AP isolation or aggressive IGMP snooping can pass ordinary TCP while dropping multicast
between wireless and wired hosts. Confirm the multicast path specifically by checking that the
server can see the phone's *own* Bonjour advertisements:

```bash
sudo apt install -y avahi-utils
avahi-browse -art | grep -iE "iphone|ipad|_companion-link|_rdlink|_airplay|_raop"
```

If the phone appears there, `_altserver._tcp` will reach it too. If it does not, fix multicast
before touching anything else — nothing downstream can work without it.

**CONFIRMED 2026-09-14.** `avahi-browse -art` on the VM (192.168.9.16, ens18, Ubuntu 24.04.4)
sees `_companion-link._tcp` from Apple devices over both IPv4 and IPv6. Multicast crosses from
Wi-Fi to the wired VM. Topology is settled. Installing `avahi-utils` also pulled in
`avahi-daemon`, which was NOT previously present and which the compat layer requires — so that
was a necessary prerequisite obtained by accident.

### Pairing needs a one-time USB connection

A pair record can only be created over USB in this tree. `libraries/libimobiledevice` falls back
to `lockdownd_pair`, `tools/idevicepair.c:180-182` states wireless pairing is Apple-TV-only, and
`makefiles/libimobiledevice-build/config.h:99` is `#undef HAVE_WIRELESS_PAIRING`. So the phone
must physically touch the Linux box once, with a cable, and Trust it. The "no Mac, no PC" premise
holds for steady-state operation but does not budget for that one cable trip.

### The phone ALSO fails silently — both ends at once

`AltStore/Operations/BackgroundRefreshAppsOperation.swift:60` sets
`ignoresServerNotFoundError = true`, consumed at `:221-223` to suppress the alert. So a 3am
background refresh that cannot find the server posts **no notification on the phone** and logs
nothing on the server — the first symptom is an app that will not open, seven days later.

Useful asymmetry: `AltStore/Intents/App Intents/RefreshAllAppsIntent.swift:187` sets it to
**false**, so a *manually* triggered refresh does surface the error. Manual refresh is therefore
the diagnostic tool; background refresh is the thing that goes quiet.

This is the strongest argument for an external watchdog — something that independently checks the
server is advertising and that a refresh actually succeeded, rather than trusting either end.

### mDNS advertisement is a SILENT failure — the top risk for unattended operation

`libraries/dnssd_loader/dnssd_loader.cpp` does not link Bonjour. `DNSServiceRegister` builds a
Python one-liner, forks, and `execlp`s `python3 -c "from ctypes import *; dll = CDLL('libdns_sd.so'); ..."`
(`:25`, `:66`). The parent branch of the fork is literally `else { ; }` — `status` is declared at
`:53` and never used, there is no `waitpid`, and the function `return 0`s unconditionally at
`:69`. **Advertisement failure is therefore indistinguishable from success inside AltServer.**

Consequences, and they are exactly the wrong shape for a headless box:

- If `python3` is missing, or `libdns_sd.so` cannot be dlopened, AltServer runs normally, reports
  nothing wrong, and is permanently undiscoverable by the phone. The only evidence is the child's
  Python traceback on stderr — unlabelled, and under systemd it lands in the journal interleaved
  with the parent's output. Reproduced in the alpine build container.
- **`libavahi-compat-libdnssd1` alone is NOT sufficient**, despite being the usual advice: it
  ships `libdns_sd.so.1`, while `CDLL('libdns_sd.so')` dlopens the *unversioned* soname, whose
  symlink comes from **`libavahi-compat-libdnssd-dev`**. Verify with the same call the program
  makes:

  ```bash
  python3 -c "from ctypes import CDLL; CDLL('libdns_sd.so'); print('libdns_sd.so OK')"
  ```

- `avahi-daemon` must be installed AND running for the compat layer to work.
- For the container plan (item 7): the image needs `python3` **and** the compat dev package. A
  minimal image will silently fail to advertise.


### Next up

1. **`ServerError` recovery suggestion is Windows-only advice.** `ServerError.hpp:170` returns
   "download the latest versions of iTunes and iCloud… not from the Microsoft Store" for
   `InvalidAnisetteData`, appended to the CLI alert by `AltServerApp.cpp:1614`. Now newly
   visible, since the anisette work routes failures through `ServerError`. Fix by adding a
   substitution to `makefiles/rewrite_altserver_source.py` (it already rewrites this file).
   Note: stuffing `NSLocalizedRecoverySuggestionErrorKey` into `userInfo` does **not** work —
   `ServerError::localizedRecoverySuggestion()` returns from its `case` before reaching
   `default`.
2. ~~**PR #135 — sanitize `X-MMe-Client-Info`.**~~ **DONE** — see the Done table. Original note kept for the caveats: Rewrite `com.apple.dt.Xcode` → `com.apple.akd` at
   `src/AnisetteDataManager.cpp`, the single point where anisette data enters. Apple's GSA edge
   503s any request carrying that substring as of ~2026-09. Trivial. Closes no open issue
   (nobody has reported it — the anisette failure fired first) and needs a real Apple ID to
   confirm 503→401. Only two of four call sites hit `gsa.apple.com`; the others hit
   `developerservices2.apple.com` and were never in the author's A/B test.
3. ~~**corecrypto layer 3.**~~ **SOLVED** — the diagnosis below was wrong, corrected from CI logs.
   The real first error is `Cannot find source file: corecrypto_static/ccrng_static.c`;
   `No SOURCES given to target` is a follow-on and the one people notice. Apple's 2024
   distribution moved `ccrng_static.c` to the tree root but left `CoreCryptoSources.cmake:251`
   pointing at the now-nonexistent `corecrypto_static/` subdirectory. Exactly one entry. Fixed by
   a guarded sed in `buildenv/Dockerfile`; **full `make; make install` exits 0 — #111 CLOSED.** Also
   settled: it is NOT arch-specific — the amd64 CI run fails identically to local aarch64.
   Superseded note: `CORECRYPTO_SRCS` is populated at `CoreCryptoSources.cmake:189` and
   Linux subtracts `CORECRYPTO_EXCLUDE_SRCS` at `CMakeLists.txt:262`, but the list ends up empty
   at `add_library` (`:266`). Cheapest next probe: build the amd64 leg to see whether it is
   arch-specific. Closes #111.

### Bigger

4. ~~**Fix #131**~~ — **DONE**, see the Done table. Left here only as a pointer: the heavier
   alternative, if the rewriter patch ever proves insufficient, is bumping `upstream_repo` to
   1.7.4 (`ldid.cpp`
   truncates a hash to 20 bytes before it becomes the SHA-256 attribute; CoreTrust rejects it).
   `.gitmodules` pins `branch = develop`, whose tip is from 2022, so `--remote` can never reach
   it. Needs a hand-edit: the new `Signer.cpp:277` passes `app.path() + "\\"` and
   `rewrite_altsign_source.py` does no backslash translation. Harden `removePart()` to assert
   each regex matched before attempting this. **Cross-check against `~/Local Work/AltStore`,
   which has the current AltSign.**
5. **README pass.** Merge PR #124 (`cd build`), apply the same fix to the cpprestsdk step, use
   the exact seds from `buildenv/Dockerfile`, lead with the `docker run` command CI uses,
   document `chmod +x` and the python3/`libdns_sd.so`/avahi requirements. Closes #124, #120,
   partially #111. Do **not** rewrite the Wi-Fi section to "keep usbmuxd running" — netmuxd
   binds the unix socket by default and the only success report in #77 says the opposite.
6. **`build_docker.yml` namespace.** Pushes to `ghcr.io/nyamisty/*`, which this fork's token
   cannot write to, so it fails on the fork regardless. Only worth fixing if we decide to own
   our own builder images. Separately, `build_docker.sh` passes no `--platform`, so all four
   builds run as host-arch regardless of the arch-specific base image.
7. **Purpose-built container for the Portainer stack.** Given the host is Docker-on-Ubuntu
   managed by Portainer, and `ghcr.io/ben-diehlci/` already exists, the natural end state is an
   image containing `AltServer-x86_64`, `python3` and `libavahi-compat-libdnssd1`, deployed with
   `network_mode: host` and an absolute bind mount for `AltServerData`. Pairs with item 6.
   Remember `./AltServerData` is a **relative** path, so `WorkingDirectory` / the container
   workdir matters.
8. ~~**getopt hygiene**~~ **DONE** (`src/AltServerMain.cpp`). `case 'a'` has no `break` and falls through to
   `case 'p'`, so `-a` sets *both* appleID and password; `-h` is documented and handled at
   `case 'h'` but absent from the optstring `"u:i:a:p:P:d"`, so it is unreachable; five `char*`
   are uninitialised. All real UB — but fix as hygiene and claim no issue: across 16 pasted
   command lines in the issue corpus, nobody wrote `-p` before `-a`.

### Future / research — NOT scheduled, recorded so they are not lost

These are the operator's stated end-goals for the project. Do not start them until the blockers
above are cleared; they are written down here with enough grounding to be picked up cold.

**F1. Self-contained deploy — LARGELY BUILT.** `Dockerfile` (multi-stage, installs every runtime
prerequisite including the python3 + `libavahi-compat-libdnssd-dev` pair that otherwise fails
silently, and *verifies* `CDLL('libdns_sd.so')` at build time so a broken image cannot ship),
`deploy/altserver-stack.yml` (both services, `network_mode: host`, `init: true`, all the mounts),
and `.github/workflows/build_image.yml` (publishes to `ghcr.io/<owner>/altserver-linux`, using
`repository_owner` so a fork publishes to its own namespace instead of failing against someone
else's — the mistake `build_docker.yml` makes). Portainer supports Git-repository stacks natively,
so "point Portainer at the repo" now works. Remaining: make the package public once, and confirm
the stack end-to-end on the host. Original notes:

**F1 (original).** Point Portainer (or any compose-based platform) at the GitHub repo
and have everything come up with no manual steps beyond entering account credentials. Portainer
supports deploying a stack straight from a Git repository, so the shape is: a `docker-compose.yml`
in the repo, an image published to `ghcr.io/ben-diehlci/`, `network_mode: host` for mDNS, a named
volume or absolute bind mount for `AltServerData` (remember it is a **relative** path), and the
anisette server as a second service in the same stack. Depends on TODO 6 (registry namespace) and
7 (the container image). The image must contain `python3` and `libavahi-compat-libdnssd-dev` or
advertisement fails silently — see the mDNS section above.

Open questions to research: can the anisette server be bundled in the same stack, or does it need
its own identity/state? What is the minimum set of secrets, and can they be Docker secrets rather
than plain env vars? Does anything need to run privileged or with host devices for usbmuxd/netmuxd?

**F2. Web interface — now the main remaining piece, and the scope has grown.** Beyond sign-in,
2FA entry, device selection and health, the operator wants it to **help connect the phone**, so
that someone without background knowledge can get through setup. That is the right instinct:
pairing is where a novice gets stuck, and the failure modes are opaque — the device must be
*unlocked* for `idevicepair validate`; wireless pairing is impossible so a USB cable is mandatory
once; and a real device fault is *displayed* as "AltServer could not be found" because AltStore
remaps it for any server that is not `isPreferred`. A setup wizard that ran `idevice_id -l`,
reported "plug your phone in and tap Trust", and distinguished those cases would remove most of
the difficulty. Original notes:

**F2 (original).** On macOS and Windows AltServer has a tray/GUI
for signing in, entering the 2FA code, choosing a device and triggering a refresh. This port
replaced all of that with a console implementation injected by
`makefiles/rewrite_altserver_source.py`. A web UI is the natural equivalent for a headless box.

**This is probably not optional.** `rewrite_altserver_source.py:96` reads the two-factor code with
`std::cin >> _verificationCode`, i.e. from **stdin**. Under systemd stdin is `/dev/null`, and in
Docker without `-i` likewise, so 2FA sign-in cannot currently be completed in the target
deployment at all. `ShowAlert` (`:132`) has the same problem in reverse — it calls `getchar()` and
would block on an interactive TTY.

So the first research question is narrower than "build a web UI": **how often is 2FA actually
required?** If Apple's session or token is persisted under `AltServerData` and reused, this is a
one-time interactive step that could be handled by running the container once with `-it`, and a
web UI is then a convenience. If a code is needed on every refresh, an out-of-band way to submit
it is mandatory and the whole unattended premise depends on it. Establish that before designing
anything.

If built: it should cover sign-in, 2FA entry, device selection, manual refresh, and — given how
much of this session was spent on silent failures — visible health, i.e. is the server advertising,
is the anisette server reachable, when did the last successful refresh happen, and when do the
current certificates expire.

9. ~~**Accept the Apple ID password from somewhere other than argv.**~~ **DONE** — `dcac3de`. `-p` puts the password in
   `ps` output for the life of the process and in shell history. An `ALTSERVER_APPLE_PASSWORD`
   env var, or reading from stdin when `-p` is absent, would fix it. Small, and it matters more
   once this runs unattended, where the password has to live somewhere anyway.

### Explicitly not doing

- **PR #98 (CMake rewrite).** Author wrote "doesn't 100% work" in 2023 and never returned; keyed
  to a 2023 upstream while we are pinned at 2022. Would remove the one build path known to work.
- **PR #57 (macOS).** Replaces the idempotent out-of-tree rewriters with in-place patching,
  architecturally incompatible with submodule syncing.
- **AltJIT on iOS 17+ (#103).** Needs personalized DDI, TSS signing and a RemoteXPC tunnel, none
  of which exist in our 2021 libimobiledevice pin. Document the limit; point at pymobiledevice3.
- **Opening PRs for the ~28 non-issues.** -36607 is Apple refusing an abused shared anisette
  identity. #117 is netmuxd's log output. #125 is an AltStore error code this binary cannot emit
  (`ServerErrorCode` tops out at 101).

---

## Open questions

- Is the `release` job correct? It is tag-gated, so `download-artifact@v8` and
  `action-gh-release@v3` are still unexercised. Testing means pushing a tag, which publishes a
  real GitHub Release — and `action-gh-release@v3`'s `make_latest` has no default, so it may
  displace the current "latest".
- Which anisette server should we actually recommend? `nyamisty/alt_anisette_server` was last
  published April 2022 and is not verified against Apple's current flow.
