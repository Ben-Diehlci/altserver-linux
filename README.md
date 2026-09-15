# AltServer-Linux

AltServer for AltStore, but on-device.

> **This is a fork** of [NyaMisty/AltServer-Linux](https://github.com/NyaMisty/AltServer-Linux),
> whose last real code commit predates 2025 and whose CI had been failing on every run. The goal
> here is **running unattended on a Linux home server**, so sideloaded apps keep refreshing on the
> 7-day cycle without a Mac or PC being powered on.
>
> - **[BOOTSTRAP.md](BOOTSTRAP.md)** — first-time setup, start to finish
> - **[REVIVAL.md](REVIVAL.md)** — what changed, why, and what is still broken
> - **[deploy/](deploy/)** — Portainer / compose stacks

---

## Status

| | |
|---|---|
| CI | **Fixed.** Was dying at "Set up job" on every run; no binaries since 2025-03 |
| Apple sign-in | **Working**, including 2FA, team lookup, device registration and certificate issuance |
| Apple's 2026 GSA client-info block | **Fixed** and confirmed in both directions against live Apple infrastructure |
| GrandSlam `429` on connection reuse | **Fixed**; proven with a zero-credential probe |
| corecrypto build (#111) | **Fixed.** The buildenv image is rebuildable from source again |
| iOS 26 launch crash (#131) | Fixed in code; AltStore **installs and launches** |
| Wireless refresh | netmuxd ships in the stack; the sockaddr-layout bug that broke every Wi-Fi connection is **fixed**, but not yet proven against a phone |
| AltJIT on iOS 17+ | **Not supported.** Needs personalised DDI, TSS signing and a RemoteXPC tunnel. Use [pymobiledevice3](https://github.com/doronz88/pymobiledevice3) |

---

## Quick start

Everything runs as one stack: AltServer, an anisette server, and a setup web UI.

**Portainer → Stacks → Add stack → Repository**, pointing at this repo with compose path
`deploy/altserver-stack.yml`. Or with plain compose:

```bash
docker compose -f deploy/altserver-stack.yml up -d
```

No host preparation is needed — it uses named volumes, and the image bundles every runtime
dependency. The AltStore IPA is fetched automatically on start, resolved from AltStore's own
catalogue so it is always current.

Then open **`http://<your-host>:8099`**.

The one prerequisite Docker cannot handle: **the phone must be paired over USB once.** Wireless
pairing is not possible in this build. After that, refreshing happens over Wi-Fi and the cable is
never needed again. The web UI walks you through it.

### The web UI

| Page | What it does |
|---|---|
| `/` | Health: anisette contract, clock, pairing, mDNS publication, process |
| `/pairing` | Guided pairing, distinguishing the "nothing shows up" cases |
| `/install` | Apple ID sign-in with 2FA entry in the browser |

The status page exists because **this software cannot report its own health.** avahi can report a
successful registration while publishing nothing; AltStore suppresses the one error it would raise
during a background refresh; and nearly everything is logged to stdout at info level, so
`journalctl -p err` stays empty no matter what breaks. Without an external check, the first symptom
of a dead deployment is an app that will not open, a week later.

> **The install page takes an Apple ID password over plain HTTP.** On a trusted LAN that is a
> considered trade-off. Anywhere else, set `--host 127.0.0.1` in the stack and use an SSH tunnel
> (`ssh -L 8099:127.0.0.1:8099 you@host`). Never put it behind a reverse proxy or a tunnel — this
> is LAN-only by design.

---

## Running it directly

```
Usage:  AltServer-Linux options [ ipa-file ]
  -h  --help             Display this usage information.
  -u  --udid UDID        Device's UDID, only needed when installing IPA.
  -a  --appleID AppleID  Apple ID to sign the ipa, only needed when installing IPA.
  -p  --password passwd  Password of Apple ID, only needed when installing IPA.
  -d  --debug            Print debug output, can be used several times to increase debug level.
```

No IPA argument starts the daemon. With one, it performs a one-time install — which needs a real
terminal, because the 2FA code is read from stdin.

### Environment

| Variable | Purpose |
|---|---|
| `ALTSERVER_ANISETTE_SERVER` | **Required.** Full URL including scheme, e.g. `http://127.0.0.1:6969`. There is no default |
| `ALTSERVER_UDID` / `ALTSERVER_APPLE_ID` / `ALTSERVER_APPLE_PASSWORD` | Alternatives to `-u` / `-a` / `-p`. Prefer these: a password passed as `-p` is visible in `ps` to every user on the host |
| `ALTSERVER_NO_CLIENTINFO_SANITIZE` | Set to `1` to stop rewriting `com.apple.dt.Xcode` in `X-MMe-Client-Info`. Diagnostic only — leave unset |
| `ALTSTORE_SKIP_FETCH` | Set to `1` to stop the container refreshing `AltStore.ipa` on start |

There is deliberately **no default anisette server**. The one that used to be hardcoded has
returned HTTP 502 since 2026-09, and pointing every user at a single shared anisette identity can
get Apple IDs locked.

---

## Runtime requirements

Bundled in the container image. Needed on the host if you run the binary directly:

| Requirement | Why | If missing |
|---|---|---|
| `python3` | The binary is `-static` and cannot dlopen Bonjour, so it shells out to python3 | Advertisement fails |
| `libavahi-compat-libdnssd-dev` | Provides the **unversioned** `libdns_sd.so` the code dlopens | Advertisement fails |
| `avahi-daemon` running | Performs the actual mDNS publishing | Advertisement fails |
| `usbmuxd` for cabled pairing; **`netmuxd` for Wi-Fi** | Device access | No device found |
| An anisette server | Apple machine identity | Sign-in fails |
| Accurate clock **on the anisette host** | Its timestamp is forwarded to Apple verbatim | Opaque `-36607` |

Note the **`-dev`** package, not `libavahi-compat-libdnssd1`: the runtime package ships only
`libdns_sd.so.1`, while the code dlopens the unversioned name. This is the single most common way
to end up with a server that runs, reports nothing wrong, and is invisible to your phone.

### Wi-Fi refresh

**Included in the stack** — there is nothing to install. The `netmuxd` service provides it.

This matters more than it sounds. Stock `usbmuxd` enumerates USB and nothing else, so with no cable
AltServer sees no device and every refresh fails as what looks like a device fault. And on Ubuntu
the `usbmuxd` unit is `static` and udev-activated — it starts when a cable is plugged in and
**exits when the last device is unplugged**, so an unattended server has no mux running at all:

```
$ idevice_id -l
ERROR: Unable to retrieve device list!     # not "no devices" — nothing was listening
$ systemctl is-active usbmuxd
inactive
```

[netmuxd](https://github.com/jkcoxson/netmuxd) browses `_apple-mobdev2._tcp`, tracks the phone, and
serves the usbmuxd protocol with the device presented as `ConnectionType: Network`.

It runs on **its own socket path** in a shared volume rather than taking over `/var/run/usbmuxd`.
The host's usbmuxd is then left completely alone to handle the cable for first-time pairing, and
nothing contends for anything. AltServer is pointed at netmuxd with `USBMUXD_SOCKET_ADDRESS`
(note the spelling — the widely-copied `USBMUXD_SOCKET_ADRESS`, one D, is silently ignored).

Requirements on the phone side, both normally already true after the USB pairing:

| Needs to be true | How to check from the server |
|---|---|
| Phone advertises itself | `avahi-browse -rt _apple-mobdev2._tcp` shows it |
| `lockdownd` accepts network connections | port **62078** open on the phone's IP |

Note that 62078 is the port that matters. The port in the mDNS TXT record is a different service
and refuses connections — which looks alarming and is not.

---

## Download

- Container image: `ghcr.io/<owner>/altserver-linux:latest`, built by
  [`build_image.yml`](.github/workflows/build_image.yml)
- Static binaries: GitHub Actions artifacts. Branch pushes build **amd64** only; tags build all
  four architectures. **`chmod +x` after downloading** — artifact upload does not preserve the
  executable bit

---

## Advanced: building from source

- Preparation: `git clone --recursive <this repo>`

- Easiest, using the same prebuilt toolchain CI uses (it already has corecrypto, cpprestsdk, boost
  and libzip):
  ```
  docker run --rm -v "$PWD":/workdir -w /workdir \
    ghcr.io/nyamisty/altserver_builder_alpine_amd64 \
    bash -c 'mkdir -p build; cd build; make -f ../Makefile -j"$(nproc)"'
  ```
  Or build the container image directly: `docker build -t altserver .`

- By hand (note the `cd build` — the Makefile builds into the *current* directory):
  ```
  cd AltServer-Linux
  mkdir build
  cd build
  make -f ../Makefile -j3
  ls AltServer-*
  ```

### How the build works

This project never forked AltServer-Windows. `upstream_repo/` is a submodule of it, and the build
**rewrites those sources at compile time** — `makefiles/rewrite_altserver_source.py` and friends
convert `L"…"` to `U("…")`, `std::wstring` to `std::string`, `boost::filesystem` to
`std::filesystem`, strip the Win32 GUI and splice in a console implementation. Win32 gaps are
filled by `-include shims/windows_shim.h`.

Patches to vendored code live in those rewriters rather than in the submodule, because the
libraries are submodules: an edit in place cannot be committed here — only the submodule pointer
would move, to a commit that does not exist upstream, breaking every fresh clone.

| Rewriter | Fails the build if its pattern stops matching |
|---|---|
| `makefiles/rewrite_altserver_source.py` | **No.** It has no `raise`, `assert` or `sys.exit` at all — a substitution that silently stops matching produces a quietly wrong binary |
| `makefiles/AltSign-build/rewrite_altsign_source.py` | Yes |
| `makefiles/AltSign-build/rewrite_ldid_source.py` | Yes |
| `makefiles/libimobiledevice-build/rewrite_idevice_source.py` | Yes |

That first row is a real gap, not a stylistic one: it is the largest rewriter and it patches the
code that talks to Apple. Fixing it is on the TODO list in [REVIVAL.md](REVIVAL.md).

### Building the buildenv image

`buildenv/Dockerfile` builds the toolchain. Apple's current corecrypto distribution needs three
fixes, all applied there:

1. The archive extracts to `corecrypto-2024/`, not `corecrypto/`. Docker's `WORKDIR` silently
   *creates* the missing directory, so the error surfaces one step later as a confusing
   "does not appear to contain CMakeLists.txt"
2. `CMakeLists.txt` includes `scripts/code-coverage.cmake`, which Apple does not ship
3. `CoreCryptoSources.cmake` still points at `corecrypto_static/ccrng_static.c`, which moved to the
   tree root. The visible error is "No SOURCES given to target"; the real one is the
   "Cannot find source file" line above it

The old note about removing `-mno-default` for ARM is **stale** — the Makefile already guards that
flag to i386/i686, so ARM builds work unmodified.

---

## Credits

Original Linux port by [NyaMisty](https://github.com/NyaMisty/AltServer-Linux). AltStore, AltServer
and AltSign by [Riley Testut](https://github.com/rileytestut). This fork only revives and extends
that work. Licensed AGPL-3.0, as upstream.
