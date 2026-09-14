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
| `a266861` | **corecrypto**, 2 of 3 layers. Does *not* close #111. |
| `b885501` | **anisette error handling** rewritten; `mktime`→`timegm`; `ResetProvisioning` Windows-path bug. Closes #104. |

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
- **D. iOS-version signing compatibility — CONFIRMED IN SCOPE.** The target device runs
  **iOS 26.x**, so #131 applies: apps install successfully and then crash at launch. This is not
  hypothetical and cannot be worked around by configuration; it needs the `upstream_repo` bump
  (item 4 below), which is the largest remaining code task. Note the failure mode is
  *deceptive* — installation reports success, so everything looks fine until the app is opened.

Items A and C are deployment/config work rather than patches, and both need a real device to
confirm. B is a small patch. D is a substantial one. None are blocked by anything already done.

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
2. **PR #135 — sanitize `X-MMe-Client-Info`.** Rewrite `com.apple.dt.Xcode` → `com.apple.akd` at
   `src/AnisetteDataManager.cpp`, the single point where anisette data enters. Apple's GSA edge
   503s any request carrying that substring as of ~2026-09. Trivial. Closes no open issue
   (nobody has reported it — the anisette failure fired first) and needs a real Apple ID to
   confirm 503→401. Only two of four call sites hit `gsa.apple.com`; the others hit
   `developerservices2.apple.com` and were never in the author's A/B test.
3. **corecrypto layer 3.** `CORECRYPTO_SRCS` is populated at `CoreCryptoSources.cmake:189` and
   Linux subtracts `CORECRYPTO_EXCLUDE_SRCS` at `CMakeLists.txt:262`, but the list ends up empty
   at `add_library` (`:266`). Cheapest next probe: build the amd64 leg to see whether it is
   arch-specific. Closes #111.

### Bigger

4. **Bump `upstream_repo` to 1.7.4.** The only fix for #131 (iOS 26.4 launch crash — `ldid.cpp`
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
8. **Make mDNS advertisement failure loud.** `dnssd_loader.cpp` never waits on the forked
   python3 child and always returns success, so an unadvertised server is silently invisible —
   the single worst failure mode for unattended operation. Fix: `waitpid` with a short timeout,
   or at minimum check the child did not exit immediately, and log unmistakably on failure.
   Small patch, high value for this deployment.
9. **getopt hygiene** (`src/AltServerMain.cpp`). `case 'a'` has no `break` and falls through to
   `case 'p'`, so `-a` sets *both* appleID and password; `-h` is documented and handled at
   `case 'h'` but absent from the optstring `"u:i:a:p:P:d"`, so it is unreachable; five `char*`
   are uninitialised. All real UB — but fix as hygiene and claim no issue: across 16 pasted
   command lines in the issue corpus, nobody wrote `-p` before `-a`.

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
