# AltServer-Linux revival log

Working log for the `bd/revival` branch of `Ben-Diehlci/altserver-linux`, a fork of
`NyaMisty/AltServer-Linux`. Upstream's last real code commit predates 2025 - everything since
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
  mode - hence the startup check.
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
| `upstream_repo/` (submodule) | `rileytestut/AltServer-Windows`, pinned at `071b1dd`, **2022-04-25** | What this fork actually compiles. Four years stale - see TODO. |
| `libraries/*` (submodules) | libimobiledevice family | All pinned 2020-2022. |
| `NyaMisty/AltServer-Linux` issues | 53 open, most long stale | Triaged 2026-09-13; ~28 of 53 are Apple-side, obsolete, or another project's bug. |

Cross-referencing AltStore has already paid off twice: it confirmed `NSISO8601DateFormatter`
with default options is the canonical `X-Apple-I-Client-Time` form
(`Dependencies/AltSign/AltSign/Apple API/ALTAppleAPI.m:47`), and that
`AltServer/Anisette Data/AnisetteDataManager.swift:165` still emits a client-info string
containing `com.apple.dt.Xcode/3594.4.19` as of v2.3.3 - consistent with the Apple GSA block
being a recent (~2026-09) change rather than a long-standing one.

---

## How to build

macOS cannot build this. A Colima VM is set up; the build tree lives **inside** the VM at
`~/altserver-linux` (ext4, case-sensitive) - *not* the macOS copy, which is case-insensitive
APFS and can mask CI-only include-casing errors.

```bash
colima start --vm-type=vz --vz-rosetta --cpu 8 --memory 12 --disk 60

colima ssh -- bash -lc 'cd ~/altserver-linux && docker run --rm \
  -v "$HOME/altserver-linux":/workdir -w /workdir \
  ghcr.io/ben-diehlci/altserver_builder_alpine_aarch64 \
  bash -c "mkdir -p build; cd build; make -f ../Makefile -j8"'
```

`aarch64` is native on Apple Silicon. `amd64` and `386` work via Rosetta/QEMU. **`arm/v7` is
not registered by default** and needs an extra binfmt handler. Changes do not sync between the
macOS and VM copies automatically - copy deliberately in both directions.

To exercise runtime behaviour without an iPhone, link a harness against the built objects,
excluding `AltServerMain.cpp.o` (it owns `main`) and stubbing `make_uuid()`,
`temporary_directory()` and `readFile()`.

---

## Done

| Commit | What |
|---|---|
| `df570bd` | **CI unbroken.** Dead `gautamkrishnar/keepalive-workflow@master` removed; five actions off retired node12/16; `::set-output` -> `$GITHUB_OUTPUT`; per-job `permissions`; `sync_upstream` boolean bug. |
| `6cd382a` | **node24 bump**, part 1: checkout v4->v7, setup-qemu v3->v4, login-action v3->v4, gh-release v2->v3. |
| `8494e36` | **node24 bump**, part 2: third-party uploader -> `actions/upload-artifact@v7`, download-artifact v4->v8, matrix restructured to carry an `arch` label. |
| `a266861` + `790973d` | **corecrypto: all 3 layers fixed - CLOSES #111.** Full `make; make install` exits 0. The buildenv image is rebuildable from source again. |
| `04dd7ee` | **Bootstrap blockers cleared:** PR #135 client-info sanitization (with an `ALTSERVER_NO_CLIENTINFO_SANITIZE` escape hatch), and the getopt `-a` fallthrough / uninitialised argv pointers / unreachable `-h`. |
| `0e8090b` | **#131 fixed** via the ldid rewriter: capture the SHA-256 CodeDirectory hash before truncating to 20 bytes. Build-verified; **not** verified on an iOS 26 device. |
| `04e928a` | **mDNS advertisement failure made loud.** Both failure paths verified; success path NOT verified locally - no working avahi in the build container. Must be confirmed on the real host. |
| `b885501` | **anisette error handling** rewritten; `mktime`->`timegm`; `ResetProvisioning` Windows-path bug. Closes #104. |
| `301eb8b` | **Daemon warns at startup** when no anisette server is configured, instead of looking healthy and failing at first use. Warns rather than exits: only `AnisetteDataRequest` needs anisette; AltJIT and profile requests do not. |
| `3cd30ac` | **Anisette as a Portainer stack** - and the volume path the upstream README gets wrong (`lib/` holds only the `.so` cache; the identity is one level up). |
| `118e9fb` | **Non-200 from Apple's auth endpoint reported** instead of surfacing as "invalid response". The status was logged then discarded, so a 429 became a plist parse failure. |
| `061af4c` | `--help` registered as a long option - it was documented and handled but missing from `long_options`, so it hit the error path. |
| `3f29bcb` | Pairing wizard detects an existing backup, and only counts one containing **both** plists. |
| `725677d` | README: removed instructions that were actively wrong (missing `cd build`, the stale `-mno-default` ARM warning, corecrypto steps for a distribution Apple no longer ships). |
| `eb053f1` | **GrandSlam 429 fixed.** `gsaClient()` returns a fresh client per call, so each request opens its own connection. Proven by probe, then confirmed by a real sign-in. |
| `38bddab` + `023755c` | **CI iteration cost cut.** `fail-fast: false`, and amd64-only on branch pushes with the full matrix on tags / schedule / `all_arches`. |
| `4f40fdd` | **Credentials from the environment** (`ALTSERVER_UDID` / `_APPLE_ID` / `_APPLE_PASSWORD`), so a detached container works and the password is not in `ps`. |
| `1fe89a3` | **Containerised.** Multi-stage `Dockerfile` bundling every runtime prerequisite and verifying `libdns_sd.so` loads at build time; `deploy/altserver-stack.yml`; `build_image.yml` publishing to the fork's OWN namespace. |
| `7f2032e` | **Named volumes** - no host `mkdir`, and no `chown`, since a fresh volume inherits the image path's ownership. |
| `215885a` | **AltStore IPA fetched automatically** on start, resolved from AltStore's own catalogue so it is always current. Idempotent, atomic, verifies a `Payload/*.app`, non-fatal on failure. |
| `5b4fe84` `af35345` `5e41eee` | **Setup web UI**, three slices: status dashboard, pairing wizard, and install with in-browser 2FA entry. |
| `663cfaf` | Web UI runs as a stack service on `:8099`; image gains the tools its checks shell out to. |
| `9baa62b` | **`ThreadingHTTPServer`** - the single-threaded server was serialising every page behind slow checks. Measured: a 10s request no longer blocks others. |
| `d7e3da5` | Install page auto-populates the UDID from `/api/pairing`. |
| `a79aab5` | Two status checks that reported nonsense: the process check was a PID-namespace false negative, and the clock check used `timedatectl`, which cannot work in a container. |
| `70b646e` | **mDNS from a container needs AppArmor rules.** Tested profile + installer; see below. |
| `f6e98de` | README rewritten for the current state, leading with an honest status table. |
| `501e40f` | **JS syntax error fixed** - a literal newline in a string literal was killing every page's script block. All three pages now `node --check`ed. |
| `f7f3130` | **`paths:` filter fixed** - `web/**` changes were not rebuilding the image, so five commits of fixes never shipped. |
| `503225f` | REVIVAL.md: both findings written up. |
| *(this)* | **Two CI guards added** (`tests/`), each verified to fail by reintroducing the original bug. |
| *(this)* | **netmuxd added to the image and the stack** - wireless device transport, the last missing piece for unattended refresh. |
| *(this)* | **Repo is pure ASCII.** 264 non-keyboard characters replaced; `check_ascii_punctuation.py` guards it. Two silent traps documented below. |
| *(this)* | **mDNS advertisement self-heals.** avahi restarted 09-22 and the advert never came back: three-day silent outage. The helper now browses for its own record and re-registers. Guarded by `check_mdns_watchdog.py` (8 cases, 8 mutants). |
| *(this)* | **Failures are visible to machines.** `/api/status` was 200 and `status_checks.py` exited 0 no matter what they found, so every monitor saw green through the outage. Now 503 and Nagios exit codes, guarded end-to-end by `check_status_signals.py`. |
| *(this)* | **Editing a rewriter rebuilds again.** Three of four rewriter rules were not prerequisites of their output and `clean` left the patched trees, so rewriter edits silently did nothing. Plus the clock check no longer reports OK for a comparison it never ran. |
| *(this)* | **Status history.** Each run is recorded, so the page shows a per-check timeline and "last not-ok 2h ago" instead of only a snapshot. Answers the question the 09-22 outage could not. |

### CONFIRMED 2026-09-14: the Apple GSA client-info block is real

Upstream PR #135 was an unverified third-party claim. It is now reproduced against real Apple
infrastructure, in both directions, on this deployment:

| `ALTSERVER_NO_CLIENTINFO_SANITIZE` | `X-MMe-Client-Info` sent | First GSA response |
|---|---|---|
| unset (sanitizer ON) | `...(com.apple.akd/3594.4.19)` | **200** |
| `=1` (sanitizer OFF) | `...(com.apple.dt.Xcode/3594.4.19)` | **503** |

So Apple's `gsa.apple.com/grandslam/GsService2` does reject any request carrying the substring
`com.apple.dt.Xcode`, and the `com.apple.akd` rewrite does get past it. **Keep the sanitizer on.**
Worth reporting back on PR #135 - the author's curl repro is independently confirmed.

This also cleanly separates two issues that looked like one. The remaining failure is a **429 on
the SECOND GSA request**, which is unaffected by the client-info value, reproduced identically 28
minutes apart, and present on the very first attempt ever made from this machine - so it is not
cumulative volume throttling.

**CAUSE IDENTIFIED (high confidence): HTTP connection reuse.** An earlier hypothesis recorded
here - that the one-time password was being replayed - was WRONG and is retracted. Upstream passes
a single anisette object to init, complete *and* apptokens
(`AltStore/Dependencies/AltSign/.../ALTAppleAPI+Authentication.swift`, same object at lines 69, 98
and 165), so one OTP per transaction is the designed and historically working behaviour.

The real mechanism, verified in this tree: `AppleAPI` is a process-wide singleton holding ONE
`_gsaClient`, built in its constructor (`AppleAPI.cpp:112-117`). `gsaClient()` (`:1013`) returns a
copy sharing the same cpprestsdk impl and therefore the same asio connection pool, and the second
GSA request is issued from a `.then()` continuation the instant the first completes - textbook
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
entire release note is *"Disabled reqwest pooling to alleviate http 429 from grandslam"* - on a
client that **already carried** the akd fix, which is why the akd rewrite *reveals* the 429 rather
than causing it; iloader #709 places the 429 at the proof/complete request across five Apple IDs
on five machines, so it is not account-scoped.

**PROVEN 2026-09-14, with zero Apple ID attempts.** `docs/gsa-connection-probe.sh` sends four
`o=init` requests (no password, nonexistent `.invalid` address) against the real endpoint:

| Run | Request | Status | `num_connects` |
|---|---|---|---|
| A - one curl, `--next` | 1 | `200` | 1 |
| A - one curl, `--next` | 2 | **`429`** | **0** <- socket reused |
| B - `Connection: close` | 1 | `200` | 1 |
| B - `Connection: close` | 2 | **`200`** | 1 <- fresh socket |

Byte-identical requests; the only variable is whether the socket was reused. This is **not volume
throttling** - waiting between attempts was never going to help, and the per-connection rule
explains every observation: positional, stable, non-cumulative, present on the first attempt ever.

**Fixed** in `makefiles/AltSign-build/rewrite_altsign_source.py`: `gsaClient()` returns a fresh
client per call. The fix matches the proven mechanism; still to be confirmed by an actual sign-in.

Two corrections recorded so they are not re-derived. The probe's first run returned `HTTP 000`
with "self-signed certificate in certificate chain", which looks like interception but is not:
`gsa.apple.com` is served from **`Apple Server Authentication CA`**, Apple's own private CA, which
no public trust store contains. DNS is clean (both resolvers answer inside Apple's `17.0.0.0/8`;
differing addresses are Akamai geo-routing). Consequently `set_validate_certificates(false)` on the
GSA client in `AppleAPI.cpp` is **required**, not careless - pinning Apple's CA would be an
improvement, but it is not the security hole it resembles.

Known remaining divergence, NOT the cause: our sanitizer replaces only the bundle-id substring, so
the wire value is `com.apple.akd/3594.4.19` - akd has never carried an Xcode build number.
Upstream PR #1790 replaces the whole token with `com.apple.akd/1.0`. Worth tightening separately;
it is identical in both requests so it cannot explain 200-then-429.

### CONFIRMED 2026-09-14: mDNS from a container is blocked by AppArmor, not by anything it looks like

The daemon ran, anisette was healthy, pairing was valid - and `_altserver._tcp` was never
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
ever consulted - the call never leaves the container.

Four hypotheses were wrong before the right one: TLS interception (the cert is genuinely Apple's,
from their private CA), a missing `/etc/machine-id` (mounting it changed nothing), socket
permissions, and D-Bus policy. **The kernel audit log named it exactly.** For anything
inexplicably denied inside a container, `sudo dmesg | grep -i 'apparmor.*DENIED'` is the first
move, not the last.

Fix, both shipped:

| | |
|---|---|
| `apparmor=unconfined` | The stack default - **only** because a container requesting a profile the host has not loaded FAILS TO START, which would break deploying straight from the repo |
| `deploy/apparmor/altserver-mdns` | `docker-default` verbatim plus the narrowest D-Bus rules Bonjour needs (bus handshake + `org.freedesktop.Avahi`, nothing else). Install with `deploy/apparmor/install.sh` |

Tested rather than argued: under `docker-default` the call fails `Access denied` with one AppArmor
denial in `dmesg`; under `altserver-mdns` it reaches the daemon with **zero** denials, and
`/proc/self/attr/current` reads `altserver-mdns (enforce)`.

**This step cannot be automated into the deployment.** AppArmor profiles load into the host kernel
as root, and `security_opt` only *selects* an already-loaded profile. That is a property of
AppArmor, not a packaging gap - the only alternative would be a privileged init container mounting
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
   Note `:latest` does not re-pull on its own - a Portainer stack update needs **Re-pull image**
   ticked, so a deploy can silently reuse a months-old layer.

**`tests/check_workflow_paths.py` now enforces rule 1**: it cross-checks every `COPY` in the
Dockerfile against the `paths:` filter and fails if something entering the image would not
rebuild it. It runs from `build.yml`, not `build_image.yml` - a guard living inside the filtered
workflow would be skipped by precisely the bug it detects.

### CONFIRMED 2026-09-14: the stale-image bug had a second half -- `libraries/**` was never listed

The `paths:` filter fixed above listed `src/**`, `shims/**`, `makefiles/**`, `Makefile`,
`upstream_repo` and `web/**` -- but **not `libraries/**`**. `Dockerfile:33` runs
`make -f ../Makefile`, and `makefiles/main.mak:9` points `LIB_DIR` straight at `libraries/`, so
the four vendored submodules (libimobiledevice, libusbmuxd, libplist, libimobiledevice-glue)
plus the plain sources in `libraries/dnssd_loader` are compiled **into the `-static` binary**.

(This originally said *five*, counting `ideviceinstaller`. It was never compiled -- it is a
standalone CLI with its own `main()`, unlinkable into AltServer even deliberately -- and the audit
below removed the submodule. The conclusion stands regardless: four of them ARE compiled, so
`libraries/**` still has to be in the filter.)

So bumping a submodule -- the single most likely reason to touch that tree, and exactly what the
netmuxd/`libimobiledevice` compatibility work above leads to -- would have changed the shipped
binary while publishing no new image. Same silent failure as the `web/**` omission, one level
down, and worse: a stale *binary* cannot be spotted by grepping a file inside the container the
way a stale `server.py` can.

Found by reading, not by being bitten. Nothing had bumped a submodule since the filter was
written, which is the only reason this had not already cost a debugging session.

**The check was blind to it, and blind in an instructive way.** `check_workflow_paths.py` cross-
referenced the Dockerfile's `COPY` lines against the filter -- correct for things *copied* in, but
the compile inputs were a **hardcoded tuple**, `("src", "shims", "makefiles", "Makefile",
"upstream_repo")`. That list was written when the filter was, from the same incomplete mental
model, so it agreed with the bug and printed green. Fourth instance this session of **a check
sharing its blind spot with the thing it checks** (after the anisette fields, `idevice_id`, and
`yaml.safe_load`).

The fix is therefore not "add `libraries` to the tuple" -- that just reloads the same gun. The
check now **derives** the compile inputs from the makefiles: it resolves `$(MAIN_DIR)`,
`$(LIB_DIR)`, `$(UPSTREAM_DIR)`, `$(SHIM_DIR)` and friends to repo-relative paths and requires the
filter to cover every top-level directory the build actually reaches into. A newly vendored tree
is caught the moment a makefile references it, with no list to remember.

Two make details the resolver has to respect, both of which produced wrong answers first:

- **`ROOT_DIR` is per-file.** It is `$(dir $(abspath $(lastword $(MAKEFILE_LIST))))`, so every
  sub-makefile redefines it to *its own* directory. Treating it as the repo root put
  `makefiles/AltSign-build/rewrite_altsign_source.py` at the repo root and reported four
  nonexistent top-level paths.
- **Commented-out lines still mention variables.** `Makefile:50` is a dead
  `#libimobiledevice_include := -I$(LIB_DIR)/...`, and citing it as the reason `libraries` is a
  build input would have sent the next reader to dead code. Comments are blanked, not dropped, so
  reported line numbers stay real.

Verified in both directions before committing: deleting `- 'libraries/**'` from the filter fails
the check (exit 1) naming `libraries` and citing `makefiles/main.mak:9`; adding a fresh
`VENDOR_DIR := $(MAIN_DIR)/vendor` to a makefile fails it naming `vendor`, with the filter
otherwise complete.

### CONFIRMED 2026-09-14: one bad JS escape broke every page

The install page did nothing: the form would not submit, no log appeared, the state stayed on its
placeholder, and the UDID never filled in. The backend was provably fine the whole time - `curl`
against `/api/install/start` returned correct validation errors, and no exception ever reached the
container log.

An escaped newline had collapsed into a **literal newline inside a JavaScript string literal**
when the page was generated, which is a syntax error and kills the entire `<script>` block. Every
symptom followed from that one line, which is why it presented as several unrelated faults.

Now built with `String.fromCharCode(10)`, which nothing between Python and the browser can mangle.
`installer.py` had been well tested against a mock; the thing the browser actually runs had never
been parsed by anything. **`tests/check_page_js.py` now extracts every `<script>` block from all
three pages and runs `node --check` on it** - verified to fail by reintroducing this exact bug.

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
| Phone advertising | `_apple-mobdev2._tcp` -> `<device-name>.local`, TXT `authTag` + `identifier` |
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

### CONFIRMED 2026-09-14: the runtime base must be trixie, not bookworm

Adding netmuxd failed CI immediately:

```
/usr/local/bin/netmuxd: /lib/x86_64-linux-gnu/libc.so.6: version `GLIBC_2.38' not found
```

netmuxd's release binaries are dynamically linked and need glibc >= 2.38. bookworm ships 2.36;
trixie ships 2.41. **The build-time `netmuxd --about` check is what caught this** -- without it the
image would have published successfully and netmuxd would have crash-looped in the stack, which
reads as "wireless is broken" rather than "the binary cannot start". Same value as the
`libdns_sd.so` load check, and the same reason to keep both.

Bumping the base is safe here because **AltServer is linked `-static`**: the runtime image's libc
is irrelevant to it. Only python3, libimobiledevice-utils, avahi-utils and netmuxd are affected.

Verified locally on trixie BEFORE pushing, rather than push-and-see -- both packages and binary:

```
PASS: libdns_sd.so loads
ldd (Debian GLIBC 2.41-12+deb13u3) 2.41
netmuxd v0.4.2 - a network multiplexer          <- v0.4.3 tag, unbumped version string. Cosmetic.
```

### CONFIRMED 2026-09-14: netmuxd v0.4.3 is INCOMPATIBLE with our pinned libimobiledevice

Verified in both trees, not inferred. netmuxd v0.4.3 `src/devices.rs:45` picks its address layout:

```rust
let bsd_sockaddr = cfg!(any(target_os = "macos", target_os = "ios", target_os = "freebsd", ...));
IpAddr::V4(ip) => { if bsd_sockaddr { data[0]=0x10; data[1]=0x02; }   // sa_len, AF_INET
                    else            { data[0]=0x02; data[1]=0x00; } } // Linux: family as u16 LE
```

On Linux it writes a **native** sockaddr. Our vendored libimobiledevice is pinned at
`c6f89dea` (2021-12-09) and expects the **BSD/Apple** layout:

* `libraries/libimobiledevice/src/idevice.c:287` -- `addrlen = conn_data[0]`, i.e. byte 0 is a
  BSD `sa_len`. With `0x02` there it mallocs **2 bytes** and truncates the address.
* `idevice.c:483` -- tests `conn_data[1]` against `0x02` / `0x1E`. `0x1E` is 30 = `AF_INET6`
  **on BSD**; Linux uses 10. The code's own comment says `(bsd)` and carries a
  `FIXME: Improve handling of this platform/host dependent connection data`.
* With `conn_data[1] == 0x00` it falls through to `idevice.c:497` -> `IDEVICE_E_UNKNOWN_ERROR`.

Upstream fixed this in `a172604e5af` and `806ab8d37cf` (both 2023-06). Neither is an ancestor of
our pin (`git merge-base --is-ancestor` returns false for both).

**The failure shape is the worst available.** `libraries/libusbmuxd/src/libusbmuxd.c:309` rejects a
network device only when `conn_data[0]` is ZERO -- `0x02` passes -- so **the device enumerates
normally**. `idevice_new_with_options` succeeds and `DeviceNotFound` is never raised; the failure
lands one line later at `DeviceManager.cpp:763-765` as `ServerErrorCode::ConnectionFailed`.

netmuxd **v0.1.4** wrote `data[0]=10; data[1]=0x02` unconditionally, which this parser accepts.
So the old README note ("netmuxd >= 0.3") was wrong for THIS build, in the opposite direction from
what we assumed.

Two fixes, MUTUALLY EXCLUSIVE -- a bumped libimobiledevice rejects v0.1.4's layout and vice versa:

1. Bump `libraries/libimobiledevice` past `806ab8d37cf` (plus libusbmuxd / libimobiledevice-glue,
   and re-derive `makefiles/libimobiledevice-build/config.h`). Keeps v0.4.3 and its iOS 26.4+ TXT
   matching. Substantial.
2. Patch the vendored parser to accept both layouts -- must fix BOTH sites, since `addrlen` at
   :287 truncates before :483 is ever reached. Surgical.

Pinning netmuxd to v0.1.4 is a DIAGNOSTIC ONLY: it predates the heartbeat, the async pair-record
cache, and iOS 26.4+ TXT matching, on an iOS 26 phone.

### CONFIRMED 2026-09-14: `dir/**` does NOT match a submodule bump -- and the guard accepted it

Found while checking whether the new `libraries/**` filter entry actually fires. It does, but the
reasoning matters and the guard was one edit away from being wrong again.

**A submodule bump changes only the gitlink, so git reports the BARE path.** Verified against this
repo's own history -- `git show --name-only fa3abe2` prints exactly:

```
upstream_repo
```

not `upstream_repo/<anything>`. So for a filter pattern:

| Compile input | Bump reports | `X/**` matches? | Needs |
|---|---|---|---|
| `upstream_repo` (a submodule itself) | `upstream_repo` | **No** -- no `upstream_repo/` prefix | bare `upstream_repo` |
| `libraries` (a directory *containing* submodules) | `libraries/libimobiledevice` | **Yes** | `libraries/**` |

So `libraries/**` is correct, and `upstream_repo` is correctly bare. **No submodule bump is needed
to prove this** -- the path shape is already in the history, and bumping one purely to test CI
would change the shipped binary for no information.

**The guard did not know this.** `covered()` stripped `/**` and compared base names, so rewriting
the bare `upstream_repo` entry as `upstream_repo/**` -- which looks like a tidy-up, since every
other entry is a glob -- still printed `ok ... covered by 'upstream_repo/**'` and exited 0, while
the trigger would have silently stopped firing. Fixed: the guard now reads gitlinks from
`git ls-files -s` (mode 160000) and requires an exact match for any compile input that is itself a
submodule, while still allowing globs to cover submodules *nested* inside a covered directory.

Both directions mutation-tested: `upstream_repo` -> `upstream_repo/**` now fails naming the
submodule and the glob; dropping `libraries/**` still fails as before.

That is the third time in this session a check shared a blind spot with the thing it checks.

### FIXED AND CONFIRMED 2026-09-14: the sockaddr layout bug above -- wireless refresh now works, and there were THREE sites, not two

Option 2 from the section above, implemented. The bug is no longer inferred from reading two trees:
it was **observed in production** on this deployment, then fixed and the fix proven.

**Runtime confirmation.** `docker logs netmuxd` emitted, for the phone:

```
ConnectionType: "Network"
NetworkAddress: Data(02 00 00 00 C0 A8 08 2D ...)   Len: 128
```

`02 00` is `sin_family` as a little-endian `uint16` -- the **Linux** layout, exactly as predicted.
`00 00` is the port (zero; harmless, see below) and `C0 A8 08 2D` is 192.168.8.45. In the same
window AltServer logged a successful mDNS advertisement, accepted a 124932582-byte app upload from
AltStore, fetched anisette with HTTP 200, and then:

```
Unzipping .ipa...
Failed to handle request:There was an error connecting to the device.
```

Discovery, client connection and anisette all worked. Only the device connection failed. That is
the signature the section above predicted.

**CORRECTION to the section above: there are three affected sites, and it named the wrong one as
critical.** The old note said "must fix BOTH sites, since `addrlen` at :287 truncates before :483
is ever reached." That causal chain is wrong. `:287` lives in `idevice_get_device_list_extended`,
which AltServer does **not** use to open a connection. AltServer calls
`idevice_new_with_options` (`upstream_repo/AltServer/DeviceManager.cpp:757` and eight other call
sites), which is `idevice.c:401` and reaches the mux device through a **third** site:

| # | Site | Function | On AltServer's connect path? |
|---|---|---|---|
| 1 | `idevice.c:287` | `idevice_get_device_list_extended` | No -- enumeration only |
| 2 | `idevice.c:388` | `idevice_from_mux_device`, called from `idevice_new_with_options:416` | **Yes -- this is the one that bit** |
| 3 | `idevice.c:483` | `idevice_connect` family test | **Yes** |

Sites 1 and 2 are the same `conn_data[0]`-as-length bug in two different functions. Anyone
following the old note would have patched 1 and 3, left 2 alone, and still had `malloc(2)` with a
truncated address on the live path. All three are fixed.

**Severity was understated, too.** This is not only a functional bug. With byte 0 = `0x02` the
allocation is **2 bytes**, and `idevice_connect` then reads **14 or 26** bytes out of it -- a heap
over-read on every wireless connection attempt, not merely a failed one.

**The fix.**

| File | Role |
|---|---|
| `makefiles/libimobiledevice-build/rewrite_idevice_source.py` | NEW. Rewrites the three sites |
| `makefiles/libimobiledevice-build/libimobiledevice.mak` | Wires the rewriter in |
| `tests/check_conn_data_layout.py` | NEW. Regression guard, runs in CI |

It is a **build-time source rewriter**, not an edit to the submodule, because
`libraries/libimobiledevice` is a submodule: editing it in place could not be committed here --
only the submodule pointer would move, to a commit that does not exist upstream, breaking every
fresh clone. This is the same convention `rewrite_altserver_source.py`, `rewrite_altsign_source.py`
and `rewrite_ldid_source.py` already use.

Two things made the patch much smaller than expected:

* **Both layouts put the port at offset 2 and the address at offset 4.** Only the first two bytes
  differ. So the existing `memcpy(&saddr->sa_data[0], conn_data + 2, 14 /* or 26 */)` was already
  correct for both, and `sa_data` begins at offset 2 on Linux as well. Only the family test and the
  copy length needed to change.
* **Detection is unambiguous.** Byte 1 is the family on BSD (never 0 for AF_INET/AF_INET6) and the
  high half of a little-endian `uint16` family on Linux (always 0). No collision:

  | Layout | byte 0 | byte 1 |
  |---|---|---|
  | BSD AF_INET | `sa_len` | `0x02` |
  | BSD AF_INET6 | `sa_len` | `0x1E` |
  | Linux AF_INET | `0x02` | `0x00` |
  | Linux AF_INET6 | `0x0A` | `0x00` |

For the two length sites it now allocates and copies a **fixed 28 bytes** -- the largest form
`idevice_connect` reads back (`sockaddr_in6` plus scope id) -- instead of trusting byte 0. Safe
because the source, `usbmuxd_device_info_t::conn_data`, is a fixed `uint8_t[200]`
(`libraries/libusbmuxd/include/usbmuxd.h:55`). That removes the under-allocation class outright
rather than computing a size that could be wrong again.

**The zero port does not matter.** `socket_connect_addr(saddr, port)`
(`libraries/libimobiledevice-glue/src/socket.c`) overwrites `sin_port` / `sin6_port` from its own
`port` argument before connecting, so netmuxd's zero is never used.

**Verification done.**

* Compiles clean, no warnings, in the Alpine buildenv. `make -n` confirms the explicit rule beats
  the generic `objs/%.c.o` pattern rule: `idevice.c.o` builds from the rewritten copy, every other
  file still builds from the original tree untouched.
* The rewriter `sys.exit(1)`s with an explanatory message if any pattern stops matching, so a
  submodule bump fails the build loudly instead of silently shipping a binary without the fix.
  (Note: `rewrite_altserver_source.py` does **not** do this -- it has no `raise`/`assert`/
  `sys.exit` at all, so the README's claim that every rewriter "fails the build loudly" is true of
  three of the four, now including this one.)
* `tests/check_conn_data_layout.py` compiles the **shipped** macros -- extracted from the
  rewriter's output, never retyped -- against real captured bytes including netmuxd's own
  `02 00 00 00 C0 A8 08 2D`, and asserts the patched source is structurally correct (no raw
  `conn_data[0]` / `[1]` indexing survives outside the macro definitions).
* **Mutation-tested.** Seven deliberate regressions were introduced one at a time and all seven
  failed the test: BSD-only `IS_INET`, BSD-only `IS_INET6`, a family-collision, a moved anchor,
  byte-0-as-length restored as a `malloc` argument, only one of the two copy sites fixed, and a
  `memcpy` length reverted.
* Under `CI=1` the test **refuses to skip**: a missing submodule or missing compiler is a failure,
  not a green pass. An earlier draft would have skipped silently in the `guards` job, which checks
  out no submodules -- the exact "test that cannot fail" trap already recorded twice in this log.

**Three defects were found by adversarial review AFTER the patch looked finished, and all three
were reproduced before being fixed.** Recorded because two of them are traps that would recur:

1. **The new build rule raced against itself and broke the build ~1 run in 3.** The root
   `Makefile:24` declares `$(BUILD_DIR)/libimobiledevice.a $(BUILD_DIR)/libplist.a :` as a single
   **multi-target** rule. GNU make expands that into two independent targets each carrying the same
   recursive recipe, both are `.PHONY`, and both are reachable in parallel -- so
   `libimobiledevice.mak` runs **twice, concurrently**, on every build. Every shipping build is
   parallel (`-j3` in CI, `-j$(nproc)` in the Dockerfile). Two sub-makes writing one fixed
   `idevice.c.tmp` meant one renamed it away and the other's `mv` failed outright:

   ```
   mv: can't rename '.../patched/libimobiledevice/idevice.c.tmp': No such file or directory
   make: *** [.../idevice.c] Error 1
   ```

   Measured **2 failures in 6 runs**. Fixed with a per-process temp name (`$@.$$$$.tmp`).
   Now **0 failures in 12 runs**. The single clean `-j$(nproc)` build done before review was luck,
   not evidence -- a reminder that one green parallel build proves nothing about a race.

2. **The first attempt at that fix failed 100% of the time, for a different reason.** Make runs
   **each recipe line in its own shell**, so with the write on one line and the `mv` on the next,
   the two shells expanded `$$` to two different pids and the rename could never find its file.
   The write and the rename must stay on **one line joined by `&&`**. Worth remembering: the
   broken form fails always, which is at least loud -- it was the *first* version, with a shared
   temp, that failed intermittently and would have reached CI.

3. **The regression guard counted `ALTSERVER_CD_SIZE` but never checked its value.** Setting it to
   `2` -- byte for byte the original bug, since that is what the unpatched code computed from
   netmuxd's `02 00 ...` blob -- still passed green, and the test's own success message claimed
   "both copies bounded". `16` was subtler and more dangerous: IPv4 keeps working and only IPv6
   over-reads, so it would ship and fail intermittently. Fixed with a `_Static_assert` on the
   shipped constant plus a runtime case that copies through an `ALTSERVER_CD_SIZE` buffer. The
   mutation suite now stands at **nine mutants, all caught**.

The lesson from 3 is the one this log keeps relearning: *asserting that a token appears is not
asserting that the code is correct.* The first version of this guard checked for the literal
string `"conn_data)[0];"` -- with a trailing semicolon -- and a mutant that reintroduced the same
read as a `malloc()` argument, with no semicolon, sailed straight through.

**PROVEN END TO END on the deployed stack, 2026-09-14 (01:39Z).** A refresh triggered from
AltStore on the phone produced, in `docker logs altserver`:

```
Receiving 44900 bytes...
Removed profile: com.<team>.com.rileytestut.AltStore (<uuid>)
Removed profile: com.<team>.com.rileytestut.AltStore.AltWidget (<uuid>)
Installed profile: com.<team>.com.rileytestut.AltStore (<new uuid>)
Installed profile: com.<team>.com.rileytestut.AltStore.AltWidget (<new uuid>)
Finished handling request!
```

No `Failed to handle request:` anywhere in the run. Compare the pre-fix log, where a request of
almost exactly this shape (44887 bytes) died at
`Failed to handle request:There was an error connecting to the device.`

**Why those four lines are conclusive and not merely encouraging.** They are emitted by
`DeviceManager.cpp:985` / `:1029`, inside the function at `:757` that does, in order:

1. `idevice_new_with_options(..., IDEVICE_LOOKUP_NETWORK | IDEVICE_LOOKUP_USBMUX)` -- the entry
   point that reaches `idevice_from_mux_device`, patch site 2;
2. `lockdownd_client_new_with_handshake(...)` -- which requires a real TCP connection through
   `idevice_connect`, patch site 3;
3. `lockdownd_start_service(... "com.apple.misagent" ...)` and `misagent_client_new`.

None of that can run, let alone print, unless the network device connection was established. The
old-profile removal followed by new UUIDs is a genuine 7-day refresh cycle, not a no-op.

**The input bytes did not change -- only the parser did.** Checked at the same time, `docker logs
netmuxd` still reports the device exactly as before:

```
ConnectionType: "Network"
NetworkAddress: Data(02 00 00 00 C0 A8 08 2D 00 00 ... Len: 128)
```

Byte for byte the blob that used to fail. netmuxd was not upgraded, reconfigured or restarted into
a different code path, so "something else changed" is excluded: the same input now parses because
the parser was fixed. This is the controlled comparison the original diagnosis predicted, and it is
worth more than the success log on its own.

**A second risk closed as a side effect.** `lockdownd_client_new_with_handshake` succeeding over
the network means the pairing record is usable for a *network* device, not just a cabled one.
That had been flagged as the most likely next failure and is now empirically ruled out.

**Still not proven by this run**, and neither follows from it:

* **Unattended refresh.** This was triggered by hand from the phone. Whether iOS ever wakes
  AltStore on its own is the separate and much harder ceiling recorded below -- AltStore registers
  no `BGTaskScheduler` task and relies on the deprecated background-fetch API.
* **The full-install path.** The pre-fix failure also included a 124932582-byte app upload. That
  larger path was not re-exercised here. It uses the same `idevice_connect` network path, so there
  is no specific reason to expect trouble, but it has not been observed working.

Two traps from earlier in this log applied while testing, and both held:

* `docker exec altserver idevice_id -l` uses **Debian's** post-2023 libimobiledevice, not the
  vendored 2021 copy AltServer links. It parsed this address correctly all along. A green result
  there was never evidence about AltServer, before or after.
* The status page cannot see this either, for the same reason. The only thing that could confirm
  the fix was AltServer's own log, which is what was used.

### CONFIRMED 2026-09-14: mDNS advertising now works after the AppArmor redeploy

The `-65553` / `Bonjour Registration Error: -65537` was the **old** container running under
`docker-default`. The user's `dmesg` matched the prediction in `deploy/apparmor/altserver-mdns`
word for word:

```
apparmor="DENIED" operation="dbus_method_call" bus="system"
path="/org/freedesktop/DBus" member="Hello" label="docker-default"
```

After redeploying with `apparmor=unconfined`, the new container logs
`Advertising this server over mDNS as _altserver._tcp on port 51693`, and from another machine
`avahi-browse -rt _altserver._tcp` shows `altserver-host` on `ens18 IPv4 192.168.9.16` with
`txt = ["serverID=1234567"]`. AltStore found it and connected.

Remaining `dmesg` denials are from ad-hoc `docker run` / `docker exec` diagnostics, which do not
inherit the stack's `security_opt`. They are noise, not a regression.

### CONFIRMED 2026-09-15: the reachability check used a USB-only flag, so it could never pass

The status page showed **FAIL -- "No device on either transport"** while wireless refresh was
demonstrably working, profiles installing over Wi-Fi with no cable attached.

Not a timing artefact or a stale reading. `web/status_checks.py` probed the netmuxd socket with
`idevice_id -l`, and `-l` is not a verbosity switch -- it selects the transport. From
`libraries/libimobiledevice/tools/idevice_id.c`:

```c
case 'l': mode = MODE_LIST_DEVICES; include_usb = 1;     break;
case 'n': mode = MODE_LIST_DEVICES; include_network = 1; break;
...
} else if (argc == 0 && optind == 1) { include_usb = 1; include_network = 1; }
```

netmuxd only ever presents the phone as `ConnectionType: Network`, so `idevice_id -l` against its
socket returns an empty list **unconditionally**. The check was structurally incapable of
reporting OK for a wireless-only deployment -- which is the only deployment this project targets
and the exact thing the check exists to verify.

`idevicepair validate` on the success path had the same defect: `tools/idevicepair.c:372` selects
`(use_network) ? IDEVICE_LOOKUP_NETWORK : IDEVICE_LOOKUP_USBMUX`, so without `-n` it validates a
USB pairing that does not exist on a cable-free server and reports a stale pairing record. Both
would have had to be right for the check to ever go green; neither was.

Fixed: `-n` for the netmuxd probe, `-l` kept for the host usbmuxd probe, and `-n` added to
`idevicepair validate`. The transport flag is now a required argument of `_devices_via` rather
than a literal inside it, so the two probes cannot silently drift onto the same transport again.

**This also explains an earlier loose end.** `docker exec altserver idevice_id -l` printing nothing
was recorded as unexplained. Same cause: `-l`, USB only, against a network-only mux. It was never
evidence of anything.

Note the direction of the error. During the sockaddr bug this check said FAIL, and was read as
confirmation. It was right by accident, for a reason unrelated to the actual fault -- and it kept
saying FAIL after the fault was fixed. A check that cannot pass is not a strict check, it is a
broken one, and it is worth less than no check because it is trusted.

### CONFIRMED 2026-09-14: the status page CANNOT detect the bug above -- it reports green

`web/status_checks.py` shells out to `idevice_id` / `idevicepair`, which are **Debian's**
libimobiledevice-utils. `Dockerfile` now uses `debian:trixie-slim` (forced by netmuxd's glibc 2.38
requirement), and trixie ships a **post-2023-fix** libimobiledevice that parses v0.4.3's Linux
sockaddr correctly. AltServer links the **vendored 2021** copy.

So `docker exec altserver idevice_id -l` prints the UDID and `idevicepair validate` passes, while
every AltServer refresh fails. **A green wireless check is not evidence the refresh path works.**
This was handed to the user as "the whole ballgame" -- it was a test that could not fail. Same
class as the anisette check that compared four fields against empty files.

The zero-risk way to actually test it, no phone interaction and no rebuild: run the same query
under a PRE-fix libimobiledevice (bookworm) and a POST-fix one (trixie) against the live socket.

```bash
docker run --rm -v <stack>_muxd-socket:/run/muxd -e USBMUXD_SOCKET_ADDRESS=UNIX:/run/muxd/usbmuxd \
  debian:bookworm-slim sh -c 'apt-get -qq update && apt-get -qq install -y libimobiledevice-utils \
  >/dev/null && dpkg -l | grep libimobiledevice && idevice_id -n && ideviceinfo -n -k ProductVersion'
```

bookworm failing where trixie succeeds confirms it end to end. bookworm SUCCEEDING puts the whole
diagnosis in doubt and the rebuild should not start.

### CONFIRMED 2026-09-14: the real ceiling on unattended refresh is iOS, not the server

**AltStore registers no `BGTaskScheduler` task anywhere** (grep over `AltStore/`, `AltStoreCore/`,
`AltWidget/`, `Shared/` finds nothing). Its only unattended wake is the **deprecated**
background-fetch API: `AppDelegate.swift:272` calls `setMinimumBackgroundFetchInterval(1*60*60)`
via a shim in `Types/DeprecatedAPIs.swift:19-22`, entered at
`application(_:performFetchWithCompletionHandler:)` (`AppDelegate.swift:297`). The push path is
compiled out of release builds -- `registerForRemoteNotifications()` sits inside `#if DEBUG` at
`AppDelegate.swift:277-279`.

iOS schedules legacy background fetch **opportunistically from usage heuristics**, and AltStore's
own UI admits it: *"The more you open AltStore, the more chances it's given to refresh apps in the
background"* (`AppDelegate.swift:306`).

So the goal is achievable without a Mac or PC, but **not with a phone nobody touches**. Every
server-side fix can be perfect and apps still expire.

**The deterministic fix is on the phone**: a Shortcuts Personal Automation on a daily time trigger
running the "Refresh All Apps" App Shortcut, exposed with no setup at
`Intents/App Intents/AppShortcuts.swift:16-25`. It is strictly better than background fetch here
because it sets `ignoresServerNotFoundError = false` (`RefreshAllAppsIntent.swift:187`) so a
discovery failure SURFACES, where background fetch sets it true and goes silent
(`BackgroundRefreshAppsOperation.swift:60`). Caveat to test on hardware: the intent is
iOS 17+, is a `ForegroundContinuableIntent`, and has a ~27s budget
(`RefreshAllAppsIntent.swift:97`) after which it requests foreground continuation.

**Cheapest triage in the whole project, and it needs no server access**: on the phone,
**AltStore -> Settings -> Refresh Attempts**. Rows are written for every attempt that reached
`finish()`, independent of notifications (`BackgroundRefreshAppsOperation.swift:270-274`). Empty or
sparse => iOS is not waking AltStore. "AltServer could not be found" => discovery. A connection
error => the sockaddr bug above. Two-factor => the 2FA blocker.

### CORRECTION 2026-09-14: the Apple ID password advice was incomplete

This log previously recorded "change the Apple ID password" as an outstanding action, after live
year-long bearer tokens appeared in pasted install logs. The security reason stands, but the
operational consequence was never stated and it is severe:

`AuthenticationOperation.swift:362-364` -- on `incorrectCredentials` or
`appSpecificPasswordRequired` AltStore calls `authenticate()`, which needs to present a view
controller. In a background refresh there is none, so it returns
`OperationError.notAuthenticated`. **A password change permanently kills unattended refresh until
a human opens AltStore and re-enters credentials**, with no notification, because it arrives during
a background refresh.

There is also a split-brain hazard: the phone keeps its own copy in its keychain
(`AltStoreCore/Components/Keychain.swift:70-71`) while the server has
`ALTSERVER_APPLE_PASSWORD` in the stack env. Updating only the stack leaves a server that can still
install and a phone that can no longer refresh.

**Operational rule: if the password is rotated, re-enter it in AltStore ON THE PHONE in the same
sitting -- not just in Portainer.** Do not enable app-specific-password enforcement on this account.

### CONFIRMED 2026-09-14: a cable would remove six layers from the critical path

`FindServerOperation.swift:74-80` prefers a USB-connected AltServer over any wireless one, checked
before the `isPreferred` branch at :81, and the wired lookup uses `IDEVICE_LOOKUP_USBMUX` alone
(`DeviceManager.cpp:1510`, `:1626`). A permanently attached cable therefore removes netmuxd, the
sockaddr bug, mDNS, avahi, D-Bus and AppArmor from the refresh path in one move -- and keeps
usbmuxd running, which also removes the `/var/run/usbmuxd` bind-mount hazard. Worth deciding BEFORE
committing to a substantial libimobiledevice bump. The server is a VM, so this needs USB passthrough.

### RISK 2026-09-14: re-running the one-shot install can revoke the live certificate

Upstream gates the revoke behind a modal at `AltServerApp.cpp:885-896`; **that prompt is compiled
out on Linux**, so the revoke proceeds unattended. The cached p12 early-return at
`AltServerApp.cpp:858-867` protects only if the file both exists AND loads. Do not re-run the
install flow casually now that AltStore works, and never run a second signing agent (a Mac/Windows
AltServer, Sideloadly, Xcode) against this Apple ID -- each side re-revokes the other's certificate.

### CONFIRMED 2026-09-14: yaml.safe_load does NOT validate a compose file

A duplicate `depends_on:` key was added to the altserver service and shipped. The local check was
`yaml.safe_load`, which **silently keeps the last duplicate key** rather than erroring -- so
validation printed a correct-looking `['anisette', 'netmuxd']` from a file Docker Compose refuses:

```
failed to parse deploy/altserver-stack.yml:
line 231: mapping key "depends_on" already defined at line 137
```

It surfaced at deploy time in Portainer, after a pull -- the slowest place to find it.
`tests/check_compose.py` now loads every compose file with a SafeLoader subclass whose mapping
constructor raises on a repeated key, and additionally checks that every named volume a service
mounts is declared and every `depends_on` target exists. Verified by reintroducing the exact
duplicate.

Third time this session that a check was weaker than the thing it claimed to verify (after the
anisette fields compared against empty files, and `idevice_id` run against the wrong
libimobiledevice). The pattern is the same each time: **the check and the real consumer were not
the same code path.**

### Other things learned the hard way

- **`gsa.apple.com` is served from `Apple Server Authentication CA`**, Apple's own private CA,
  which no public trust store contains. `curl` rejects it as self-signed. So
  `set_validate_certificates(false)` in `AppleAPI.cpp` is **required**, not careless - pinning
  Apple's CA would be better, but validation cannot succeed as things stand.
- **A verification that cannot fail is worse than none.** The anisette persistence check reported
  four fields "stable" when the server was returning nothing, because `jq -r` on an empty file
  prints an empty string and exits 0. Same family as `DNSServiceRegister` returning success
  unconditionally, and the CI `chmod +x` that was a no-op.
- **`HTTPServer` is single-threaded.** Three pages polling at 30s/5s/1.5s serialised behind checks
  that take up to 15s, which presented as the UI freezing when switching pages.
- **Containers have separate PID namespaces**, so a sidecar's `pgrep` cannot see the daemon -
  a confident false negative, fixed with `pid: host` plus a check that knows when it is blind.
- **`timedatectl` cannot work in a container.** Drift against the *anisette server's* timestamp is
  both measurable there and the thing that actually breaks sign-in.

### Verified facts worth not re-deriving

- **The CI failure was an unresolvable action, not the node12 versions.** `uses:` resolution
  happens in "Set up job", before any script runs. `gautamkrishnar/keepalive-workflow` has no
  `action.yml` at any ref. Because `build`/`release`/`update_submodule` gate on `check` via
  `needs:`, the whole workflow skipped - which is what #121 is really reporting.
- **`@actions/artifact` has exactly one backend boundary**: toolkit 2.0.0, the v3->v4 service
  migration. The download path has no format-version gating. But the old uploader served blobs
  with Content-Type `zip`, which `download-artifact@v8` does *not* recognise as a zip - so the
  uploader and downloader had to move together or raw `.zip` files would land in releases.
- **`upload-artifact` rejects `/ \ : < > | * ? "` in artifact names**, which is why the matrix
  grew an `arch` label instead of reusing `matrix.builder`.
- **Artifact upload does not preserve the executable bit** - so `chmod +x` in the build step is
  a no-op. Likely the root of #126, unfixable in CI alone: raw GitHub Release assets never carry
  the bit. Needs docs or tarball packaging.
- **The four `ghcr.io/nyamisty/altserver_builder_alpine_*` images are alive and public.** The
  build depended entirely on them, and at the time nobody could rebuild them (see #111).
  **Both halves are now resolved:** #111 is closed, and on 2026-09-15 a full set was built
  from this repo's own `buildenv/Dockerfile` and published under `ghcr.io/ben-diehlci/`.
  Every consumer now points there, so the fork no longer depends on another account's
  packages staying alive.
- **Apple versioned the corecrypto archive**: it now extracts to `corecrypto-2024/`. Docker's
  `WORKDIR` silently *creates* a missing directory, which is why the error surfaced one line
  later as a confusing "no CMakeLists.txt".
- **The build rewrites Windows source at compile time.** `makefiles/rewrite_altsign_source.py`
  contains `content.replace(b'winsock2.h', b'WinSock2.h')` - that is how lowercase Windows
  includes resolve against capitalised shim filenames on case-sensitive Linux.
- **`-mno-default` is already guarded to i386/i686.** The README's "remove it for ARM" note is
  stale; ARM builds work unmodified.
- **`SPOOF_MAC` is never defined**, so the `#else` provisioning block in `AnisetteDataManager`
  is dead code.
- **Only `AnisetteDataRequest` needs an anisette server.** The daemon's other five request types -
  PrepareApp, Install/RemoveProvisioningProfiles, RemoveApp, EnableUnsignedCodeExecution
  (AltJIT) - work without one. This is why the startup check warns instead of exiting.
- **Errors reach the phone, not just the log.** `ClientConnection::ErrorResponse` dynamic_casts
  to `ServerError` and forwards `userInfo`, so a `ServerError` with `NSLocalizedFailure` set is
  displayed in AltStore.

---

## Deployment research findings (2026-09-14)

**Scope: one iPhone, iOS 26.x. No iPad, no second device, no other systems.** So multi-device
concerns, `activeProfiles` juggling and device-slot exhaustion (#113, #86) are all out of scope.

### Host prerequisites - concrete checklist

- **`python3` on the service's PATH.** A *runtime* dependency, not a build one:
  `dnssd_loader.cpp:68` `execlp`s it, because the AltServer binary is `-static` and cannot dlopen
  Bonjour itself. Needs only stdlib `ctypes`.
- **`libavahi-compat-libdnssd-dev`**, not `...-libdnssd1`. Confirmed on the target host.
- **`avahi-daemon` running, with dbus under it.** avahi-compat is a thin client proxying to the
  daemon; it owns UDP/5353, not us.
- **`/etc/avahi/avahi-daemon.conf`**: `[publish] disable-publishing=no`,
  `disable-user-service-publishing=no`, and `allow-interfaces=ens18` so avahi does not also
  publish `docker0`/`virbr0` - `DNSServiceRegister` is called with `interfaceIndex 0`
  (`ConnectionManager.cpp:122`). Consider `use-ipv6=no`: `ConnectionManager.cpp:140,152` binds
  AF_INET only, while upstream macOS uses a dual-stack listener.
- **`avahi-utils`** for `avahi-browse` - the only way to distinguish *published* from
  *DNSServiceRegister returned 0*.
- **`usbmuxd` + `libimobiledevice-utils`** for the one-time cabled pairing and for triaging
  netmuxd without involving AltServer.
- **`netmuxd` >= 0.3 owning `/var/run/usbmuxd`, with `usbmuxd` STOPPED.** Stock usbmuxd never
  emits ConnectionType "Network", and netmuxd binds that socket by default, so the two collide.
  The only success report in #77 is: netmuxd, no flags, usbmuxd not running. If pointing at TCP
  instead, the variable is `USBMUXD_SOCKET_ADDRESS` - the widely-copied instruction in #49
  misspells it `USBMUXD_SOCKET_ADRESS` (one D) and is silently ignored.
- **`/var/lib/lockdown` on real persistent storage, never tmpfs.** Back up `<UDID>.plist` **and**
  `SystemConfiguration.plist` as a unit - they are not independent, and half a pairing is
  indistinguishable from none. In Docker this lives in whichever container runs the muxer.
- **Anisette on the same box, loopback, plain HTTP**, ADI state on a named volume. Plain
  `http://127.0.0.1:6969` also sidesteps TLS trust entirely: `FetchAnisetteData` uses the default
  http_client config, so certificate verification is ON (unlike AltSign's gsa client).
- **Accurate NTP on whichever host runs the ANISETTE server**, not the AltServer host. Linux
  forwards the anisette server's `X-Apple-I-Client-Time` verbatim; macOS stamps `Date()` locally.
- **Absolute `WorkingDirectory`** (systemd) or workdir (docker) - `./AltServerData` is relative
  and systemd defaults CWD to `/`.
- **Disable journald rate limiting** for the unit (`LogRateLimitIntervalSec=0`,
  `LogRateLimitBurst=0`). `WirelessConnection.cpp:95,122` print two unbuffered lines per <=4096-byte
  chunk, so a large transfer trips the 10000-per-30s default - and the suppressed messages are the
  ones at the END of an install, exactly the errors you want.
- **Firewall: the whole ephemeral TCP range on the LAN interface, plus UDP/5353 both ways.**
  `ConnectionManager.cpp:151` sets `sin_port = 0`, so the port differs every start and no static
  rule is writable.
- **Docker only:** `network_mode: host`, a bind mount of the dbus system bus socket (or
  avahi-daemon inside the image), and **`init: true`** - the binary installs no SIGTERM handler,
  so as PID 1 the kernel drops `docker stop`'s SIGTERM and every restart costs the full grace
  period then SIGKILL.
- **`chmod +x` the downloaded release binary** - artifact upload does not preserve the bit.
  systemd at least fails legibly here: `status=203/EXEC`.

### Silent failure modes - the real enemy for unattended operation

Ranked. These are the ways it stops refreshing with nobody finding out.

1. **Both ends go quiet at once.** Covered above: the phone suppresses server-not-found on
   background refresh, and the server logs nothing because no connection was attempted. Zero
   evidence anywhere. This is why the Shortcuts intent path
   (`RefreshAllAppsIntent.swift:187`, `ignoresServerNotFoundError = false`) is a requirement.
2. **`DNSServiceRegister result: 0` is not proof of publication.** A user in `closed_issue_0051`
   got result 0 with avahi-daemon *stopped*. **The `AVAHI_CLIENT_NO_FAIL` explanation previously
   given here was wrong** - avahi-compat calls `avahi_client_new` with flags `0`, not
   `AVAHI_CLIENT_NO_FAIL`. The real reason is narrower and worse: a 0 return means only that the
   daemon ACCEPTED the commit, never that the name survived probing. `04e928a` could not close
   this - it catches a child that exits and a non-zero result, but not avahi lying about success.
   **The watchdog added 2026-09-25 does close it**, within two intervals, because it browses for
   the record instead of trusting the return value.
3. **avahi restarts and nothing re-registers.** **THIS HAPPENED. 2026-09-22, three-day outage -
   see the dated section below.** It was written down here as a prediction before it occurred,
   which is the argument for writing these down. `StartAdvertising` is called exactly once
   (`ConnectionManager.cpp:174`). No retry, no health check, and nothing calls
   `DNSServiceProcessResult`, so the registration callback never fires either way. **FIXED
   2026-09-25** by the watchdog in `dnssd_loader.cpp`, guarded by
   `tests/check_mdns_watchdog.py`.
4. **A stale python3 child can advertise a dead port.** `PR_SET_PDEATHSIG` fires when the
   *forking thread* exits, not the process, and the fork happens on the listening thread. The
   child can survive holding a registration for an ephemeral port that no longer exists; since
   `flags = 0` (no `NoAutoRename`), avahi renames rather than replaces, and the phone takes
   `discoveredServers.first` - a coin flip between live and dead.
5. **A dropped client pins a worker at 100% CPU and floods the disk.** `ReceiveData` ignores
   `recv()`'s return (`WirelessConnection.cpp:116`); on peer close it returns 0 forever while
   select still reports readable, so the loop spins printing two lines per pass. Enough of these
   exhaust cpprest's ~40-thread pool and the daemon stops answering while still reporting
   `active (running)`. Never filed - it presents as "the server stopped refreshing".
6. **A response that failed to send is logged as success.** `SendData` never checks `send()`'s
   return while SIGPIPE is ignored, and its break condition is true on the first iteration
   regardless. Journal says "Finished handling request!"; the device saw a timeout.
7. **Anisette identity silently regenerates.** Every identity field comes from the HTTP response;
   Linux holds no local ADI state. A container recreated onto the wrong volume path means Apple
   sees a new machine and demands 2FA - which background refresh can never surface. #86 reports
   exactly this.
8. **Anisette clock drift is invisible here.** We parse and re-emit the *server's* timestamp, so
   NTP on the AltServer box proves nothing; skew surfaces as an opaque -36607.
9. **`X-Apple-I-MD-RINFO` uses `std::atoi`**, which returns 0 for non-numeric input with no error -
   the one field of the ten not guarded by `requireString`. Pre-existing; unchanged by our work.
10. **Real device faults are displayed as "AltServer could not be found".** AltStore remaps
    deviceNotFound/lostConnection to serverNotFound for any wireless server that is not
    `isPreferred`, and AltServer-Linux hardcodes serverID `"1234567"` while Mac/Windows use a
    UUID - so unless the Linux box itself installed AltStore, `isPreferred` is permanently false
    and every netmuxd/pairing fault wears the wrong error message. **This will send you to debug
    mDNS when mDNS is fine.**
11. **Adding `-d` makes the decisive lines disappear.** `AltServerMain.cpp:179` calls
    `libusbmuxd_set_debug_level(debugLogLevel - 2)`, so one `-d` sets level -1 and
    `LIBUSBMUXD_ERROR` stops printing - losing exactly the two messages that diagnose a netmuxd
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

## Credential sweep, 2026-09-15

**Tooling:** [`deploy/credential-hygiene.sh`](../deploy/credential-hygiene.sh) audits every
location below and can clear the safe ones. Report-only by default; `--clean-logs` truncates
container logs, `--clean-strays` removes leftover copies. It never prints a secret value, and no
flag touches working state.

### Detail
 -- where the operator's data actually lives

The repo is clean and its history was rewritten (see the commits above). This section records the
OTHER half, which matters more: credentials do not live in the repo, they live on the server. The
sweep that produced this was cut short by a session limit, so the 14 items below were found but
NOT independently verified -- except the four marked (verified), which were checked directly
against the source.

**Not leaks -- working state. Deleting these breaks unattended refresh.** Remove them only when
decommissioning, or if the Apple ID is believed compromised.

| What | Where | Consequence if removed |
|---|---|---|
| Apple ID password (verified) | container env + Portainer's saved stack definition. `deploy/altserver-stack.yml:197-199` uses `${VAR:-}` interpolation, so the REPO holds no value | re-enter to install again |
| Signing cert + private key (verified) | `<team-id>.p12` under `AltServerData/Certificates`, `AltServerApp.cpp:1969`, in the `altserver-data` volume | forces re-sign-in; revokes nothing by itself |
| Apple machine identity (verified) | `anisette-config` volume at `/home/Alcoholic/.config/anisette-v3` | re-provisions; can trip Apple rate limits |
| iPhone pairing record | `/var/lib/lockdown` on the host | **breaks wireless refresh** -- needs USB re-pair |

**Stray copies, safe to delete.** None of these are needed by the running stack:

- a browser-saved password for `http://<host>:8099` -- the install form is plain HTTP
- `~/.bash_history` / `~/.zsh_history` lines from any bare-metal `AltServer -p "<password>"` run
- `~/AltServerData/` left by a bare-metal BOOTSTRAP Phase 4 install (a second copy of the p12)
- `~/anisette-state.tgz`, `~/lockdown-backup*.tgz` if those backups were ever taken
- UUID-named leftovers in a container's `/tmp` from an interrupted install (hold `ALTCertificate.p12`)

**Logs, safe to truncate, no state lost.** A sign-in prints the full Apple account record and
bearer tokens including a one-year `com.apple.gs.icloud.auth` (verified:
`AppleAPI+Authentication.cpp:522` `odslog("Data: " ...)`), and every refresh prints the anisette
identity (verified: `src/AnisetteDataManager.cpp:191`). Both reach `docker logs`, which the
json-file driver persists under `/var/lib/docker/containers/<id>/`. `web/installer.py` redacts what
reaches the BROWSER; it does not touch what reaches `docker logs`.

**One real exposure worth fixing:** `altserver-web` binds `0.0.0.0:8099`, and the status page
publishes the device UDID and anisette Device-Id to anyone on the LAN. Bind `127.0.0.1` and use an
SSH tunnel -- already the documented recommendation for the password form, and it applies here too.

### CONFIRMED 2026-09-15: the buildenv workflow could never have worked in a fork

`buildenv/build_docker.sh` hardcoded `ghcr.io/nyamisty/...` as the push target, so in this fork
every `docker push` was denied -- a fork's `GITHUB_TOKEN` cannot write to another account's
packages. The job spent several minutes building four architectures under QEMU and then failed at
the first push, every time, for anyone who ran it.

It now derives the namespace from `GITHUB_REPOSITORY_OWNER`, which Actions supplies, overridable
with `GHCR_NAMESPACE`, and lowercased because GHCR requires that (`Ben-Diehlci` -> `ben-diehlci`).
It also gained `set -euo pipefail`; without it a failed build fell through to the push, and a
failed push fell through to the next architecture.

**At the time this only changed where images were PUBLISHED**, and every consumer still pulled
`ghcr.io/nyamisty/altserver_builder_alpine_*` -- repointing before a replacement set existed would
have broken the build outright. The workflow then ran successfully (about three hours; three of
the four architectures build under QEMU), all four images published public, and a build against
the new aarch64 image produced a working binary containing the sockaddr patch. The consumers were
repointed after that, not before.

### CONFIRMED 2026-09-15: the AppArmor profile blocked D-Bus SIGNALS, so browsing broke while publishing worked

After switching from `apparmor=unconfined` to `altserver-mdns`, both mDNS rows on the status page
started reporting `avahi-browse timed out after 15s`. Refresh itself was unaffected and the server
stayed discoverable from another machine -- a monitoring failure, not a functional one, which is
exactly the combination that makes a status page dangerous rather than merely wrong.

`dmesg` named the cause precisely:

```
apparmor="DENIED" operation="dbus_signal" bus="system"
path="/Client417/ServiceBrowser1" interface="org.freedesktop.Avahi.ServiceBrowser"
member="ItemNew" name=":1.3" mask="receive" label="altserver-mdns"
```

**A D-Bus signal carries the sender's UNIQUE connection name, not its well-known one.** The profile
allowed `peer=(name=org.freedesktop.Avahi)`, which matches method calls and their replies -- so
publishing kept working -- but never matches a signal from `:1.3`. `avahi-browse` subscribed,
received neither `ItemNew` nor `CacheExhausted`, and sat there until its own timeout.

Fixed by matching on the interface, which is stable regardless of which unique name avahi holds:

```
dbus receive bus=system interface=org.freedesktop.Avahi*,
```

**The ptrace denials in the same dmesg are NOT a bug and were deliberately left alone.**
`altserver-web` runs with `pid: host` so the status page's `pgrep -af AltServer` walks every
process on the host. AltServer is under this profile and therefore readable; the denials are for
unrelated host and `docker-default` processes it passes on the way. Widening to `peer=unconfined`
would let the web container read other services' `/proc/PID/environ`, which is where their secrets
live. Denials there are the profile working.

Worth noting the shape: the narrow profile was tested against publishing, which is what it was
written for, and shipped without anyone browsing from inside the container. The check that would
have caught it -- `avahi-browse` from the container -- is the one the status page runs, and it was
reporting UNKNOWN rather than FAIL, which reads as "not applicable" rather than "broken".

### CONFIRMED 2026-09-22: replacing em dashes has two traps that no syntax check catches

The docs had 264 characters that cannot be typed on a US keyboard: 218 em dashes, 28 arrows,
14 ellipses, one en dash, one `>=` sign. All are now ASCII, and
`tests/check_ascii_punctuation.py` keeps them out. Two of the substitutions were load-bearing
rather than cosmetic, and both fail silently:

1. **A line that STARTS with an em dash becomes a Markdown list item.** Three continuation lines
   began with one (README.md:192, REVIVAL.md:917 and :1013). Swapping the character in place
   turns `- text` into a bullet, which splits the paragraph mid-sentence. The result is *valid*
   Markdown, so nothing errors; it simply renders wrongly, and only on the rendered page. Fixed
   by moving the dash to the end of the previous line BEFORE the global swap, then asserting that
   no line begins with one.

2. **GitHub strips an em dash from a heading's anchor slug but KEEPS a hyphen.** So
   `### Path A <em dash> amd64` is `#path-a--amd64` and `### Path A - amd64` is
   `#path-a---amd64`: three hyphens, not two. Nine headings were affected. Nothing in the repo
   linked to any of them, verified rather than assumed, so no links needed updating. Anyone
   retitling a heading should re-check every `](#...)` that points at it.

A third trap was avoided rather than hit: ` -- ` is the repo's pre-existing ASCII prose dash and
appears 297 times, but it is NOT safe to normalise blindly. Some instances are real shell syntax
(`colima ssh -- bash -lc ...`, `git submodule update --remote -- upstream_repo`) and one is in
the vendored Apple header `libraries/dnssd_loader/dns_sd.h`. It is left alone deliberately;
`--` is on a keyboard, which was the whole point of the change.

Method note worth keeping: the substitution was proved content-neutral by applying the same
character map to the `HEAD` text and diffing it against the working tree, which came out
byte-identical for all seven files. That is a stronger check than reading the diff, which for
218 changed lines is unreadable.

### CONFIRMED 2026-09-25: avahi restarted, the advertisement died, and nothing could have noticed

Silent failure mode 3 above, in production, for three days. Predicted in this file before it
happened.

**Timeline, from the host's own journal.** Host booted 09-14 15:23. The `altserver` container
started 09-16 00:34 and registered `_altserver._tcp` on port 37497. avahi-daemon restarted
**09-22 06:48:02** ("Server startup complete", re-registering every address from scratch), which
dropped every registration on the box. Nothing re-created AltServer's. Found 09-25 when a human
opened the status page. No package upgrade was involved: `dpkg.log` shows the avahi packages last
touched 09-14, so the restart had another trigger - likely Docker interface churn, since avahi on
that host tracks ~25 veth and bridge interfaces.

**The diagnostic that settles this class of bug in one command.** `docker exec altserver ps -ef`
showed the python3 helper STILL ALIVE, started Sep16, parked on `Event().wait()`, holding a handle
to a service that had not existed for three days. Every other check was green: anisette OK, clock
OK, phone reachable over Wi-Fi via netmuxd OK, AltServer process running. Only browsing for the
record showed anything wrong.

**Why no in-process signal could ever have caught it.** Four vendor facts, none rediscoverable
from this repo:

1. **avahi-compat's worker thread is a command-driven one-shot, not a poll loop.** `sdref_new`
   queues exactly ONE `COMMAND_POLL`; `thread_func` reads it, calls `avahi_simple_poll_run`,
   writes `COMMAND_POLL_DONE`, and blocks on `read_command` forever. `avahi_simple_poll_run`
   performs the `poll()` syscall and dispatches NOTHING - callbacks are invoked only by
   `avahi_simple_poll_dispatch`, and the only caller of that, and the only writer of a further
   `COMMAND_POLL`, is `DNSServiceProcessResult()`.
2. **Nothing in this project calls `DNSServiceProcessResult`.** Zero call sites; the only
   mentions are comments in `dns_sd.h`. So the compat event loop is parked permanently after the
   first poll. The client never transitions to `AVAHI_CLIENT_FAILURE` and avahi's
   `NameOwnerChanged` signal sits unread in the D-Bus socket buffer indefinitely.
3. **The fd that would drive that loop is a sentinel.** `DNSServiceRefSockFD` is overridden in
   `dnssd_loader.cpp` to return the literal `0xDEADBEEF`. `ConnectionManager.cpp:129` stores it,
   its only accessor has no callers, and nothing ever selects on it.
4. **A failed client never reconnects.** compat passes flags `0`, so once the client reaches
   FAILURE it is permanently dead - re-registering requires building a NEW client, which is what
   the watchdog does.

Together: the helper cannot detect the loss, cannot report it, and cannot recover from it.
**Liveness is therefore not a health signal here** - the process stays alive and healthy-looking
with a dead registration, so any watchdog keyed to "is the child running" would have reported
green for the full three days.

**Why nothing else caught it either. FIXED 2026-09-25.** `/api/status` returned HTTP 200 when
`overall` was `"fail"`, and `status_checks.py` exited 0 when checks failed, so anything monitoring
by status code or exit code saw green throughout. This was the worse half of the incident: the
checks were RIGHT the entire time and both machine-readable channels threw the answer away.
Following the README's own advice to "have something poll it" would have shown green through a
total outage, which is worse than no monitoring, because it answers "is it up?" with a confident
yes.

Now `/api/status` returns **503** on fail and `status_checks.py` exits with Nagios plugin codes
(0 OK, 1 WARNING, 2 CRITICAL). Guarded by `tests/check_status_signals.py`, which drives the real
handler and runs the real script rather than testing the mapping functions -- a correct mapping
that no caller uses is the identical bug, and that is exactly what the original was:
`status_checks.py` computed `overall` perfectly and discarded it one line later.

The coupling worth remembering: the page's own `fetch()` must NOT gate on `response.ok`, or a 503
would blank the dashboard during precisely the outage it exists to describe. That is asserted, and
the rendered page was checked in a browser against a real 503 rather than reasoned about.

**Deliberately NOT changed:** WARN stays HTTP 200, so a monitor watching only the status code
cannot see a degraded result -- including the case where the mDNS check cannot run at all. Use
the exit code or read `overall` from the body if that distinction matters.

**Still open:** there are no healthchecks in the stack, and `restart: unless-stopped` can never
fire because AltServer never exits. Nothing runs the checks periodically except a browser hitting
the page, so the fixes above only help once something is actually polling.

**The fix.** `libraries/dnssd_loader/dnssd_loader.cpp` - the helper now browses for its own record
every `ALTSERVER_MDNS_RECHECK_SECONDS` (default 60, `0` disables) and re-registers when it is
gone. Three rules that are easy to get wrong and invisible when wrong:

  * **Match on port plus the `serverID` TXT value, never the service name.** `flags` is 0, so on a
    name collision avahi RENAMES to "AltServer #2" rather than withdrawing. A name-based check
    would see its own healthy record as missing and re-register forever. The guard's `renamed`
    case pins this: with name matching it produces 3 registrations and 2 deallocations where it
    should produce 1 and 0.
  * **A failed browse is UNKNOWN, not absent.** Re-registering because `avahi-browse` timed out
    would turn a blip into a self-inflicted outage.
  * **Two consecutive misses before acting**, deallocating first, so one false negative costs a
    sub-second gap rather than a duplicate record.
  * **The `serverID` TXT value is part of the match, not decoration.** Ports are ephemeral and
    handed out per process, so a second AltServer anywhere on the LAN can land on the same one.
    Matching port alone would read a stranger's advertisement as proof our own is healthy. The
    guard's `foreign_server` case pins this; it was found by mutation testing, not by design.

**The watchdog also has to not fail silently itself.** If `avahi-browse` cannot run, every check
returns UNKNOWN, the loop correctly refuses to act, and the result is a watchdog that is loaded,
running, and completely blind - the same silent failure one level up. So it probes once at
startup and says loudly if it cannot verify anything, and warns again after 5 and 50 consecutive
unreadable checks. Both are asserted by the guard, because a warning nobody emits is not a
warning.

Two latent bugs were fixed in passing, both exposed by the fix rather than by the incident:
`sdRef` was declared `c_int` while `DNSServiceRef` is a pointer, so on any 64-bit build the daemon
wrote 8 bytes into 4 and the stored handle was truncated - harmless only while nothing used the
handle, which the watchdog now does; and the TXT record was hexed through a SIGNED `char`, which
sign-extends any byte >= 0x80 into `\xFFFFFF80`. Only ASCII `serverID=` is sent today, so it had
never bitten.

The helper also moved from an interpolated one-liner to a raw string literal taking its parameters
as **argv**, which is what lets `tests/check_mdns_watchdog.py` execute the shipped text verbatim
against a fake avahi. Before this, a syntax error in the helper was discoverable only on a live
host, where the parent reports it as the generic "the python3 helper exited immediately".

**Still not covered:** the helper being *killed*. The parent stops looking after ~1 second, and
`PR_SET_PDEATHSIG` fires when the forking THREAD exits, not the process (mode 4 above). The status
page shows it; nothing self-heals it.

### CONFIRMED 2026-09-25: a sweep for the same shape found three more, two now fixed

After the mDNS incident, 38 agents swept the repo across four lenses for the pattern behind it:
code that reports success while failing. 34 candidates, 28 refuted under adversarial review, 6
confirmed. **Every finding from the C/C++ lens was refuted** - the native code is clean; the
problems are in the Python, the build glue and the compose file.

**FIXED: editing a source rewriter rebuilt nothing.** This project patches vendored code by
rewriting it at compile time, so `makefiles/rewrite_*.py` IS the patch and editing one is the
documented way to change vendored behaviour. Three of the four rules that run a rewriter listed
only the vendored SOURCE as a prerequisite, never the rewriter. Measured before the fix:

    touch makefiles/rewrite_altserver_source.py
    make          ->  regenerates 0 files
    make clean    ->  patched tree still present, 35 files
    make          ->  regenerates 0 files

The build reported complete success with the previous patch compiled in. `clean` did not help
because it never removed `AltServer_patched`, `AltSign_patched` or `ldid_patched` - so
`make clean && make` rebuilt every object from stale rewritten sources and was not a clean build
at all. Only deleting `build/` outright worked, which is exactly why CI never saw this: CI always
starts from an empty tree. Locally it can silently waste hours - edit a rewriter, rebuild,
redeploy, observe the old behaviour, with nothing anywhere indicating why. After the fix:

    build, then make    ->  regenerates 0     (quiet when nothing changed)
    touch a rewriter    ->  regenerates 35

Also added `.DELETE_ON_ERROR` to all three makefiles that run a rewriter. This is the same class
one step along: the rewriters exit non-zero on a pattern mismatch BY DESIGN, and `>` has already
created the output file by then, so make left a TRUNCATED source in place and treated it as
finished. `rewrite_idevice_source.py` already avoided this with a tmp-and-mv, which is stronger
still because it also survives an interruption; `tests/check_rewriter_deps.py` accepts either.

**FIXED: the clock check reported OK for a comparison it never performed.** `check_clock` wrapped
its real anisette-drift comparison in a bare `except Exception: pass` and then fell through to
`timedatectl` - which measures LOCAL NTP, the very thing its own docstring says "proves nothing on
its own", because Linux forwards the ANISETTE server's `X-Apple-I-Client-Time` to Apple verbatim.

The trigger is not hypothetical, and this is the part worth keeping: **AltServer's own C parses
that field with `strptime()`, a PREFIX match, while the check used a full match on a fixed 19-char
slice.** A non-zero-padded month is accepted by one and rejected by the other:

    "2026-9-25T12:34:56Z"        C ACCEPTS (tail "Z")   Python RAISES
    "2026-09-25 12:34:56Z"       C rejects              Python RAISES
    "1758800000"                 C rejects              Python RAISES

So a legal timestamp from an independent anisette implementation would leave refresh working
normally while the clock check became a permanent silent no-op - and clock skew is the failure
that surfaces as an opaque Apple `-36607` with nothing naming time as the cause. A parse failure
now returns an explicit WARN naming the value, and the `timedatectl` fallback no longer reports
OK: it says "Local NTP synchronised, anisette drift NOT checked", because a verification that
cannot fail is worse than none. Fixing the swallow also repaired a message that actively lied -
the fallback used to say "No anisette timestamp to compare against" when one was present, and
"resolves itself once the anisette check above succeeds" when that check was already succeeding.

**ALL FOUR REMAINING ITEMS FIXED 2026-09-25.** Guarded by `tests/check_observability.py`
(19 assertions, 14 mutants).

1. **Healthchecks on all four services.** Plain Compose and Portainer never RESTART an unhealthy
   container -- that is Swarm -- so these exist to make failure VISIBLE. Without any healthcheck
   Docker computes no health state at all, which is why the dashboard was structurally incapable
   of showing the 09-22 outage. The altserver probe browses for its own advertisement, the only
   honest test; its window (2m x 3 = 6 minutes) deliberately OUTLASTS the watchdog's recovery
   (2 x 60s), so it cannot flag an outage already being repaired. anisette's healthcheck was
   restored from `deploy/anisette-stack.yml`, where it had existed all along for the same image
   -- and it must keep hitting `/v3/client_info`, never `/`, because the v1 route performs REAL
   provisioning against Apple and a polling healthcheck on it would hammer Apple's endpoint at
   exactly the moment an identity volume has gone missing.

   **It judges the SERVER, not whether the phone is home** (`--server-only`). `check_device`
   returns FAIL when the device is on neither transport, which is correct on the dashboard and
   wrong as a verdict on the server: without this the container would go unhealthy every time its
   owner walked out of the house. A badge that is red half the time gets ignored, and an ignored
   badge is no better than the no-badge state that let 09-22 run for days. The four checks that
   remain are the ones the server is actually responsible for: anisette, clock agreement, its own
   mDNS advertisement, and the daemon process.

   **The altserver-web healthcheck is also what finally RUNS the checks on a schedule.** Until
   now `status_checks.py` executed only inside a browser's HTTP GET, so the verdict existed
   solely in the instant somebody opened the page -- which meant the 503 and Nagios exit codes
   added earlier the same day had no listener at all, in any deployment. `-q` prints one line
   naming what is wrong, because Docker truncates healthcheck output to 4KB and the full JSON
   would be clipped mid-object.

2. **`fetch_altstore` returns EX_TEMPFAIL (75), not 0.** The entrypoint wraps the call in
   `if ! fetch-altstore` purely to warn, and returning 0 made that handler unreachable on the
   common path -- any server that has run before has an IPA. It also now records
   `AltStore.ipa.version.lastfail`, so repeated fail-open is datable rather than looking like a
   first occurrence every time.

3. **The entrypoint EXERCISES the log filter** instead of stat-ing it: it pipes a probe line
   through `redact-log` before committing to the redirect. `[ -x ... ]` says nothing about
   whether a thing can execute, and AltServer ignores SIGPIPE, so a filter that failed to start
   took every log line with it while the container stayed Up and answering.

4. **`redact-log` reports and retries a failed tee.** It used to set `tee = None` silently and
   never reopen, so a full volume froze `/data/altserver.log` for the life of the container while
   stdout kept flowing; `docker logs` and the web log panel then disagreed and neither said so.
   `/api/logs` now also reports `mtime`, `age_seconds` and `stale`, because a frozen log is
   otherwise indistinguishable from a quiet server -- and the log panel is the first thing anyone
   opens during an incident.

   **The first version of this retry was dead code**, and the way that was found is the point:
   `tee_retry_at` was compared against `n`, which counts only SUCCESSFUL tee writes and therefore
   stops advancing the instant the tee is disabled, so the condition could never become true
   again. A guard that grepped the source for `tee_failures` passed it happily. Replacing that
   with a test that actually drives `main()` against a tee which raises ENOSPC caught it
   immediately. Grep-shaped assertions do not catch behaviour; this file has now been bitten by
   that twice.

**Also found, and it is a hole in our own guard.** `tests/check_ascii_punctuation.py` scanned
file BYTES, and `\u2014` in a Python source is six ASCII characters on disk and a real em dash at
runtime. Eight such escapes sat in `web/server.py`'s page markup -- two em dashes, two ellipses,
two arrows, a check mark and a middle dot -- in link text, a placeholder and a status line. The
files were ASCII; the PAGE served to every visitor was not, and the guard reported clean
throughout. It now makes a second pass with `ast`, checking what every string literal EVALUATES
to, with the guard's own mapping table the single documented exemption.

### CONFIRMED 2026-09-26: the dashboard now answers "when", not just "whether"

Every check result used to be computed fresh and discarded, so the status page could say "broken
now" and never "broken since Tuesday". During the 09-22 outage the honest answer to "how long has
this been down" was "somewhere between three and nine days" -- reconstructed from `journalctl` and
a container start time, because nothing in this project recorded anything.

`status_checks.py --record` appends one line per run to `/data/status-history.jsonl`
(`ALTSERVER_HISTORY`, bounded by `ALTSERVER_HISTORY_MAX`, default 5000 runs ~ 17 days at the
healthcheck's five-minute cadence). The altserver-web healthcheck is the writer: it already runs
on a fixed schedule, which is a better timeline than page loads would give. `/api/history` serves
it and the status page draws a per-check timeline with "last not-ok 2h ago" beside each row.

**A design decision from earlier the same day had to be reversed.** `--server-only` originally
SKIPPED the device checks. That was right for the healthcheck verdict -- a phone that has left the
house must not mark the container unhealthy -- and wrong once history existed, because those
checks would then never be recorded and "when did the phone drop off the network" would be
unanswerable for exactly the reason the mDNS outage was. `--server-only` now narrows the VERDICT
only; every check still runs and is still recorded. The guard was rewritten to assert the
intent (a failing device check does not make the server unhealthy, a failing server check still
does) rather than the implementation detail it previously pinned.

**Two bugs in this feature were found by the guard, not by review, and both are the house
pattern.** `record()`'s trim was gated on `os.path.getsize(path) > HISTORY_MAX * 400`, a cheap
way to avoid reading the file every run -- but that guess does not bind: with the short lines a
four-check run produces, the file held 93 entries against a bound of 25, roughly double the
intended window, and how long the window actually was depended on how long the check names
happened to be. And `_result` as a variable name in `__main__` shadowed the module-level
`_result()` function, so the "could not record history" path raised `TypeError: 'dict' object is
not callable` -- the error handler was broken, and only running the failure path showed it.

**A THIRD form of the non-ASCII hole, found by reading the served page rather than the source.**
`tests/check_ascii_punctuation.py` already scanned file bytes, and then string literal VALUES
after `\uXXXX` escapes are decoded. It still missed HTML entities: an em-dash entity is seven
ASCII characters in the file AND in the response body, and an em dash only once a browser has
rendered it. Three were live on the status page. The guard now makes a third pass over entities,
exempting the escaping ones (`&amp;` and friends exist to make text safe, not to decorate it).
The served page is now ASCII at every layer: raw bytes, decoded string values, and after full
entity expansion.

### CONFIRMED 2026-09-26: the monitoring advice paged you for leaving the house, and polled Apple

Two defects in work from the day before, found by a README audit rather than by use.

**1. `/api/status` returned 503 whenever the phone was away.** The endpoint the README told you
to monitor judged its status code on EVERY check, including `iPhone reachability`, which fails
whenever the phone is asleep, out of the house or off Wi-Fi. An alert that fires every time its
owner goes out is silenced within a week -- which lands back at no monitoring at all, the exact
state that let 09-22 run for days. The healthcheck already used `--server-only` for this reason;
the HTTP endpoint did not. The status code now follows a `server_overall` verdict, the body
carries both, and `?full=1` restores the everything-judged behaviour for anyone who wants it.

**2. Anything polling the status hit the anisette route that provisions against Apple.**
`check_anisette` fetches the bare root, which is the v1 DATA route:
`deploy/anisette-stack.yml:62-73` refuses to point even a healthcheck at it, because on a server
whose machine identity is missing it performs REAL PROVISIONING against Apple, and polling it
"would hammer Apple's endpoint at exactly the moment your identity volume has gone missing --
turning a restore-the-backup incident into rate-limit / account-lock territory."

Two polling loops were doing exactly that. The altserver-web healthcheck added the day before:
288 times a day. And, larger and pre-existing, the status page's own 30-second auto-refresh
(`setInterval(load, 30000)`): **2880 times a day for any tab left open**. Nobody had connected
the compose file's warning to the page it was sitting next to.

Polled requests now probe `/v3/client_info`, which is static and contacts Apple for nothing, and
say plainly that the field contract was not verified. A human opening the page gets the real
fetch once; the refresh does not. `--record` and `--server-only` imply polling so the compose
healthcheck cannot opt out of safety by omission.

The guard intercepts `urlopen` and asserts WHICH URL each mode fetches, rather than checking that
some flag is present -- the flag being present proves nothing about where the request goes.

**Also fixed: a guard that depended on undeclared host state.** `check_status_signals.py` drove
`pairing.diagnose()`, which returns early when `/var/run/usbmuxd` is absent. That path EXISTS on
macOS and does not on the Linux CI runner, so the test passed locally, bailed at that step in CI,
and graded the early-return's text as a feature failure -- turning the Guards job red over
nothing. Every host dependency is now pinned and the test is verified under BOTH conditions. It
also gained a `reached_pairing()` precondition, because the assertions graded output TEXT: an
early return looked like a wrong answer instead of a broken setup.

## Repository audit, 2026-09-15

Run after `bd/revival` merged into `new`, to answer two questions: is every tracked file
supposed to be here, and could a stranger actually use this repo. 46 agents across four lenses
(stale files, fresh-clone usability, leaked personal data, repo hygiene); every finding below
survived an adversarial attempt to refute it, and roughly twice as many were refuted and dropped.

**Infrastructure verified good, so it is not re-checked later:** the GHCR image is public
(anonymous pull returns 200), all six submodule URLs are reachable, netmuxd v0.4.3's release
assets return 200 for both architectures, and the digest-pinned anisette image resolves. No
secrets, build output, caches or editor files are tracked. Every shim -- including the 0-byte
ones -- and both CI workflows are genuinely reached; `build_docker.yml` is not a duplicate of
`build_image.yml`, it builds the buildenv toolchain image.

Status key: **OPEN** = not yet addressed. Tick these off in the same commit that fixes them.

### High severity

- [x] **A1. FIXED 2026-09-15. BOOTSTRAP.md, the doc README sends new users to, is written for one specific machine and cannot be followed by anyone else**
      `BOOTSTRAP.md` -- fix
      README.md:10 advertises it as "first-time setup, start to finish", but it is an operator log
      for one host. BOOTSTRAP.md:7 "Target: Dell OptiPlex 5060 -> Proxmox -> Ubuntu 24.04 VM
      (`192.168.9.16`, `ens18`)". BOOTSTRAP.md:21 opens Phase 0 with `git push origin bd/revival` -
      a stranger has no push access, and that branch is already merged into `new`.

- [x] **A2. FIXED 2026-09-15. BOOTSTRAP tells the reader the project's core functions are unproven or broken; README says they are fixed**
      `BOOTSTRAP.md` -- fix
      Direct contradiction between the two docs a new user reads first. BOOTSTRAP.md:217 (Phase 2
      "Honest confidence" table): "| This produces a successful Apple sign-in | **Low - unproven.**
      No one has reported a completed AltServer-Linux sign-in in 2026 |" versus README.md:21 "|
      Apple sign-in | **Working**, including 2FA, team lookup, device registration and certificate
      issuance |" and REVIVAL.md:1102 "**Phase 4 - install: sign-in WORKS.**" BOOTSTRAP.md:296-298
      still frames #131 as an open unknown ("the fix in `0e8090b` did not work, and that is exactly

- [x] **A3. FIXED 2026-09-15. "No host preparation is needed" is wrong, and the missing host paths are silently created as directories that permanently break mDNS**
      `README.md` -- fix
      README.md:42-44 "No host preparation is needed - it uses named volumes, and the image bundles
      every runtime dependency", and README.md:104 says the runtime requirements are "Bundled in the
      container image. Needed on the host if you run the binary directly". The stack contradicts
      both: deploy/altserver-stack.yml:220-224 bind-mounts `/var/run/dbus/system_bus_socket` and
      `/var/run/avahi-daemon/socket` with the comment "the actual publishing is done by the HOST's
      avahi-daemon", and the Dockerfile installs avahi-utils but no avahi-daemon (Dockerfile:50-64).

- [x] **A4. FIXED 2026-09-15. BOOTSTRAP and the shipped pairing page tell users to stop usbmuxd and let netmuxd take /var/run/usbmuxd, contradicting the design the stack actually ships**
      `BOOTSTRAP.md` -- fix
      BOOTSTRAP.md:318-320: "**Stop `usbmuxd`, start `netmuxd`** (>= 0.3). They collide: stock
      usbmuxd never emits ConnectionType `Network`, and netmuxd binds `/var/run/usbmuxd` by
      default." web/pairing.py:101-102 repeats it inside the UI: "If you are running netmuxd for
      wireless refresh instead, it OWNS this same socket and usbmuxd must be stopped -- the two
      collide." Both are wrong for what this repo deploys: deploy/altserver-stack.yml:107-110 runs
      netmuxd with `--socket-path /run/muxd/usbmuxd`, and README.md:138-140 says "It runs on **its

### Medium severity

- [x] **A5. FIXED 2026-09-15. libraries/ideviceinstaller is a submodule nothing compiles, and two places assert that it does**
      `.gitmodules` -- delete
      VERDICT: dead submodule, cloned on every CI checkout, and actively mis-described. Evidence it
      is never built: - makefiles/libimobiledevice-build/libimobiledevice-files.mak lists the
      compile inputs explicitly: libimobiledevice/src, libimobiledevice/common, libimobiledevice-
      glue/src, libusbmuxd/src, libusbmuxd/common. ideviceinstaller is not among them.

- [x] **A6. FIXED 2026-09-15. BOOTSTRAP.md Phase 7 instructs the opposite of the netmuxd configuration the stack actually ships**
      `BOOTSTRAP.md` -- fix
      BOOTSTRAP.md is reachable -- README.md links it twice as "first-time setup, start to finish"
      -- but three of its phases now describe a deployment that no longer exists, and Phase 7 will
      actively break a working setup. Phase 7, BOOTSTRAP.md:318: "**Stop `usbmuxd`, start
      `netmuxd`** (>= 0.3). They collide: stock usbmuxd never emits ConnectionType `Network`, and
      netmuxd binds `/var/run/usbmuxd` by default.

- [x] **A7. FIXED 2026-09-15. The published image is amd64-only, and nothing tells a Raspberry Pi user that before the pull fails**
      `README.md` -- document
      Verified against the registry: `ghcr.io/ben-diehlci/altserver-linux:latest` is public (good)
      but its OCI index carries one platform, linux/amd64 - build_image.yml:83 sets `platforms:
      linux/amd64` with the comment "amd64 only for now" (build_image.yml:72-75). README's Quick
      start (README.md:31-44) and Download section (README.md:157) never mention this, while
      README.md:159-161 advertises four-architecture binaries and build.yml:151-154 keeps
      aarch64/armv7/i386 builders precisely because "dropping them entirely would make this fork

- [x] **A8. FIXED 2026-09-15. The commented-out build fallback in the stack uses a context that resolves to deploy/, so it cannot work as written**
      `deploy/altserver-stack.yml` -- fix
      deploy/altserver-stack.yml:130-134 offers `build:\n context: .\n dockerfile: Dockerfile` as
      the documented escape hatch ("To build from source instead, comment the image line and
      uncomment build:"). Compose resolves a relative build context against the project directory,
      which for the command README gives (`docker compose -f deploy/altserver-stack.yml up -d`,
      README.md:39) and for a Portainer repository stack with compose path `deploy/altserver-
      stack.yml` is `deploy/` - which contains no Dockerfile. The build fails with "failed to read

- [x] **A9. FIXED 2026-09-15. BOOTSTRAP's install step still uses the password-on-the-command-line path and cites a TODO that is already done**
      `BOOTSTRAP.md` -- fix
      BOOTSTRAP.md:264 instructs `~/AltServer-x86_64 -u "$UDID" -a "$APPLEID" -p "$APPLEPW"
      ~/AltStore.ipa`, and BOOTSTRAP.md:267-269 says "The password is still visible in `ps` for the
      duration of the run, because the binary accepts it only as a command-line argument ... see
      TODO 9 in REVIVAL.md". Both halves are stale: src/AltServerMain.cpp:193-199 reads
      ALTSERVER_UDID / ALTSERVER_APPLE_ID / ALTSERVER_APPLE_PASSWORD with flag-over-env precedence,
      README.md:92 says "Prefer these", and REVIVAL.md:1367 marks TODO 9 struck through and

- [x] **A10. FIXED 2026-09-15. BOOTSTRAP describes manual steps the shipped image has automated and never mentions the stack, the web UI, or netmuxd**
      `BOOTSTRAP.md` -- fix
      BOOTSTRAP.md:248 "Get `AltStore.ipa` from <https://altstore.io> onto the VM" and
      BOOTSTRAP.md:256 `ls -la ~/AltStore.ipa` predate the automatic fetch: docker-entrypoint.sh:15
      runs fetch-altstore on every start and README.md:43-44 says "The AltStore IPA is fetched
      automatically on start". The whole document also predates deploy/altserver-stack.yml, the
      :8099 web UI and netmuxd-as-a-service - its Phase 0 has the reader build and scp a bare
      binary, and Phase 7 treats wireless as a manual future step, so nothing in it matches the

- [x] **A11. FIXED 2026-09-15. web/installer.py redaction misses the anisette machine identifiers it claims to filter**
      `web/installer.py` -- fix
      The install page footer (web/server.py:248) tells the user "Credentials and account data are
      filtered out of the log above before it is shown", and installer.py's module docstring says
      "Every line is filtered before it leaves this module." Verified against the actual log output,
      that is not true for the anisette identity. `_SENSITIVE` (installer.py:32-36) matches only GsI
      dmsToken|adsid|DsPrsId|phoneNumber|<data>|com.apple.gs.*</key>|"token"|<key>token</key>|sessio
      nKey|Got token for. But src/AnisetteDataManager.cpp:315 calls `odslog(*anisetteData)`, and the

- [x] **A12. FIXED 2026-09-15. The getrandom() polyfill leaks a file descriptor on every call and ignores short reads**
      `shims/old-linux-polyfill.c` -- fix
      Lines 4-21 define a replacement for libc's `getrandom`: ```c ssize_t getrandom(void *buf,
      size_t buflen, unsigned int flags) { int randomData = open("/dev/urandom", O_RDONLY); if
      (randomData < 0) { return -1; } else { ssize_t result = read(randomData, buf, buflen); if
      (result < 0) { return -1; } return result; } } ``` The descriptor is never closed on any path.
      This file is compiled into the binary unconditionally: `makefiles/AltWindowsShim.mak:8`
      collects `shim_src := $(wildcard $(SHIM_DIR)/*.cpp) $(wildcard $(SHIM_DIR)/*.c)`, and

### Low severity

- [x] **A13. FIXED 2026-09-15. REVIVAL.md TODO item 5 is already done, and its guidance now contradicts the shipped design**
      `REVIVAL.md` -- document
      REVIVAL.md is the project's index of what is left to do, and its neighbours are carefully
      struck through when finished (items 2, 3, 4 and 8 all carry `~~...~~ **DONE**`). Item 5 is
      not, but it has been completed. REVIVAL.md:1285-1289 -- "5.

- [x] **A14. FIXED 2026-09-15. web/server.py's module docstring denies the sign-in and 2FA feature the same file serves**
      `web/server.py` -- fix
      web/server.py:21-23: "SCOPE. Read-only diagnostics. It deliberately does NOT sign in or handle
      2FA yet -- that needs a supervisor that owns the AltServer child's stdin, and it should not be
      built against an authentication flow that is not yet working." The file imports `installer`
      (server.py:38), serves INSTALL_PAGE at /install (server.py:363) and posts credentials to
      installer.INSTALLER.start at server.py:397-401; web/installer.py is exactly the stdin
      supervisor the docstring says does not exist.

- [x] **A15. FIXED 2026-09-15. Dockerfile comments describe a web UI that is not started automatically and a fetch step that moved**
      `Dockerfile` -- fix
      Dockerfile:110-113: "Run it with `docker exec altserver python3 /opt/altserver-web/server.py
      --host 0.0.0.0` / It is NOT started automatically -- it accepts an Apple ID password, so
      exposing it should be a deliberate act rather than a side effect of deploying." The stack does
      start it automatically as the `altserver-web` service on 0.0.0.0:8099 (deploy/altserver-
      stack.yml:250, :320-321), and README.md:46 tells users to open it. The paragraph above it
      (Dockerfile:108-109, "Fetches the current AltStore Classic IPA...") is also orphaned - it sits

- [x] **A16. FIXED 2026-09-15. An unpinned third-party action with contents:write - ad-m/github-push-action@master**
      `.github/workflows/build.yml` -- fix
      Line 263: `uses: ad-m/github-push-action@master`, inside the `update_submodule` job which
      declares `permissions: contents: write` (line 249) and passes `github_token: ${{
      secrets.GITHUB_TOKEN }}` (line 265). `@master` is a mutable ref: whatever is on that branch at
      run time executes with write access to the repository. Every other action in this workflow was
      deliberately moved to a pinned major during the revival (checkout@v7, upload-artifact@v7,
      download-artifact@v8, setup-qemu-action@v4, action-gh-release@v3 - recorded in REVIVAL.md's

- [x] **A17. FIXED 2026-09-15. --help calls ALTSERVER_NO_SUBSCRIBE "(*unused*)" but the build wires it up**
      `src/AltServerMain.cpp` -- fix
      Line 98 of the usage text: ``` " - ALTSERVER_NO_SUBSCRIBE: (*unused*) Please enable this for
      usbmuxd server that do not correctly usbmuxd_listen interfaces\n" ``` It is not unused.
      `makefiles/rewrite_altserver_source.py:152-155` splices this into AltServerApp::Start at build
      time: ```c const char *isNoUSB = getenv("ALTSERVER_NO_SUBSCRIBE"); if (!isNoUSB) {
      DeviceManager::instance()->Start(); } ``` Setting the variable therefore suppresses
      DeviceManager startup entirely - a significant behaviour change, and one that is plausibly

Full per-finding detail, including the evidence each verifier checked, is in the workflow
transcript for run `wf_faa3350d-635`. The summaries above are self-contained enough to act on
without it.
## TODO

### Blockers for the actual goal - a working headless refresh server

These are what stand between us and "the Linux box refreshes apps by itself". Ranked by what
breaks the premise soonest, not by how interesting the code is.

- **A. An anisette server that actually works in 2026.** Without one, sign-in fails and nothing
  refreshes - the entire goal is dead. The fork no longer ships a default (it was dead anyway),
  so one has to be chosen and run, most likely alongside AltServer on the same box. The old
  `nyamisty/alt_anisette_server` image is from April 2022 and unverified against Apple's current
  flow. **This is the number one practical blocker and is a deployment decision, not a code
  change.**
- **B. PR #135 - the Apple GSA block.** Even with a healthy anisette server, Apple 503s any
  request whose `X-MMe-Client-Info` contains `com.apple.dt.Xcode`, which anisette servers
  commonly return. Trivial code fix, listed under "Next up" below. Blocks sign-in, so it blocks
  the 7-day refresh.
- **C. Wi-Fi device discovery.** The phone will not be plugged into the server, so wireless
  refresh is mandatory here. Needs `netmuxd` (> v0.1.1) in place of or alongside `usbmuxd`.
  Covers issues #87, #81, #77, #76, #75, #122, #49, #13 - previously triaged as mostly
  user-error, but for *this* deployment they describe the critical path. Needs a verified,
  written-down working configuration.
- **D. iOS-version signing - BOOTSTRAP ONLY, NOT THE REFRESH PATH.** Corrected 2026-09-14; an
  earlier entry here wrongly called this the largest task on the critical path.
  **A 7-day refresh does not re-sign anything and does not transfer an IPA.**
  `AltStore/Operations/RefreshAppOperation.swift:68` sends exactly one request,
  `InstallProvisioningProfilesRequest`, which lands at `ClientConnection.cpp:238` ->
  `DeviceManager::InstallProvisioningProfiles` -> misagent. `Signer` has exactly **one** call site
  in the entire server, `AltServerApp.cpp:1453-1454`, inside `InstallApp`, reachable only from
  the CLI install path. So the 2022-stale signer and #131 block the *first* install on iOS 26.x,
  not the recurring refresh that is the actual goal.
  **And #131 is two lines, not a submodule bump.** `upstream_repo/ldid/ldid.cpp:2215` runs
  `hash.resize(20)` *before* `:2217-2220` captures `alternateCDSHA256 = hash`, so the SHA-256
  hash-agility attribute carries a hash truncated to 20 bytes and CoreTrust rejects it. Moving the
  capture above the resize is the whole fix - verified in source here, matching jaakkopalvaila's
  diagnosis in `open_issue_0131.md`. The competing diagnosis in that thread does not hold against
  this tree: DER entitlements *are* emitted, CodeDirectory version *is* 0x00020400, and a SHA-256
  alternate CD *is* present.
  **ANSWERED 2026-09-14: AltStore is NOT installed. Installing it is the whole point.** So the
  bootstrap path IS required, and #131 is therefore back on the critical path - it is the *first*
  thing that will bite. The mechanism analysis above still stands (it is not a *refresh* blocker),
  but you cannot reach refresh without passing through the install it breaks.
  **FIXED in `makefiles/AltSign-build/rewrite_ldid_source.py`** rather than in the submodule,
  using the project's existing build-time rewriting mechanism, with a guard that fails the build
  loudly if upstream ldid.cpp ever stops matching. **UNVERIFIED ON HARDWARE** - the diagnosis is
  confirmed in source and matches the issue reporter, but nobody has yet confirmed it makes an app
  launch on a real iOS 26 device.

Items A and C are deployment/config work rather than patches, and both need a real device to
confirm. B is a small patch. D is a substantial one. None are blocked by anything already done.

### Bootstrap progress

- **Phase 0 - binary: DONE.** `AltServer-x86_64` from a green CI run on `dbb9977`, verified by
  string check (contains the new anisette text; the dead armconverter default is absent).
- **Phase 1 - host prereqs: DONE.** avahi-daemon, avahi-utils, libavahi-compat-libdnssd-dev,
  usbmuxd, libimobiledevice-utils. `CDLL('libdns_sd.so')` loads.
- **Phase 3 - USB pairing: DONE.** Proxmox passthrough worked; `idevicepair validate` returns
  SUCCESS and `/var/lib/lockdown/` is backed up with BOTH the per-device plist and
  `SystemConfiguration.plist`. Note: validation fails with "a passcode is set" unless the device
  is unlocked at the time - expected, and it will matter again if re-pairing.
- **Phase 2 - anisette: DEPLOYED AND VERIFIED** (redeploy-persistence test still pending).
  `dadoum/anisette-v3-server`, digest-pinned, Portainer stack, `127.0.0.1:6969`.
  Contract PASS - all ten keys present, all JSON strings, HTTP 200. Clock matches `date -u`.
  **The volume mapping is proven correct**: `docker diff` shows nothing identity-related on the
  writable layer, and `adi.pb` + `device.json` + `lib/` are visible host-side at
  `/opt/stacks/anisette/config`. The `lib/`-only mount the upstream README recommends was avoided.
  Container runs as uid 1000 = host user (uid 1000).
  Quirk: `adi.pb` is mode `---x-w-rwt` (written by Apple's closed-source libCoreADI), so backups
  need `sudo`.
  **Confirmed: this server returns `com.apple.dt.Xcode/3594.4.19` in `X-MMe-Client-Info`**, so the
  PR #135 sanitizer is load-bearing for sign-in. Leave `ALTSERVER_NO_CLIENTINFO_SANITIZE` unset.
- **Phase 4 - install: sign-in WORKS.** Full Apple authentication completes - SRP, 2FA, team
  lookup, device registration, certificate issuance, all `200`. As far as can be told this is the
  first completed AltServer-Linux sign-in reported in 2026.
  **Still failing at the archive step:** `com.rileytestut.Archive (2)` / "The app could not be
  found" after "Importing app... Downloaded app!". Undiagnosed - the IPA had vanished (named-volume
  redeploy) before it could be inspected. Now fetched automatically and verified present at the
  right size, so it may not recur. If it does, `/tmp` space inside the container is the next
  suspect: unpacking a 33 MB IPA needs roughly double that.
- **Phase 5 - does AltStore open on iOS 26?** The real test of the #131 fix, and the last genuine
  unknown in the whole exercise.

### Security note

A successful sign-in prints Apple's entire account record to stdout - real name, phone number,
`adsid`, and a dozen bearer tokens including `com.apple.gs.icloud.auth` with a **one-year**
lifetime. That reaches `docker logs` and journald. The web UI filters all of it before display,
but the CLI does not. Treat any captured install log as credential material.

### Confirmed deployment facts

Target device runs **iOS 26.x** -> #131 / the `upstream_repo` bump is required.
Anisette: **none yet**, needs standing up, most likely as another container on the same box.

Host (from the operator's `HOMELAB_CONTEXT.md`):

| | |
|---|---|
| Hardware | Dell OptiPlex 5060, Intel **x86_64** |
| Stack | Proxmox -> Ubuntu VM -> Docker, managed via Portainer |
| Binary | **`AltServer-x86_64`** - already produced by our CI, confirmed by artifact name |
| mDNS | `apt install libavahi-compat-libdnssd1` on the VM; `network_mode: host` if containerised |
| Registry | Already publishes to `ghcr.io/ben-diehlci/` |
| Network | Zero open router ports; Cloudflare Tunnels + NPM for external access |
| Philosophy | Prefer simplicity; avoid unjustified complexity |

Consequences for this project:

- The x86_64 leg is the one that matters. It builds under Rosetta locally and is already green
  in CI. `aarch64` remains the fast local loop for compile-checking.
- Sideloading is **entirely LAN-local**. Cloudflare Tunnels, NPM and the zero-open-ports posture
  are irrelevant to it - no reverse proxy should be put in front of AltServer, and nothing about
  this needs to be externally reachable.
- Because they already own `ghcr.io/ben-diehlci/`, re-namespacing `build_docker.yml` (item 6)
  stops being hypothetical, and a purpose-built AltServer container for the Portainer stack
  becomes the natural deliverable - see item 8.

**Network topology - RESOLVED.** The Ubuntu VM is **bridged onto the same network as the phone**;
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
before touching anything else - nothing downstream can work without it.

**CONFIRMED 2026-09-14.** `avahi-browse -art` on the VM (192.168.9.16, ens18, Ubuntu 24.04.4)
sees `_companion-link._tcp` from Apple devices over both IPv4 and IPv6. Multicast crosses from
Wi-Fi to the wired VM. Topology is settled. Installing `avahi-utils` also pulled in
`avahi-daemon`, which was NOT previously present and which the compat layer requires - so that
was a necessary prerequisite obtained by accident.

### Pairing needs a one-time USB connection

A pair record can only be created over USB in this tree. `libraries/libimobiledevice` falls back
to `lockdownd_pair`, `tools/idevicepair.c:180-182` states wireless pairing is Apple-TV-only, and
`makefiles/libimobiledevice-build/config.h:99` is `#undef HAVE_WIRELESS_PAIRING`. So the phone
must physically touch the Linux box once, with a cable, and Trust it. The "no Mac, no PC" premise
holds for steady-state operation but does not budget for that one cable trip.

### The phone ALSO fails silently - both ends at once

`AltStore/Operations/BackgroundRefreshAppsOperation.swift:60` sets
`ignoresServerNotFoundError = true`, consumed at `:221-223` to suppress the alert. So a 3am
background refresh that cannot find the server posts **no notification on the phone** and logs
nothing on the server - the first symptom is an app that will not open, seven days later.

Useful asymmetry: `AltStore/Intents/App Intents/RefreshAllAppsIntent.swift:187` sets it to
**false**, so a *manually* triggered refresh does surface the error. Manual refresh is therefore
the diagnostic tool; background refresh is the thing that goes quiet.

This is the strongest argument for an external watchdog - something that independently checks the
server is advertising and that a refresh actually succeeded, rather than trusting either end.

### mDNS advertisement is a SILENT failure - the top risk for unattended operation

`libraries/dnssd_loader/dnssd_loader.cpp` does not link Bonjour. `DNSServiceRegister` builds a
Python one-liner, forks, and `execlp`s `python3 -c "from ctypes import *; dll = CDLL('libdns_sd.so'); ..."`
(`:25`, `:66`). The parent branch of the fork is literally `else { ; }` - `status` is declared at
`:53` and never used, there is no `waitpid`, and the function `return 0`s unconditionally at
`:69`. **Advertisement failure is therefore indistinguishable from success inside AltServer.**

Consequences, and they are exactly the wrong shape for a headless box:

- If `python3` is missing, or `libdns_sd.so` cannot be dlopened, AltServer runs normally, reports
  nothing wrong, and is permanently undiscoverable by the phone. The only evidence is the child's
  Python traceback on stderr - unlabelled, and under systemd it lands in the journal interleaved
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

0. ~~**Deploy and prove the wireless-refresh fix.**~~ **DONE 2026-09-14** -- a refresh triggered
   from AltStore removed both old profiles and installed two with fresh UUIDs over Wi-Fi, no cable
   attached, with netmuxd sending byte-identical connection data before and after. Original note: The sockaddr patch is written, builds clean and
   is mutation-tested, but has never run against the phone. Rebuild the image, redeploy the stack,
   trigger a refresh from AltStore, and confirm `Failed to handle request:There was an error
   connecting to the device.` is gone. Remember that `idevice_id -l` and the status page both use
   **Debian's** post-2023 libimobiledevice and were green throughout the bug -- neither can confirm
   this. The only real evidence is AltServer's own log.
0b. ~~**`makefiles/rewrite_altserver_source.py` fails silently.**~~ **DONE 2026-09-15** - guarded
   with match counts on the mandatory AltServerApp.cpp substitutions and post-conditions on the
   output for the global ones. Output verified byte-identical across all 35 files; nine mutations
   caught. Original note: It has no `raise`, `assert` or
   `sys.exit` anywhere, so any substitution whose pattern stops matching after an `upstream_repo`
   bump produces a quietly wrong binary rather than a failed build. It is the largest rewriter and
   it patches the code that talks to Apple. The other three rewriters all guard themselves; give
   this one the same treatment (a `replace_once`-style helper that counts matches and exits
   non-zero with the reason). README.md's build section documents the gap in the meantime.
0c. ~~**The libimobiledevice sub-make runs twice on every build.**~~ **DONE 2026-09-15** --
   `Makefile:24` was a multi-target rule; split so only libimobiledevice.a carries the recursive
   recipe and libplist.a depends on it. Measured: sub-make invocations 2 -> 1, idevice.c.o
   compiled once instead of twice, `ar rcs libplist.a` run once instead of twice. Original note: Pre-existing, found while fixing
   the race above. `Makefile:24` is a multi-target `.PHONY` rule, so `libimobiledevice.mak` is
   invoked once for `libimobiledevice.a` and again for `libplist.a`, concurrently under `-j`. Both
   copies compile the same ~40 objects to the same paths and both run `ar rcs` on the same
   archives. That is wasted build time, and two `cc` processes writing one `.o` is a latent
   corruption risk for every object, not just the patched one. The rewrite rule is now safe
   against it, but the duplication remains. Fix by giving the sub-make a single entry point, e.g.
   `$(BUILD_DIR)/libplist.a : $(BUILD_DIR)/libimobiledevice.a` with the recursive recipe only on
   the latter. Not done here: it changes shared build structure and did not belong in the same
   commit as a correctness fix.
1. ~~**`ServerError` recovery suggestion is Windows-only advice.**~~ **DONE 2026-09-15** --
   rewritten in `rewrite_altserver_source.py` (guarded, so an upstream reword fails the build) to
   name the two causes that actually apply on Linux: an unreachable or unhealthy anisette server,
   and clock skew on the anisette host. Verified in the linked binary; "Microsoft Store" is gone.
   Original note: `ServerError.hpp:170` returns
   "download the latest versions of iTunes and iCloud... not from the Microsoft Store" for
   `InvalidAnisetteData`, appended to the CLI alert by `AltServerApp.cpp:1614`. Now newly
   visible, since the anisette work routes failures through `ServerError`. Fix by adding a
   substitution to `makefiles/rewrite_altserver_source.py` (it already rewrites this file).
   Note: stuffing `NSLocalizedRecoverySuggestionErrorKey` into `userInfo` does **not** work -
   `ServerError::localizedRecoverySuggestion()` returns from its `case` before reaching
   `default`.
2. ~~**PR #135 - sanitize `X-MMe-Client-Info`.**~~ **DONE** - see the Done table. Original note kept for the caveats: Rewrite `com.apple.dt.Xcode` -> `com.apple.akd` at
   `src/AnisetteDataManager.cpp`, the single point where anisette data enters. Apple's GSA edge
   503s any request carrying that substring as of ~2026-09. Trivial. Closes no open issue
   (nobody has reported it - the anisette failure fired first) and needs a real Apple ID to
   confirm 503->401. Only two of four call sites hit `gsa.apple.com`; the others hit
   `developerservices2.apple.com` and were never in the author's A/B test.
3. ~~**corecrypto layer 3.**~~ **SOLVED** - the diagnosis below was wrong, corrected from CI logs.
   The real first error is `Cannot find source file: corecrypto_static/ccrng_static.c`;
   `No SOURCES given to target` is a follow-on and the one people notice. Apple's 2024
   distribution moved `ccrng_static.c` to the tree root but left `CoreCryptoSources.cmake:251`
   pointing at the now-nonexistent `corecrypto_static/` subdirectory. Exactly one entry. Fixed by
   a guarded sed in `buildenv/Dockerfile`; **full `make; make install` exits 0 - #111 CLOSED.** Also
   settled: it is NOT arch-specific - the amd64 CI run fails identically to local aarch64.
   Superseded note: `CORECRYPTO_SRCS` is populated at `CoreCryptoSources.cmake:189` and
   Linux subtracts `CORECRYPTO_EXCLUDE_SRCS` at `CMakeLists.txt:262`, but the list ends up empty
   at `add_library` (`:266`). Cheapest next probe: build the amd64 leg to see whether it is
   arch-specific. Closes #111.

### Bigger

4. ~~**Fix #131**~~ - **DONE**, see the Done table. Left here only as a pointer: the heavier
   alternative, if the rewriter patch ever proves insufficient, is bumping `upstream_repo` to
   1.7.4 (`ldid.cpp`
   truncates a hash to 20 bytes before it becomes the SHA-256 attribute; CoreTrust rejects it).
   `.gitmodules` pins `branch = develop`, whose tip is from 2022, so `--remote` can never reach
   it. Needs a hand-edit: the new `Signer.cpp:277` passes `app.path() + "\\"` and
   `rewrite_altsign_source.py` does no backslash translation. Harden `removePart()` to assert
   each regex matched before attempting this. **Cross-check against `~/Local Work/AltStore`,
   which has the current AltSign.**
5. ~~**README pass.**~~ **DONE** - and its closing warning has since been inverted by the code.
   It said: do **not** rewrite the Wi-Fi section to "keep usbmuxd running", because netmuxd binds
   the unix socket by default. True of stock netmuxd, false of this stack, which gives netmuxd its
   own `--socket-path` precisely so the host usbmuxd keeps the cable. Keeping usbmuxd running is
   now the correct advice and README says so. Closed #124, #120, partially #111.
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
   are uninitialised. All real UB - but fix as hygiene and claim no issue: across 16 pasted
   command lines in the issue corpus, nobody wrote `-p` before `-a`.

### Future / research - NOT scheduled, recorded so they are not lost

These are the operator's stated end-goals for the project. Do not start them until the blockers
above are cleared; they are written down here with enough grounding to be picked up cold.

**F1. Self-contained deploy - LARGELY BUILT.** `Dockerfile` (multi-stage, installs every runtime
prerequisite including the python3 + `libavahi-compat-libdnssd-dev` pair that otherwise fails
silently, and *verifies* `CDLL('libdns_sd.so')` at build time so a broken image cannot ship),
`deploy/altserver-stack.yml` (both services, `network_mode: host`, `init: true`, all the mounts),
and `.github/workflows/build_image.yml` (publishes to `ghcr.io/<owner>/altserver-linux`, using
`repository_owner` so a fork publishes to its own namespace instead of failing against someone
else's - the mistake `build_docker.yml` makes). Portainer supports Git-repository stacks natively,
so "point Portainer at the repo" now works. Remaining: make the package public once, and confirm
the stack end-to-end on the host. Original notes:

**F1 (original).** Point Portainer (or any compose-based platform) at the GitHub repo
and have everything come up with no manual steps beyond entering account credentials. Portainer
supports deploying a stack straight from a Git repository, so the shape is: a `docker-compose.yml`
in the repo, an image published to `ghcr.io/ben-diehlci/`, `network_mode: host` for mDNS, a named
volume or absolute bind mount for `AltServerData` (remember it is a **relative** path), and the
anisette server as a second service in the same stack. Depends on TODO 6 (registry namespace) and
7 (the container image). The image must contain `python3` and `libavahi-compat-libdnssd-dev` or
advertisement fails silently - see the mDNS section above.

Open questions to research: can the anisette server be bundled in the same stack, or does it need
its own identity/state? What is the minimum set of secrets, and can they be Docker secrets rather
than plain env vars? Does anything need to run privileged or with host devices for usbmuxd/netmuxd?

**F2. Web interface - now the main remaining piece, and the scope has grown.** Beyond sign-in,
2FA entry, device selection and health, the operator wants it to **help connect the phone**, so
that someone without background knowledge can get through setup. That is the right instinct:
pairing is where a novice gets stuck, and the failure modes are opaque - the device must be
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
deployment at all. `ShowAlert` (`:132`) has the same problem in reverse - it calls `getchar()` and
would block on an interactive TTY.

So the first research question is narrower than "build a web UI": **how often is 2FA actually
required?** If Apple's session or token is persisted under `AltServerData` and reused, this is a
one-time interactive step that could be handled by running the container once with `-it`, and a
web UI is then a convenience. If a code is needed on every refresh, an out-of-band way to submit
it is mandatory and the whole unattended premise depends on it. Establish that before designing
anything.

If built: it should cover sign-in, 2FA entry, device selection, manual refresh, and - given how
much of this session was spent on silent failures - visible health, i.e. is the server advertising,
is the anisette server reachable, when did the last successful refresh happen, and when do the
current certificates expire.

9. ~~**Accept the Apple ID password from somewhere other than argv.**~~ **DONE** - `4f40fdd`. `-p` puts the password in
   `ps` output for the life of the process and in shell history. An `ALTSERVER_APPLE_PASSWORD`
   env var, or reading from stdin when `-p` is absent, would fix it. Small, and it matters more
   once this runs unattended, where the password has to live somewhere anyway.

### CONFIRMED 2026-09-15: the 4-hourly scheduled build watched a repo that has not moved since 2022

`build.yml` carried `schedule: cron: "0 */4 * * *"`, inherited from NyaMisty's setup. What it
actually watched was easy to misread: **`upstream_repo`, which is
`rileytestut/AltServer-Windows`** -- not this repo's upstream, `NyaMisty/AltServer-Linux`. The pin
is `071b1dd`, 2022-04-25. Every run for years found nothing, 6 times a day.

The noise was not the real problem. On a hit the build job ran

```yaml
- name: Do Submodule Update
  if: ${{ needs.check.outputs.updated == '1' }}
  run: git submodule update --remote -- upstream_repo
```

and then built **all four architectures from that newer source without committing it**, so a
published artifact could come from code no commit in this repo describes. Given the build rewrites
those sources textually and `rewrite_altserver_source.py` has **no match guards at all**, an
upstream move could change the binary's behaviour with nothing failing anywhere. Unattended is the
worst possible place for that combination.

Removed the `schedule:` trigger. The same check still runs on demand through the existing
`sync_upstream` workflow_dispatch input, and the `github.event_name == 'schedule'` conditions in
the `check` and `matrix_setup` jobs were deliberately left in place, so restoring the cron line is
the only edit needed to bring the old behaviour back.

This does not close the underlying hazard -- it removes the unattended trigger for it. The real
fix is giving `rewrite_altserver_source.py` the same fail-loudly guards the other three rewriters
have, which remains on the TODO list above.

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
  real GitHub Release - and `action-gh-release@v3`'s `make_latest` has no default, so it may
  displace the current "latest".
- Which anisette server should we actually recommend? `nyamisty/alt_anisette_server` was last
  published April 2022 and is not verified against Apple's current flow.
